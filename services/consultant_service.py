"""Consultation-First AI Business Consultant Service & Safe Timezone Grounding.

Supports:
- Safe IANA timezone resolution via stdlib ``zoneinfo`` with ``UTC`` fallback.
- Unbounded multi-turn consultant chat (12-turn backend sliding window for LLM context).
- On-demand 0..2 column-whitelisted read-only query proposals (using relative date ops).
- PII-stripped, evidence-grounded Traditional Chinese consultant replies.
- One-click conversation distillation into editable ``BusinessIntentSpec`` + ``GapDetectionResult``.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Callable, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

try:
    from .dbt_catalog import DbtMetadataCatalog
    from .diagnostic_planner import _is_pii_column
    from .duckdb_executor import generate_gemini_structured
    from .intent_clarifier import (
        BusinessIntentSpec,
        ClarificationTurn,
        GlossaryEntry,
        _select_relevant_catalog_slice,
    )
    from .query_spec import (
        AggregationSpec,
        DataSourceRef,
        FilterCondition,
        OutputSpec,
        QueryDialect,
        QuerySpec,
    )
    from .semantic_gap_detector import GapDetectionResult, classify_semantic_gap
except (ImportError, ValueError):
    try:
        from services.dbt_catalog import DbtMetadataCatalog
        from services.diagnostic_planner import _is_pii_column
        from services.duckdb_executor import generate_gemini_structured
        from services.intent_clarifier import (
            BusinessIntentSpec,
            ClarificationTurn,
            GlossaryEntry,
            _select_relevant_catalog_slice,
        )
        from services.query_spec import (
            AggregationSpec,
            DataSourceRef,
            FilterCondition,
            OutputSpec,
            QueryDialect,
            QuerySpec,
        )
        from services.semantic_gap_detector import GapDetectionResult, classify_semantic_gap
    except (ImportError, ValueError):
        from cockpit_core.services.dbt_catalog import DbtMetadataCatalog
        from cockpit_core.services.diagnostic_planner import _is_pii_column
        from cockpit_core.services.duckdb_executor import generate_gemini_structured
        from cockpit_core.services.intent_clarifier import (
            BusinessIntentSpec,
            ClarificationTurn,
            GlossaryEntry,
            _select_relevant_catalog_slice,
        )
        from cockpit_core.services.query_spec import (
            AggregationSpec,
            DataSourceRef,
            FilterCondition,
            OutputSpec,
            QueryDialect,
            QuerySpec,
        )
        from cockpit_core.services.semantic_gap_detector import (
            GapDetectionResult,
            classify_semantic_gap,
        )


_VALID_IANA_TZ_RE = re.compile(r"^[A-Za-z0-9_+\-/]{1,64}$")
_SLIDING_WINDOW_TURNS = 12


def resolve_user_timezone(tz_name: str | None) -> tuple[str, str]:
    """Resolve an IANA timezone string to ``(tz_name, 'YYYY-MM-DD')``, falling back to ``UTC``."""
    raw = (tz_name or "").strip()
    if raw and _VALID_IANA_TZ_RE.match(raw):
        try:
            zi = ZoneInfo(raw)
            return raw, datetime.now(zi).strftime("%Y-%m-%d")
        except (ZoneInfoNotFoundError, KeyError, ValueError):
            pass
    return "UTC", datetime.now(timezone.utc).strftime("%Y-%m-%d")


class ConsultantError(RuntimeError):
    """Fail-Loud error raised when consultant reasoning cannot proceed."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class _LlmConsultantAgg(BaseModel):
    func: Literal["SUM", "COUNT", "AVG", "MIN", "MAX"] = "COUNT"
    column: str
    alias: str


class _LlmConsultantFilter(BaseModel):
    column: str
    op: Literal[
        "=", "!=", ">", ">=", "<", "<=", "IN", "NOT IN", "LIKE", "LAST_N_DAYS", "LAST_N_MONTHS"
    ] = "="
    value: str | int | float | bool | list[str | int | float]


class _LlmConsultantQueryDraft(BaseModel):
    title: str
    table: str
    dimensions: list[str] = Field(default_factory=list)
    aggregations: list[_LlmConsultantAgg] = Field(default_factory=list)
    filters: list[_LlmConsultantFilter] = Field(default_factory=list)
    limit: int = Field(default=50, ge=1, le=200)


class _LlmConsultantProposalPayload(BaseModel):
    needs_data: bool = False
    direct_reply: str = ""
    proposed_queries: list[_LlmConsultantQueryDraft] = Field(default_factory=list)


class ConsultantProposedQuery(BaseModel):
    title: str
    query_spec: QuerySpec
    compiled_sql: str
    spec_hash: str


class ConsultantProposal(BaseModel):
    needs_data: bool
    direct_reply: str = ""
    proposed_queries: list[ConsultantProposedQuery] = Field(default_factory=list)
    resolved_timezone: str = "UTC"


class ConsultantQueryOutcome(BaseModel):
    title: str
    sql: str = ""
    spec_hash: str = ""
    status: Literal["SUCCEEDED", "FAILED"]
    error: str = ""
    row_count: int = 0
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)


class _LlmConsultantAnswerPayload(BaseModel):
    reply: str


class ConsultantAnswer(BaseModel):
    reply: str
    resolved_timezone: str = "UTC"


class _LlmIntentSummaryPayload(BaseModel):
    summary_question: str
    business_intent: BusinessIntentSpec


class ConsultantIntentSummary(BaseModel):
    summary_question: str
    business_intent: BusinessIntentSpec
    gap: GapDetectionResult
    resolved_timezone: str = "UTC"


def propose_consultant_turn(
    *,
    message: str,
    catalog: DbtMetadataCatalog,
    history: list[ClarificationTurn] | None = None,
    glossary: list[GlossaryEntry] | None = None,
    tenant_value_samples: dict[str, list[str]] | None = None,
    dialect: QueryDialect = "postgres",
    user_timezone: str | None = None,
    api_key: str = "",
    structured_llm_fn: Callable[[str, type[Any]], Any] | None = None,
) -> ConsultantProposal:
    """Decide whether a consultant turn can be answered conceptually or needs 1-2 read-only queries."""
    clean_msg = (message or "").strip()
    if not clean_msg:
        raise ConsultantError("EMPTY_MESSAGE", "Message cannot be empty.")

    clean_key = (api_key or "").strip()
    if structured_llm_fn is None and not clean_key:
        raise ConsultantError(
            "API_KEY_MISSING",
            "Gemini API Key is required for AI Business Consultant chat (Fail-Loud).",
        )

    tz_name, today_str = resolve_user_timezone(user_timezone)
    recent_turns = list(history or [])[-_SLIDING_WINDOW_TURNS:]
    glossary_list = list(glossary or [])
    catalog_slice = _select_relevant_catalog_slice(clean_msg, recent_turns, catalog, glossary_list)

    prompt = (
        "You are a Senior Enterprise ERP Business Consultant (MBB / Big-4 caliber) conversing in Traditional Chinese (繁體中文).\n"
        f"CurrentDate ({tz_name}): {today_str}\n"
        "RULES:\n"
        "1. ALWAYS prioritize LatestUserMessage at the end of this prompt. If the user switches to a new business domain "
        "(e.g., from inventory to sales/revenue), IMMEDIATELY switch to the new domain's tables and NEVER repeat previous-turn queries.\n"
        "2. PROACTIVE HEALTH SNAPSHOT: If the user asks an open-ended question like '你覺得我可以怎麼分析？' or '最近營運怎麼樣？', "
        "do NOT give a generic textbook list—set needs_data=true and run 1-2 quick live health snapshot queries on core tables "
        "(such as sales_orders or purchase_orders) so your advice is grounded in this company's real numbers!\n"
        "3. Only set needs_data=false when the question is purely methodological or completely unrelated to AllowedCatalog tables "
        "(never fabricate numbers when no query was run).\n"
        "4. Every proposed query MUST use ONLY exact table and column names from AllowedCatalog. NEVER invent columns.\n"
        "5. For relative date windows (e.g. last 30 days, last 3 months), ALWAYS use structured filter op 'LAST_N_DAYS' or "
        "'LAST_N_MONTHS' with a positive integer value. NEVER put NOW()/INTERVAL inside a string value.\n"
        "6. Treat TenantSampleDimensionValues strictly as formatting/casing hints (never block solely because a value is absent).\n\n"
        f"BusinessGlossary: {json.dumps([g.model_dump() for g in glossary_list], ensure_ascii=False)}\n"
        f"TenantSampleDimensionValues (formatting hints only): {json.dumps(tenant_value_samples or {}, ensure_ascii=False)}\n"
        f"AllowedCatalog (ranked by LatestUserMessage relevance): {json.dumps(catalog_slice, ensure_ascii=False)}\n"
        f"RecentConversationHistory (last {len(recent_turns)} turns, chronological): "
        f"{json.dumps([t.model_dump() for t in recent_turns], ensure_ascii=False)}\n"
        f"LatestUserMessage (RESPOND TO THIS MESSAGE): {clean_msg}\n"
    )

    try:
        if structured_llm_fn is not None:
            raw = structured_llm_fn(prompt, _LlmConsultantProposalPayload)
        else:
            raw = generate_gemini_structured(
                api_key=clean_key,
                prompt=prompt,
                response_model=_LlmConsultantProposalPayload,
            )
        parsed = _LlmConsultantProposalPayload.model_validate(raw)
    except Exception as exc:
        raise ConsultantError("LLM_CONSULTANT_FAILED", f"Consultant proposal failed: {exc}") from exc

    if not parsed.needs_data or not parsed.proposed_queries:
        return ConsultantProposal(
            needs_data=False,
            direct_reply=parsed.direct_reply or "請告訴我您想了解哪個業務面向，我可以協助查證實際資料或一起討論分析指標。",
            proposed_queries=[],
            resolved_timezone=tz_name,
        )

    rel_map = {r.name: r for r in catalog.relations}
    built_queries: list[ConsultantProposedQuery] = []

    for idx, qdraft in enumerate(parsed.proposed_queries[:2], start=1):
        if qdraft.table not in rel_map:
            raise ConsultantError(
                "UNKNOWN_RELATION",
                f"Consultant proposed unknown table '{qdraft.table}'.",
            )
        valid_cols = {c.name for c in rel_map[qdraft.table].columns}
        bad_cols = [
            col
            for col in [
                *qdraft.dimensions,
                *[a.column for a in qdraft.aggregations if a.column != "*"],
                *[f.column for f in qdraft.filters],
            ]
            if col not in valid_cols
        ]
        if bad_cols:
            raise ConsultantError(
                "HALLUCINATED_COLUMN",
                f"Consultant query '{qdraft.title}' referenced non-existent column(s) {bad_cols} on '{qdraft.table}'.",
            )

        spec = QuerySpec(
            spec_id=f"qs_cons_{idx}_{hashlib.sha256(qdraft.title.encode('utf-8')).hexdigest()[:6]}",
            purpose="ad_hoc",
            question=f"{clean_msg} — {qdraft.title}",
            data_sources=[DataSourceRef(relation=qdraft.table, grain=list(qdraft.dimensions))],
            projection=list(qdraft.dimensions),
            aggregations=[
                AggregationSpec(field=a.column, func=a.func, alias=a.alias)
                for a in qdraft.aggregations
            ],
            group_by=list(qdraft.dimensions),
            filters=[
                FilterCondition(field=f.column, op=f.op, value=f.value)
                for f in qdraft.filters
            ],
            dialect=dialect,
            output=OutputSpec(max_rows=qdraft.limit, certification="VALIDATED_EXPLORATORY"),
        )
        spec_hash = spec.compute_spec_hash()
        spec.review.spec_hash = spec_hash
        compiled_sql = spec.compile_sql()
        built_queries.append(
            ConsultantProposedQuery(
                title=qdraft.title,
                query_spec=spec,
                compiled_sql=compiled_sql,
                spec_hash=spec_hash,
            )
        )

    return ConsultantProposal(
        needs_data=True,
        direct_reply="",
        proposed_queries=built_queries,
        resolved_timezone=tz_name,
    )


def answer_consultant_turn(
    *,
    message: str,
    query_outcomes: list[ConsultantQueryOutcome],
    history: list[ClarificationTurn] | None = None,
    user_timezone: str | None = None,
    api_key: str = "",
    structured_llm_fn: Callable[[str, type[Any]], Any] | None = None,
) -> ConsultantAnswer:
    """Write a plain Traditional Chinese consultant reply strictly grounded in PII-stripped ERP outcomes."""
    clean_key = (api_key or "").strip()
    if structured_llm_fn is None and not clean_key:
        raise ConsultantError(
            "API_KEY_MISSING",
            "Gemini API Key is required for consultant evidence reply (Fail-Loud).",
        )

    tz_name, today_str = resolve_user_timezone(user_timezone)
    recent_turns = list(history or [])[-_SLIDING_WINDOW_TURNS:]

    sanitized_outcomes: list[dict[str, Any]] = []
    for o in query_outcomes:
        safe_cols = [c for c in o.columns if not _is_pii_column(c)]
        safe_rows = [
            {k: v for k, v in row.items() if not _is_pii_column(str(k))}
            for row in o.rows[:20]
            if isinstance(row, dict)
        ]
        sanitized_outcomes.append(
            {
                "title": o.title,
                "status": o.status,
                "error": o.error,
                "row_count": o.row_count,
                "columns": safe_cols,
                "rows": safe_rows,
            }
        )

    prompt = (
        "You are a Senior Enterprise ERP Business Consultant (hypothesis-driven, SO-WHAT mindset) answering the user "
        "in clear, natural Traditional Chinese (繁體中文).\n"
        f"CurrentDate ({tz_name}): {today_str}\n"
        "STRICT ANTI-HALLUCINATION & CONSULTING DEPTH RULES:\n"
        "1. ONLY cite real numbers explicitly present in SUCCEEDED QueryOutcomes below. If a query FAILED or returned 0 rows, "
        "state that honestly and NEVER fabricate numbers.\n"
        "2. Do NOT merely recite raw table rows like a Q&A bot. Structure your response like a real senior consultant:\n"
        "   - **核心發現（So What?）**：point out concentration risks, top-share imbalances, anomalies, or key ratios directly from the real data.\n"
        "   - **可能商業假說（2 Hypotheses）**：propose 2 concrete, testable business hypotheses explaining WHY this pattern may be occurring.\n"
        "   - **建議下一步延伸**：proactively recommend 1-2 sharp follow-up angles the user can ask next (and mention they can click '進入商業語意分析' whenever ready to turn this into a governed model).\n\n"
        f"RecentConversationHistory: {json.dumps([t.model_dump() for t in recent_turns], ensure_ascii=False)}\n"
        f"SanitizedQueryOutcomes: {json.dumps(sanitized_outcomes, ensure_ascii=False)}\n"
        f"LatestUserMessage (RESPOND TO THIS MESSAGE): {message}\n"
    )

    try:
        if structured_llm_fn is not None:
            raw = structured_llm_fn(prompt, _LlmConsultantAnswerPayload)
        else:
            raw = generate_gemini_structured(
                api_key=clean_key,
                prompt=prompt,
                response_model=_LlmConsultantAnswerPayload,
            )
        parsed = _LlmConsultantAnswerPayload.model_validate(raw)
    except Exception as exc:
        raise ConsultantError("LLM_ANSWER_FAILED", f"Consultant answer synthesis failed: {exc}") from exc

    return ConsultantAnswer(reply=parsed.reply, resolved_timezone=tz_name)


def summarize_consultation_intent(
    *,
    history: list[ClarificationTurn],
    catalog: DbtMetadataCatalog,
    glossary: list[GlossaryEntry] | None = None,
    tenant_value_samples: dict[str, list[str]] | None = None,
    user_timezone: str | None = None,
    api_key: str = "",
    structured_llm_fn: Callable[[str, type[Any]], Any] | None = None,
) -> ConsultantIntentSummary:
    """Distill a consultant transcript into an editable BusinessIntentSpec + GapDetectionResult."""
    if not history:
        raise ConsultantError("EMPTY_HISTORY", "At least one consultation turn is required before entering analysis.")

    clean_key = (api_key or "").strip()
    if structured_llm_fn is None and not clean_key:
        raise ConsultantError(
            "API_KEY_MISSING",
            "Gemini API Key is required to summarize consultation intent (Fail-Loud).",
        )

    tz_name, today_str = resolve_user_timezone(user_timezone)
    recent_turns = list(history)[-_SLIDING_WINDOW_TURNS:]
    glossary_list = list(glossary or [])
    combined_q = " ".join(t.content for t in recent_turns if t.role == "user")
    catalog_slice = _select_relevant_catalog_slice(combined_q, recent_turns, catalog, glossary_list)

    prompt = (
        "You are an Analytics Engineer distilling a business consultation transcript into a crisp, "
        "auditable BusinessIntentSpec in Traditional Chinese (繁體中文).\n"
        f"CurrentDate ({tz_name}): {today_str}\n"
        "RULES:\n"
        "1. Summarize the user's finalized analytical objective into summary_question (1 concise sentence).\n"
        "2. Populate business_intent (metric, metric_basis, timeframe, comparison_period, grain, breakdown_dimensions, "
        "filters, intent_type='SINGLE_QUERY' or 'DIAGNOSTIC_ANALYSIS', target_relations, assumptions).\n"
        "3. target_relations MUST contain ONLY exact table names from AllowedCatalog.\n\n"
        f"ConsultationTranscript: {json.dumps([t.model_dump() for t in recent_turns], ensure_ascii=False)}\n"
        f"BusinessGlossary: {json.dumps([g.model_dump() for g in glossary_list], ensure_ascii=False)}\n"
        f"TenantSampleDimensionValues (formatting hints only): {json.dumps(tenant_value_samples or {}, ensure_ascii=False)}\n"
        f"AllowedCatalog: {json.dumps(catalog_slice, ensure_ascii=False)}\n"
    )

    try:
        if structured_llm_fn is not None:
            raw = structured_llm_fn(prompt, _LlmIntentSummaryPayload)
        else:
            raw = generate_gemini_structured(
                api_key=clean_key,
                prompt=prompt,
                response_model=_LlmIntentSummaryPayload,
            )
        parsed = _LlmIntentSummaryPayload.model_validate(raw)
    except Exception as exc:
        raise ConsultantError("LLM_SUMMARY_FAILED", f"Intent summarization failed: {exc}") from exc

    allowed_names = {r.name for r in catalog.relations}
    valid_relations = [t for t in parsed.business_intent.target_relations if t in allowed_names]
    clean_intent = parsed.business_intent.model_copy(
        update={
            "original_question": parsed.summary_question,
            "target_relations": valid_relations,
        }
    )
    gap = classify_semantic_gap(parsed.summary_question, catalog)

    return ConsultantIntentSummary(
        summary_question=parsed.summary_question,
        business_intent=clean_intent,
        gap=gap,
        resolved_timezone=tz_name,
    )
