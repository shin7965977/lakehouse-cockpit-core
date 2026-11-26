"""Seam 1 TDD tests: Control Plane Candidate SQL Draft & Iterative Refinement API."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from api.app import create_app


LIVE_CATALOG = [
    {
        "name": "sales_orders",
        "schema": "public",
        "columns": [
            {"name": "id", "data_type": "integer", "description": "Order ID"},
            {"name": "tenant_id", "data_type": "integer", "description": "Tenant ID"},
            {"name": "order_number", "data_type": "text", "description": "Order Number"},
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
]

AUTH_HEADERS = {"X-Internal-Token": "test-secret"}


def _client(structured_llm_fn: Any = None) -> TestClient:
    app = create_app(internal_token="test-secret", structured_llm_fn=structured_llm_fn)
    return TestClient(app)


def test_v1_carries_over_evidence_reuses_certified_metric_and_validates_ast() -> None:
    captured_prompts: list[str] = []

    def fake_llm(prompt: str, schema: type[Any]) -> Any:
        captured_prompts.append(prompt)
        return schema.model_validate(
            {
                "title": "Last 30 Days Sales Revenue by Status",
                "sql": (
                    'SELECT "status", SUM("total_amount") AS "sales_revenue", COUNT("id") AS "order_count" '
                    'FROM "sales_orders" '
                    "WHERE CAST(\"order_date\" AS DATE) >= CAST(CURRENT_DATE - INTERVAL '30 days' AS DATE) "
                    'GROUP BY "status" LIMIT 100'
                ),
                "plain_language_explanation": (
                    "This query sums total sales revenue and counts orders over the last 30 days, "
                    "broken down by order status. Does this match what you want?"
                ),
                "assumptions": ["Includes all order statuses over the last 30 days."],
                "group_by": ["status"],
            }
        )

    client = _client(structured_llm_fn=fake_llm)
    prior_sql = (
        'SELECT "status", SUM("total_amount") AS "total_revenue", COUNT("id") AS "order_count" '
        'FROM "sales_orders" WHERE CAST("order_date" AS DATE) >= CAST(CURRENT_DATE - INTERVAL \'30 days\' AS DATE) '
        'GROUP BY "status" LIMIT 50'
    )
    resp = client.post(
        "/v1/consultant/draft-candidate-query",
        headers=AUTH_HEADERS,
        json={
            "history": [
                {"role": "user", "content": "How was our sales revenue over the last 30 days?"},
                {"role": "assistant", "content": "Total sales revenue was 537,563.00 across 34 orders."},
            ],
            "evidence_queries": [
                {"title": "Sales Revenue Over Last 30 Days", "sql": prior_sql, "status": "SUCCEEDED"}
            ],
            "live_catalog": LIVE_CATALOG,
            "live_metrics": LIVE_METRICS,
            "dialect": "postgres",
            "tenant_id": 1,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version"] == 1
    assert body["gap"]["classification"] == "REUSE_EXISTING"
    assert body["validation"]["status"] == "VALID"
    assert body["approvable"] is True
    assert body["group_by"] == ["status"]
    assert body["spec_hash"]
    assert 'SUM("total_amount")' in body["validation"]["executable_sql"]
    assert "tenant_id = 1" in body["validation"]["executable_sql"]
    # Verify evidence SQL was carried into prompt & pure English rule enforced
    import json
    assert json.dumps(prior_sql) in captured_prompts[0]
    assert "ENGLISH" in captured_prompts[0].upper()


def test_v1_to_v2_iterative_refinement_increments_version_and_updates_hash() -> None:
    def fake_llm(prompt: str, schema: type[Any]) -> Any:
        assert "last 10 days" in prompt.lower()
        return schema.model_validate(
            {
                "title": "Last 10 Days Completed Sales Revenue",
                "sql": (
                    'SELECT "status", SUM("total_amount") AS "sales_revenue" '
                    'FROM "sales_orders" '
                    "WHERE \"status\" = 'Completed' "
                    "AND CAST(\"order_date\" AS DATE) >= CAST(CURRENT_DATE - INTERVAL '10 days' AS DATE) "
                    'GROUP BY "status" LIMIT 100'
                ),
                "plain_language_explanation": (
                    "Updated to filter only Completed orders over the last 10 days. Is this what you want?"
                ),
                "assumptions": ["Filtered strictly to Completed orders."],
                "group_by": ["status"],
            }
        )

    client = _client(structured_llm_fn=fake_llm)
    resp = client.post(
        "/v1/consultant/draft-candidate-query",
        headers=AUTH_HEADERS,
        json={
            "history": [
                {"role": "user", "content": "How was our sales revenue over the last 30 days?"},
            ],
            "previous_candidate": {
                "version": 1,
                "title": "Last 30 Days Sales Revenue by Status",
                "sql": 'SELECT "status", SUM("total_amount") AS "sales_revenue" FROM "sales_orders" GROUP BY "status" LIMIT 100',
                "plain_language_explanation": "Last 30 days revenue.",
                "group_by": ["status"],
                "spec_hash": "old_hash_v1",
            },
            "refinement_feedback": "Change to last 10 days and only Completed orders",
            "live_catalog": LIVE_CATALOG,
            "live_metrics": LIVE_METRICS,
            "dialect": "postgres",
            "tenant_id": 1,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version"] == 2
    assert body["spec_hash"] != "old_hash_v1"
    assert "10 days" in body["validation"]["executable_sql"].lower()
    assert "'Completed'" in body["validation"]["executable_sql"]
    assert body["approvable"] is True


def test_supports_detail_listing_multi_table_left_join_and_window_function() -> None:
    cases = [
        (
            "List recent sales_orders details without aggregation",
            'SELECT "order_number", "customer_id", "order_date", "total_amount", "status" FROM "sales_orders" LIMIT 50',
            [],
            "VALID",
        ),
        (
            "RFM analysis across customers and sales_orders",
            (
                'SELECT "customers"."id", "customers"."name", MAX("sales_orders"."order_date") AS "last_order_date", '
                'COUNT("sales_orders"."id") AS "frequency", SUM("sales_orders"."total_amount") AS "monetary" '
                'FROM "customers" LEFT JOIN "sales_orders" ON "customers"."id" = "sales_orders"."customer_id" '
                'GROUP BY "customers"."id", "customers"."name" LIMIT 100'
            ),
            ["customers.id", "customers.name"],
            "VALID",
        ),
        (
            "Running total sales_orders revenue with window function",
            (
                'SELECT "order_date", "total_amount", '
                'SUM("total_amount") OVER (ORDER BY "order_date") AS "running_total" '
                'FROM "sales_orders" LIMIT 100'
            ),
            [],
            "REQUIRES_AE_REVIEW",
        ),
    ]

    for user_msg, mock_sql, mock_gb, expected_status in cases:
        def _make_llm(s: str, gb: list[str]):
            def _fn(_prompt: str, schema: type[Any]) -> Any:
                return schema.model_validate(
                    {
                        "title": "Draft",
                        "sql": s,
                        "plain_language_explanation": "Plain explanation.",
                        "assumptions": [],
                        "group_by": gb,
                    }
                )
            return _fn

        client = _client(structured_llm_fn=_make_llm(mock_sql, mock_gb))
        resp = client.post(
            "/v1/consultant/draft-candidate-query",
            headers=AUTH_HEADERS,
            json={
                "history": [{"role": "user", "content": user_msg}],
                "live_catalog": LIVE_CATALOG,
                "live_metrics": LIVE_METRICS,
                "dialect": "postgres",
                "tenant_id": 1,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["validation"]["status"] == expected_status, f"Case '{user_msg}' got {body['validation']}"
        assert body["group_by"] == mock_gb
        if "LEFT JOIN" in mock_sql:
            assert '"customers"."tenant_id" = 1' in body["validation"]["executable_sql"]


def test_fail_loud_on_missing_api_key_source_gap_and_blocked_dml() -> None:
    # 1. Missing API key -> 400 API_KEY_MISSING
    client_no_key = _client(structured_llm_fn=None)
    resp_no_key = client_no_key.post(
        "/v1/consultant/draft-candidate-query",
        headers=AUTH_HEADERS,
        json={
            "history": [{"role": "user", "content": "Show sales_orders by status"}],
            "live_catalog": LIVE_CATALOG,
            "live_metrics": LIVE_METRICS,
            "gemini_api_key": "",
        },
    )
    assert resp_no_key.status_code == 400

    # 2. Out-of-domain external source -> SOURCE_GAP, no fabricated SQL, approvable=False
    def fail_if_called(_p: str, _s: type[Any]) -> Any:
        raise AssertionError("LLM must not fabricate SQL on SOURCE_GAP")

    client_gap = _client(structured_llm_fn=fail_if_called)
    resp_gap = client_gap.post(
        "/v1/consultant/draft-candidate-query",
        headers=AUTH_HEADERS,
        json={
            "history": [{"role": "user", "content": "GA4 ad clicks and Facebook ROAS"}],
            "live_catalog": LIVE_CATALOG,
            "live_metrics": LIVE_METRICS,
            "gemini_api_key": "fake-key",
        },
    )
    assert resp_gap.status_code == 200
    body_gap = resp_gap.json()
    assert body_gap["gap"]["classification"] == "SOURCE_GAP"
    assert body_gap["sql"] == ""
    assert body_gap["approvable"] is False

    # 3. Unsafe DML -> AST status BLOCKED, approvable=False
    def dml_llm(_p: str, schema: type[Any]) -> Any:
        return schema.model_validate(
            {
                "title": "Bad",
                "sql": "DROP TABLE sales_orders",
                "plain_language_explanation": "Drops table.",
                "assumptions": [],
                "group_by": [],
            }
        )

    client_dml = _client(structured_llm_fn=dml_llm)
    resp_dml = client_dml.post(
        "/v1/consultant/draft-candidate-query",
        headers=AUTH_HEADERS,
        json={
            "history": [{"role": "user", "content": "Check sales_orders"}],
            "live_catalog": LIVE_CATALOG,
            "live_metrics": LIVE_METRICS,
            "tenant_id": 1,
        },
    )
    assert resp_dml.status_code == 200
    body_dml = resp_dml.json()
    assert body_dml["validation"]["status"] == "BLOCKED"
    assert body_dml["approvable"] is False


def test_tenant_isolation_allows_compound_and_idempotency_and_blocks_bypasses() -> None:
    """Regression: SQLGlot exp.And inherits from exp.Binary and must NOT false-block valid AND tenant_id = 1."""
    client = _client(structured_llm_fn=None)

    # 1. Valid SQL already containing date filter AND tenant_id = 1 (idempotent re-validation)
    already_scoped_sql = (
        'SELECT "status", SUM("total_amount") AS "total_revenue", COUNT("id") AS "order_count" '
        'FROM "sales_orders" '
        "WHERE CAST(\"order_date\" AS DATE) >= CAST(CURRENT_DATE - INTERVAL '30 days' AS DATE) "
        'AND "tenant_id" = 1 '
        'GROUP BY "status" LIMIT 100'
    )
    resp_ok = client.post(
        "/v1/sql/validate-and-reconcile",
        headers=AUTH_HEADERS,
        json={
            "sql": already_scoped_sql,
            "dialect": "postgres",
            "tenant_id": 1,
            "live_catalog": LIVE_CATALOG,
        },
    )
    assert resp_ok.status_code == 200, resp_ok.text
    body_ok = resp_ok.json()
    assert body_ok["status"] == "VALID", body_ok
    assert body_ok["executable_sql"].lower().count("tenant_id") == 1

    # 2. Bypass attempts MUST still be BLOCKED
    bypass_queries = [
        'SELECT "status" FROM "sales_orders" WHERE "status" = \'Completed\' AND "tenant_id" != 1 LIMIT 10',
        'SELECT "status" FROM "sales_orders" WHERE "status" = \'Completed\' OR "tenant_id" = 1 LIMIT 10',
        'SELECT "status" FROM "sales_orders" WHERE "status" = \'Completed\' AND "tenant_id" = 2 LIMIT 10',
        'SELECT "status" FROM "sales_orders" WHERE NOT ("tenant_id" = 1) LIMIT 10',
        'SELECT "status" FROM "sales_orders" WHERE "tenant_id" IN (1, 2) LIMIT 10',
        'SELECT "status" FROM "sales_orders" WHERE "tenant_id" BETWEEN 1 AND 2 LIMIT 10',
        # Column-to-different-column equality on tenant_id MUST be blocked
        'SELECT "sales_orders"."id" FROM "sales_orders" JOIN "customers" ON "sales_orders"."tenant_id" = "customers"."id" LIMIT 10',
    ]
    for bad_sql in bypass_queries:
        resp_bad = client.post(
            "/v1/sql/validate-and-reconcile",
            headers=AUTH_HEADERS,
            json={
                "sql": bad_sql,
                "dialect": "postgres",
                "tenant_id": 1,
                "live_catalog": LIVE_CATALOG,
            },
        )
        assert resp_bad.status_code == 200, resp_bad.text
        assert resp_bad.json()["status"] == "BLOCKED", f"Expected BLOCKED for: {bad_sql}"


def test_tenant_isolation_supports_multitenant_join_and_cte_scoping() -> None:
    """Verify a.tenant_id = b.tenant_id JOIN is allowed, still enforces WHERE tenant_id = 1, and scopes CTEs safely."""
    client = _client(structured_llm_fn=None)

    # 1. Multi-table JOIN with a.tenant_id = b.tenant_id AND explicit WHERE a.tenant_id = 1 -> VALID
    join_with_where = (
        'SELECT "customers"."id", "customers"."name", COUNT("sales_orders"."id") AS "frequency", '
        'SUM("sales_orders"."total_amount") AS "monetary" '
        'FROM "sales_orders" '
        'JOIN "customers" ON "sales_orders"."customer_id" = "customers"."id" '
        'AND "sales_orders"."tenant_id" = "customers"."tenant_id" '
        'WHERE "sales_orders"."tenant_id" = 1 '
        'GROUP BY "customers"."id", "customers"."name" LIMIT 100'
    )
    resp1 = client.post(
        "/v1/sql/validate-and-reconcile",
        headers=AUTH_HEADERS,
        json={"sql": join_with_where, "dialect": "postgres", "tenant_id": 1, "live_catalog": LIVE_CATALOG},
    )
    assert resp1.status_code == 200, resp1.text
    assert resp1.json()["status"] == "VALID", resp1.json()

    # 2. Multi-table JOIN with ONLY ON a.tenant_id = b.tenant_id (missing WHERE) -> MUST auto-inject WHERE ...tenant_id = 1
    join_without_where = (
        'SELECT "customers"."id", "customers"."name", COUNT("sales_orders"."id") AS "frequency" '
        'FROM "sales_orders" '
        'JOIN "customers" ON "sales_orders"."customer_id" = "customers"."id" '
        'AND "sales_orders"."tenant_id" = "customers"."tenant_id" '
        'GROUP BY "customers"."id", "customers"."name" LIMIT 100'
    )
    resp2 = client.post(
        "/v1/sql/validate-and-reconcile",
        headers=AUTH_HEADERS,
        json={"sql": join_without_where, "dialect": "postgres", "tenant_id": 1, "live_catalog": LIVE_CATALOG},
    )
    assert resp2.status_code == 200, resp2.text
    body2 = resp2.json()
    assert body2["status"] == "VALID", body2
    assert 'WHERE "sales_orders"."tenant_id" = 1' in body2["executable_sql"]

    # 3. CTE reading sales_orders -> tenant_id = 1 must be injected inside CTE, NOT on outer SELECT * FROM rfm
    cte_sql = (
        'WITH rfm AS (SELECT "customer_id", SUM("total_amount") AS "monetary" '
        'FROM "sales_orders" GROUP BY "customer_id") '
        'SELECT * FROM rfm LIMIT 20'
    )
    resp3 = client.post(
        "/v1/sql/validate-and-reconcile",
        headers=AUTH_HEADERS,
        json={"sql": cte_sql, "dialect": "postgres", "tenant_id": 1, "live_catalog": LIVE_CATALOG},
    )
    assert resp3.status_code == 200, resp3.text
    body3 = resp3.json()
    assert body3["status"] == "REQUIRES_AE_REVIEW", body3
    exec_sql = body3["executable_sql"]
    assert "WHERE tenant_id = 1" in exec_sql
    # Ensure outer SELECT does NOT reference non-existent sales_orders.tenant_id
    outer_part = exec_sql.split("SELECT * FROM rfm")[-1]
    assert "sales_orders" not in outer_part
