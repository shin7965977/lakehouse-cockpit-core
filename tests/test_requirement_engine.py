"""Unit tests for Ticket 03: Fail-Loud Requirement & QuerySpec Drafting Engine."""

from __future__ import annotations

import json

import pytest

from services.requirement_engine import RequirementDraftError, draft_and_classify_query_spec
from services.semantic_gap_detector import GapClassification
from tests.test_dbt_catalog_and_gap import _build_synthetic_catalog


def test_reuse_existing_builds_certified_spec_without_llm() -> None:
    cat = _build_synthetic_catalog()
    resp = draft_and_classify_query_spec("查詢各月營業收入", cat, api_key="")
    assert resp.gap.classification == GapClassification.REUSE_EXISTING
    assert resp.query_spec is not None
    assert resp.query_spec.output.certification == "CERTIFIED"
    assert resp.query_spec.semantic_match == "operating_revenue"
    assert resp.compiled_sql


def test_exploratory_drafting_fails_loud_without_api_key() -> None:
    cat = _build_synthetic_catalog()
    with pytest.raises(RequirementDraftError) as exc_info:
        draft_and_classify_query_spec("計算每月的 order_volume", cat, api_key="")
    assert exc_info.value.code == "API_KEY_MISSING"


def test_exploratory_drafting_with_injected_llm_builds_valid_spec() -> None:
    cat = _build_synthetic_catalog()

    def _fake_llm(_prompt: str) -> str:
        return json.dumps(
            {
                "relation": "fct_monthly_ledger",
                "projection": ["year_month"],
                "aggregations": [{"field": "order_volume", "func": "SUM", "alias": "total_orders"}],
                "group_by": ["year_month"],
                "filters": [],
                "assumptions": ["Summed across monthly ledger grain"],
            }
        )

    resp = draft_and_classify_query_spec(
        "計算每月的 order_volume", cat, llm_callable=_fake_llm
    )
    assert resp.gap.classification == GapClassification.NEW_SEMANTIC_METRIC
    assert resp.query_spec is not None
    assert 'SUM("order_volume") AS "total_orders"' in resp.compiled_sql


def test_exploratory_drafting_fails_loud_on_invalid_llm_json() -> None:
    cat = _build_synthetic_catalog()
    with pytest.raises(RequirementDraftError) as exc_info:
        draft_and_classify_query_spec(
            "計算每月的 order_volume", cat, llm_callable=lambda _p: "not valid json"
        )
    assert exc_info.value.code == "LLM_INVALID_JSON"


def test_exploratory_drafting_fails_loud_when_catalog_columns_missing() -> None:
    from services.dbt_catalog import DbtMetadataCatalog

    empty_cols_cat = DbtMetadataCatalog.from_payloads(
        manifest={
            "nodes": {
                "model.demo.mart_no_cols": {
                    "resource_type": "model",
                    "name": "mart_no_cols",
                    "schema": "gold",
                    "columns": {},
                }
            }
        }
    )
    with pytest.raises(RequirementDraftError) as exc_info:
        draft_and_classify_query_spec(
            "分析 mart_no_cols 銷售額",
            empty_cols_cat,
            llm_callable=lambda _p: '{"relation":"mart_no_cols","projection":["x"]}',
        )
    assert exc_info.value.code == "INCOMPLETE_CATALOG_METADATA"


def test_exploratory_drafting_fails_loud_on_hallucinated_column() -> None:
    cat = _build_synthetic_catalog()

    def _hallucinating_llm(_prompt: str) -> str:
        return json.dumps(
            {
                "relation": "fct_monthly_ledger",
                "projection": ["year_month"],
                "aggregations": [{"field": "sales_amount", "func": "SUM", "alias": "total_sales"}],
                "group_by": ["year_month"],
                "filters": [],
                "assumptions": [],
            }
        )

    with pytest.raises(RequirementDraftError) as exc_info:
        draft_and_classify_query_spec(
            "計算每月的 order_volume", cat, llm_callable=_hallucinating_llm
        )
    assert exc_info.value.code == "HALLUCINATED_COLUMN"
    assert "sales_amount" in exc_info.value.message

