"""Reusable Fail-Loud Requirement & QuerySpec Drafting Engine.

Separates deterministic semantic gap classification from LLM-backed QuerySpec synthesis.
Strictly enforces the Fail-Loud Policy: missing API keys or Gemini errors are never
silently swallowed or replaced by offline keyword guesses.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any, Callable

from pydantic import BaseModel, Field

try:
    from .dbt_catalog import DbtMetadataCatalog
    from .duckdb_executor import generate_gemini_text
    from .query_spec import (
        AggregationSpec,
        DataSourceRef,
        FilterCondition,
        JoinSpec,
        OutputSpec,
        QueryDialect,
        QuerySpec,
    )
    from .semantic_gap_detector import (
        GapClassification,
        GapDetectionResult,
        classify_semantic_gap,
    )
except (ImportError, ValueError):
    try:
        from services.dbt_catalog import DbtMetadataCatalog
        from services.duckdb_executor import generate_gemini_text
        from services.query_spec import (
            AggregationSpec,
            DataSourceRef,
            FilterCondition,
            JoinSpec,
            OutputSpec,
            QueryDialect,
            QuerySpec,
        )
        from services.semantic_gap_detector import (
            GapClassification,
            GapDetectionResult,
            classify_semantic_gap,
        )
    except (ImportError, ValueError):
        from cockpit_core.services.dbt_catalog import DbtMetadataCatalog
        from cockpit_core.services.duckdb_executor import generate_gemini_text
        from cockpit_core.services.query_spec import (
            AggregationSpec,
            DataSourceRef,
            FilterCondition,
            JoinSpec,
            OutputSpec,
            QueryDialect,
            QuerySpec,
        )
        from cockpit_core.services.semantic_gap_detector import (
            GapClassification,
            GapDetectionResult,
            classify_semantic_gap,
        )


class RequirementDraftError(RuntimeError):
    """Explicit Fail-Loud error raised when LLM drafting cannot safely proceed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DraftAndClassifyResponse(BaseModel):
    gap: GapDetectionResult
    query_spec: QuerySpec | None = None
    compiled_sql: str = ""
    clarification_prompt: str = ""
    warnings: list[str] = Field(default_factory=list)


def _spec_id_for(question: str) -> str:
    digest = hashlib.sha1((question or "draft").encode("utf-8")).hexdigest()[:6]
    return f"qs_{digest}"


def draft_and_classify_query_spec(
    question: str,
    catalog: DbtMetadataCatalog,
    *,
    dialect: QueryDialect = "duckdb",
    api_key: str = "",
    is_authorized: bool = True,
    allowed_relations: set[str] | None = None,
    llm_callable: Callable[[str], str] | None = None,
) -> DraftAndClassifyResponse:
    """Classify semantic gap deterministically, then build or LLM-draft a versioned QuerySpec.

    Fail-Loud rules:
    - ACCESS_BLOCKED / SEMANTIC_AMBIGUITY / SOURCE_GAP return immediately with explicit reason & evidence.
    - REUSE_EXISTING builds a deterministic CERTIFIED QuerySpec referencing the matched metric/relation.
    - Exploratory cases (NEW_SEMANTIC_METRIC / NEW_DBT_MODEL / TRANSFORMATION_GAP) require a valid
      Gemini API key (or injected `llm_callable`); missing key or parse errors raise RequirementDraftError.
    """
    q = (question or "").strip()
    if not q:
        raise RequirementDraftError("EMPTY_QUESTION", "Question cannot be empty.")

    gap = classify_semantic_gap(
        q,
        catalog,
        is_authorized=is_authorized,
        allowed_relations=allowed_relations,
    )

    if gap.classification == GapClassification.ACCESS_BLOCKED:
        return DraftAndClassifyResponse(
            gap=gap,
            clarification_prompt="Access denied for the requested analytical domain.",
        )

    if gap.classification == GapClassification.SEMANTIC_AMBIGUITY:
        opts = ", ".join(f"{m.name} ({m.label})" for m in gap.candidate_metrics)
        return DraftAndClassifyResponse(
            gap=gap,
            clarification_prompt=f"Please clarify which certified metric you mean: {opts}",
        )

    if gap.classification == GapClassification.SOURCE_GAP:
        return DraftAndClassifyResponse(
            gap=gap,
            clarification_prompt="Requested entity/grain is not present in the authorized catalog. Propose a new source ingestion contract instead of fabricating SQL.",
        )

    if gap.classification == GapClassification.REUSE_EXISTING and gap.matched_metric is not None:
        metric = gap.matched_metric
        rel_name = metric.source_relation or (
            gap.candidate_relations[0] if gap.candidate_relations else ""
        )
        sources = (
            [DataSourceRef(relation=rel_name, catalog_ref=f"dbt://model/{rel_name}")]
            if rel_name
            else []
        )
        dims = metric.dimensions[:2]
        spec = QuerySpec(
            spec_id=_spec_id_for(q),
            purpose="ad_hoc",
            question=q,
            semantic_match=metric.name,
            data_sources=sources,
            projection=dims,
            group_by=dims,
            metrics=[metric.name],
            assumptions=[f"Reusing certified MetricFlow metric '{metric.name}' ({metric.label})."],
            dialect=dialect,
            output=OutputSpec(max_rows=100, certification="CERTIFIED"),
        )
        compiled = spec.compile_sql() if sources else ""
        return DraftAndClassifyResponse(
            gap=gap,
            query_spec=spec,
            compiled_sql=compiled,
        )

    # Exploratory gap (NEW_SEMANTIC_METRIC / NEW_DBT_MODEL / TRANSFORMATION_GAP) -> LLM required (Fail-Loud)
    if llm_callable is None and not (api_key or "").strip():
        raise RequirementDraftError(
            "API_KEY_MISSING",
            "⚠️ Exploratory QuerySpec drafting requires a Gemini API Key. Silent offline keyword fallback is disabled.",
        )

    candidate_rels = [
        catalog.get_relation(r) for r in gap.candidate_relations if catalog.get_relation(r) is not None
    ]
    rels_with_cols = [r for r in candidate_rels if r is not None and len(r.columns) > 0]
    if not rels_with_cols:
        raise RequirementDraftError(
            "INCOMPLETE_CATALOG_METADATA",
            f"Candidate relation(s) {gap.candidate_relations} have no documented columns in the dbt catalog; "
            "refusing to let the LLM guess column names.",
        )

    schema_lines = [
        f"- {r.name} ({r.layer}): columns={[c.name for c in r.columns]}"
        for r in rels_with_cols
    ]
    today_taipei = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    prompt = (
        "You are an Analytics Engineer drafting a structured QuerySpec JSON.\n"
        f"CurrentDate (Asia/Taipei): {today_taipei}\n"
        f"Dialect: {dialect}\n"
        f"Question: {q}\n"
        f"Gap Classification: {gap.classification.value}\n"
        "Authorized Candidate Relations (you MUST use ONLY the exact column names listed below):\n"
        + "\n".join(schema_lines)
        + "\n\nReturn ONLY a JSON object with keys:\n"
        '- "relation": exact primary relation name from above\n'
        '- "joins": optional list of {"left_relation": "<exact_rel>", "right_relation": "<exact_rel>", "on": "<join_cond>", "join_type": "LEFT|INNER"} when multiple relations are needed\n'
        '- "projection": list of exact dimension column names (use "table.column" when joining multiple tables)\n'
        '- "aggregations": list of {"field": "<exact_col_or_table.col>", "func": "SUM|AVG|COUNT|MIN|MAX", "alias": "<name>"}\n'
        '- "group_by": list of exact group-by column names\n'
        '- "filters": list of {"field": "<exact_col_or_table.col>", "op": "=|!=|>|>=|<|<=|LAST_N_DAYS|LAST_N_MONTHS", "value": <val>} '
        '(For relative date windows like last 30 days / last 3 months, use op="LAST_N_DAYS" or "LAST_N_MONTHS" with an integer value; never put NOW()/INTERVAL in a string value.)\n'
        '- "assumptions": list of explicit business assumptions\n'
    )

    try:
        raw = llm_callable(prompt) if llm_callable is not None else generate_gemini_text(api_key=api_key, prompt=prompt)
    except Exception as exc:
        raise RequirementDraftError("LLM_CALL_FAILED", f"Gemini API drafting failed: {exc}") from exc

    m = re.search(r"\{.*\}", (raw or "").strip(), re.DOTALL)
    if not m:
        raise RequirementDraftError(
            "LLM_INVALID_JSON",
            f"Gemini returned non-JSON output for QuerySpec drafting: {(raw or '')[:200]}",
        )

    try:
        parsed: dict[str, Any] = json.loads(m.group(0))
    except Exception as exc:
        raise RequirementDraftError("LLM_INVALID_JSON", f"Failed to decode QuerySpec JSON: {exc}") from exc

    rel_name = str(parsed.get("relation", "")).strip()
    target_rel = catalog.get_relation(rel_name) if rel_name else None
    if not rel_name or target_rel is None:
        raise RequirementDraftError(
            "UNKNOWN_RELATION",
            f"Drafted relation '{rel_name}' does not exist in the authorized dbt catalog.",
        )

    if not target_rel.columns:
        raise RequirementDraftError(
            "INCOMPLETE_CATALOG_METADATA",
            f"Target relation '{rel_name}' has no documented columns in the dbt catalog.",
        )

    aggs = [
        AggregationSpec.model_validate(a)
        for a in (parsed.get("aggregations") or [])
        if isinstance(a, dict) and a.get("field")
    ]
    filters = [
        FilterCondition.model_validate(f)
        for f in (parsed.get("filters") or [])
        if isinstance(f, dict) and f.get("field")
    ]
    joins = [
        JoinSpec.model_validate(j)
        for j in (parsed.get("joins") or [])
        if isinstance(j, dict) and j.get("right_relation")
    ]
    projection = [str(c) for c in (parsed.get("projection") or [])]
    group_by = [str(c) for c in (parsed.get("group_by") or [])]

    # Strict multi-table column whitelist check across base relation and all JOIN relations
    rel_col_map: dict[str, set[str]] = {
        rel_name.lower(): {c.name.lower() for c in target_rel.columns}
    }
    for j in joins:
        j_rel = catalog.get_relation(j.right_relation)
        if j_rel is None:
            raise RequirementDraftError(
                "UNKNOWN_RELATION",
                f"Joined relation '{j.right_relation}' does not exist in the authorized catalog.",
            )
        if not j_rel.columns:
            raise RequirementDraftError(
                "INCOMPLETE_CATALOG_METADATA",
                f"Joined relation '{j.right_relation}' has no documented columns in the catalog.",
            )
        rel_col_map[j.right_relation.lower()] = {c.name.lower() for c in j_rel.columns}

    all_allowed_cols_low: set[str] = set()
    for cols in rel_col_map.values():
        all_allowed_cols_low.update(cols)

    referenced_cols = (
        projection
        + group_by
        + [a.field for a in aggs if a.field != "*"]
        + [f.field for f in filters]
    )
    hallucinated: list[str] = []
    for col in referenced_cols:
        clean_col = col.strip()
        if "." in clean_col:
            t_part, c_part = clean_col.split(".", 1)
            t_low, c_low = t_part.strip().strip('"').lower(), c_part.strip().strip('"').lower()
            if t_low not in rel_col_map or c_low not in rel_col_map[t_low]:
                hallucinated.append(clean_col)
        else:
            if clean_col.strip('"').lower() not in all_allowed_cols_low:
                hallucinated.append(clean_col)

    hallucinated = sorted(set(hallucinated))
    if hallucinated:
        authorized_summary = {t: sorted(cols) for t, cols in rel_col_map.items()}
        raise RequirementDraftError(
            "HALLUCINATED_COLUMN",
            f"Drafted column(s) {hallucinated} do not exist in authorized relation(s) {list(rel_col_map.keys())}. "
            f"Authorized columns: {authorized_summary}.",
        )

    spec = QuerySpec(
        spec_id=_spec_id_for(q),
        purpose="ad_hoc",
        question=q,
        data_sources=[DataSourceRef(relation=rel_name, catalog_ref=f"dbt://model/{rel_name}")],
        projection=projection,
        aggregations=aggs,
        group_by=group_by,
        filters=filters,
        joins=joins,
        assumptions=[str(a) for a in (parsed.get("assumptions") or [])],
        dialect=dialect,
        output=OutputSpec(max_rows=100, certification="VALIDATED_EXPLORATORY"),
    )
    return DraftAndClassifyResponse(
        gap=gap,
        query_spec=spec,
        compiled_sql=spec.compile_sql(),
    )
