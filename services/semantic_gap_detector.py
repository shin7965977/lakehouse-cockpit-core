"""Deterministic 7-State Semantic Gap Detector backed by DbtMetadataCatalog."""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, Field

try:
    from .dbt_catalog import CatalogMetric, DbtMetadataCatalog
except (ImportError, ValueError):
    try:
        from services.dbt_catalog import CatalogMetric, DbtMetadataCatalog
    except (ImportError, ValueError):
        from cockpit_core.services.dbt_catalog import CatalogMetric, DbtMetadataCatalog


class GapClassification(str, Enum):
    REUSE_EXISTING = "REUSE_EXISTING"
    NEW_SEMANTIC_METRIC = "NEW_SEMANTIC_METRIC"
    NEW_DBT_MODEL = "NEW_DBT_MODEL"
    TRANSFORMATION_GAP = "TRANSFORMATION_GAP"
    SOURCE_GAP = "SOURCE_GAP"
    SEMANTIC_AMBIGUITY = "SEMANTIC_AMBIGUITY"
    ACCESS_BLOCKED = "ACCESS_BLOCKED"


class GapDetectionResult(BaseModel):
    classification: GapClassification
    question: str
    reason: str
    matched_metric: CatalogMetric | None = None
    candidate_metrics: list[CatalogMetric] = Field(default_factory=list)
    candidate_relations: list[str] = Field(default_factory=list)
    matched_columns: list[dict[str, str]] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


# Generic ambiguous business terms that map to multiple competing accounting/operational metrics
_DEFAULT_AMBIGUOUS_TERMS: dict[str, list[str]] = {
    "營收": ["operating_revenue", "sales_order_amount", "total_income", "total_gmv"],
    "revenue": ["operating_revenue", "sales_order_amount", "total_income", "total_gmv"],
    "收入": ["operating_revenue", "total_income"],
}

# Explicit fine-grained entity keywords that cannot be fabricated from monthly aggregate tables
_FINE_GRAIN_KEYWORDS = (
    "客戶別",
    "每位客戶",
    "單筆訂單",
    "訂單明細",
    "customer_id",
    "customer-level",
    "order-level",
    "order_id",
)


def _extract_tokens(text: str) -> list[str]:
    clean = re.sub(r"[^\w\u4e00-\u9fff]+", " ", (text or "").lower())
    return [tok for tok in clean.split() if len(tok) >= 2]


def classify_semantic_gap(
    question: str,
    catalog: DbtMetadataCatalog,
    *,
    is_authorized: bool = True,
    allowed_relations: set[str] | None = None,
) -> GapDetectionResult:
    """Deterministically classify a user question into one of the 7 governance states."""
    q = (question or "").strip()
    q_low = q.lower()

    # 1. Access control gate
    if not is_authorized:
        return GapDetectionResult(
            classification=GapClassification.ACCESS_BLOCKED,
            question=q,
            reason="Caller is not authorized to query this analytical domain.",
            evidence=["role_authorization=denied"],
        )

    # Filter catalog view if caller has an explicit relation allowlist
    visible_relations = [
        r
        for r in catalog.relations
        if allowed_relations is None or r.name in allowed_relations
    ]
    visible_rel_names = {r.name for r in visible_relations}
    visible_metrics = [
        m
        for m in catalog.metrics
        if not m.source_relation
        or allowed_relations is None
        or m.source_relation in visible_rel_names
    ]

    # 2. Check for exact / unambiguous certified metric hit first
    exact_metric_hits: list[CatalogMetric] = []
    for m in visible_metrics:
        name_low = m.name.lower()
        label_low = m.label.lower()
        # Extract parenthetical Chinese/English alias inside label, e.g. "Operating Revenue (營業收入)"
        alias_parts = [p.strip() for p in re.split(r"[()（）]", label_low) if len(p.strip()) >= 2]
        if (
            name_low in q_low
            or (label_low and label_low in q_low)
            or any(alias in q_low for alias in alias_parts)
        ):
            exact_metric_hits.append(m)

    # Check whether question requests fine-grained entity missing from matched relation
    asks_fine_grain = any(kw in q_low for kw in _FINE_GRAIN_KEYWORDS)

    if len(exact_metric_hits) == 1 and not asks_fine_grain:
        hit = exact_metric_hits[0]
        return GapDetectionResult(
            classification=GapClassification.REUSE_EXISTING,
            question=q,
            reason=f"Matched certified MetricFlow metric '{hit.name}' ({hit.label}).",
            matched_metric=hit,
            candidate_metrics=[hit],
            candidate_relations=[hit.source_relation] if hit.source_relation else [],
            evidence=[
                f"metric={hit.name}",
                f"source_relation={hit.source_relation or 'unknown'}",
                f"semantic_model={hit.semantic_model or 'unknown'}",
            ],
        )

    if len(exact_metric_hits) > 1:
        return GapDetectionResult(
            classification=GapClassification.SEMANTIC_AMBIGUITY,
            question=q,
            reason="Multiple certified metrics match this question; explicit business disambiguation is required.",
            candidate_metrics=exact_metric_hits,
            candidate_relations=sorted({m.source_relation for m in exact_metric_hits if m.source_relation}),
            evidence=[f"ambiguous_metrics={[m.name for m in exact_metric_hits]}"],
        )

    # 3. Check known ambiguous business terms when multiple competing metrics exist in catalog
    for term, candidate_names in _DEFAULT_AMBIGUOUS_TERMS.items():
        if term in q_low and not asks_fine_grain:
            competing = [m for m in visible_metrics if m.name.lower() in candidate_names]
            if len(competing) >= 2:
                return GapDetectionResult(
                    classification=GapClassification.SEMANTIC_AMBIGUITY,
                    question=q,
                    reason=(
                        f"Term '{term}' is ambiguous across multiple certified metrics "
                        f"({', '.join(m.name for m in competing)}). Please clarify the intended definition."
                    ),
                    candidate_metrics=competing,
                    candidate_relations=sorted({m.source_relation for m in competing if m.source_relation}),
                    evidence=[f"ambiguous_term={term}", f"candidates={[m.name for m in competing]}"],
                )

    # 4. Inspect catalog columns & relations across Gold / Silver / Bronze layers
    tokens = _extract_tokens(q)
    matched_cols: list[dict[str, str]] = []
    layers_hit: dict[str, set[str]] = {"gold": set(), "silver": set(), "bronze": set(), "other": set()}

    for rel in visible_relations:
        rel_name_low = rel.name.lower()
        rel_desc_low = rel.description.lower()
        for col in rel.columns:
            col_low = col.name.lower()
            col_desc_low = col.description.lower()
            if col_low in q_low or any(
                tok in col_low or (len(tok) >= 3 and tok in col_desc_low) for tok in tokens
            ):
                matched_cols.append(
                    {
                        "relation": rel.name,
                        "layer": rel.layer,
                        "column": col.name,
                        "data_type": col.data_type,
                        "description": col.description,
                    }
                )
                layers_hit.setdefault(rel.layer, set()).add(rel.name)
        if rel_name_low in q_low or any(tok in rel_name_low or (len(tok) >= 4 and tok in rel_desc_low) for tok in tokens):
            layers_hit.setdefault(rel.layer, set()).add(rel.name)

    # If user explicitly asked for fine-grain entity (e.g. customer_id / order_id) check if ANY column actually provides it
    if asks_fine_grain:
        has_fine_col = any(
            any(k in c["column"].lower() for k in ("customer", "order_id", "return_reason"))
            for c in matched_cols
        )
        if not has_fine_col:
            return GapDetectionResult(
                classification=GapClassification.SOURCE_GAP,
                question=q,
                reason="Requested fine-grained entity/grain is absent from all authorized catalog relations.",
                candidate_relations=sorted(layers_hit["gold"] | layers_hit["silver"] | layers_hit["bronze"]),
                matched_columns=matched_cols[:10],
                evidence=[
                    "requested_fine_grain_not_in_catalog",
                    f"inspected_relations={len(visible_relations)}",
                ],
            )

    if layers_hit["gold"]:
        rels = sorted(layers_hit["gold"])
        return GapDetectionResult(
            classification=GapClassification.NEW_SEMANTIC_METRIC,
            question=q,
            reason=f"Required fields exist in Gold relation(s) {rels}, but no certified MetricFlow metric matches.",
            candidate_relations=rels,
            matched_columns=matched_cols[:15],
            evidence=[f"gold_relations={rels}", f"matched_columns={len(matched_cols)}"],
        )

    if layers_hit["silver"]:
        rels = sorted(layers_hit["silver"])
        return GapDetectionResult(
            classification=GapClassification.NEW_DBT_MODEL,
            question=q,
            reason=f"Required inputs exist in Silver relation(s) {rels}, but no Gold mart exposes this grain.",
            candidate_relations=rels,
            matched_columns=matched_cols[:15],
            evidence=[f"silver_relations={rels}", f"matched_columns={len(matched_cols)}"],
        )

    if layers_hit["bronze"]:
        rels = sorted(layers_hit["bronze"])
        return GapDetectionResult(
            classification=GapClassification.TRANSFORMATION_GAP,
            question=q,
            reason=f"Required fields exist only in Bronze staging relation(s) {rels} and require Silver/Gold transformation.",
            candidate_relations=rels,
            matched_columns=matched_cols[:15],
            evidence=[f"bronze_relations={rels}", f"matched_columns={len(matched_cols)}"],
        )

    return GapDetectionResult(
        classification=GapClassification.SOURCE_GAP,
        question=q,
        reason="No matching relation, column, or metric was found in the authorized dbt catalog.",
        candidate_relations=[],
        matched_columns=[],
        evidence=[f"inspected_relations={len(visible_relations)}", f"inspected_metrics={len(visible_metrics)}"],
    )
