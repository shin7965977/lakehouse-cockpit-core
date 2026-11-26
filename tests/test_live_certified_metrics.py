"""Seam 1 TDD tests: Online Certified Semantic Layer (`live_metrics`) on Control Plane HTTP API."""

from __future__ import annotations

from fastapi.testclient import TestClient

from api.app import create_app


LIVE_CATALOG = [
    {
        "name": "sales_orders",
        "schema": "public",
        "columns": [
            {"name": "id", "data_type": "integer", "description": "Order ID"},
            {"name": "tenant_id", "data_type": "integer", "description": "Tenant ID"},
            {"name": "customer_id", "data_type": "integer", "description": "Customer ID"},
            {"name": "order_date", "data_type": "text", "description": "Order date"},
            {"name": "status", "data_type": "text", "description": "Order status"},
            {"name": "total_amount", "data_type": "numeric", "description": "Total sales amount"},
        ],
    },
    {
        "name": "customers",
        "schema": "public",
        "columns": [
            {"name": "id", "data_type": "integer", "description": "Customer ID"},
            {"name": "tenant_id", "data_type": "integer", "description": "Tenant ID"},
            {"name": "name", "data_type": "text", "description": "Customer name"},
            {"name": "is_active", "data_type": "boolean", "description": "Active status"},
        ],
    },
]

LIVE_METRICS = [
    {
        "name": "sales_revenue",
        "label": "Sales Revenue (銷售訂單業績 / 銷售額 / 銷售業績)",
        "description": "Total sales order amount from sales_orders.total_amount.",
        "metric_type": "simple",
        "semantic_model": "erp_sales_orders",
        "source_relation": "sales_orders",
        "time_granularity": "day",
        "measure_expr": "total_amount",
        "measure_agg": "SUM",
        "dimensions": ["order_date", "status", "customer_id"],
        "entities": ["sales_order", "tenant", "customer"],
    },
    {
        "name": "sales_order_count",
        "label": "Sales Order Count (銷售訂單數 / 訂單筆數)",
        "description": "Count of sales orders in sales_orders.",
        "metric_type": "simple",
        "semantic_model": "erp_sales_orders",
        "source_relation": "sales_orders",
        "time_granularity": "day",
        "dimensions": ["order_date", "status"],
        "entities": ["sales_order", "tenant"],
    },
    {
        "name": "active_customer_count",
        "label": "Active Customer Count (活躍客戶數 / 客戶數)",
        "description": "Count of active customers where customers.is_active is true.",
        "metric_type": "simple",
        "semantic_model": "erp_customers",
        "source_relation": "customers",
        "dimensions": ["is_active"],
        "entities": ["customer", "tenant"],
    },
]

AUTH_HEADERS = {"X-Internal-Token": "test-secret"}


def _client() -> TestClient:
    app = create_app(internal_token="test-secret")
    return TestClient(app)


def test_catalog_search_returns_live_certified_metrics() -> None:
    client = _client()
    resp = client.post(
        "/v1/catalog/search",
        headers=AUTH_HEADERS,
        json={
            "query": "sales",
            "limit": 10,
            "live_catalog": LIVE_CATALOG,
            "live_metrics": LIVE_METRICS,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    metric_names = {m["name"] for m in body["metrics"]}
    assert "sales_revenue" in metric_names
    assert "sales_order_count" in metric_names


def test_gap_detect_reuses_certified_sales_revenue_metric_zh_and_en() -> None:
    client = _client()

    for question in [
        "最近 30 天銷售訂單業績表現如何？",
        "How was our sales revenue over the last 30 days?",
    ]:
        resp = client.post(
            "/v1/semantic-gap/detect",
            headers=AUTH_HEADERS,
            json={
                "question": question,
                "live_catalog": LIVE_CATALOG,
                "live_metrics": LIVE_METRICS,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["classification"] == "REUSE_EXISTING", f"Failed for: {question} -> {body}"
        assert body["matched_metric"]["name"] == "sales_revenue"


def test_gap_detect_flags_uncertified_rfm_as_new_semantic_metric() -> None:
    client = _client()
    resp = client.post(
        "/v1/semantic-gap/detect",
        headers=AUTH_HEADERS,
        json={
            "question": "我想對 customers 和 sales_orders 做 RFM 模型分析",
            "live_catalog": LIVE_CATALOG,
            "live_metrics": LIVE_METRICS,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["classification"] == "NEW_SEMANTIC_METRIC"
    assert set(body["candidate_relations"]) == {"customers", "sales_orders"}


def test_gap_detect_fails_loud_as_source_gap_for_missing_external_source() -> None:
    client = _client()
    resp = client.post(
        "/v1/semantic-gap/detect",
        headers=AUTH_HEADERS,
        json={
            "question": "GA4 廣告點擊率與 Facebook ROAS 表現如何？",
            "live_catalog": LIVE_CATALOG,
            "live_metrics": LIVE_METRICS,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["classification"] == "SOURCE_GAP"


def test_reuse_existing_draft_compiles_measure_aggregation_explicit_dim_and_date_window() -> None:
    client = _client()
    resp = client.post(
        "/v1/query-spec/draft-and-classify",
        headers=AUTH_HEADERS,
        json={
            "question": "What was the sales revenue and order status breakdown over the last 30 days?; Breakdown: status",
            "dialect": "postgres",
            "live_catalog": LIVE_CATALOG,
            "live_metrics": LIVE_METRICS,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["gap"]["classification"] == "REUSE_EXISTING"
    sql = body["compiled_sql"]
    assert 'SUM("total_amount") AS "sales_revenue"' in sql
    assert 'GROUP BY "status"' in sql
    assert "INTERVAL '30 days'" in sql

