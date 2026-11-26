"""Deterministic 7-State Semantic Gap Detector backed by DbtMetadataCatalog."""

from __future__ import annotations

import re
from enum import Enum

import sqlglot
from pydantic import BaseModel, Field
from sqlglot import exp

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
        # Extract parenthetical / slash-separated Chinese/English aliases inside label,
        # e.g. "Sales Revenue (銷售訂單業績 / 銷售額)"
        alias_parts = [p.strip() for p in re.split(r"[()（）/、]", label_low) if len(p.strip()) >= 2]
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
    explicit_layers_hit: dict[str, set[str]] = {"gold": set(), "silver": set(), "bronze": set(), "other": set()}

    # Statistical document-frequency filter (zero hardcoded blacklists):
    # columns or description tokens appearing in >30% of visible tables lack table-level discriminating power.
    col_doc_freq: dict[str, int] = {}
    desc_doc_freq: dict[str, int] = {}
    for rel in visible_relations:
        for c_name in {c.name.lower() for c in rel.columns}:
            col_doc_freq[c_name] = col_doc_freq.get(c_name, 0) + 1
        for d_tok in set(_extract_tokens(rel.description)):
            desc_doc_freq[d_tok] = desc_doc_freq.get(d_tok, 0) + 1
    total_rels = len(visible_relations)

    def _is_ubiquitous_desc_tok(tok: str) -> bool:
        return total_rels >= 5 and (desc_doc_freq.get(tok, 0) / total_rels) > 0.30

    def _whole_ident_in_q(ident: str) -> bool:
        if not ident:
            return False
        return bool(re.search(rf"(?<![a-z0-9_]){re.escape(ident)}(?![a-z0-9_])", q_low))

    for rel in visible_relations:
        rel_name_low = rel.name.lower()
        rel_desc_tokens = set(_extract_tokens(rel.description))
        rel_explicitly_named = _whole_ident_in_q(rel_name_low) or any(
            (tok == rel_name_low)
            or (len(tok) >= 4 and tok in rel_desc_tokens and not _is_ubiquitous_desc_tok(tok))
            for tok in tokens
        )
        if rel_explicitly_named:
            explicit_layers_hit.setdefault(rel.layer, set()).add(rel.name)
            layers_hit.setdefault(rel.layer, set()).add(rel.name)

        for col in rel.columns:
            col_low = col.name.lower()
            col_desc_low = col.description.lower()
            is_ubiquitous = total_rels >= 5 and (col_doc_freq.get(col_low, 0) / total_rels) > 0.30
            col_exact_hit = _whole_ident_in_q(col_low) or any(
                (tok == col_low) or (len(tok) >= 3 and tok in col_desc_low) for tok in tokens
            )
            if col_exact_hit:
                if is_ubiquitous and not rel_explicitly_named:
                    continue
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

    # When explicit relation name(s) matched in the question, prioritize them over noisy column-only hits
    for layer_key in ("gold", "silver", "bronze", "other"):
        if explicit_layers_hit.get(layer_key):
            layers_hit[layer_key] = set(explicit_layers_hit[layer_key])

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
        display_rels = rels[:6] if len(rels) <= 6 else [*rels[:6], f"... (+{len(rels) - 6} more)"]
        return GapDetectionResult(
            classification=GapClassification.NEW_SEMANTIC_METRIC,
            question=q,
            reason=f"Required fields exist in Gold relation(s) {display_rels}, but no certified MetricFlow metric matches.",
            candidate_relations=rels,
            matched_columns=matched_cols[:15],
            evidence=[f"gold_relations={display_rels}", f"matched_columns={len(matched_cols)}"],
        )

    if layers_hit["silver"]:
        rels = sorted(layers_hit["silver"])
        display_rels = rels[:6] if len(rels) <= 6 else [*rels[:6], f"... (+{len(rels) - 6} more)"]
        return GapDetectionResult(
            classification=GapClassification.NEW_DBT_MODEL,
            question=q,
            reason=f"Required inputs exist in Silver relation(s) {display_rels}, but no Gold mart exposes this grain.",
            candidate_relations=rels,
            matched_columns=matched_cols[:15],
            evidence=[f"silver_relations={display_rels}", f"matched_columns={len(matched_cols)}"],
        )

    if layers_hit["bronze"]:
        rels = sorted(layers_hit["bronze"])
        display_rels = rels[:6] if len(rels) <= 6 else [*rels[:6], f"... (+{len(rels) - 6} more)"]
        return GapDetectionResult(
            classification=GapClassification.TRANSFORMATION_GAP,
            question=q,
            reason=f"Required fields exist only in Bronze staging relation(s) {display_rels} and require Silver/Gold transformation.",
            candidate_relations=rels,
            matched_columns=matched_cols[:15],
            evidence=[f"bronze_relations={display_rels}", f"matched_columns={len(matched_cols)}"],
        )

    return GapDetectionResult(
        classification=GapClassification.SOURCE_GAP,
        question=q,
        reason="No matching relation, column, or metric was found in the authorized dbt catalog.",
        candidate_relations=[],
        matched_columns=[],
        evidence=[f"inspected_relations={len(visible_relations)}", f"inspected_metrics={len(visible_metrics)}"],
    )


_AST_AGG_MAP: dict[type[exp.Expression], str] = {
    exp.Sum: "SUM",
    exp.Avg: "AVG",
    exp.Count: "COUNT",
    exp.Min: "MIN",
    exp.Max: "MAX",
}


def classify_sql_semantic_gap(
    sql: str,
    catalog: DbtMetadataCatalog,
    *,
    question: str = "",
    dialect: str = "postgres",
    precheck_gap: GapDetectionResult | None = None,
) -> GapDetectionResult:
    """AST-first Semantic Gap Classification: fingerprint Candidate SQL (tables, measures, dimensions) against live_metrics."""
    raw = (sql or "").strip()
    if not raw:
        return precheck_gap or classify_semantic_gap(question, catalog)

    try:
        tree = sqlglot.parse_one(raw, read=dialect)
    except Exception:
        return precheck_gap or classify_semantic_gap(question, catalog)

    cte_names = {cte.alias.lower() for cte in tree.find_all(exp.CTE) if cte.alias}
    tables = sorted(
        {
            tbl.name
            for tbl in tree.find_all(exp.Table)
            if (tbl.name or "").strip() and tbl.name.lower() not in cte_names
        }
    )
    if not tables:
        return precheck_gap or classify_semantic_gap(question, catalog)

    tables_low = {t.lower() for t in tables}
    rel_layer_map = {r.name.lower(): r.layer for r in catalog.relations}
    layers_present = {rel_layer_map.get(t.lower(), "gold") for t in tables}

    # Extract SQL measures (AGG, col_lower, label)
    measures: list[tuple[str, str, str]] = []
    if isinstance(tree, exp.Select):
        for expr in tree.expressions:
            target = expr.this if isinstance(expr, exp.Alias) else expr
            matched_agg = next(
                (fn for cls_t, fn in _AST_AGG_MAP.items() if isinstance(target, cls_t)),
                None,
            )
            if matched_agg:
                if isinstance(target, exp.Count) and isinstance(target.this, exp.Distinct):
                    cols = list(target.this.find_all(exp.Column))
                    col_name = cols[0].name.lower() if cols else "*"
                    measures.append(("COUNT_DISTINCT", col_name, f"COUNT(DISTINCT {col_name})"))
                else:
                    cols = list(target.find_all(exp.Column))
                    col_name = cols[0].name.lower() if cols else "*"
                    measures.append((matched_agg, col_name, f"{matched_agg}({col_name})"))

    # Extract GROUP BY bare column names
    group_cols: list[str] = []
    if isinstance(tree, exp.Select):
        grp = tree.args.get("group")
        if grp:
            for g_expr in grp.expressions:
                if isinstance(g_expr, exp.Column):
                    c_low = g_expr.name.lower()
                    if c_low not in group_cols:
                        group_cols.append(c_low)

    has_complex = bool(
        (isinstance(tree, exp.Select) and (tree.args.get("with_") or tree.args.get("with")))
        or any(True for _ in tree.find_all(exp.Window, exp.Case))
    )

    # Match extracted SQL measures against certified catalog metrics
    matched_metrics: list[CatalogMetric] = []
    matched_metric_names: list[str] = []
    uncertified_measures: list[str] = []

    for agg_func, col_low, measure_label in measures:
        hit: CatalogMetric | None = None
        for m in catalog.metrics:
            m_rel = (m.source_relation or "").lower()
            if m_rel and m_rel not in tables_low:
                continue
            m_agg = (m.measure_agg or "").upper()
            m_expr = (m.measure_expr or "").lower()
            agg_ok = (m_agg == agg_func) or (
                m_agg in ("COUNT", "COUNT_DISTINCT") and agg_func in ("COUNT", "COUNT_DISTINCT")
            )
            expr_ok = (m_expr == col_low) or (
                agg_func == "COUNT" and col_low in ("*", "id") and m_expr in ("*", "id")
            )
            if agg_ok and expr_ok:
                hit = m
                break
        if hit is not None:
            if hit.name not in matched_metric_names:
                matched_metrics.append(hit)
                matched_metric_names.append(hit.name)
        else:
            uncertified_measures.append(measure_label)

    # Check whether matched metrics cover ALL referenced tables and GROUP BY dimensions
    covered_tables = {(m.source_relation or "").lower() for m in matched_metrics if m.source_relation}
    extra_tables = sorted(t for t in tables if t.lower() not in covered_tables)
    allowed_dims = {d.lower() for m in matched_metrics for d in (m.dimensions or [])}
    uncovered_dims = [d for d in group_cols if allowed_dims and d not in allowed_dims]

    # Case 1: Full certified coverage -> REUSE_EXISTING
    if (
        measures
        and not uncertified_measures
        and matched_metrics
        and not extra_tables
        and not uncovered_dims
        and not has_complex
    ):
        primary = matched_metrics[0]
        return GapDetectionResult(
            classification=GapClassification.REUSE_EXISTING,
            question=question,
            reason=(
                f"AST-verified reuse of certified MetricFlow metric(s) {matched_metric_names} "
                f"on {tables}."
            ),
            matched_metric=primary,
            candidate_metrics=matched_metrics,
            candidate_relations=tables,
            evidence=[
                f"ast_tables={tables}",
                f"matched_metrics={matched_metric_names}",
                f"group_by={group_cols}",
            ],
        )

    # Case 2: Determine layer-based gap classification strictly from AST-referenced tables
    gap_details: list[str] = []
    if uncertified_measures:
        gap_details.append(f"uncertified calculation(s) {uncertified_measures}")
    if matched_metrics and extra_tables:
        gap_details.append(f"cross-table join on {extra_tables}")
    if uncovered_dims:
        gap_details.append(f"uncertified dimension(s) {uncovered_dims}")
    if has_complex:
        gap_details.append("custom CTE/window/case logic")
    if not measures:
        gap_details.append("row-level detail query (no certified metric aggregation)")

    gap_summary = "; ".join(gap_details) if gap_details else "no certified MetricFlow metric covers this query"

    if "bronze" in layers_present:
        classification = GapClassification.TRANSFORMATION_GAP
        reason = f"SQL references Bronze relation(s) {tables} ({gap_summary}); Silver/Gold transformation is required."
    elif "silver" in layers_present:
        classification = GapClassification.NEW_DBT_MODEL
        reason = f"SQL references Silver relation(s) {tables} ({gap_summary}); a Gold mart model is recommended."
    else:
        classification = GapClassification.NEW_SEMANTIC_METRIC
        if matched_metric_names:
            reason = (
                f"SQL uses Gold relation(s) {tables} (reusing {matched_metric_names}), "
                f"but introduces {gap_summary}."
            )
        else:
            reason = f"SQL uses Gold relation(s) {tables}, but no certified MetricFlow metric matches ({gap_summary})."

    return GapDetectionResult(
        classification=classification,
        question=question,
        reason=reason,
        matched_metric=matched_metrics[0] if matched_metrics else None,
        candidate_metrics=matched_metrics,
        candidate_relations=tables,
        evidence=[
            f"ast_tables={tables}",
            f"matched_metrics={matched_metric_names}",
            f"uncertified_measures={uncertified_measures}",
            f"group_by={group_cols}",
        ],
    )
