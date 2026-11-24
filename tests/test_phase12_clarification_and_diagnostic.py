"""Vertical-slice TDD tests at Seam 1 (FastAPI Control Plane HTTP boundary) for Phase 1 & Phase 2."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from api.app import create_app


_SAMPLE_LIVE_CATALOG = [
    {
        "name": "sales_orders",
        "columns": [
            {"name": "id", "data_type": "integer"},
            {"name": "tenant_id", "data_type": "text"},
            {"name": "status", "data_type": "text"},
            {"name": "order_date", "data_type": "date"},
            {"name": "total_amount", "data_type": "numeric"},
            {"name": "customer_id", "data_type": "integer"},
        ],
    },
    {
        "name": "products",
        "columns": [
            {"name": "id", "data_type": "integer"},
            {"name": "tenant_id", "data_type": "text"},
            {"name": "name", "data_type": "text"},
            {"name": "category", "data_type": "text"},
        ],
    },
]

_SAMPLE_GLOSSARY = [
    {
        "business_term": "銷售訂單金額",
        "synonyms": ["收入", "營收", "銷售額", "訂單金額"],
        "tables": ["sales_orders"],
        "key_columns": ["total_amount", "order_date", "status"],
    },
    {
        "business_term": "產品",
        "synonyms": ["商品", "品項", "分類"],
        "tables": ["products"],
        "key_columns": ["name", "category"],
    },
]


def test_clarify_endpoint_rejects_missing_token():
    app = create_app(internal_token="secret-token")
    client = TestClient(app)

    resp = client.post("/v1/intent/clarify", json={"question": "最近為什麼收入不好？"})
    assert resp.status_code == 401


def test_clarify_fast_path_skips_chat_for_precise_table_query():
    llm_called = False

    def _should_not_be_called(*args: Any, **kwargs: Any) -> Any:
        nonlocal llm_called
        llm_called = True
        raise AssertionError("Fast-path must not invoke LLM")

    app = create_app(internal_token="secret-token", structured_llm_fn=_should_not_be_called)
    client = TestClient(app)

    resp = client.post(
        "/v1/intent/clarify",
        headers={"X-Internal-Token": "secret-token"},
        json={
            "question": "查詢 sales_orders 各狀態的訂單筆數",
            "live_catalog": _SAMPLE_LIVE_CATALOG,
            "glossary": _SAMPLE_GLOSSARY,
            "tenant_id": 1,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "FAST_PATH_SQL_READY"
    assert llm_called is False


def test_clarify_fails_loud_when_api_key_missing_on_ambiguous_question(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    app = create_app(internal_token="secret-token")
    client = TestClient(app)

    resp = client.post(
        "/v1/intent/clarify",
        headers={"X-Internal-Token": "secret-token"},
        json={
            "question": "最近為什麼收入不好？",
            "live_catalog": _SAMPLE_LIVE_CATALOG,
            "glossary": _SAMPLE_GLOSSARY,
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "API_KEY_MISSING"


def test_clarify_multi_turn_returns_options_and_caps_at_3_rounds():
    def _fake_structured_llm(prompt: str, response_model: Any) -> Any:
        # Simulate LLM returning CLARIFYING even on turn 3 to verify hard 3-round server cap
        payload = {
            "state": "CLARIFYING",
            "assistant_message": "「最近收入不好」有不同的衡量口徑，請選擇您想調查的指標。",
            "next_question": "您希望用哪個指標衡量收入？",
            "options": [
                {"label": "銷售訂單金額", "value": "銷售訂單金額 (sales_orders.total_amount)"},
                {"label": "會計營業收入", "value": "會計營業收入 (operating_revenue)"},
            ],
            "business_intent": {
                "original_question": "最近為什麼收入不好？",
                "intent_type": "DIAGNOSTIC_ANALYSIS",
                "metric": "銷售訂單金額",
                "metric_basis": "sales_orders.total_amount",
                "timeframe": "最近三個月",
                "comparison_period": "前三個月",
                "grain": "month",
                "breakdown_dimensions": ["status", "category"],
                "filters": [],
                "target_relations": ["sales_orders"],
                "assumptions": ["預設比較最近三個月與前三個月"],
            },
        }
        return response_model.model_validate(payload)

    app = create_app(internal_token="secret-token", structured_llm_fn=_fake_structured_llm)
    client = TestClient(app)

    # Turn 1 -> CLARIFYING with clickable options
    r1 = client.post(
        "/v1/intent/clarify",
        headers={"X-Internal-Token": "secret-token"},
        json={
            "question": "最近為什麼收入不好？",
            "history": [],
            "live_catalog": _SAMPLE_LIVE_CATALOG,
            "glossary": _SAMPLE_GLOSSARY,
            "gemini_api_key": "test-key",
        },
    )
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1["state"] == "CLARIFYING"
    assert b1["turn_number"] == 1
    assert len(b1["options"]) == 2
    assert b1["business_intent"]["intent_type"] == "DIAGNOSTIC_ANALYSIS"

    # Turn 3 -> Hard cap forces transition to READY_FOR_REVIEW with explicit assumptions
    r3 = client.post(
        "/v1/intent/clarify",
        headers={"X-Internal-Token": "secret-token"},
        json={
            "question": "最近為什麼收入不好？",
            "history": [
                {"role": "assistant", "content": "請問指標？"},
                {"role": "user", "content": "銷售訂單金額"},
                {"role": "assistant", "content": "請問期間？"},
                {"role": "user", "content": "最近三個月"},
            ],
            "live_catalog": _SAMPLE_LIVE_CATALOG,
            "glossary": _SAMPLE_GLOSSARY,
            "gemini_api_key": "test-key",
        },
    )
    assert r3.status_code == 200
    b3 = r3.json()
    assert b3["turn_number"] == 3
    assert b3["state"] == "READY_FOR_REVIEW"
    assert len(b3["business_intent"]["assumptions"]) >= 1


def test_clarify_blocks_unmappable_question_at_cap():
    def _fake_unmappable_llm(prompt: str, response_model: Any) -> Any:
        payload = {
            "state": "CLARIFYING",
            "assistant_message": "找不到對應的資料表。",
            "next_question": "請問資料來源？",
            "options": [],
            "business_intent": {
                "original_question": "火星基地庫存多少？",
                "intent_type": "SINGLE_QUERY",
                "metric": "火星庫存",
                "target_relations": ["mars_inventory"],
                "assumptions": [],
            },
        }
        return response_model.model_validate(payload)

    app = create_app(internal_token="secret-token", structured_llm_fn=_fake_unmappable_llm)
    client = TestClient(app)

    r = client.post(
        "/v1/intent/clarify",
        headers={"X-Internal-Token": "secret-token"},
        json={
            "question": "火星基地庫存多少？",
            "history": [
                {"role": "assistant", "content": "q1"},
                {"role": "user", "content": "a1"},
                {"role": "assistant", "content": "q2"},
                {"role": "user", "content": "a2"},
            ],
            "live_catalog": _SAMPLE_LIVE_CATALOG,
            "gemini_api_key": "test-key",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "BLOCKED_UNMAPPABLE"
    assert body["blocking_reason"]


def test_diagnostic_plan_generates_bounded_steps_with_spec_hash_and_blocks_hallucinated_columns():
    def _fake_plan_llm(prompt: str, response_model: Any) -> Any:
        payload = {
            "plan_title": "銷售訂單金額下滑診斷計畫",
            "steps": [
                {
                    "step_id": "step_1",
                    "title": "訂單狀態與總金額總覽",
                    "rationale": "確認各訂單狀態的筆數與金額分布",
                    "table": "sales_orders",
                    "dimensions": ["status"],
                    "aggregations": [
                        {"func": "COUNT", "column": "id", "alias": "order_count"},
                        {"func": "SUM", "column": "total_amount", "alias": "total_sales"},
                    ],
                    "filters": [],
                    "limit": 100,
                },
                {
                    "step_id": "step_2",
                    "title": "客單價與客戶維度檢視",
                    "rationale": "拆解各客戶的訂單筆數與平均金額",
                    "table": "sales_orders",
                    "dimensions": ["customer_id"],
                    "aggregations": [
                        {"func": "COUNT", "column": "id", "alias": "order_count"},
                        {"func": "AVG", "column": "total_amount", "alias": "avg_order_value"},
                    ],
                    "filters": [],
                    "limit": 50,
                },
            ],
        }
        return response_model.model_validate(payload)

    app = create_app(internal_token="secret-token", structured_llm_fn=_fake_plan_llm)
    client = TestClient(app)

    resp = client.post(
        "/v1/diagnostic/plan",
        headers={"X-Internal-Token": "secret-token"},
        json={
            "business_intent": {
                "original_question": "最近為什麼收入不好？",
                "intent_type": "DIAGNOSTIC_ANALYSIS",
                "metric": "銷售訂單金額",
                "metric_basis": "sales_orders.total_amount",
                "timeframe": "最近三個月",
                "comparison_period": "前三個月",
                "grain": "status",
                "breakdown_dimensions": ["status", "customer_id"],
                "target_relations": ["sales_orders"],
            },
            "dialect": "postgres",
            "live_catalog": _SAMPLE_LIVE_CATALOG,
            "gemini_api_key": "test-key",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert 2 <= len(body["steps"]) <= 4
    assert body["plan_hash"]
    for step in body["steps"]:
        assert step["compiled_sql"].upper().startswith("SELECT")
        assert step["spec_hash"]
        assert step["query_spec"]["review"]["spec_hash"] == step["spec_hash"]

    # Negative Fail-Loud: hallucinated column must be rejected (422)
    def _fake_hallucinated_plan_llm(prompt: str, response_model: Any) -> Any:
        payload = {
            "plan_title": "壞計畫",
            "steps": [
                {
                    "step_id": "step_1",
                    "title": "不存在欄位查詢",
                    "rationale": "測試防幻覺",
                    "table": "sales_orders",
                    "dimensions": ["status"],
                    "aggregations": [
                        {"func": "SUM", "column": "non_existent_revenue_col", "alias": "bad_sum"}
                    ],
                    "filters": [],
                    "limit": 50,
                }
            ],
        }
        return response_model.model_validate(payload)

    bad_app = create_app(internal_token="secret-token", structured_llm_fn=_fake_hallucinated_plan_llm)
    bad_client = TestClient(bad_app)
    bad_resp = bad_client.post(
        "/v1/diagnostic/plan",
        headers={"X-Internal-Token": "secret-token"},
        json={
            "business_intent": {
                "original_question": "最近為什麼收入不好？",
                "intent_type": "DIAGNOSTIC_ANALYSIS",
                "target_relations": ["sales_orders"],
            },
            "dialect": "postgres",
            "live_catalog": _SAMPLE_LIVE_CATALOG,
            "gemini_api_key": "test-key",
        },
    )
    assert bad_resp.status_code == 422
    assert bad_resp.json()["detail"]["code"] == "HALLUCINATED_COLUMN"


def test_diagnostic_synthesize_strips_pii_and_records_failed_step_gaps():
    captured_prompts: list[str] = []

    def _fake_synth_llm(prompt: str, response_model: Any) -> Any:
        captured_prompts.append(prompt)
        payload = {
            "executive_summary": "依據步驟 step_1 真實結果，Completed 狀態共 4681 筆訂單，為主要來源；步驟 step_2 查詢失敗未納入推論。",
            "key_findings": [
                {
                    "step_id": "step_1",
                    "finding": "Completed 訂單為 4681 筆，Draft 僅 3 筆。",
                    "cited_metrics": ["order_count=4681"],
                }
            ],
            "data_gaps": ["step_2 執行失敗（0 rows），未針對此維度做結論推論。"],
            "recommended_followups": ["進一步依月份拆解 Completed 訂單金額趨勢"],
        }
        return response_model.model_validate(payload)

    app = create_app(internal_token="secret-token", structured_llm_fn=_fake_synth_llm)
    client = TestClient(app)

    resp = client.post(
        "/v1/diagnostic/synthesize",
        headers={"X-Internal-Token": "secret-token"},
        json={
            "business_intent": {
                "original_question": "最近為什麼收入不好？",
                "intent_type": "DIAGNOSTIC_ANALYSIS",
                "metric": "銷售訂單金額",
                "target_relations": ["sales_orders"],
            },
            "step_results": [
                {
                    "step_id": "step_1",
                    "title": "訂單狀態分布",
                    "spec_hash": "abc123456789",
                    "status": "SUCCEEDED",
                    "row_count": 2,
                    "columns": ["status", "order_count", "customer_email"],
                    "rows": [
                        {"status": "Completed", "order_count": 4681, "customer_email": "secret@corp.com"},
                        {"status": "Draft", "order_count": 3, "customer_email": "boss@corp.com"},
                    ],
                },
                {
                    "step_id": "step_2",
                    "title": "產品分類下鑽",
                    "spec_hash": "def987654321",
                    "status": "FAILED",
                    "error": "Query returned 0 rows",
                    "row_count": 0,
                    "columns": [],
                    "rows": [],
                },
            ],
            "gemini_api_key": "test-key",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "4681" in body["executive_summary"]
    assert len(body["key_findings"]) == 1
    assert len(body["data_gaps"]) >= 1
    # Defensive PII stripping check: secret@corp.com must NEVER reach the LLM prompt
    assert captured_prompts
    assert "secret@corp.com" not in captured_prompts[0]
    assert "boss@corp.com" not in captured_prompts[0]


def test_clarify_forwards_tenant_value_samples_as_formatting_hints_without_blocking():
    captured_prompts: list[str] = []

    def _capture_llm(prompt: str, response_model: Any) -> Any:
        captured_prompts.append(prompt)
        return response_model.model_validate(
            {
                "state": "READY_FOR_REVIEW",
                "assistant_message": "已對齊狀態格式為 Completed，請確認以下規格。",
                "business_intent": {
                    "original_question": "查詢已完成訂單數量",
                    "intent_type": "SINGLE_QUERY",
                    "metric": "訂單筆數",
                    "filters": ["status = 'Completed'"],
                    "target_relations": ["sales_orders"],
                },
            }
        )

    app = create_app(internal_token="secret-token", structured_llm_fn=_capture_llm)
    client = TestClient(app)

    resp = client.post(
        "/v1/intent/clarify",
        headers={"X-Internal-Token": "secret-token"},
        json={
            "question": "查詢已完成訂單數量",
            "live_catalog": _SAMPLE_LIVE_CATALOG,
            "tenant_value_samples": {
                "warehouses.name": ["台北總倉", "桃園倉"],
                "sales_orders.status": ["Completed", "Confirmed", "Draft"],
            },
            "gemini_api_key": "test-key",
            "tenant_id": 1,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["state"] == "READY_FOR_REVIEW"
    assert captured_prompts
    assert "TenantSampleDimensionValues" in captured_prompts[0]
    assert "NEVER reject a question solely because a specific filter value is missing" in captured_prompts[0]
    assert "Completed" in captured_prompts[0]
