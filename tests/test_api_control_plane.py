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
