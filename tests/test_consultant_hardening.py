"""Seam 1 TDD tests for Consultant Hardening (Tickets 01, 02, 03)."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from api.app import create_app
from services.dbt_catalog import DbtMetadataCatalog
from services.requirement_engine import RequirementDraftError, draft_and_classify_query_spec
from services.semantic_gap_detector import GapClassification, classify_semantic_gap


def _build_multi_table_live_catalog() -> DbtMetadataCatalog:
    tables = [
        {
            "name": "sales_orders",
            "columns": [
                {"name": "id", "data_type": "integer"},
                {"name": "tenant_id", "data_type": "integer"},
                {"name": "customer_id", "data_type": "integer"},
                {"name": "salesperson_id", "data_type": "integer"},
                {"name": "total_amount", "data_type": "numeric"},
                {"name": "status", "data_type": "text"},
            ],
        },
        {
            "name": "customers",
            "columns": [
                {"name": "id", "data_type": "integer"},
                {"name": "tenant_id", "data_type": "integer"},
                {"name": "name", "data_type": "text"},
                {"name": "category", "data_type": "text"},
            ],
        },
        {
            "name": "stock_entries",
            "columns": [
                {"name": "id", "data_type": "integer"},
                {"name": "tenant_id", "data_type": "integer"},
                {"name": "warehouse_id", "data_type": "integer"},
                {"name": "quantity", "data_type": "numeric"},
            ],
        },
    ]
    # Add 7 more unrelated tables that all share `id` and `tenant_id`
    for name in (
        "accounting_settings",
        "admin_users",
        "approvals",
        "assets",
        "departments",
        "projects",
        "workstations",
    ):
        tables.append(
            {
                "name": name,
                "columns": [
                    {"name": "id", "data_type": "integer"},
                    {"name": "tenant_id", "data_type": "integer"},
                    {"name": "code", "data_type": "text"},
                ],
            }
        )
    return DbtMetadataCatalog.from_live_schema(tables)


def test_ticket01_gap_detector_does_not_match_all_tables_via_id_substring():
    cat = _build_multi_table_live_catalog()
    res = classify_semantic_gap(
        "分析 sales_orders 依 salesperson_id 統計的總金額",
        cat,
    )
    assert res.classification == GapClassification.NEW_SEMANTIC_METRIC
    assert res.candidate_relations == ["sales_orders"]


def test_ticket02_requirement_engine_supports_native_join_and_rejects_bad_joined_column():
    cat = _build_multi_table_live_catalog()

    def _valid_join_llm(_prompt: str) -> str:
        return json.dumps(
            {
                "relation": "sales_orders",
                "joins": [
                    {
                        "left_relation": "sales_orders",
                        "right_relation": "customers",
                        "on": '"sales_orders"."customer_id" = "customers"."id"',
                        "join_type": "LEFT",
                    }
                ],
                "projection": ["customers.category"],
                "aggregations": [
                    {"field": "sales_orders.total_amount", "func": "SUM", "alias": "total_sales"}
                ],
                "group_by": ["customers.category"],
                "filters": [{"field": "sales_orders.status", "op": "=", "value": "Completed"}],
                "assumptions": ["以 Completed 訂單關聯 customers 計算各客戶分類總營收"],
            }
        )

    resp = draft_and_classify_query_spec(
        "查詢 sales_orders 關聯 customers 各 category 的 total_amount",
        cat,
        dialect="postgres",
        llm_callable=_valid_join_llm,
    )
    assert resp.query_spec is not None
    assert len(resp.query_spec.joins) == 1
    assert "LEFT JOIN \"customers\"" in resp.compiled_sql
    assert "\"customers\".\"category\"" in resp.compiled_sql

    # Negative Fail-Loud: hallucinated column on joined table must be rejected
    def _bad_join_llm(_prompt: str) -> str:
        return json.dumps(
            {
                "relation": "sales_orders",
                "joins": [
                    {
                        "left_relation": "sales_orders",
                        "right_relation": "customers",
                        "on": '"sales_orders"."customer_id" = "customers"."id"',
                        "join_type": "LEFT",
                    }
                ],
                "projection": ["customers.fake_tier_col"],
                "aggregations": [
                    {"field": "sales_orders.total_amount", "func": "SUM", "alias": "total_sales"}
                ],
                "group_by": ["customers.fake_tier_col"],
                "filters": [],
                "assumptions": [],
            }
        )

    try:
        draft_and_classify_query_spec(
            "查詢 sales_orders 關聯 customers 各等級的 total_amount",
            cat,
            dialect="postgres",
            llm_callable=_bad_join_llm,
        )
        raise AssertionError("Expected HALLUCINATED_COLUMN for fake joined column")
    except RequirementDraftError as exc:
        assert exc.code == "HALLUCINATED_COLUMN"


def test_ticket03_consultant_prompt_puts_latest_message_last_and_prioritizes_latest_domain():
    captured_propose: list[str] = []
    captured_answer: list[str] = []

    def _propose_llm(prompt: str, response_model: Any) -> Any:
        captured_propose.append(prompt)
        return response_model.model_validate(
            {"needs_data": False, "direct_reply": "ok", "proposed_queries": []}
        )

    live_tables = [
        {
            "name": "stock_entries",
            "columns": [
                {"name": "id", "data_type": "integer"},
                {"name": "warehouse_id", "data_type": "integer"},
                {"name": "quantity", "data_type": "numeric"},
            ],
        },
        {
            "name": "sales_orders",
            "columns": [
                {"name": "id", "data_type": "integer"},
                {"name": "total_amount", "data_type": "numeric"},
                {"name": "status", "data_type": "text"},
            ],
        },
    ]
    glossary = [
        {"business_term": "庫存", "synonyms": ["倉儲", "庫存異動"], "tables": ["stock_entries"]},
        {"business_term": "銷售訂單金額", "synonyms": ["銷售", "營收"], "tables": ["sales_orders"]},
    ]

    client = TestClient(create_app(internal_token="tok", structured_llm_fn=_propose_llm))
    r = client.post(
        "/v1/consultant/propose",
        headers={"X-Internal-Token": "tok"},
        json={
            "message": "1. 銷售與營收分析：檢視各產品類別與銷售佔比",
            "history": [
                {"role": "user", "content": "4. 庫存週轉狀況與庫存異動"},
                {"role": "assistant", "content": "目前庫存異動查無資料，建議查詢庫存水位。"},
            ],
            "live_catalog": live_tables,
            "glossary": glossary,
            "gemini_api_key": "k",
        },
    )
    assert r.status_code == 200
    p = captured_propose[-1]
    assert p.index("RecentConversationHistory") < p.index("LatestUserMessage (RESPOND TO THIS MESSAGE):")
    # LatestUserMessage ("銷售與營收") must rank sales_orders ahead of previous-turn stock_entries
    allowed_part = p.split("AllowedCatalog (ranked by LatestUserMessage relevance):")[1]
    assert allowed_part.index('"sales_orders"') < allowed_part.index('"stock_entries"')

    def _answer_llm(prompt: str, response_model: Any) -> Any:
        captured_answer.append(prompt)
        return response_model.model_validate({"reply": "結論與假說..."})

    ans_client = TestClient(create_app(internal_token="tok", structured_llm_fn=_answer_llm))
    r_ans = ans_client.post(
        "/v1/consultant/answer",
        headers={"X-Internal-Token": "tok"},
        json={
            "message": "幫我看銷售營收",
            "history": [],
            "query_outcomes": [
                {
                    "title": "銷售額",
                    "status": "SUCCEEDED",
                    "row_count": 1,
                    "columns": ["total_sales"],
                    "rows": [{"total_sales": 110000}],
                }
            ],
            "gemini_api_key": "k",
        },
    )
    assert r_ans.status_code == 200
    assert "hypotheses" in captured_answer[-1].lower() or "so what" in captured_answer[-1].lower()
