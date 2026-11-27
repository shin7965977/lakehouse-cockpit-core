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
    from .semantic_gap_detector import (
        GapClassification,
        GapDetectionResult,
        classify_semantic_gap,
        classify_sql_semantic_gap,
    )
    from .sql_validator import (
        SqlValidationResult,
        extract_output_group_by_columns,
        validate_and_reconcile_sql,
    )
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
        from services.semantic_gap_detector import (
            GapClassification,
            GapDetectionResult,
            classify_semantic_gap,
            classify_sql_semantic_gap,
        )
        from services.sql_validator import (
            SqlValidationResult,
            extract_output_group_by_columns,
            validate_and_reconcile_sql,
        )
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
            GapClassification,
            GapDetectionResult,
            classify_semantic_gap,
            classify_sql_semantic_gap,
        )
        from cockpit_core.services.sql_validator import (
            SqlValidationResult,
            extract_output_group_by_columns,
            validate_and_reconcile_sql,
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
        "You are a Senior Enterprise ERP Business Consultant (MBB / Big-4 caliber).\n"
        "LANGUAGE RULE: ALWAYS reply in the EXACT SAME language as LatestUserMessage "
        "(English if the user writes in English; Traditional Chinese 繁體中文 if the user writes in Chinese).\n"
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

    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", message or ""))
    if has_cjk:
        language_rule = (
            "LANGUAGE RULE: Reply strictly in Traditional Chinese (繁體中文).\n"
        )
        structure_rule = (
            "2. Do NOT merely recite raw table rows like a Q&A bot. Structure your response like a real senior consultant:\n"
            "   - **核心發現（So What?）**：point out concentration risks, top-share imbalances, anomalies, or key ratios directly from the real data.\n"
            "   - **可能商業假說（2 Hypotheses）**：propose 2 concrete, testable business hypotheses explaining WHY this pattern may be occurring.\n"
            "   - **建議下一步延伸**：proactively recommend 1-2 sharp follow-up angles the user can ask next (and mention they can click '進入商業語意分析' whenever ready to turn this into a governed model).\n\n"
        )
    else:
        language_rule = (
            "LANGUAGE RULE: Reply STRICTLY in executive-level ENGLISH. Do NOT output any Chinese characters.\n"
        )
        structure_rule = (
            "2. Do NOT merely recite raw table rows like a Q&A bot. Structure your response like a real senior consultant:\n"
            "   - **Key Finding (So What?)**: point out concentration risks, top-share imbalances, anomalies, or key ratios directly from the real data.\n"
            "   - **Business Hypotheses (2 Hypotheses)**: propose 2 concrete, testable business hypotheses explaining WHY this pattern may be occurring.\n"
            "   - **Recommended Next Step**: proactively recommend 1-2 sharp follow-up angles the user can ask next (and mention they can click 'Draft Governed Query' whenever ready to turn this into a governed query or dbt model).\n\n"
        )

    prompt = (
        "You are a Senior Enterprise ERP Business Consultant (hypothesis-driven, SO-WHAT mindset).\n"
        + language_rule
        + f"CurrentDate ({tz_name}): {today_str}\n"
        "STRICT ANTI-HALLUCINATION & CONSULTING DEPTH RULES:\n"
        "1. ONLY cite real numbers explicitly present in SUCCEEDED QueryOutcomes below. If a query FAILED or returned 0 rows, "
        "state that honestly and NEVER fabricate numbers.\n"
        + structure_rule
        + f"RecentConversationHistory: {json.dumps([t.model_dump() for t in recent_turns], ensure_ascii=False)}\n"
        f"SanitizedQueryOutcomes: {json.dumps(sanitized_outcomes, ensure_ascii=False, default=str)}\n"
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
        "auditable BusinessIntentSpec (match the user's primary language: English if English, Traditional Chinese if Chinese).\n"
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


class EvidenceQuerySummary(BaseModel):
    title: str = ""
    sql: str = ""
    status: str = "SUCCEEDED"


class PreviousCandidateDraft(BaseModel):
    version: int = 1
    title: str = ""
    sql: str = ""
    plain_language_explanation: str = ""
    group_by: list[str] = Field(default_factory=list)
    spec_hash: str = ""


class _LlmCandidateSqlPayload(BaseModel):
    can_answer: bool = True
    source_gap_reason: str = ""
    title: str = ""
    sql: str = ""
    plain_language_explanation: str = ""
    assumptions: list[str] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)


class CandidateQueryDraft(BaseModel):
    version: int = 1
    title: str = ""
    sql: str = ""
    plain_language_explanation: str = ""
    assumptions: list[str] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    gap: GapDetectionResult
    validation: SqlValidationResult | None = None
    query_spec: QuerySpec | None = None
    spec_hash: str = ""
    approvable: bool = False
    resolved_timezone: str = "UTC"


def draft_candidate_query(
    *,
    history: list[ClarificationTurn],
    catalog: DbtMetadataCatalog,
    evidence_queries: list[EvidenceQuerySummary] | None = None,
    previous_candidate: PreviousCandidateDraft | None = None,
    refinement_feedback: str = "",
    glossary: list[GlossaryEntry] | None = None,
    tenant_value_samples: dict[str, list[str]] | None = None,
    dialect: QueryDialect = "postgres",
    user_timezone: str | None = None,
    tenant_id: int | str | None = None,
    tenant_scoped_relations: set[str] | None = None,
    api_key: str = "",
    structured_llm_fn: Callable[[str, type[Any]], Any] | None = None,
) -> CandidateQueryDraft:
    """Draft or iteratively refine (v1 -> v2) a governed candidate SQL + plain-language explanation."""
    clean_feedback = (refinement_feedback or "").strip()
    if not history and not clean_feedback:
        raise ConsultantError(
            "EMPTY_HISTORY",
            "At least one consultation turn or refinement feedback is required to draft a candidate query.",
        )

    clean_key = (api_key or "").strip()
    if structured_llm_fn is None and not clean_key:
        raise ConsultantError(
            "API_KEY_MISSING",
            "Gemini API Key is required to draft a candidate SQL query (Fail-Loud).",
        )

    tz_name, today_str = resolve_user_timezone(user_timezone)
    recent_turns = list(history)[-_SLIDING_WINDOW_TURNS:]
    glossary_list = list(glossary or [])
    user_msgs = [t.content for t in recent_turns if t.role == "user"]
    latest_text = clean_feedback or (user_msgs[-1] if user_msgs else "")
    combined_q = " ".join([*user_msgs, clean_feedback]).strip()
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", latest_text))
    next_version = (previous_candidate.version + 1) if previous_candidate is not None else 1

    # 1. Build Evidence Carry-Over + Refinement prompt grounded in AllowedCatalog & Certified Metrics
    # NOTE: No brittle regex/keyword pre-check blocking here — semantic availability (SOURCE_GAP)
    # is decided via LLM Structured Abstention + deterministic SQLGlot AST allowlist & fingerprinting.
    catalog_slice = _select_relevant_catalog_slice(latest_text or combined_q, recent_turns, catalog, glossary_list)
    succeeded_evidence = [
        e.model_dump() for e in (evidence_queries or []) if (e.sql or "").strip()
    ][-4:]

    certified_metrics_payload = [
        {
            "name": m.name,
            "label": m.label,
            "relation": m.source_relation,
            "formula": f"{m.measure_agg}({m.measure_expr})" if m.measure_agg and m.measure_expr else "",
            "dimensions": list(m.dimensions or []),
        }
        for m in catalog.metrics
    ]

    lang_rule = (
        "LANGUAGE RULE: Write `title`, `plain_language_explanation`, `source_gap_reason`, and `assumptions` STRICTLY in Traditional Chinese (繁體中文).\n"
        if has_cjk
        else "LANGUAGE RULE: Write `title`, `plain_language_explanation`, `source_gap_reason`, and `assumptions` STRICTLY in clear, executive-level ENGLISH. Do NOT use Chinese characters.\n"
    )

    prompt = (
        "You are a Senior Analytics Engineer drafting a single executable READ-ONLY SQL query plus a concise "
        "plain-language explanation for business sign-off.\n"
        + lang_rule
        + f"TargetDialect: {dialect}\n"
        f"CurrentDate ({tz_name}): {today_str}\n"
        + "STRICT RULES:\n"
        "0. STRUCTURED ABSTENTION (SOURCE_GAP): If the user asks for external data sources or domains that CANNOT "
        "be answered from AllowedCatalog (e.g., Facebook Ads ROAS, Google Analytics / GA4 clicks, Instagram ads), "
        "set `can_answer=false`, `sql=\"\"`, explain clearly what source data is missing in `source_gap_reason` and "
        "`plain_language_explanation`, and NEVER fabricate tables or columns. Conversely, if PriorVerifiedEvidenceQueries "
        "already succeeded or the required tables exist in AllowedCatalog, set `can_answer=true`.\n"
        "1. Output ONLY a single READ-ONLY SELECT statement using exact table and column names from AllowedCatalog. "
        "NEVER fabricate tables or columns. If a CertifiedMetrics entry matches the user's requested metric, honor its exact formula.\n"
        "2. EVIDENCE CARRY-OVER: If PriorVerifiedEvidenceQueries already executed a valid query answering the user, "
        "preserve its proven aggregations (SUM/COUNT/AVG), joins, and date filters instead of dropping them.\n"
        "3. ITERATIVE REFINEMENT: If PreviousCandidateDraft and RefinementFeedback are provided, modify PreviousCandidateDraft "
        "to incorporate the exact requested changes.\n"
        "4. EXPRESSIVENESS & STYLE: Support aggregated metrics (GROUP BY), raw detail listings (no GROUP BY, group_by=[]), "
        "multi-table LEFT/INNER JOINs, CTEs, and Window Functions (OVER (...)). "
        "Follow dbt Style Guide: NEVER use meaningless table aliases like t1, t2, a, b — always use full table names "
        "(e.g. \"sales_orders\".\"id\", \"customers\".\"name\") when qualifying columns on joins.\n"
        "5. For date comparisons on text date columns (e.g. order_date), always use CAST(\"order_date\" AS DATE) >= "
        "CAST(CURRENT_DATE - INTERVAL '<n> days' AS DATE).\n"
        "6. In `plain_language_explanation`, clearly explain in 2-4 sentences what this SQL computes, filters, joins, "
        "and groups by, and end by asking if this matches what they want (or what they would like to adjust).\n"
        "7. Populate `group_by` with the exact SELECT output column names / aliases (no table prefixes like 't1.'; "
        "e.g. ['customer_id', 'customer_name'], or [] for raw detail listings / single scalar totals).\n\n"
        f"CertifiedMetrics: {json.dumps(certified_metrics_payload, ensure_ascii=False)}\n"
        f"AllowedCatalog: {json.dumps(catalog_slice, ensure_ascii=False)}\n"
        f"BusinessGlossary: {json.dumps([g.model_dump() for g in glossary_list], ensure_ascii=False)}\n"
        f"TenantSampleDimensionValues (formatting hints only): {json.dumps(tenant_value_samples or {}, ensure_ascii=False)}\n"
        f"PriorVerifiedEvidenceQueries: {json.dumps(succeeded_evidence, ensure_ascii=False)}\n"
        f"PreviousCandidateDraft: {json.dumps(previous_candidate.model_dump() if previous_candidate else None, ensure_ascii=False)}\n"
        f"RefinementFeedback: {clean_feedback}\n"
        f"ConsultationTranscript: {json.dumps([t.model_dump() for t in recent_turns], ensure_ascii=False)}\n"
    )

    try:
        if structured_llm_fn is not None:
            raw = structured_llm_fn(prompt, _LlmCandidateSqlPayload)
        else:
            raw = generate_gemini_structured(
                api_key=clean_key,
                prompt=prompt,
                response_model=_LlmCandidateSqlPayload,
            )
        parsed = _LlmCandidateSqlPayload.model_validate(raw)
    except Exception as exc:
        raise ConsultantError("LLM_CANDIDATE_FAILED", f"Candidate SQL drafting failed: {exc}") from exc

    # 2. Structured Abstention -> Fail-Loud SOURCE_GAP without fabricating SQL
    clean_sql = (parsed.sql or "").strip()
    if not parsed.can_answer or not clean_sql:
        gap_reason = (
            parsed.source_gap_reason
            or parsed.plain_language_explanation
            or "Requested domain or data source is not present in the authorized ERP catalog."
        )
        source_gap = GapDetectionResult(
            classification=GapClassification.SOURCE_GAP,
            question=latest_text or combined_q,
            reason=gap_reason,
            candidate_relations=[],
            evidence=["llm_structured_abstention=source_gap"],
        )
        return CandidateQueryDraft(
            version=next_version,
            title=parsed.title or "Source Gap Detected",
            sql="",
            plain_language_explanation=parsed.plain_language_explanation or f"Cannot draft governed SQL: {gap_reason}",
            assumptions=list(parsed.assumptions),
            group_by=[],
            gap=source_gap,
            validation=None,
            query_spec=None,
            spec_hash="",
            approvable=False,
            resolved_timezone=tz_name,
        )

    # 3. Pre-validate through SQLGlot AST & Tenant Security Gate
    allowed_names = {r.name for r in catalog.relations}
    scoped_rels = tenant_scoped_relations
    if scoped_rels is None:
        scoped_rels = {
            r.name for r in catalog.relations if any(c.name.lower() == "tenant_id" for c in r.columns)
        }

    base_spec = QuerySpec(
        spec_id=f"qs_cand_v{next_version}_{hashlib.sha256(clean_sql.encode('utf-8')).hexdigest()[:6]}",
        version=next_version,
        purpose="ad_hoc",
        question=latest_text or parsed.title,
        semantic_match=None,
        group_by=list(parsed.group_by),
        assumptions=list(parsed.assumptions),
        dialect=dialect,
        raw_sql_override=clean_sql,
        output=OutputSpec(max_rows=100, certification="VALIDATED_EXPLORATORY"),
    )

    validation = validate_and_reconcile_sql(
        clean_sql,
        base_spec,
        allowed_relations=allowed_names,
        server_tenant_id=tenant_id,
        tenant_scoped_relations=scoped_rels,
    )

    final_sql = (validation.executable_sql or clean_sql).strip()

    # 4. Ground Semantic Gap & Output Grain directly in verified SQL AST (eliminates keyword noise)
    ast_group_by = extract_output_group_by_columns(final_sql, dialect=dialect)
    normalized_group_by = (
        ast_group_by
        if ast_group_by
        else [g.split(".")[-1].strip('"').strip() for g in parsed.group_by if g and g.strip()]
    )
    if validation.status == "BLOCKED" and "UNLISTED_RELATION" in validation.reason_codes:
        # Anti-hallucination hard guard: LLM referenced a non-existent table -> Fail-Loud SOURCE_GAP
        gap = GapDetectionResult(
            classification=GapClassification.SOURCE_GAP,
            question=latest_text or combined_q,
            reason="; ".join(validation.messages) or "SQL referenced an unlisted table not in the authorized catalog.",
            candidate_relations=[],
            evidence=["ast_allowlist_blocked=UNLISTED_RELATION"],
        )
    elif validation.status != "BLOCKED" and final_sql:
        gap = classify_sql_semantic_gap(
            final_sql,
            catalog,
            question=latest_text or combined_q,
            dialect=dialect,
        )
    else:
        gap = GapDetectionResult(
            classification=GapClassification.NEW_SEMANTIC_METRIC,
            question=latest_text or combined_q,
            reason="; ".join(validation.messages) or "Candidate SQL blocked by AST security gate.",
            candidate_relations=[],
            evidence=list(validation.reason_codes),
        )

    final_spec = validation.reconciled_spec or base_spec
    updated_output = final_spec.output.model_copy(
        update={
            "certification": (
                "CERTIFIED"
                if gap.classification == GapClassification.REUSE_EXISTING
                else "VALIDATED_EXPLORATORY"
            )
        }
    )
    final_spec = final_spec.model_copy(
        update={
            "version": next_version,
            "semantic_match": gap.matched_metric.name if gap.matched_metric else None,
            "group_by": normalized_group_by,
            "raw_sql_override": final_sql,
            "output": updated_output,
        }
    )
    spec_hash = hashlib.sha256(f"v{next_version}:{final_sql}".encode("utf-8")).hexdigest()[:16]
    final_spec.review.spec_hash = spec_hash

    approvable = validation.status != "BLOCKED" and bool(final_sql)

    return CandidateQueryDraft(
        version=next_version,
        title=parsed.title,
        sql=final_sql,
        plain_language_explanation=parsed.plain_language_explanation,
        assumptions=list(parsed.assumptions),
        group_by=normalized_group_by,
        gap=gap,
        validation=validation,
        query_spec=final_spec,
        spec_hash=spec_hash,
        approvable=approvable,
        resolved_timezone=tz_name,
    )
