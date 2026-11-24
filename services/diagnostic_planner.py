"""Phase 2 Diagnostic Multi-Query Planner & Evidence-Grounded Synthesis Service.

Generates bounded (2-4 step) diagnostic query plans where every sub-query is a canonical,
column-whitelisted QuerySpec with its own SHA-256 spec_hash. Also synthesizes PII-stripped,
evidence-grounded Chinese diagnostic summaries from verified step execution results.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

try:
    from .dbt_catalog import DbtMetadataCatalog
    from .duckdb_executor import generate_gemini_structured
    from .intent_clarifier import BusinessIntentSpec
    from .query_spec import (
        AggregationSpec,
        DataSourceRef,
        FilterCondition,
        OutputSpec,
        QueryDialect,
        QuerySpec,
    )
except (ImportError, ValueError):
    try:
        from services.dbt_catalog import DbtMetadataCatalog
        from services.duckdb_executor import generate_gemini_structured
        from services.intent_clarifier import BusinessIntentSpec
        from services.query_spec import (
            AggregationSpec,
            DataSourceRef,
            FilterCondition,
            OutputSpec,
            QueryDialect,
            QuerySpec,
        )
    except (ImportError, ValueError):
        from cockpit_core.services.dbt_catalog import DbtMetadataCatalog
        from cockpit_core.services.duckdb_executor import generate_gemini_structured
        from cockpit_core.services.intent_clarifier import BusinessIntentSpec
        from cockpit_core.services.query_spec import (
            AggregationSpec,
            DataSourceRef,
            FilterCondition,
            OutputSpec,
            QueryDialect,
            QuerySpec,
        )


_PII_COLUMN_TOKENS = (
    "email",
    "phone",
    "mobile",
    "address",
    "password",
    "passwd",
    "secret",
    "token",
    "ssn",
    "credit_card",
    "passport",
    "id_number",
    "tax_id",
)


class DiagnosticPlanningError(RuntimeError):
    """Fail-Loud error raised when diagnostic planning or synthesis cannot proceed."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class _LlmAggDraft(BaseModel):
    func: Literal["SUM", "COUNT", "AVG", "MIN", "MAX"] = "COUNT"
    column: str
    alias: str


class _LlmFilterDraft(BaseModel):
    column: str
    op: Literal["=", "!=", ">", ">=", "<", "<=", "IN", "NOT IN", "LIKE"] = "="
    value: str | int | float | bool | list[str | int | float]


class _LlmPlanStepDraft(BaseModel):
    step_id: str
    title: str
    rationale: str
    table: str
    dimensions: list[str] = Field(default_factory=list)
    aggregations: list[_LlmAggDraft] = Field(default_factory=list)
    filters: list[_LlmFilterDraft] = Field(default_factory=list)
    limit: int = Field(default=100, ge=1, le=500)


class _LlmPlanDraftPayload(BaseModel):
    plan_title: str
    steps: list[_LlmPlanStepDraft] = Field(default_factory=list)


class DiagnosticStep(BaseModel):
    step_id: str
    title: str
    rationale: str
    query_spec: QuerySpec
    compiled_sql: str
    spec_hash: str


class DiagnosticPlan(BaseModel):
    plan_title: str
    plan_hash: str
    business_intent: BusinessIntentSpec
    steps: list[DiagnosticStep] = Field(default_factory=list)


class StepExecutionOutcome(BaseModel):
    step_id: str
    title: str
    spec_hash: str = ""
    status: Literal["SUCCEEDED", "FAILED"]
    error: str = ""
    row_count: int = 0
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)


class DiagnosticFinding(BaseModel):
    step_id: str
    finding: str
    cited_metrics: list[str] = Field(default_factory=list)


class DiagnosticSynthesis(BaseModel):
    executive_summary: str
    key_findings: list[DiagnosticFinding] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)
    recommended_followups: list[str] = Field(default_factory=list)


def _is_pii_column(col_name: str) -> bool:
    low = (col_name or "").lower()
    return any(tok in low for tok in _PII_COLUMN_TOKENS)


def _strip_pii_and_cap_rows(outcome: StepExecutionOutcome, max_rows: int = 20) -> dict[str, Any]:
    safe_cols = [c for c in outcome.columns if not _is_pii_column(c)]
    safe_rows: list[dict[str, Any]] = []
    for row in outcome.rows[:max_rows]:
        if isinstance(row, dict):
            safe_rows.append({k: v for k, v in row.items() if not _is_pii_column(str(k))})
    return {
        "step_id": outcome.step_id,
        "title": outcome.title,
        "spec_hash": outcome.spec_hash,
        "status": outcome.status,
        "error": outcome.error,
        "row_count": outcome.row_count,
        "columns": safe_cols,
        "rows": safe_rows,
    }


def build_diagnostic_plan(
    *,
    business_intent: BusinessIntentSpec,
    catalog: DbtMetadataCatalog,
    dialect: QueryDialect = "postgres",
    api_key: str = "",
    max_steps: int = 4,
    structured_llm_fn: Callable[[str, type[Any]], Any] | None = None,
) -> DiagnosticPlan:
    """Synthesize a bounded 2-4 step column-whitelisted diagnostic plan from confirmed intent."""
    clean_key = (api_key or "").strip()
    if structured_llm_fn is None and not clean_key:
        raise DiagnosticPlanningError(
            "API_KEY_MISSING",
            "Gemini API Key is required to build a diagnostic analysis plan (Fail-Loud).",
        )

    rel_map = {r.name: r for r in catalog.relations}
    target_names = [t for t in business_intent.target_relations if t in rel_map]
    if not target_names:
        target_names = [r.name for r in catalog.relations[:15]]
    if not target_names:
        raise DiagnosticPlanningError(
            "EMPTY_CATALOG",
            "Catalog contains no available tables to build a diagnostic plan.",
        )

    schema_ctx = [
        {
            "table": rel_map[t].name,
            "columns": [{"name": c.name, "type": c.data_type} for c in rel_map[t].columns],
        }
        for t in target_names
        if t in rel_map
    ]

    prompt = (
        "You are an expert Analytics Engineer designing a bounded Diagnostic Analysis Plan "
        f"(between 2 and {max_steps} read-only SQL steps) in Traditional Chinese (繁體中文).\n"
        "CRITICAL RULES:\n"
        "1. Every step MUST use ONLY real table names and EXACT real column names from AvailableTables.\n"
        "2. NEVER invent columns (no made-up revenue/date/category columns if not listed).\n"
        "3. Decompose the business question logically (e.g., Step 1: overall trend or status distribution; "
        "Step 2: Order Count & AVG/SUM decomposition; Step 3: breakdown by available dimension).\n"
        "4. Each step must be a single-table aggregation query with explicit dimensions and aggregations.\n\n"
        f"ConfirmedBusinessIntent: {json.dumps(business_intent.model_dump(), ensure_ascii=False)}\n"
        f"AvailableTables: {json.dumps(schema_ctx, ensure_ascii=False)}\n"
    )

    try:
        if structured_llm_fn is not None:
            raw_plan = structured_llm_fn(prompt, _LlmPlanDraftPayload)
        else:
            raw_plan = generate_gemini_structured(
                api_key=clean_key,
                prompt=prompt,
                response_model=_LlmPlanDraftPayload,
            )
        parsed = _LlmPlanDraftPayload.model_validate(raw_plan)
    except Exception as exc:
        raise DiagnosticPlanningError(
            "LLM_PLAN_FAILED",
            f"Gemini diagnostic plan synthesis failed: {exc}",
        ) from exc

    if not parsed.steps:
        raise DiagnosticPlanningError(
            "EMPTY_DIAGNOSTIC_PLAN",
            "Diagnostic planner returned 0 query steps.",
        )

    bounded_steps = parsed.steps[:max_steps]
    built_steps: list[DiagnosticStep] = []

    for idx, step in enumerate(bounded_steps, start=1):
        if step.table not in rel_map:
            raise DiagnosticPlanningError(
                "UNKNOWN_RELATION",
                f"Step {step.step_id} referenced unknown table '{step.table}'.",
            )
        valid_cols = {c.name for c in rel_map[step.table].columns}
        if not valid_cols:
            raise DiagnosticPlanningError(
                "INCOMPLETE_CATALOG_METADATA",
                f"Table '{step.table}' has no column metadata in catalog.",
            )

        bad_cols: list[str] = []
        for d in step.dimensions:
            if d not in valid_cols:
                bad_cols.append(d)
        for a in step.aggregations:
            if a.column != "*" and a.column not in valid_cols:
                bad_cols.append(a.column)
        for f in step.filters:
            if f.column not in valid_cols:
                bad_cols.append(f.column)
        if bad_cols:
            raise DiagnosticPlanningError(
                "HALLUCINATED_COLUMN",
                f"Step '{step.title}' referenced non-existent column(s) {bad_cols} on table '{step.table}'. "
                f"Available columns: {sorted(valid_cols)}",
            )

        spec = QuerySpec(
            spec_id=f"qs_diag_{idx}_{hashlib.sha256(step.title.encode('utf-8')).hexdigest()[:6]}",
            purpose="ad_hoc",
            question=f"{business_intent.original_question} — {step.title}",
            data_sources=[DataSourceRef(relation=step.table, grain=list(step.dimensions))],
            projection=list(step.dimensions),
            aggregations=[
                AggregationSpec(field=a.column, func=a.func, alias=a.alias)
                for a in step.aggregations
            ],
            group_by=list(step.dimensions),
            filters=[
                FilterCondition(field=f.column, op=f.op, value=f.value)
                for f in step.filters
            ],
            dialect=dialect,
            output=OutputSpec(max_rows=step.limit, certification="VALIDATED_EXPLORATORY"),
        )
        spec_hash = spec.compute_spec_hash()
        spec.review.spec_hash = spec_hash
        compiled_sql = spec.compile_sql()

        built_steps.append(
            DiagnosticStep(
                step_id=step.step_id or f"step_{idx}",
                title=step.title,
                rationale=step.rationale,
                query_spec=spec,
                compiled_sql=compiled_sql,
                spec_hash=spec_hash,
            )
        )

    plan_hash = hashlib.sha256(
        json.dumps([s.spec_hash for s in built_steps]).encode("utf-8")
    ).hexdigest()

    return DiagnosticPlan(
        plan_title=parsed.plan_title or f"診斷分析計畫：{business_intent.original_question}",
        plan_hash=plan_hash,
        business_intent=business_intent,
        steps=built_steps,
    )


def synthesize_diagnostic_findings(
    *,
    business_intent: BusinessIntentSpec,
    step_results: list[StepExecutionOutcome],
    api_key: str = "",
    structured_llm_fn: Callable[[str, type[Any]], Any] | None = None,
) -> DiagnosticSynthesis:
    """Synthesize a PII-safe, evidence-grounded Traditional Chinese diagnostic summary."""
    clean_key = (api_key or "").strip()
    if structured_llm_fn is None and not clean_key:
        raise DiagnosticPlanningError(
            "API_KEY_MISSING",
            "Gemini API Key is required for diagnostic evidence synthesis (Fail-Loud).",
        )

    sanitized_results = [_strip_pii_and_cap_rows(r, max_rows=20) for r in step_results]
    failed_steps = [r for r in step_results if r.status == "FAILED" or r.row_count == 0]

    prompt = (
        "You are a Principal Analytics Consultant writing an Evidence-Grounded Diagnostic Summary "
        "in Traditional Chinese (繁體中文).\n"
        "STRICT ANTI-HALLUCINATION RULES:\n"
        "1. ONLY cite numbers that explicitly appear in SUCCEEDED step_results below.\n"
        "2. NEVER invent, estimate, or extrapolate numbers for FAILED or 0-row steps.\n"
        "3. Every FAILED or 0-row step MUST be explicitly listed in data_gaps.\n"
        "4. Each item in key_findings must cite the exact step_id and concrete metric values.\n\n"
        f"BusinessIntent: {json.dumps(business_intent.model_dump(), ensure_ascii=False)}\n"
        f"SanitizedStepResults: {json.dumps(sanitized_results, ensure_ascii=False)}\n"
    )

    try:
        if structured_llm_fn is not None:
            raw_synth = structured_llm_fn(prompt, DiagnosticSynthesis)
        else:
            raw_synth = generate_gemini_structured(
                api_key=clean_key,
                prompt=prompt,
                response_model=DiagnosticSynthesis,
            )
        parsed = DiagnosticSynthesis.model_validate(raw_synth)
    except Exception as exc:
        raise DiagnosticPlanningError(
            "LLM_SYNTHESIS_FAILED",
            f"Gemini diagnostic synthesis failed: {exc}",
        ) from exc

    # Deterministically ensure any failed/0-row step is reflected in data_gaps
    gaps = list(parsed.data_gaps)
    existing_gap_text = " ".join(gaps)
    for fstep in failed_steps:
        if fstep.step_id not in existing_gap_text:
            reason = fstep.error or "查無資料 (0 rows)"
            gaps.append(f"{fstep.step_id}（{fstep.title}）未取得有效資料：{reason}（未納入結論推論）。")

    return parsed.model_copy(update={"data_gaps": gaps})
