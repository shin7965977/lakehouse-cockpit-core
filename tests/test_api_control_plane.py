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


def test_day3_assets_ingestion_promotions_and_draft_pr_endpoints(tmp_path) -> None:
    import pandas as pd
    from integrations.langfuse_adapter import ObservabilityTracker
    from services.query_spec import AggregationSpec, DataSourceRef, QuerySpec
    from services.warehouse_adapters import DuckDBAdapter

    df = pd.DataFrame(
        {
            "tenant_id": [1, 1],
            "year_month": ["2026-01", "2026-02"],
            "ledger_amount": [5000.0, 7500.0],
        }
    )
    gap_log = tmp_path / "gap_events.jsonl"
    tracker = ObservabilityTracker(gap_log_path=gap_log, langfuse_public_key="", langfuse_secret_key="")
    tracker.record_gap_event(
        question="Monthly average order value",
        classification="NEW_SEMANTIC_METRIC",
        candidate_relations=["fct_monthly_ledger"],
        tenant_id=1,
    )
    tracker.record_gap_event(
        question="Monthly average order value",
        classification="NEW_SEMANTIC_METRIC",
        candidate_relations=["fct_monthly_ledger"],
        tenant_id=1,
    )

    app = create_app(
        catalog=_build_synthetic_catalog(),
        internal_token=TOKEN,
        warehouse_adapter=DuckDBAdapter(dataframes={"fct_monthly_ledger": df}),
        tracker=tracker,
    )
    client = TestClient(app)
    headers = {"X-Internal-Token": TOKEN}

    spec = QuerySpec(
        spec_id="qs_d3_http",
        purpose="long_term",
        question="Monthly ledger summary",
        data_sources=[DataSourceRef(relation="fct_monthly_ledger", grain=["tenant_id", "year_month"])],
        projection=["tenant_id", "year_month"],
        aggregations=[AggregationSpec(field="ledger_amount", func="SUM", alias="total_ledger")],
        group_by=["tenant_id", "year_month"],
        dialect="duckdb",
    ).confirm_intent(confirmed_by="admin")

    # 1. Build & isolated validate
    build_resp = client.post(
        "/v1/assets/build-and-validate",
        json={
            "query_spec": spec.model_dump(mode="json"),
            "model_name": "mart_monthly_ledger_summary",
            "grain": ["tenant_id", "year_month"],
        },
        headers=headers,
    )
    assert build_resp.status_code == 200
    build_body = build_resp.json()
    assert build_body["validation"]["passed"] is True
    assert build_body["bundle"]["bi_asset_manifest"]["lightdash_status"] == "STAGING_LOCAL_ONLY"

    # 2. Source ingestion proposal (blocked external URL)
    bad_ing = client.post(
        "/v1/ingestion/propose",
        json={
            "source_type": "http_scrape",
            "source_locator": "https://untrusted.example/data.csv",
            "target_bronze_table": "stg_bad",
        },
        headers=headers,
    )
    assert bad_ing.status_code == 200
    assert bad_ing.json()["status"] == "BLOCKED"

    # 3. Promotion candidates from JSONL
    promo_resp = client.get("/v1/promotions/candidates", headers=headers)
    assert promo_resp.status_code == 200
    assert len(promo_resp.json()["candidates"]) == 1
    assert promo_resp.json()["candidates"][0]["certification_status"] == "PROPOSED_ASSET"

    # 4. Draft PR dry-run bundle
    pr_resp = client.post(
        "/v1/releases/draft-pr",
        json={
            "model_name": "mart_monthly_ledger_summary",
            "question": "Monthly ledger summary",
            "files": {"models/gold/mart_monthly_ledger_summary.sql": build_body["bundle"]["dbt_sql"]},
            "validation_summary": "All isolated checks passed",
        },
        headers=headers,
    )
    assert pr_resp.status_code == 200
    assert pr_resp.json()["pr_mode"] == "DRY_RUN_BUNDLE"


