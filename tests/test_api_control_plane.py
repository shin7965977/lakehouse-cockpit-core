"""End-to-end FastAPI Control Plane tests for Ticket 04."""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.app import create_app
from tests.test_dbt_catalog_and_gap import _build_synthetic_catalog

TOKEN = "test-internal-secret-token"


def _client() -> TestClient:
    app = create_app(catalog=_build_synthetic_catalog(), internal_token=TOKEN)
    return TestClient(app)


def test_health_endpoint_reports_catalog_summary() -> None:
    client = _client()
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["catalog"]["relation_count"] == 3
    assert body["catalog"]["metric_count"] == 2


def test_protected_endpoints_reject_missing_or_wrong_internal_token() -> None:
    client = _client()
    assert client.post("/v1/catalog/search", json={"query": "ledger"}).status_code == 401
    assert (
        client.post(
            "/v1/catalog/search",
            json={"query": "ledger"},
            headers={"X-Internal-Token": "wrong"},
        ).status_code
        == 401
    )


def test_catalog_search_and_gap_classification_over_http() -> None:
    client = _client()
    headers = {"X-Internal-Token": TOKEN}

    search_resp = client.post(
        "/v1/catalog/search",
        json={"query": "operating_revenue"},
        headers=headers,
    )
    assert search_resp.status_code == 200
    assert any(m["name"] == "operating_revenue" for m in search_resp.json()["metrics"])

    # REUSE_EXISTING certified hit
    reuse_resp = client.post(
        "/v1/query-spec/draft-and-classify",
        json={"question": "請提供各月營業收入"},
        headers=headers,
    )
    assert reuse_resp.status_code == 200
    reuse_body = reuse_resp.json()
    assert reuse_body["gap"]["classification"] == "REUSE_EXISTING"
    assert reuse_body["query_spec"]["output"]["certification"] == "CERTIFIED"

    # SEMANTIC_AMBIGUITY
    ambig_resp = client.post(
        "/v1/query-spec/draft-and-classify",
        json={"question": "本月營收多少"},
        headers=headers,
    )
    assert ambig_resp.status_code == 200
    assert ambig_resp.json()["gap"]["classification"] == "SEMANTIC_AMBIGUITY"

    # Exploratory gap without Gemini API key -> Fail-Loud 400
    fail_loud_resp = client.post(
        "/v1/query-spec/draft-and-classify",
        json={"question": "計算每月的 order_volume", "gemini_api_key": ""},
        headers=headers,
    )
    assert fail_loud_resp.status_code == 400
    assert fail_loud_resp.json()["detail"]["code"] == "API_KEY_MISSING"


def test_day2_validate_repair_and_confirmed_execute_endpoints() -> None:
    import pandas as pd
    from services.warehouse_adapters import DuckDBAdapter

    df = pd.DataFrame(
        {
            "tenant_id": [1, 1, 2],
            "year_month": ["2026-01", "2026-02", "2026-01"],
            "ledger_amount": [5000.0, 7500.0, 99999.0],
            "order_volume": [10, 15, 99],
        }
    )
    app = create_app(
        catalog=_build_synthetic_catalog(),
        internal_token=TOKEN,
        warehouse_adapter=DuckDBAdapter(dataframes={"fct_monthly_ledger": df}),
    )
    client = TestClient(app)
    headers = {"X-Internal-Token": TOKEN}

    # 1. Cross-tenant SQL blocked at /v1/sql/validate-and-reconcile
    bad_val = client.post(
        "/v1/sql/validate-and-reconcile",
        json={
            "sql": "SELECT year_month, ledger_amount FROM fct_monthly_ledger WHERE tenant_id = 2",
            "tenant_id": 1,
        },
        headers=headers,
    )
    assert bad_val.status_code == 200
    assert bad_val.json()["status"] == "BLOCKED"
    assert "CROSS_TENANT_VIOLATION" in bad_val.json()["reason_codes"]

    # 2. Valid SQL reconciled and auto-scoped to tenant_id = 1
    good_val = client.post(
        "/v1/sql/validate-and-reconcile",
        json={
            "sql": "SELECT year_month, SUM(ledger_amount) AS rev FROM fct_monthly_ledger GROUP BY year_month LIMIT 10",
            "tenant_id": 1,
        },
        headers=headers,
    )
    assert good_val.status_code == 200
    good_body = good_val.json()
    assert good_body["status"] == "VALID"
    reconciled_spec = good_body["reconciled_spec"]

    # 3. Execute without confirmed spec_hash -> rejected 400
    unconfirmed_exec = client.post(
        "/v1/query-spec/execute",
        json={"query_spec": reconciled_spec, "tenant_id": 1},
        headers=headers,
    )
    assert unconfirmed_exec.status_code == 400

    # 4. Confirm spec_hash and execute -> returns ONLY tenant 1 rows (2 rows, never 99999.0)
    reconciled_spec["review"]["intent_confirmed"] = True
    ok_exec = client.post(
        "/v1/query-spec/execute",
        json={"query_spec": reconciled_spec, "tenant_id": 1},
        headers=headers,
    )
    assert ok_exec.status_code == 200
    exec_body = ok_exec.json()
    assert exec_body["row_count"] == 2
    assert all(r["rev"] != 99999.0 for r in exec_body["rows"])

    # 5. Repair without Gemini API key -> Fail-Loud 400
    repair_resp = client.post(
        "/v1/sql/repair",
        json={
            "sql": "SELECT bad_col FROM fct_monthly_ledger",
            "error_message": "Binder Error: column bad_col not found",
            "tenant_id": 1,
            "gemini_api_key": "",
        },
        headers=headers,
    )
    assert repair_resp.status_code == 400
    assert repair_resp.json()["detail"]["code"] == "API_KEY_MISSING"

