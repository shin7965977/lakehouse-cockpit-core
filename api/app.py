"""Authenticated Internal FastAPI Control Plane for AI Analytics Workbench.

Endpoints:
- GET  /health
- POST /v1/catalog/search
- POST /v1/query-spec/draft-and-classify

Protected endpoints require a valid `X-Internal-Token` header matching
`AE_INTERNAL_TOKEN` (defaulting to a fail-closed check).
"""

from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

try:
    from ..services.dbt_catalog import CatalogSearchResult, DbtMetadataCatalog
    from ..services.query_spec import QueryDialect
    from ..services.requirement_engine import (
        DraftAndClassifyResponse,
        RequirementDraftError,
        draft_and_classify_query_spec,
    )
except (ImportError, ValueError):
    from services.dbt_catalog import CatalogSearchResult, DbtMetadataCatalog
    from services.query_spec import QueryDialect
    from services.requirement_engine import (
        DraftAndClassifyResponse,
        RequirementDraftError,
        draft_and_classify_query_spec,
    )


class CatalogSearchRequest(BaseModel):
    query: str = ""
    limit: int = Field(default=10, ge=1, le=50)


class DraftAndClassifyRequest(BaseModel):
    question: str
    dialect: QueryDialect = "duckdb"
    is_authorized: bool = True
    allowed_relations: list[str] | None = None
    gemini_api_key: str = ""


def create_app(
    catalog: DbtMetadataCatalog | None = None,
    internal_token: str | None = None,
) -> FastAPI:
    """Create a configured FastAPI control-plane application."""
    app = FastAPI(
        title="AI Analytics Workbench — AE Control Plane",
        version="0.1.0",
    )
    active_catalog = catalog or DbtMetadataCatalog.from_target_dir()

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
            return draft_and_classify_query_spec(
                req.question,
                active_catalog,
                dialect=req.dialect,
                api_key=effective_key,
                is_authorized=req.is_authorized,
                allowed_relations=(
                    set(req.allowed_relations) if req.allowed_relations is not None else None
                ),
            )
        except RequirementDraftError as exc:
            status_code = (
                status.HTTP_400_BAD_REQUEST
                if exc.code in ("EMPTY_QUESTION", "API_KEY_MISSING")
                else status.HTTP_422_UNPROCESSABLE_ENTITY
            )
            raise HTTPException(
                status_code=status_code,
                detail={"code": exc.code, "message": exc.message},
            ) from exc

    return app


app = create_app()
