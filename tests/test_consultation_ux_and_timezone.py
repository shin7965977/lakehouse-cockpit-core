"""Seam 1 (FastAPI HTTP boundary) TDD tests for Consultation-First UX & Auto Timezone Grounding."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from api.app import create_app


_CATALOG = [
    {
        "name": "sales_orders",
        "columns": [
            {"name": "id", "data_type": "integer"},
            {"name": "tenant_id", "data_type": "integer"},
            {"name": "status", "data_type": "text"},
            {"name": "order_date", "data_type": "date"},
            {"name": "total_amount", "data_type": "numeric"},
            {"name": "customer_email", "data_type": "text"},
        ],
    }
]


def test_ticket01_timezone_header_propagates_valid_iana_and_falls_back_to_utc():
    prompts: list[str] = []

    def _capture_llm(prompt: str, response_model: Any) -> Any:
        prompts.append(prompt)
        return response_model.model_validate(
            {
                "needs_data": False,
                "direct_reply": "我們可以先比較近 30 天與前 30 天的銷售訂單金額。",
                "proposed_queries": [],
            }
        )

    app = create_app(internal_token="tok", structured_llm_fn=_capture_llm)
    client = TestClient(app)

    r_ok = client.post(
        "/v1/consultant/propose",
        headers={"X-Internal-Token": "tok", "X-User-Timezone": "Europe/Berlin"},
        json={
            "message": "最近的業績如何？",
            "history": [],
            "live_catalog": _CATALOG,
            "gemini_api_key": "k",
        },
    )
    assert r_ok.status_code == 200
    assert "CurrentDate (Europe/Berlin):" in prompts[-1]

    r_bad = client.post(
        "/v1/consultant/propose",
        headers={"X-Internal-Token": "tok", "X-User-Timezone": "Mars/Fake; DROP TABLE x"},
        json={
            "message": "最近的業績如何？",
            "history": [],
            "live_catalog": _CATALOG,
            "gemini_api_key": "k",
        },
    )
    assert r_bad.status_code == 200
    assert "CurrentDate (UTC):" in prompts[-1]


def test_ticket02_consultant_propose_sliding_window_column_whitelist_and_answer_pii_strip():
    prompts: list[str] = []

    def _data_propose_llm(prompt: str, response_model: Any) -> Any:
        prompts.append(prompt)
        return response_model.model_validate(
            {
                "needs_data": True,
                "direct_reply": "",
                "proposed_queries": [
                    {
                        "title": "近 30 天銷售訂單各狀態統計",
                        "table": "sales_orders",
                        "dimensions": ["status"],
                        "aggregations": [
                            {"func": "COUNT", "column": "id", "alias": "order_count"},
                            {"func": "SUM", "column": "total_amount", "alias": "total_sales"},
                        ],
                        "filters": [{"column": "order_date", "op": "LAST_N_DAYS", "value": 30}],
                        "limit": 50,
                    }
                ],
            }
        )

    app = create_app(internal_token="tok", structured_llm_fn=_data_propose_llm)
    client = TestClient(app)

    # Rejects missing token
    assert client.post("/v1/consultant/propose", json={"message": "hi"}).status_code == 401

    # 20 turns passed -> sliding window trims oldest turns (only latest 12 turns sent to LLM)
    long_history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn_{i}"} for i in range(20)]
    r = client.post(
        "/v1/consultant/propose",
        headers={"X-Internal-Token": "tok"},
        json={
            "message": "幫我查最近 30 天各狀態訂單",
            "history": long_history,
            "live_catalog": _CATALOG,
            "gemini_api_key": "k",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["needs_data"] is True
    assert len(body["proposed_queries"]) == 1
    assert "CURRENT_DATE - INTERVAL '30 days'" in body["proposed_queries"][0]["compiled_sql"]
    assert "turn_0" not in prompts[-1]
    assert "turn_19" in prompts[-1]

    # Hallucinated column fails loud (422)
    def _bad_col_llm(prompt: str, response_model: Any) -> Any:
        return response_model.model_validate(
            {
                "needs_data": True,
                "direct_reply": "",
                "proposed_queries": [
                    {
                        "title": "壞欄位",
                        "table": "sales_orders",
                        "dimensions": ["status"],
                        "aggregations": [{"func": "SUM", "column": "fake_profit_col", "alias": "p"}],
                        "filters": [],
                        "limit": 50,
                    }
                ],
            }
        )

    bad_client = TestClient(create_app(internal_token="tok", structured_llm_fn=_bad_col_llm))
    r_bad = bad_client.post(
        "/v1/consultant/propose",
        headers={"X-Internal-Token": "tok"},
        json={"message": "毛利多少", "live_catalog": _CATALOG, "gemini_api_key": "k"},
    )
    assert r_bad.status_code == 422
    assert r_bad.json()["detail"]["code"] == "HALLUCINATED_COLUMN"

    # Answer endpoint strips PII defensively
    ans_prompts: list[str] = []

    def _ans_llm(prompt: str, response_model: Any) -> Any:
        ans_prompts.append(prompt)
        return response_model.model_validate(
            {"reply": "近 30 天 Completed 訂單共有 4681 筆。"}
        )

    ans_client = TestClient(create_app(internal_token="tok", structured_llm_fn=_ans_llm))
    r_ans = ans_client.post(
        "/v1/consultant/answer",
        headers={"X-Internal-Token": "tok"},
        json={
            "message": "幫我查最近 30 天各狀態訂單",
            "history": [],
            "query_outcomes": [
                {
                    "title": "近 30 天銷售訂單各狀態統計",
                    "sql": "SELECT status, COUNT(id) FROM sales_orders GROUP BY status",
                    "spec_hash": "hash123",
                    "status": "SUCCEEDED",
                    "row_count": 1,
                    "columns": ["status", "order_count", "customer_email"],
                    "rows": [{"status": "Completed", "order_count": 4681, "customer_email": "ceo@corp.com"}],
                }
            ],
            "gemini_api_key": "k",
        },
    )
    assert r_ans.status_code == 200
    assert "4681" in r_ans.json()["reply"]
    assert "ceo@corp.com" not in ans_prompts[-1]


def test_ticket03_consultant_summarize_intent_returns_editable_intent_and_gap():
    def _sum_llm(prompt: str, response_model: Any) -> Any:
        return response_model.model_validate(
            {
                "summary_question": "分析近 30 天 sales_orders 各狀態的銷售訂單總金額與筆數",
                "business_intent": {
                    "original_question": "分析近 30 天 sales_orders 各狀態的銷售訂單總金額與筆數",
                    "intent_type": "DIAGNOSTIC_ANALYSIS",
                    "metric": "銷售訂單金額",
                    "metric_basis": "sales_orders.total_amount",
                    "timeframe": "最近 30 天",
                    "comparison_period": "前 30 天",
                    "grain": "status",
                    "breakdown_dimensions": ["status"],
                    "filters": [],
                    "target_relations": ["sales_orders", "fake_hallucinated_table"],
                    "assumptions": ["以 sales_orders.total_amount 計算"],
                },
            }
        )

    app = create_app(internal_token="tok", structured_llm_fn=_sum_llm)
    client = TestClient(app)

    r = client.post(
        "/v1/consultant/summarize-intent",
        headers={"X-Internal-Token": "tok"},
        json={
            "history": [
                {"role": "user", "content": "最近的業績如何？"},
                {"role": "assistant", "content": "我們可以先看近 30 天銷售訂單金額。"},
                {"role": "user", "content": "好，幫我按訂單狀態比較近 30 天跟前 30 天"},
            ],
            "live_catalog": _CATALOG,
            "gemini_api_key": "k",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["summary_question"]
    assert body["business_intent"]["target_relations"] == ["sales_orders"]
    assert body["gap"]["classification"]
