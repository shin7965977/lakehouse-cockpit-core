"""Authenticated Internal FastAPI Control Plane for AI Analytics Workbench.

Endpoints:
- GET  /health
- POST /v1/catalog/search
- POST /v1/query-spec/draft-and-classify
- POST /v1/sql/validate-and-reconcile
- POST /v1/sql/repair
- POST /v1/query-spec/execute

Protected endpoints require a valid `X-Internal-Token` header matching
`AE_INTERNAL_TOKEN` (fail-closed).
"""

from __future__ import annotations

import hmac
import os
import uuid
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

try:
    from ..integrations.langfuse_adapter import ObservabilityTracker
    from ..integrations.lightdash_adapter import LightdashAdapter
    from ..services.consultant_service import (
        ConsultantAnswer,
        ConsultantError,
        ConsultantIntentSummary,
        ConsultantProposal,
        ConsultantQueryOutcome,
        answer_consultant_turn,
        propose_consultant_turn,
        summarize_consultation_intent,
    )
    from ..services.dbt_asset_builder import (
        DbtAndLightdashBundle,
        build_dbt_and_lightdash_bundle,
    )
    from ..services.dbt_catalog import CatalogSearchResult, DbtMetadataCatalog
    from ..services.dbt_validator import (
        AssetValidationCheck,
        IsolatedValidationResult,
        validate_isolated_dbt_asset,
    )
    from ..services.diagnostic_planner import (
        DiagnosticPlan,
        DiagnosticPlanningError,
        DiagnosticSynthesis,
        StepExecutionOutcome,
        build_diagnostic_plan,
        synthesize_diagnostic_findings,
    )
    from ..services.intent_clarifier import (
        BusinessIntentSpec,
        ClarificationResult,
        ClarificationTurn,
        GlossaryEntry,
        IntentClarificationError,
        clarify_business_intent,
    )
    from ..services.query_spec import QueryDialect, QuerySpec
    from ..services.release_orchestrator import DraftPRResult, orchestrate_draft_pr
    from ..services.requirement_engine import (
        DraftAndClassifyResponse,
        RequirementDraftError,
        draft_and_classify_query_spec,
    )
    from ..services.semantic_promotion import (
        PromotionCandidate,
        generate_promotion_candidates,
    )
    from ..services.source_ingestion import (
        SourceIngestionProposal,
        propose_source_ingestion,
    )
    from ..services.sql_repair import RepairError, SqlRepairResult, repair_sql_bounded
    from ..services.sql_validator import SqlValidationResult, validate_and_reconcile_sql
    from ..services.warehouse_adapters import (
        DuckDBAdapter,
        ExecutionResult,
        WarehouseAdapter,
        WarehouseAdapterError,
    )
except (ImportError, ValueError):
    from integrations.langfuse_adapter import ObservabilityTracker
    from integrations.lightdash_adapter import LightdashAdapter
    from services.consultant_service import (
        ConsultantAnswer,
        ConsultantError,
        ConsultantIntentSummary,
        ConsultantProposal,
        ConsultantQueryOutcome,
        answer_consultant_turn,
        propose_consultant_turn,
        summarize_consultation_intent,
    )
    from services.dbt_asset_builder import (
        DbtAndLightdashBundle,
        build_dbt_and_lightdash_bundle,
    )
    from services.dbt_catalog import CatalogSearchResult, DbtMetadataCatalog
    from services.dbt_validator import (
        AssetValidationCheck,
        IsolatedValidationResult,
        validate_isolated_dbt_asset,
    )
    from services.diagnostic_planner import (
        DiagnosticPlan,
        DiagnosticPlanningError,
        DiagnosticSynthesis,
        StepExecutionOutcome,
        build_diagnostic_plan,
        synthesize_diagnostic_findings,
    )
    from services.intent_clarifier import (
        BusinessIntentSpec,
        ClarificationResult,
        ClarificationTurn,
        GlossaryEntry,
        IntentClarificationError,
        clarify_business_intent,
    )
    from services.query_spec import QueryDialect, QuerySpec
    from services.release_orchestrator import DraftPRResult, orchestrate_draft_pr
    from services.requirement_engine import (
        DraftAndClassifyResponse,
        RequirementDraftError,
        draft_and_classify_query_spec,
    )
    from services.semantic_promotion import (
        PromotionCandidate,
        generate_promotion_candidates,
    )
    from services.source_ingestion import (
        SourceIngestionProposal,
        propose_source_ingestion,
    )
    from services.sql_repair import RepairError, SqlRepairResult, repair_sql_bounded
    from services.sql_validator import SqlValidationResult, validate_and_reconcile_sql
    from services.warehouse_adapters import (
        DuckDBAdapter,
        ExecutionResult,
        WarehouseAdapter,
        WarehouseAdapterError,
    )


class CatalogSearchRequest(BaseModel):
    query: str = ""
    limit: int = Field(default=10, ge=1, le=50)
    live_catalog: list[dict[str, Any]] | None = None


class DraftAndClassifyRequest(BaseModel):
    question: str
    dialect: QueryDialect = "duckdb"
    is_authorized: bool = True
    tenant_id: int | str | None = None
    allowed_relations: list[str] | None = None
    gemini_api_key: str = ""
    live_catalog: list[dict[str, Any]] | None = None


class SqlValidateRequest(BaseModel):
    sql: str
    base_spec: QuerySpec | None = None
    dialect: QueryDialect = "duckdb"
    tenant_id: int | str | None = None
    live_catalog: list[dict[str, Any]] | None = None


class SqlRepairRequest(BaseModel):
    sql: str
    error_message: str
    base_spec: QuerySpec | None = None
    dialect: QueryDialect = "duckdb"
    tenant_id: int | str | None = None
    gemini_api_key: str = ""
    max_attempts: int = Field(default=2, ge=1, le=2)
    live_catalog: list[dict[str, Any]] | None = None


class QuerySpecExecuteRequest(BaseModel):
    query_spec: QuerySpec
    tenant_id: int | str | None = None


class AssetBuildRequest(BaseModel):
    query_spec: QuerySpec
    model_name: str
    grain: list[str] = Field(default_factory=list)
    business_owner: str = "analytics-engineering@company.com"
    golden_sql: str | None = None
    reconcile_column: str | None = None
    skip_local_validation: bool = False


class AssetBuildResponse(BaseModel):
    bundle: DbtAndLightdashBundle
    validation: IsolatedValidationResult
    lightdash: dict[str, Any]
    compiled_sql: str = ""


class SourceIngestionRequest(BaseModel):
    source_type: str
    source_locator: str
    target_bronze_table: str
    owner: str = "data-engineering@company.com"
    execute_isolated_bronze_load: bool = False


class PromotionCandidatesResponse(BaseModel):
    candidates: list[PromotionCandidate]


class DraftPRRequest(BaseModel):
    model_name: str
    question: str
    files: dict[str, str]
    validation_summary: str
    lightdash_status: str = "STAGING_LOCAL_ONLY"


class IntentClarifyRequest(BaseModel):
    question: str
    history: list[ClarificationTurn] = Field(default_factory=list)
    current_intent: BusinessIntentSpec | None = None
    live_catalog: list[dict[str, Any]] | None = None
    glossary: list[GlossaryEntry] = Field(default_factory=list)
    tenant_value_samples: dict[str, list[str]] = Field(default_factory=dict)
    gemini_api_key: str = ""
    max_turns: int = 3
    tenant_id: str | int | None = None


class DiagnosticPlanRequest(BaseModel):
    business_intent: BusinessIntentSpec
    dialect: QueryDialect = "postgres"
    live_catalog: list[dict[str, Any]] | None = None
    tenant_value_samples: dict[str, list[str]] = Field(default_factory=dict)
    gemini_api_key: str = ""
    max_steps: int = 4
    tenant_id: str | int | None = None


class DiagnosticSynthesizeRequest(BaseModel):
    business_intent: BusinessIntentSpec
    step_results: list[StepExecutionOutcome] = Field(default_factory=list)
    gemini_api_key: str = ""
    tenant_id: str | int | None = None


class ConsultantProposeRequest(BaseModel):
    message: str
    history: list[ClarificationTurn] = Field(default_factory=list)
    live_catalog: list[dict[str, Any]] | None = None
    glossary: list[GlossaryEntry] = Field(default_factory=list)
    tenant_value_samples: dict[str, list[str]] = Field(default_factory=dict)
    dialect: QueryDialect = "postgres"
    timezone: str | None = None
    gemini_api_key: str = ""
    tenant_id: str | int | None = None


class ConsultantAnswerRequest(BaseModel):
    message: str
    history: list[ClarificationTurn] = Field(default_factory=list)
    query_outcomes: list[ConsultantQueryOutcome] = Field(default_factory=list)
    timezone: str | None = None
    gemini_api_key: str = ""
    tenant_id: str | int | None = None


class ConsultantSummarizeRequest(BaseModel):
    history: list[ClarificationTurn] = Field(default_factory=list)
    live_catalog: list[dict[str, Any]] | None = None
    glossary: list[GlossaryEntry] = Field(default_factory=list)
    tenant_value_samples: dict[str, list[str]] = Field(default_factory=dict)
    timezone: str | None = None
    gemini_api_key: str = ""
    tenant_id: str | int | None = None


def _tenant_scoped_tables_from_catalog(catalog: DbtMetadataCatalog) -> set[str]:
    scoped: set[str] = set()
    for rel in catalog.relations:
        if any(c.name.lower() == "tenant_id" for c in rel.columns):
            scoped.add(rel.name)
    return scoped


def create_app(
    catalog: DbtMetadataCatalog | None = None,
    internal_token: str | None = None,
    warehouse_adapter: WarehouseAdapter | None = None,
    tracker: ObservabilityTracker | None = None,
    structured_llm_fn: Any | None = None,
) -> FastAPI:
    """Create a configured FastAPI control-plane application."""
    app = FastAPI(
        title="AI Analytics Workbench — AE Control Plane",
        version="0.4.0",
    )

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next: Any) -> Any:
        req_id = (request.headers.get("X-Request-Id") or "").strip() or f"req-{uuid.uuid4().hex[:12]}"
        request.state.request_id = req_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = req_id
        return response
    active_catalog = catalog or DbtMetadataCatalog.from_target_dir()
    active_tracker = tracker or ObservabilityTracker()

    if warehouse_adapter is not None:
        active_adapter: WarehouseAdapter = warehouse_adapter
    else:
        db_file = os.getenv("AE_DUCKDB_PATH", "").strip()
        active_adapter = DuckDBAdapter(db_path=db_file if db_file else None)

    allowed_relation_names = {r.name for r in active_catalog.relations}
    tenant_scoped_relations = _tenant_scoped_tables_from_catalog(active_catalog)

    def _resolve_catalog(live_catalog: list[dict[str, Any]] | None) -> tuple[DbtMetadataCatalog, set[str], set[str]]:
        if live_catalog:
            dyn_cat = DbtMetadataCatalog.from_live_schema(live_catalog)
            return (
                dyn_cat,
                {r.name for r in dyn_cat.relations},
                _tenant_scoped_tables_from_catalog(dyn_cat),
            )
        return active_catalog, allowed_relation_names, tenant_scoped_relations

    def _expected_token() -> str:
        if internal_token is not None:
            return internal_token
        return os.getenv("AE_INTERNAL_TOKEN", "").strip()

    def _require_internal_token(
        x_internal_token: str | None = Header(default=None, alias="X-Internal-Token"),
    ) -> None:
        expected = _expected_token()
        if not expected:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AE_INTERNAL_TOKEN is not configured on control plane (fail-closed).",
            )
        if not x_internal_token or not hmac.compare_digest(x_internal_token.strip(), expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing X-Internal-Token.",
            )

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "catalog": active_catalog.summary(),
            "warehouse": active_adapter.capabilities(),
            "langfuse_enabled": active_tracker.langfuse_enabled,
        }

    @app.post(
        "/v1/consultant/propose",
        response_model=ConsultantProposal,
        dependencies=[Depends(_require_internal_token)],
    )
    def consultant_propose_endpoint(
        req: ConsultantProposeRequest,
        x_user_timezone: str | None = Header(default=None, alias="X-User-Timezone"),
    ) -> ConsultantProposal:
        cat, _, _ = _resolve_catalog(req.live_catalog)
        effective_key = (req.gemini_api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        tz = req.timezone or x_user_timezone
        try:
            res = propose_consultant_turn(
                message=req.message,
                catalog=cat,
                history=req.history,
                glossary=req.glossary,
                tenant_value_samples=req.tenant_value_samples,
                dialect=req.dialect,
                user_timezone=tz,
                api_key=effective_key,
                structured_llm_fn=structured_llm_fn,
            )
            active_tracker.record_span(
                "consultant_propose",
                {
                    "needs_data": res.needs_data,
                    "query_count": len(res.proposed_queries),
                    "timezone": res.resolved_timezone,
                    "tenant_id": req.tenant_id,
                },
            )
            return res
        except ConsultantError as exc:
            status_code = (
                status.HTTP_400_BAD_REQUEST
                if exc.code in ("EMPTY_MESSAGE", "API_KEY_MISSING")
                else status.HTTP_422_UNPROCESSABLE_ENTITY
            )
            raise HTTPException(
                status_code=status_code,
                detail={"code": exc.code, "message": exc.message},
            ) from exc

    @app.post(
        "/v1/consultant/answer",
        response_model=ConsultantAnswer,
        dependencies=[Depends(_require_internal_token)],
    )
    def consultant_answer_endpoint(
        req: ConsultantAnswerRequest,
        x_user_timezone: str | None = Header(default=None, alias="X-User-Timezone"),
    ) -> ConsultantAnswer:
        effective_key = (req.gemini_api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        tz = req.timezone or x_user_timezone
        try:
            res = answer_consultant_turn(
                message=req.message,
                query_outcomes=req.query_outcomes,
                history=req.history,
                user_timezone=tz,
                api_key=effective_key,
                structured_llm_fn=structured_llm_fn,
            )
            active_tracker.record_span(
                "consultant_answer",
                {
                    "outcome_count": len(req.query_outcomes),
                    "timezone": res.resolved_timezone,
                    "tenant_id": req.tenant_id,
                },
            )
            return res
        except ConsultantError as exc:
            status_code = (
                status.HTTP_400_BAD_REQUEST
                if exc.code == "API_KEY_MISSING"
                else status.HTTP_422_UNPROCESSABLE_ENTITY
            )
            raise HTTPException(
                status_code=status_code,
                detail={"code": exc.code, "message": exc.message},
            ) from exc

    @app.post(
        "/v1/consultant/summarize-intent",
        response_model=ConsultantIntentSummary,
        dependencies=[Depends(_require_internal_token)],
    )
    def consultant_summarize_endpoint(
        req: ConsultantSummarizeRequest,
        x_user_timezone: str | None = Header(default=None, alias="X-User-Timezone"),
    ) -> ConsultantIntentSummary:
        cat, _, _ = _resolve_catalog(req.live_catalog)
        effective_key = (req.gemini_api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        tz = req.timezone or x_user_timezone
        try:
            res = summarize_consultation_intent(
                history=req.history,
                catalog=cat,
                glossary=req.glossary,
                tenant_value_samples=req.tenant_value_samples,
                user_timezone=tz,
                api_key=effective_key,
                structured_llm_fn=structured_llm_fn,
            )
            active_tracker.record_span(
                "consultant_summarize_intent",
                {
                    "classification": res.gap.classification.value,
                    "intent_type": res.business_intent.intent_type,
                    "timezone": res.resolved_timezone,
                    "tenant_id": req.tenant_id,
                },
            )
            return res
        except ConsultantError as exc:
            status_code = (
                status.HTTP_400_BAD_REQUEST
                if exc.code in ("EMPTY_HISTORY", "API_KEY_MISSING")
                else status.HTTP_422_UNPROCESSABLE_ENTITY
            )
            raise HTTPException(
                status_code=status_code,
                detail={"code": exc.code, "message": exc.message},
            ) from exc

    @app.post(
        "/v1/intent/clarify",
        response_model=ClarificationResult,
        dependencies=[Depends(_require_internal_token)],
    )
    def clarify_intent_endpoint(req: IntentClarifyRequest) -> ClarificationResult:
        cat, _, _ = _resolve_catalog(req.live_catalog)
        effective_key = (req.gemini_api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        try:
            res = clarify_business_intent(
                question=req.question,
                catalog=cat,
                history=req.history,
                current_intent=req.current_intent,
                glossary=req.glossary,
                tenant_value_samples=req.tenant_value_samples,
                api_key=effective_key,
                max_turns=req.max_turns,
                structured_llm_fn=structured_llm_fn,
            )
            active_tracker.record_span(
                "intent_clarify",
                {
                    "state": res.state,
                    "turn_number": res.turn_number,
                    "intent_type": res.business_intent.intent_type,
                    "tenant_id": req.tenant_id,
                },
            )
            return res
        except IntentClarificationError as exc:
            status_code = (
                status.HTTP_400_BAD_REQUEST
                if exc.code in ("EMPTY_QUESTION", "API_KEY_MISSING")
                else status.HTTP_422_UNPROCESSABLE_ENTITY
            )
            raise HTTPException(
                status_code=status_code,
                detail={"code": exc.code, "message": exc.message},
            ) from exc

    @app.post(
        "/v1/diagnostic/plan",
        response_model=DiagnosticPlan,
        dependencies=[Depends(_require_internal_token)],
    )
    def diagnostic_plan_endpoint(req: DiagnosticPlanRequest) -> DiagnosticPlan:
        cat, _, _ = _resolve_catalog(req.live_catalog)
        effective_key = (req.gemini_api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        try:
            plan = build_diagnostic_plan(
                business_intent=req.business_intent,
                catalog=cat,
                dialect=req.dialect,
                tenant_value_samples=req.tenant_value_samples,
                api_key=effective_key,
                max_steps=req.max_steps,
                structured_llm_fn=structured_llm_fn,
            )
            active_tracker.record_span(
                "diagnostic_plan",
                {
                    "plan_hash": plan.plan_hash,
                    "step_count": len(plan.steps),
                    "tenant_id": req.tenant_id,
                },
            )
            return plan
        except DiagnosticPlanningError as exc:
            status_code = (
                status.HTTP_400_BAD_REQUEST
                if exc.code in ("API_KEY_MISSING", "EMPTY_CATALOG")
                else status.HTTP_422_UNPROCESSABLE_ENTITY
            )
            raise HTTPException(
                status_code=status_code,
                detail={"code": exc.code, "message": exc.message},
            ) from exc

    @app.post(
        "/v1/diagnostic/synthesize",
        response_model=DiagnosticSynthesis,
        dependencies=[Depends(_require_internal_token)],
    )
    def diagnostic_synthesize_endpoint(req: DiagnosticSynthesizeRequest) -> DiagnosticSynthesis:
        effective_key = (req.gemini_api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        try:
            synth = synthesize_diagnostic_findings(
                business_intent=req.business_intent,
                step_results=req.step_results,
                api_key=effective_key,
                structured_llm_fn=structured_llm_fn,
            )
            active_tracker.record_span(
                "diagnostic_synthesize",
                {
                    "findings_count": len(synth.key_findings),
                    "gaps_count": len(synth.data_gaps),
                    "tenant_id": req.tenant_id,
                },
            )
            return synth
        except DiagnosticPlanningError as exc:
            status_code = (
                status.HTTP_400_BAD_REQUEST
                if exc.code == "API_KEY_MISSING"
                else status.HTTP_422_UNPROCESSABLE_ENTITY
            )
            raise HTTPException(
                status_code=status_code,
                detail={"code": exc.code, "message": exc.message},
            ) from exc

    @app.post(
        "/v1/catalog/search",
        response_model=CatalogSearchResult,
        dependencies=[Depends(_require_internal_token)],
    )
    def search_catalog(req: CatalogSearchRequest) -> CatalogSearchResult:
        cat, _, _ = _resolve_catalog(req.live_catalog)
        return cat.search(req.query, limit=req.limit)

    @app.post(
        "/v1/query-spec/draft-and-classify",
        response_model=DraftAndClassifyResponse,
        dependencies=[Depends(_require_internal_token)],
    )
    def draft_and_classify(
        req: DraftAndClassifyRequest, request: Request
    ) -> DraftAndClassifyResponse:
        req_id = getattr(request.state, "request_id", None)
        cat, _, _ = _resolve_catalog(req.live_catalog)
        effective_key = (req.gemini_api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        try:
            resp = draft_and_classify_query_spec(
                req.question,
                cat,
                dialect=req.dialect,
                api_key=effective_key,
                is_authorized=req.is_authorized,
                allowed_relations=(
                    set(req.allowed_relations) if req.allowed_relations is not None else None
                ),
            )
            active_tracker.record_gap_event(
                question=req.question,
                classification=resp.gap.classification.value,
                candidate_relations=resp.gap.candidate_relations,
                tenant_id=req.tenant_id,
                spec_hash=(
                    resp.query_spec.compute_spec_hash() if resp.query_spec is not None else None
                ),
                request_id=req_id,
            )
            return resp
        except RequirementDraftError as exc:
            active_tracker.record_span(
                "draft_and_classify_error",
                {"code": exc.code, "question": req.question, "tenant_id": req.tenant_id},
            )
            status_code = (
                status.HTTP_400_BAD_REQUEST
                if exc.code in ("EMPTY_QUESTION", "API_KEY_MISSING")
                else status.HTTP_422_UNPROCESSABLE_ENTITY
            )
            raise HTTPException(
                status_code=status_code,
                detail={"code": exc.code, "message": exc.message},
            ) from exc

    @app.post(
        "/v1/sql/validate-and-reconcile",
        response_model=SqlValidationResult,
        dependencies=[Depends(_require_internal_token)],
    )
    def validate_sql_endpoint(req: SqlValidateRequest) -> SqlValidationResult:
        _, allowed_rels, scoped_rels = _resolve_catalog(req.live_catalog)
        base_spec = req.base_spec or QuerySpec(dialect=req.dialect)
        result = validate_and_reconcile_sql(
            req.sql,
            base_spec,
            allowed_relations=allowed_rels,
            server_tenant_id=req.tenant_id,
            tenant_scoped_relations=scoped_rels,
        )
        active_tracker.record_span(
            "sql_validate_and_reconcile",
            {
                "status": result.status,
                "reason_codes": result.reason_codes,
                "tenant_id": req.tenant_id,
            },
        )
        return result

    @app.post(
        "/v1/sql/repair",
        response_model=SqlRepairResult,
        dependencies=[Depends(_require_internal_token)],
    )
    def repair_sql_endpoint(req: SqlRepairRequest) -> SqlRepairResult:
        _, allowed_rels, scoped_rels = _resolve_catalog(req.live_catalog)
        base_spec = req.base_spec or QuerySpec(dialect=req.dialect)
        effective_key = (req.gemini_api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        try:
            res = repair_sql_bounded(
                req.sql,
                req.error_message,
                base_spec=base_spec,
                allowed_relations=allowed_rels,
                adapter=active_adapter,
                server_tenant_id=req.tenant_id,
                tenant_scoped_relations=scoped_rels,
                api_key=effective_key,
                max_attempts=req.max_attempts,
            )
            active_tracker.record_span(
                "sql_repair",
                {
                    "repaired": res.repaired,
                    "attempts": res.attempts,
                    "error_class": res.error_class,
                    "tenant_id": req.tenant_id,
                },
            )
            return res
        except RepairError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": exc.code, "message": exc.message},
            ) from exc

    @app.post(
        "/v1/query-spec/execute",
        response_model=ExecutionResult,
        dependencies=[Depends(_require_internal_token)],
    )
    def execute_query_spec(req: QuerySpecExecuteRequest) -> ExecutionResult:
        spec = req.query_spec
        if not spec.is_confirmation_valid():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "SPEC_NOT_CONFIRMED",
                    "message": "QuerySpec must have intent_confirmed=True bound to the exact current spec_hash before execution.",
                },
            )

        compiled_sql = spec.compile_sql()
        val = validate_and_reconcile_sql(
            compiled_sql,
            spec,
            allowed_relations=allowed_relation_names,
            server_tenant_id=req.tenant_id,
            tenant_scoped_relations=tenant_scoped_relations,
        )
        if val.status == "BLOCKED":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "SQL_BLOCKED", "reason_codes": val.reason_codes, "messages": val.messages},
            )

        try:
            exec_res = active_adapter.execute_readonly(
                val.executable_sql, max_rows=spec.output.max_rows
            )
            active_tracker.record_span(
                "query_spec_execute",
                {
                    "spec_id": spec.spec_id,
                    "spec_hash": spec.compute_spec_hash(),
                    "row_count": exec_res.row_count,
                    "tenant_id": req.tenant_id,
                },
            )
            return exec_res
        except WarehouseAdapterError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": exc.code, "message": exc.message},
            ) from exc

    @app.post(
        "/v1/assets/build-and-validate",
        response_model=AssetBuildResponse,
        dependencies=[Depends(_require_internal_token)],
    )
    def build_and_validate_asset(req: AssetBuildRequest) -> AssetBuildResponse:
        bundle = build_dbt_and_lightdash_bundle(
            query_spec=req.query_spec,
            model_name=req.model_name,
            grain=req.grain,
            business_owner=req.business_owner,
        )
        candidate_sql = req.query_spec.compile_sql()
        if req.skip_local_validation:
            validation = IsolatedValidationResult(
                model_name=bundle.contract.model_name,
                passed=False,
                materialized_rows=0,
                columns=[],
                checks=[
                    AssetValidationCheck(
                        name="delegated_to_data_plane",
                        passed=False,
                        detail="Local DuckDB validation skipped; caller Data Plane must execute isolated read-only validation.",
                    )
                ],
                preview_rows=[],
            )
        else:
            source_dfs = getattr(active_adapter, "dataframes", None)
            source_db = getattr(active_adapter, "db_path", None)
            validation = validate_isolated_dbt_asset(
                model_name=bundle.contract.model_name,
                candidate_sql=candidate_sql,
                source_dataframes=source_dfs,
                source_duckdb_path=source_db,
                grain=req.grain,
                not_null_columns=req.grain,
                golden_sql=req.golden_sql,
                reconcile_column=req.reconcile_column,
            )
        ld_status = LightdashAdapter().check_status()
        return AssetBuildResponse(
            bundle=bundle,
            validation=validation,
            lightdash=ld_status,
            compiled_sql=candidate_sql,
        )

    @app.post(
        "/v1/ingestion/propose",
        response_model=SourceIngestionProposal,
        dependencies=[Depends(_require_internal_token)],
    )
    def propose_ingestion(req: SourceIngestionRequest) -> SourceIngestionProposal:
        return propose_source_ingestion(
            source_type=req.source_type,
            source_locator=req.source_locator,
            target_bronze_table=req.target_bronze_table,
            owner=req.owner,
            execute_isolated_bronze_load=req.execute_isolated_bronze_load,
        )

    @app.get(
        "/v1/promotions/candidates",
        response_model=PromotionCandidatesResponse,
        dependencies=[Depends(_require_internal_token)],
    )
    def list_promotion_candidates(min_occurrences: int = 2) -> PromotionCandidatesResponse:
        candidates = generate_promotion_candidates(
            active_tracker.gap_log_path,
            min_occurrences=max(1, min_occurrences),
        )
        return PromotionCandidatesResponse(candidates=candidates)

    @app.post(
        "/v1/releases/draft-pr",
        response_model=DraftPRResult,
        dependencies=[Depends(_require_internal_token)],
    )
    def create_draft_pr(req: DraftPRRequest) -> DraftPRResult:
        return orchestrate_draft_pr(
            model_name=req.model_name,
            question=req.question,
            files=req.files,
            validation_summary=req.validation_summary,
            lightdash_status=req.lightdash_status,
        )

    return app


app = create_app()
