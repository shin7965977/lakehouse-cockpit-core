"""dbt-First Metadata Catalog Service.

Ingests compiled dbt target artifacts (`manifest.json`, `catalog.json`, `run_results.json`,
`semantic_manifest.json`) from a configurable `DBT_TARGET_DIR` (or fallback sample manifest)
and builds a normalized searchable metadata index.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class CatalogColumn(BaseModel):
    name: str
    data_type: str = ""
    description: str = ""
    tests: list[str] = Field(default_factory=list)


class CatalogRelation(BaseModel):
    unique_id: str
    name: str
    schema_name: str = ""
    layer: str = "other"  # "gold" | "silver" | "bronze" | "source" | "other"
    resource_type: str = "model"
    description: str = ""
    columns: list[CatalogColumn] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    children: list[str] = Field(default_factory=list)
    last_run_status: str = "unknown"
    test_failures: int = 0
    compiled_sql: str = ""


class CatalogMetric(BaseModel):
    name: str
    label: str = ""
    description: str = ""
    metric_type: str = "simple"
    semantic_model: str = ""
    source_relation: str = ""
    time_granularity: str = ""
    filter_expr: str = ""
    dimensions: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)


class CatalogSearchResult(BaseModel):
    query: str
    metrics: list[CatalogMetric] = Field(default_factory=list)
    relations: list[CatalogRelation] = Field(default_factory=list)
    matched_columns: list[dict[str, str]] = Field(default_factory=list)


def _infer_layer(schema_name: str, model_name: str, fqn: list[str] | None = None) -> str:
    joined = " ".join([schema_name or "", model_name or ""] + (fqn or [])).lower()
    if "gold" in joined or model_name.lower().startswith(("fct_", "dim_", "mart_")):
        return "gold"
    if "silver" in joined or model_name.lower().startswith("int_"):
        return "silver"
    if "bronze" in joined or model_name.lower().startswith(("stg_", "raw_")):
        return "bronze"
    return "other"


def _load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


class DbtMetadataCatalog:
    """Normalized in-memory dbt-first metadata catalog."""

    def __init__(
        self,
        relations: list[CatalogRelation] | None = None,
        metrics: list[CatalogMetric] | None = None,
        source_dir: str = "",
        generated_at: str = "",
    ) -> None:
        self.relations: list[CatalogRelation] = relations or []
        self.metrics: list[CatalogMetric] = metrics or []
        self.source_dir = source_dir
        self.generated_at = generated_at
        self._relations_by_name: dict[str, CatalogRelation] = {
            r.name.lower(): r for r in self.relations
        }

    @classmethod
    def from_target_dir(cls, target_dir: str | Path | None = None) -> DbtMetadataCatalog:
        """Load dbt target artifacts from `target_dir` or `DBT_TARGET_DIR` env var."""
        resolved_dir: Path | None = None
        if target_dir:
            resolved_dir = Path(target_dir)
        elif os.getenv("DBT_TARGET_DIR", "").strip():
            resolved_dir = Path(os.getenv("DBT_TARGET_DIR", "").strip())

        if resolved_dir and resolved_dir.is_file():
            # Allow passing a single manifest.json path directly
            manifest = _load_json_if_exists(resolved_dir)
            catalog_raw: dict[str, Any] = {}
            run_results_raw: dict[str, Any] = {}
            semantic_raw: dict[str, Any] = {}
            source_label = str(resolved_dir)
        elif resolved_dir and resolved_dir.exists():
            manifest = _load_json_if_exists(resolved_dir / "manifest.json")
            catalog_raw = _load_json_if_exists(resolved_dir / "catalog.json")
            run_results_raw = _load_json_if_exists(resolved_dir / "run_results.json")
            semantic_raw = _load_json_if_exists(resolved_dir / "semantic_manifest.json")
            source_label = str(resolved_dir)
        else:
            # Fallback to bundled sample manifest when DBT_TARGET_DIR is unset
            repo_root = Path(__file__).resolve().parent.parent
            sample_manifest = repo_root / "examples" / "sample_manifest.json"
            manifest = _load_json_if_exists(sample_manifest)
            catalog_raw = {}
            run_results_raw = {}
            semantic_raw = {}
            source_label = str(sample_manifest) if sample_manifest.exists() else "empty"

        return cls.from_payloads(
            manifest=manifest,
            catalog=catalog_raw,
            run_results=run_results_raw,
            semantic_manifest=semantic_raw,
            source_dir=source_label,
        )

    @classmethod
    def from_payloads(
        cls,
        manifest: dict[str, Any] | None = None,
        catalog: dict[str, Any] | None = None,
        run_results: dict[str, Any] | None = None,
        semantic_manifest: dict[str, Any] | None = None,
        source_dir: str = "in-memory",
    ) -> DbtMetadataCatalog:
        """Build a normalized catalog directly from parsed dbt JSON dictionaries."""
        manifest = manifest or {}
        catalog = catalog or {}
        run_results = run_results or {}
        semantic_manifest = semantic_manifest or {}

        generated_at = str(
            manifest.get("metadata", {}).get("generated_at")
            or run_results.get("metadata", {}).get("generated_at")
            or ""
        )

        # Index run_results by unique_id
        run_status_map: dict[str, str] = {}
        test_failure_map: dict[str, int] = {}
        for res in run_results.get("results", []):
            if not isinstance(res, dict):
                continue
            uid = str(res.get("unique_id", ""))
            status = str(res.get("status", "unknown"))
            run_status_map[uid] = status
            if uid.startswith("test.") and status not in ("pass", "success"):
                test_failure_map[uid] = int(res.get("failures") or 1)

        # Index physical columns from catalog.json
        catalog_nodes = catalog.get("nodes", {}) if isinstance(catalog.get("nodes"), dict) else {}
        child_map = manifest.get("child_map", {}) if isinstance(manifest.get("child_map"), dict) else {}
        nodes = manifest.get("nodes", {}) if isinstance(manifest.get("nodes"), dict) else {}

        # Map tests attached to each model
        model_tests: dict[str, list[str]] = {}
        model_test_failures: dict[str, int] = {}
        for uid, node in nodes.items():
            if not isinstance(node, dict) or node.get("resource_type") != "test":
                continue
            deps = node.get("depends_on", {}).get("nodes", [])
            test_name = str(node.get("name", uid))
            failed = test_failure_map.get(uid, 0)
            for dep in deps:
                model_tests.setdefault(dep, []).append(test_name)
                model_test_failures[dep] = model_test_failures.get(dep, 0) + failed

        relations: list[CatalogRelation] = []
        for uid, node in nodes.items():
            if not isinstance(node, dict) or node.get("resource_type") != "model":
                continue
            name = str(node.get("name", ""))
            schema_name = str(node.get("schema", ""))
            fqn = node.get("fqn") if isinstance(node.get("fqn"), list) else []
            layer = _infer_layer(schema_name, name, [str(x) for x in fqn])

            doc_cols = node.get("columns", {}) if isinstance(node.get("columns"), dict) else {}
            phys_cols = (
                catalog_nodes.get(uid, {}).get("columns", {})
                if isinstance(catalog_nodes.get(uid), dict)
                else {}
            )

            all_col_names: list[str] = []
            for cname in list(phys_cols.keys()) + list(doc_cols.keys()):
                if cname not in all_col_names:
                    all_col_names.append(cname)

            columns: list[CatalogColumn] = []
            for cname in all_col_names:
                p_meta = phys_cols.get(cname, {}) if isinstance(phys_cols.get(cname), dict) else {}
                d_meta = doc_cols.get(cname, {}) if isinstance(doc_cols.get(cname), dict) else {}
                columns.append(
                    CatalogColumn(
                        name=str(p_meta.get("name") or d_meta.get("name") or cname),
                        data_type=str(p_meta.get("type") or d_meta.get("data_type") or ""),
                        description=str(d_meta.get("description") or p_meta.get("comment") or ""),
                    )
                )

            raw_deps = node.get("depends_on", {}).get("nodes", [])
            clean_deps = [str(d).split(".")[-1] for d in raw_deps]
            clean_children = [
                str(c).split(".")[-1]
                for c in child_map.get(uid, [])
                if str(c).startswith("model.")
            ]

            relations.append(
                CatalogRelation(
                    unique_id=uid,
                    name=name,
                    schema_name=schema_name,
                    layer=layer,
                    resource_type="model",
                    description=str(node.get("description", "")),
                    columns=columns,
                    depends_on=clean_deps,
                    children=clean_children,
                    last_run_status=run_status_map.get(uid, "unknown"),
                    test_failures=model_test_failures.get(uid, 0),
                    compiled_sql=str(node.get("compiled_code") or node.get("raw_code") or ""),
                )
            )

        # Parse MetricFlow semantic_models and metrics (from semantic_manifest.json or manifest.json)
        sm_list = semantic_manifest.get("semantic_models") or list(
            (manifest.get("semantic_models") or {}).values()
            if isinstance(manifest.get("semantic_models"), dict)
            else []
        )
        measure_to_sm: dict[str, dict[str, Any]] = {}
        for sm in sm_list:
            if not isinstance(sm, dict):
                continue
            sm_name = str(sm.get("name", ""))
            node_rel = sm.get("node_relation") or {}
            rel_name = str(node_rel.get("alias") or sm.get("model") or "")
            dims = [str(d.get("name", "")) for d in sm.get("dimensions", []) if isinstance(d, dict)]
            ents = [str(e.get("name", "")) for e in sm.get("entities", []) if isinstance(e, dict)]
            for meas in sm.get("measures", []):
                if isinstance(meas, dict) and meas.get("name"):
                    measure_to_sm[str(meas["name"])] = {
                        "semantic_model": sm_name,
                        "source_relation": rel_name,
                        "dimensions": dims,
                        "entities": ents,
                    }

        raw_metrics = semantic_manifest.get("metrics") or list(
            (manifest.get("metrics") or {}).values()
            if isinstance(manifest.get("metrics"), dict)
            else []
        )
        metrics: list[CatalogMetric] = []
        for m in raw_metrics:
            if not isinstance(m, dict) or not m.get("name"):
                continue
            tp = m.get("type_params") if isinstance(m.get("type_params"), dict) else {}
            measure_ref = tp.get("measure")
            measure_name = (
                str(measure_ref.get("name", ""))
                if isinstance(measure_ref, dict)
                else str(measure_ref or "")
            )
            sm_info = measure_to_sm.get(measure_name, {})
            filt = m.get("filter")
            filter_expr = (
                str(filt.get("where_sql_template", ""))
                if isinstance(filt, dict)
                else str(filt or "")
            )
            metrics.append(
                CatalogMetric(
                    name=str(m["name"]),
                    label=str(m.get("label") or m["name"]),
                    description=str(m.get("description") or ""),
                    metric_type=str(m.get("type") or "simple"),
                    semantic_model=sm_info.get("semantic_model", ""),
                    source_relation=sm_info.get("source_relation", ""),
                    time_granularity=str(m.get("time_granularity") or ""),
                    filter_expr=filter_expr,
                    dimensions=sm_info.get("dimensions", []),
                    entities=sm_info.get("entities", []),
                )
            )

        return cls(
            relations=relations,
            metrics=metrics,
            source_dir=source_dir,
            generated_at=generated_at,
        )

    @classmethod
    def from_live_schema(
        cls,
        tables: list[dict[str, Any]],
        source_label: str = "live_erp_postgres",
        metrics: list[dict[str, Any]] | None = None,
    ) -> DbtMetadataCatalog:
        """Build a normalized catalog directly from live database information_schema reflection."""
        relations: list[CatalogRelation] = []
        for tbl in tables:
            if not isinstance(tbl, dict):
                continue
            name = str(tbl.get("name", "")).strip()
            if not name:
                continue
            schema_name = str(tbl.get("schema", "public")).strip() or "public"
            raw_cols = tbl.get("columns") or []
            cols = [
                CatalogColumn(
                    name=str(c.get("name", "")).strip(),
                    data_type=str(c.get("data_type", "")).strip(),
                    description=str(c.get("description", "")).strip(),
                )
                for c in raw_cols
                if isinstance(c, dict) and str(c.get("name", "")).strip()
            ]
            relations.append(
                CatalogRelation(
                    unique_id=f"live.{schema_name}.{name}",
                    name=name,
                    schema_name=schema_name,
                    layer=str(tbl.get("layer", "gold")).strip() or "gold",
                    resource_type="table",
                    description=str(tbl.get("description") or f"Live ERP PostgreSQL table ({schema_name}.{name})"),
                    columns=cols,
                    last_run_status="live",
                )
            )

        parsed_metrics: list[CatalogMetric] = []
        for m in metrics or []:
            if not isinstance(m, dict):
                continue
            m_name = str(m.get("name", "")).strip()
            if not m_name:
                continue
            parsed_metrics.append(
                CatalogMetric(
                    name=m_name,
                    label=str(m.get("label") or m_name),
                    description=str(m.get("description") or ""),
                    metric_type=str(m.get("metric_type") or m.get("type") or "simple"),
                    semantic_model=str(m.get("semantic_model") or ""),
                    source_relation=str(m.get("source_relation") or m.get("relation_name") or ""),
                    time_granularity=str(m.get("time_granularity") or ""),
                    filter_expr=str(m.get("filter_expr") or ""),
                    dimensions=[str(d) for d in (m.get("dimensions") or []) if str(d).strip()],
                    entities=[str(e) for e in (m.get("entities") or []) if str(e).strip()],
                )
            )
        return cls(relations=relations, metrics=parsed_metrics, source_dir=source_label)

    def get_relation(self, name: str) -> CatalogRelation | None:
        return self._relations_by_name.get(name.strip().lower())

    def search(self, query: str, limit: int = 10) -> CatalogSearchResult:
        """Search metrics, relations, and columns by keyword tokens."""
        q = (query or "").strip().lower()
        tokens = [t for t in q.replace(",", " ").replace("、", " ").split() if t]

        def _matches(text: str) -> bool:
            if not q:
                return True
            low = text.lower()
            return q in low or any(tok in low for tok in tokens)

        matched_metrics = [
            m
            for m in self.metrics
            if _matches(f"{m.name} {m.label} {m.description} {m.source_relation}")
        ][:limit]

        matched_relations: list[CatalogRelation] = []
        matched_columns: list[dict[str, str]] = []
        for rel in self.relations:
            rel_text = f"{rel.name} {rel.schema_name} {rel.layer} {rel.description}"
            rel_hit = _matches(rel_text)
            col_hit = False
            for col in rel.columns:
                if _matches(f"{col.name} {col.description}"):
                    col_hit = True
                    matched_columns.append(
                        {
                            "relation": rel.name,
                            "layer": rel.layer,
                            "column": col.name,
                            "data_type": col.data_type,
                            "description": col.description,
                        }
                    )
            if rel_hit or col_hit:
                matched_relations.append(rel)

        return CatalogSearchResult(
            query=query,
            metrics=matched_metrics,
            relations=matched_relations[:limit],
            matched_columns=matched_columns[: limit * 2],
        )

    def summary(self) -> dict[str, Any]:
        layer_counts: dict[str, int] = {}
        for r in self.relations:
            layer_counts[r.layer] = layer_counts.get(r.layer, 0) + 1
        return {
            "source_dir": self.source_dir,
            "generated_at": self.generated_at,
            "relation_count": len(self.relations),
            "metric_count": len(self.metrics),
            "layers": layer_counts,
        }
