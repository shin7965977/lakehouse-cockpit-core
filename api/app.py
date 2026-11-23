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
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

try:
    from ..integrations.langfuse_adapter import ObservabilityTracker
    from ..services.dbt_catalog import CatalogSearchResult, DbtMetadataCatalog
    from ..services.query_spec import QueryDialect, QuerySpec
    from ..services.requirement_engine import (
        DraftAndClassifyResponse,
        RequirementDraftError,
        draft_and_classify_query_spec,
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
    from services.dbt_catalog import CatalogSearchResult, DbtMetadataCatalog
    from services.query_spec import QueryDialect, QuerySpec
    from services.requirement_engine import (
        DraftAndClassifyResponse,
        RequirementDraftError,
        draft_and_classify_query_spec,
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


class DraftAndClassifyRequest(BaseModel):
    question: str
    dialect: QueryDialect = "duckdb"
    is_authorized: bool = True
    tenant_id: int | str | None = None
    allowed_relations: list[str] | None = None
    gemini_api_key: str = ""


class SqlValidateRequest(BaseModel):
    sql: str
    base_spec: QuerySpec | None = None
    dialect: QueryDialect = "duckdb"
    tenant_id: int | str | None = None


class SqlRepairRequest(BaseModel):
    sql: str
    error_message: str
    base_spec: QuerySpec | None = None
    dialect: QueryDialect = "duckdb"
    tenant_id: int | str | None = None
    gemini_api_key: str = ""
    max_attempts: int = Field(default=2, ge=1, le=2)


class QuerySpecExecuteRequest(BaseModel):
    query_spec: QuerySpec
    tenant_id: int | str | None = None


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
) -> FastAPI:
    """Create a configured FastAPI control-plane application."""
    app = FastAPI(
        title="AI Analytics Workbench — AE Control Plane",
        version="0.2.0",
    )
    active_catalog = catalog or DbtMetadataCatalog.from_target_dir()
    active_tracker = tracker or ObservabilityTracker()

    if warehouse_adapter is not None:
        active_adapter: WarehouseAdapter = warehouse_adapter
    else:
        db_file = os.getenv("AE_DUCKDB_PATH", "").strip()
        active_adapter = DuckDBAdapter(db_path=db_file if db_file else None)

    allowed_relation_names = {r.name for r in active_catalog.relations}
    tenant_scoped_relations = _tenant_scoped_tables_from_catalog(active_catalog)

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
        "/v1/catalog/search",
        response_model=CatalogSearchResult,
        dependencies=[Depends(_require_internal_token)],
    )
    def search_catalog(req: CatalogSearchRequest) -> CatalogSearchResult:
        return active_catalog.search(req.query, limit=req.limit)

    @app.post(
        "/v1/query-spec/draft-and-classify",
        response_model=DraftAndClassifyResponse,
        dependencies=[Depends(_require_internal_token)],
    )
    def draft_and_classify(req: DraftAndClassifyRequest) -> DraftAndClassifyResponse:
        effective_key = (req.gemini_api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        try:
            resp = draft_and_classify_query_spec(
                req.question,
                active_catalog,
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
        base_spec = req.base_spec or QuerySpec(dialect=req.dialect)
        result = validate_and_reconcile_sql(
            req.sql,
            base_spec,
            allowed_relations=allowed_relation_names,
            server_tenant_id=req.tenant_id,
            tenant_scoped_relations=tenant_scoped_relations,
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
        base_spec = req.base_spec or QuerySpec(dialect=req.dialect)
        effective_key = (req.gemini_api_key or os.getenv("GEMINI_API_KEY", "")).strip()
        try:
            res = repair_sql_bounded(
                req.sql,
                req.error_message,
                base_spec=base_spec,
                allowed_relations=allowed_relation_names,
                adapter=active_adapter,
                server_tenant_id=req.tenant_id,
                tenant_scoped_relations=tenant_scoped_relations,
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

    return app


app = create_app()
