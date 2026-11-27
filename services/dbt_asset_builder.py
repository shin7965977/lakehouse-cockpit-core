"""Data Contract, Minimal dbt Model & Separate Lightdash Semantic Builder."""

from __future__ import annotations

import re
from typing import Literal

import sqlglot
from sqlglot import exp
import yaml
from pydantic import BaseModel, Field

try:
    from .query_spec import AnalyticsAssetContract, CertificationLabel, QuerySpec
except (ImportError, ValueError):
    try:
        from services.query_spec import AnalyticsAssetContract, CertificationLabel, QuerySpec
    except (ImportError, ValueError):
        from cockpit_core.services.query_spec import (
            AnalyticsAssetContract,
            CertificationLabel,
            QuerySpec,
        )

LightdashStatus = Literal["STAGING_LOCAL_ONLY", "DEPLOYED"]


class BIAssetManifest(BaseModel):
    asset_id: str
    asset_version: int = 1
    environment: str = "staging"
    model_name: str
    warehouse_relation: str
    grain: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    measures: list[str] = Field(default_factory=list)
    upstream_models: list[str] = Field(default_factory=list)
    lightdash_explore: str = ""
    lightdash_status: LightdashStatus = "STAGING_LOCAL_ONLY"
    certification_status: CertificationLabel = "PROPOSED_ASSET"
    owner: str = "analytics-engineering@company.com"
    tenant_policy: str = "server_session_tenant_id"


class DbtAndLightdashBundle(BaseModel):
    contract: AnalyticsAssetContract
    dbt_sql: str
    schema_yaml: str
    lightdash_yaml: str
    bi_asset_manifest: BIAssetManifest


def _sanitize_model_name(name: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_]+", "_", (name or "mart_custom_asset").strip().lower()).strip("_")
    return clean or "mart_custom_asset"


def _lightdash_metric_type(agg_func: str) -> str:
    mapping = {
        "SUM": "sum",
        "AVG": "average",
        "COUNT": "count",
        "COUNT_DISTINCT": "count_distinct",
        "MIN": "min",
        "MAX": "max",
    }
    return mapping.get((agg_func or "SUM").upper(), "sum")


def _lightdash_dim_type(col_name: str, data_type: str) -> str:
    low_name = col_name.lower()
    low_type = (data_type or "").lower()
    if "date" in low_type or "timestamp" in low_type or low_name.endswith("_date"):
        return "date"
    if any(tok in low_type for tok in ("int", "double", "float", "numeric", "decimal")):
        return "number"
    return "string"


def build_dbt_and_lightdash_bundle(
    *,
    query_spec: QuerySpec,
    model_name: str,
    grain: list[str],
    business_owner: str = "analytics-engineering@company.com",
    column_types: dict[str, str] | None = None,
    freshness_sla: str = "24h",
    schema_name: str = "gold",
) -> DbtAndLightdashBundle:
    """Synthesize minimal dbt SQL, enforced schema.yml, separate Lightdash YAML, and BI Asset Manifest."""
    safe_model = _sanitize_model_name(model_name)
    col_types = {k: v for k, v in (column_types or {}).items()}
    upstream_relations = list(
        dict.fromkeys(
            [s.relation for s in query_spec.data_sources if s.relation]
            + [j.right_relation for j in query_spec.joins if j.right_relation]
        )
    )

    # 1. Strip outer LIMIT and ORDER BY via SQLGlot AST (preserving Window OVER (ORDER BY ...))
    #    so materialized Gold Mart is complete, then replace FROM/JOIN upstream tables with {{ ref(...) }}
    raw_sql = query_spec.compile_sql()
    cleaned_raw = raw_sql.strip().rstrip(";")
    sql_dialect = "postgres" if query_spec.dialect == "postgres" else "duckdb"
    try:
        parsed_ast = sqlglot.parse_one(cleaned_raw, read=sql_dialect)
        if isinstance(parsed_ast, (exp.Select, exp.Union)):
            parsed_ast.set("limit", None)
            parsed_ast.set("order", None)
        unbounded_sql = parsed_ast.sql(dialect=sql_dialect)
    except Exception:
        unbounded_sql = re.sub(r"\s+LIMIT\s+\d+\s*$", "", cleaned_raw, flags=re.IGNORECASE)
    dbt_body = unbounded_sql
    for rel in upstream_relations:
        # Only replace table references in FROM/JOIN clauses so qualified column prefixes like
        # `"sales_orders"."id"` are preserved cleanly as valid SQL.
        dbt_body = re.sub(
            rf'(\b(?:FROM|JOIN)\s+)(?:"{re.escape(rel)}"|\b{re.escape(rel)}\b)',
            rf"\1{{{{ ref('{rel}') }}}}",
            dbt_body,
            flags=re.IGNORECASE,
        )

    dbt_sql = (
        f"-- Governed dbt Gold Mart synthesized from {query_spec.spec_id} (spec_hash={query_spec.compute_spec_hash()[:12]})\n"
        f"{{{{ config(materialized='table') }}}}\n\n"
        f"{dbt_body}\n"
    )

    # 2. Collect output columns (dimensions + measures)
    dim_cols = list(dict.fromkeys(list(query_spec.projection) + list(grain)))
    agg_map: dict[str, str] = {}
    for agg in query_spec.aggregations:
        alias = agg.alias or f"{agg.func.lower()}_{agg.field.split('.')[-1]}"
        agg_map[alias] = agg.func
    measure_cols = list(agg_map.keys())
    all_cols = list(dict.fromkeys(dim_cols + measure_cols))

    # 3. Enforced dbt Data Contract (schema.yml)
    schema_columns = []
    for col in all_cols:
        dtype = col_types.get(col, "double" if col in agg_map else "varchar")
        col_entry: dict[str, object] = {
            "name": col,
            "data_type": dtype,
            "constraints": [{"type": "not_null"}] if col in grain else [],
            "tests": ["not_null"] if col in grain else [],
        }
        schema_columns.append(col_entry)

    model_tests: list[object] = []
    if len(grain) == 1:
        for c in schema_columns:
            if c["name"] == grain[0]:
                c["tests"] = ["not_null", "unique"]
    elif len(grain) > 1:
        model_tests.append(
            {
                "dbt_utils.unique_combination_of_columns": {
                    "combination_of_columns": list(grain),
                }
            }
        )

    schema_doc = {
        "version": 2,
        "models": [
            {
                "name": safe_model,
                "description": f"Governed mart for: {query_spec.question or safe_model}",
                "config": {"contract": {"enforced": True}},
                "meta": {
                    "owner": business_owner,
                    "sla_freshness": freshness_sla,
                    "spec_id": query_spec.spec_id,
                    "spec_hash": query_spec.compute_spec_hash(),
                },
                "tests": model_tests,
                "columns": schema_columns,
            }
        ],
    }
    schema_yaml = yaml.safe_dump(schema_doc, sort_keys=False, allow_unicode=True)

    # 4. Separate dbt-native Lightdash semantic YAML (meta.dimension / meta.metrics, never MetricFlow syntax)
    ld_columns = []
    for col in all_cols:
        dtype = col_types.get(col, "double" if col in agg_map else "varchar")
        if col in agg_map:
            ld_type = _lightdash_metric_type(agg_map[col])
            ld_columns.append(
                {
                    "name": col,
                    "meta": {
                        "dimension": {"type": "number", "hidden": True},
                        "metrics": {
                            f"{col}_metric": {
                                "type": ld_type,
                                "label": col.replace("_", " ").title(),
                                "description": f"Canonical {ld_type} metric for {col}",
                            }
                        },
                    },
                }
            )
        else:
            ld_columns.append(
                {
                    "name": col,
                    "meta": {
                        "dimension": {
                            "type": _lightdash_dim_type(col, dtype),
                            "label": col.replace("_", " ").title(),
                        }
                    },
                }
            )

    lightdash_doc = {
        "version": 2,
        "models": [
            {
                "name": safe_model,
                "meta": {
                    "label": safe_model.replace("_", " ").title(),
                    "group_label": "AI Analytics Workbench",
                },
                "columns": ld_columns,
            }
        ],
    }
    lightdash_yaml = yaml.safe_dump(lightdash_doc, sort_keys=False, allow_unicode=True)

    asset_id = f"asset_{query_spec.compute_spec_hash()[:8]}"
    contract = AnalyticsAssetContract(
        asset_id=asset_id,
        asset_version=query_spec.version,
        model_name=safe_model,
        business_owner=business_owner,
        query_spec=query_spec,
        metric_expression=", ".join(f"{fn}({c})" for c, fn in agg_map.items()),
        grain=list(grain),
        freshness_sla=freshness_sla,
        lightdash_explore=safe_model,
        certification_status="PROPOSED_ASSET",
    )
    manifest = BIAssetManifest(
        asset_id=asset_id,
        asset_version=query_spec.version,
        environment="staging",
        model_name=safe_model,
        warehouse_relation=f"{schema_name}.{safe_model}",
        grain=list(grain),
        dimensions=dim_cols,
        measures=measure_cols,
        upstream_models=upstream_relations,
        lightdash_explore=safe_model,
        lightdash_status="STAGING_LOCAL_ONLY",
        certification_status="PROPOSED_ASSET",
        owner=business_owner,
    )
    return DbtAndLightdashBundle(
        contract=contract,
        dbt_sql=dbt_sql,
        schema_yaml=schema_yaml,
        lightdash_yaml=lightdash_yaml,
        bi_asset_manifest=manifest,
    )
