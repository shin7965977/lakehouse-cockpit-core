"""Stateless Multi-Turn Business Intent Clarifier (Phase 1).

Implements the bounded state machine:
  FAST_PATH_SQL_READY | CLARIFYING (max 3 turns) -> READY_FOR_REVIEW | BLOCKED_UNMAPPABLE

Uses native Gemini Structured Output + Pydantic v2 (no Instructor/PydanticAI dependency)
and strictly enforces the Mandatory Fail-Loud Policy.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

try:
    from .dbt_catalog import DbtMetadataCatalog
    from .duckdb_executor import generate_gemini_structured
    from .semantic_gap_detector import GapClassification, classify_semantic_gap
except (ImportError, ValueError):
    try:
        from services.dbt_catalog import DbtMetadataCatalog
        from services.duckdb_executor import generate_gemini_structured
        from services.semantic_gap_detector import GapClassification, classify_semantic_gap
    except (ImportError, ValueError):
        from cockpit_core.services.dbt_catalog import DbtMetadataCatalog
        from cockpit_core.services.duckdb_executor import generate_gemini_structured
        from cockpit_core.services.semantic_gap_detector import GapClassification, classify_semantic_gap


ClarificationState = Literal[
    "FAST_PATH_SQL_READY",
    "CLARIFYING",
    "READY_FOR_REVIEW",
    "BLOCKED_UNMAPPABLE",
]
IntentType = Literal["SINGLE_QUERY", "DIAGNOSTIC_ANALYSIS"]

_DIAGNOSTIC_OR_VAGUE_PATTERNS = (
    "為什麼",
    "為何",
    "原因",
    "不好",
    "下降",
    "暴跌",
    "下滑",
    "成長性",
    "高成長",
    "潛力",
    "異常",
    "診斷",
    "why ",
    "why?",
    "root cause",
    "high growth",
    "underperform",
)


class IntentClarificationError(RuntimeError):
    """Fail-Loud error raised when intent clarification cannot proceed."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class GlossaryEntry(BaseModel):
    business_term: str
    synonyms: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    key_columns: list[str] = Field(default_factory=list)
    notes: str = ""


class ClarificationTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ClarificationOption(BaseModel):
    label: str
    value: str
    description: str = ""


class BusinessIntentSpec(BaseModel):
    original_question: str = ""
    intent_type: IntentType = "SINGLE_QUERY"
    metric: str = ""
    metric_basis: str = ""
    timeframe: str = ""
    comparison_period: str = ""
    grain: str = ""
    breakdown_dimensions: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    target_relations: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class _LlmClarificationPayload(BaseModel):
    state: Literal["CLARIFYING", "READY_FOR_REVIEW", "BLOCKED_UNMAPPABLE"]
    assistant_message: str
    next_question: str = ""
    options: list[ClarificationOption] = Field(default_factory=list)
    business_intent: BusinessIntentSpec = Field(default_factory=BusinessIntentSpec)
    blocking_reason: str = ""


class ClarificationResult(BaseModel):
    state: ClarificationState
    turn_number: int = 1
    max_turns: int = 3
    assistant_message: str = ""
    next_question: str = ""
    options: list[ClarificationOption] = Field(default_factory=list)
    business_intent: BusinessIntentSpec = Field(default_factory=BusinessIntentSpec)
    blocking_reason: str = ""


def _is_diagnostic_or_vague(question: str) -> bool:
    q_low = (question or "").lower()
    return any(tok in q_low for tok in _DIAGNOSTIC_OR_VAGUE_PATTERNS)


def _should_take_fast_path(
    question: str,
    catalog: DbtMetadataCatalog,
    history: list[ClarificationTurn],
) -> tuple[bool, list[str]]:
    """Return (True, matched_relations) when question is already explicit and unambiguous."""
    if history:
        return False, []
    if _is_diagnostic_or_vague(question):
        return False, []

    gap = classify_semantic_gap(question, catalog)
    if gap.classification == GapClassification.SEMANTIC_AMBIGUITY:
        return False, []
    if gap.classification == GapClassification.REUSE_EXISTING:
        rels = [gap.matched_metric.source_relation] if (gap.matched_metric and gap.matched_metric.source_relation) else []
        return True, rels

    q_low = (question or "").lower()
    explicit_relations = [
        r.name for r in catalog.relations if re.search(rf"\b{re.escape(r.name.lower())}\b", q_low)
    ]
    if explicit_relations:
        return True, explicit_relations
    return False, []


def _select_relevant_catalog_slice(
    question: str,
    history: list[ClarificationTurn],
    catalog: DbtMetadataCatalog,
    glossary: list[GlossaryEntry],
    max_tables: int = 25,
) -> list[dict[str, Any]]:
    """Keep prompt compact for 100+ live tables by prioritizing glossary + keyword hits."""
    combined = " ".join([question] + [t.content for t in history]).lower()
    prioritized: list[str] = []

    for g in glossary:
        terms = [g.business_term, *g.synonyms]
        if any(term.lower() in combined for term in terms if term):
            for tbl in g.tables:
                if tbl not in prioritized:
                    prioritized.append(tbl)

    for rel in catalog.relations:
        if rel.name.lower() in combined and rel.name not in prioritized:
            prioritized.append(rel.name)

    # Include core glossary tables even if not directly mentioned yet
    for g in glossary:
        for tbl in g.tables:
            if tbl not in prioritized:
                prioritized.append(tbl)

    rel_map = {r.name: r for r in catalog.relations}
    chosen: list[dict[str, Any]] = []
    seen: set[str] = set()

    for name in prioritized:
        if name in rel_map and name not in seen:
            r = rel_map[name]
            chosen.append(
                {
                    "table": r.name,
                    "layer": r.layer,
                    "columns": [c.name for c in r.columns[:25]],
                }
            )
            seen.add(name)
            if len(chosen) >= max_tables:
                break

    if len(chosen) < max_tables:
        for r in catalog.relations:
            if r.name not in seen:
                chosen.append(
                    {
                        "table": r.name,
                        "layer": r.layer,
                        "columns": [c.name for c in r.columns[:20]],
                    }
                )
                seen.add(r.name)
                if len(chosen) >= max_tables:
                    break

    return chosen


def clarify_business_intent(
    *,
    question: str,
    catalog: DbtMetadataCatalog,
    history: list[ClarificationTurn] | None = None,
    current_intent: BusinessIntentSpec | None = None,
    glossary: list[GlossaryEntry] | None = None,
    tenant_value_samples: dict[str, list[str]] | None = None,
    api_key: str = "",
    max_turns: int = 3,
    structured_llm_fn: Callable[[str, type[Any]], Any] | None = None,
) -> ClarificationResult:
    """Run one stateless clarification step with Smart Fast-Path and hard 3-round cap."""
    clean_q = (question or "").strip()
    if not clean_q:
        raise IntentClarificationError("EMPTY_QUESTION", "Question cannot be empty.")

    turns = list(history or [])
    glossary_list = list(glossary or [])
    user_reply_count = sum(1 for t in turns if t.role == "user")
    turn_number = 1 + user_reply_count

    # 1. Smart Fast-Path for explicit, non-diagnostic queries on turn 1
    fast_ok, fast_relations = _should_take_fast_path(clean_q, catalog, turns)
    if fast_ok:
        return ClarificationResult(
            state="FAST_PATH_SQL_READY",
            turn_number=1,
            max_turns=max_turns,
            assistant_message="問題已明確指定資料表或認證指標，直接進入 SQL 草擬與安全驗證流程。",
            business_intent=BusinessIntentSpec(
                original_question=clean_q,
                intent_type="SINGLE_QUERY",
                target_relations=fast_relations,
            ),
        )

    # 2. Strict API-key gating (Fail-Loud)
    clean_key = (api_key or "").strip()
    if structured_llm_fn is None and not clean_key:
        raise IntentClarificationError(
            "API_KEY_MISSING",
            "Gemini API Key is required for multi-turn business intent clarification (Fail-Loud).",
        )

    catalog_slice = _select_relevant_catalog_slice(clean_q, turns, catalog, glossary_list)
    metrics_slice = [
        {"name": m.name, "label": m.label, "source_relation": m.source_relation}
        for m in catalog.metrics[:25]
    ]
    glossary_slice = [g.model_dump() for g in glossary_list]
    history_payload = [t.model_dump() for t in turns]
    intent_payload = current_intent.model_dump() if current_intent else {}

    today_taipei = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    prompt = (
        "You are an expert Analytics Engineer conducting a concise Traditional Chinese (繁體中文) "
        "business intent clarification before generating SQL.\n"
        f"CurrentDate (Asia/Taipei): {today_taipei}\n"
        f"Current turn: {turn_number} of {max_turns}.\n"
        "Rules:\n"
        "1. Map Chinese business concepts strictly to REAL tables in AllowedCatalog or Glossary. NEVER invent table names.\n"
        "2. If the user asks a 'why' / root-cause / decline / growth diagnosis question, set intent_type='DIAGNOSTIC_ANALYSIS'; otherwise 'SINGLE_QUERY'.\n"
        "3. Ask AT MOST ONE highest-priority missing question per turn (priority: Metric basis -> Timeframe/Comparison -> Grain/Breakdown/Filters) with 2-4 concise clickable options.\n"
        "4. When enough information is collected (or if turn >= max_turns and at least 1 real table matches), set state='READY_FOR_REVIEW' and list any default assumptions explicitly in business_intent.assumptions.\n"
        "5. Only set state='BLOCKED_UNMAPPABLE' when the question cannot be mapped to ANY table in AllowedCatalog. "
        "Treat TenantSampleDimensionValues STRICTLY as formatting/casing hints (e.g., knowing status uses 'Completed' instead of 'completed') — "
        "NEVER reject a question solely because a specific filter value is missing from TenantSampleDimensionValues; let the database query be the final judge of whether matching rows exist.\n\n"
        f"OriginalQuestion: {clean_q}\n"
        f"ConversationHistory: {json.dumps(history_payload, ensure_ascii=False)}\n"
        f"CurrentIntent: {json.dumps(intent_payload, ensure_ascii=False)}\n"
        f"BusinessGlossary: {json.dumps(glossary_slice, ensure_ascii=False)}\n"
        f"TenantSampleDimensionValues: {json.dumps(tenant_value_samples or {}, ensure_ascii=False)}\n"
        f"CertifiedMetrics: {json.dumps(metrics_slice, ensure_ascii=False)}\n"
        f"AllowedCatalog: {json.dumps(catalog_slice, ensure_ascii=False)}\n"
    )

    try:
        if structured_llm_fn is not None:
            raw_res = structured_llm_fn(prompt, _LlmClarificationPayload)
        else:
            raw_res = generate_gemini_structured(
                api_key=clean_key,
                prompt=prompt,
                response_model=_LlmClarificationPayload,
            )
        parsed = _LlmClarificationPayload.model_validate(raw_res)
    except Exception as exc:
        raise IntentClarificationError(
            "LLM_CLARIFICATION_FAILED",
            f"Gemini structured clarification failed: {exc}",
        ) from exc

    # 3. Validate target_relations strictly against real catalog tables (Fail-Loud anti-hallucination)
    allowed_names = {r.name for r in catalog.relations}
    valid_relations = [t for t in parsed.business_intent.target_relations if t in allowed_names]
    intent = parsed.business_intent.model_copy(
        update={
            "original_question": parsed.business_intent.original_question or clean_q,
            "target_relations": valid_relations,
        }
    )

    final_state: ClarificationState = parsed.state
    blocking_reason = parsed.blocking_reason

    # 4. Enforce hard max-turn cap and unmappable Fail-Loud gate
    if final_state == "BLOCKED_UNMAPPABLE":
        if not blocking_reason:
            blocking_reason = "無法在現有 ERP 資料目錄中找到與此問題對應的資料表或指標。"
    elif turn_number >= max_turns and final_state == "CLARIFYING":
        if valid_relations:
            final_state = "READY_FOR_REVIEW"
            if not intent.assumptions:
                intent = intent.model_copy(
                    update={"assumptions": ["已達 3 輪澄清上限，未指定條件將採用預設口徑，請於下方規格卡確認或修改。"]}
                )
        else:
            final_state = "BLOCKED_UNMAPPABLE"
            blocking_reason = (
                "經過 3 輪澄清仍無法對應到 ERP 現有資料表（未找到任何合法目標資料表），已停止生成以避免幻覺。"
            )
    elif final_state == "READY_FOR_REVIEW" and not valid_relations:
        final_state = "BLOCKED_UNMAPPABLE"
        blocking_reason = (
            "LLM 回傳的目標資料表皆不在 ERP 資料目錄中，已依 Fail-Loud 原則攔截。"
        )

    return ClarificationResult(
        state=final_state,
        turn_number=turn_number,
        max_turns=max_turns,
        assistant_message=parsed.assistant_message,
        next_question="" if final_state != "CLARIFYING" else parsed.next_question,
        options=[] if final_state != "CLARIFYING" else parsed.options,
        business_intent=intent,
        blocking_reason=blocking_reason,
    )
