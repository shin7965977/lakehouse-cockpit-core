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
    {
        "name": "sales_order_count",
        "label": "Sales Order Count (銷售訂單數)",
        "description": "Total number of sales orders from sales_orders.id.",
        "metric_type": "simple",
        "semantic_model": "erp_sales_orders",
        "source_relation": "sales_orders",
        "time_granularity": "day",
        "measure_expr": "id",
        "measure_agg": "COUNT",
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
            ["id", "name"],
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


def test_multitable_join_preserves_sql_for_build_and_aligns_output_grain() -> None:
    """Verify multi-table JOIN with aliases preserves executable SQL for Build and normalizes Grain to output aliases."""
    join_sql = (
        "SELECT t1.customer_id, t2.name AS customer_name, MAX(t1.order_date) AS last_order_date, "
        "COUNT(t1.id) AS frequency, SUM(t1.total_amount) AS monetary "
        "FROM sales_orders AS t1 INNER JOIN customers AS t2 "
        "ON t1.tenant_id = t2.tenant_id AND t1.customer_id = t2.id "
        "WHERE t1.tenant_id = 1 GROUP BY t1.customer_id, t2.name LIMIT 200"
    )

    def fake_llm(_prompt: str, schema: type[Any]) -> Any:
        return schema.model_validate(
            {
                "title": "Customer RFM Analysis with Customer Names",
                "sql": join_sql,
                "plain_language_explanation": "RFM with customer names.",
                "assumptions": [],
                # Intentionally noisy table-prefixed group_by from LLM
                "group_by": ["t1.customer_id", "t2.name"],
            }
        )

    client = _client(structured_llm_fn=fake_llm)
    resp = client.post(
        "/v1/consultant/draft-candidate-query",
        headers=AUTH_HEADERS,
        json={
            "history": [{"role": "user", "content": "Join the customers table to include customer name"}],
            "live_catalog": LIVE_CATALOG,
            "live_metrics": LIVE_METRICS,
            "dialect": "postgres",
            "tenant_id": 1,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["validation"]["status"] == "VALID"
    # Grain MUST be aligned to actual SELECT output aliases (no t1./t2. prefixes)
    assert body["group_by"] == ["customer_id", "customer_name"]
    assert body["query_spec"]["group_by"] == ["customer_id", "customer_name"]
    # raw_sql_override MUST be preserved so Build does NOT strip AS t1 / AS t2
    assert body["query_spec"]["raw_sql_override"]
    assert "AS t1" in body["query_spec"]["raw_sql_override"]

    # Now build dbt bundle from this QuerySpec and verify SQL keeps aliases & valid {{ ref(...) }}
    build_resp = client.post(
        "/v1/assets/build-and-validate",
        headers=AUTH_HEADERS,
        json={
            "query_spec": body["query_spec"],
            "model_name": "mart_customer_rfm",
            "grain": body["group_by"],
            "skip_local_validation": True,
        },
    )
    assert build_resp.status_code == 200, build_resp.text
    build_data = build_resp.json()
    assert "AS t1" in build_data["compiled_sql"]
    assert "AS t2" in build_data["compiled_sql"]
    dbt_sql = build_data["bundle"]["dbt_sql"]
    assert "{{ ref('sales_orders') }} AS t1" in dbt_sql
    assert "{{ ref('customers') }} AS t2" in dbt_sql


def test_ast_semantic_fingerprint_reuses_certified_even_when_paraphrased_and_detects_join_gap() -> None:
    """Verify AST-first semantic gap: paraphrased metric -> REUSE_EXISTING; 'table' keyword does NOT dump all tables."""
    # Build a catalog with 10 tables whose descriptions all contain "Live ERP PostgreSQL table"
    large_catalog = list(LIVE_CATALOG) + [
        {
            "name": f"extra_table_{i}",
            "schema": "public",
            "columns": [{"name": "id", "data_type": "integer", "description": "ID"}],
        }
        for i in range(8)
    ]

    # 1. Paraphrased question ("How much money did we collect?") without saying "sales revenue"
    def reuse_llm(_prompt: str, schema: type[Any]) -> Any:
        return schema.model_validate(
            {
                "title": "Revenue by Status",
                "sql": 'SELECT "status", SUM("total_amount") AS "total_revenue" FROM "sales_orders" GROUP BY "status" LIMIT 100',
                "plain_language_explanation": "Total revenue by status.",
                "assumptions": [],
                "group_by": ["status"],
            }
        )

    client1 = _client(structured_llm_fn=reuse_llm)
    resp1 = client1.post(
        "/v1/consultant/draft-candidate-query",
        headers=AUTH_HEADERS,
        json={
            "history": [{"role": "user", "content": "How much money did we collect from sales_orders by status?"}],
            "live_catalog": large_catalog,
            "live_metrics": LIVE_METRICS,
            "dialect": "postgres",
            "tenant_id": 1,
        },
    )
    assert resp1.status_code == 200, resp1.text
    b1 = resp1.json()
    assert b1["gap"]["classification"] == "REUSE_EXISTING", b1["gap"]
    assert b1["gap"]["candidate_relations"] == ["sales_orders"]

    # 2. RFM multi-table JOIN with word "table" in prompt -> MUST list ONLY ['customers', 'sales_orders'] (NOT extra_table_*)
    def rfm_llm(_prompt: str, schema: type[Any]) -> Any:
        return schema.model_validate(
            {
                "title": "Customer RFM",
                "sql": (
                    'SELECT "sales_orders"."customer_id", "customers"."name" AS "customer_name", '
                    'MAX("sales_orders"."order_date") AS "last_order_date", '
                    'SUM("sales_orders"."total_amount") AS "monetary" '
                    'FROM "sales_orders" '
                    'INNER JOIN "customers" ON "sales_orders"."customer_id" = "customers"."id" '
                    'AND "sales_orders"."tenant_id" = "customers"."tenant_id" '
                    'WHERE "sales_orders"."tenant_id" = 1 '
                    'GROUP BY "sales_orders"."customer_id", "customers"."name" LIMIT 100'
                ),
                "plain_language_explanation": "RFM per customer.",
                "assumptions": [],
                "group_by": ["customer_id", "customer_name"],
            }
        )

    client2 = _client(structured_llm_fn=rfm_llm)
    resp2 = client2.post(
        "/v1/consultant/draft-candidate-query",
        headers=AUTH_HEADERS,
        json={
            "history": [{"role": "user", "content": "Join the customers table to include customer name"}],
            "live_catalog": large_catalog,
            "live_metrics": LIVE_METRICS,
            "dialect": "postgres",
            "tenant_id": 1,
        },
    )
    assert resp2.status_code == 200, resp2.text
    b2 = resp2.json()
    assert b2["gap"]["classification"] == "NEW_SEMANTIC_METRIC", b2["gap"]
    assert b2["gap"]["candidate_relations"] == ["customers", "sales_orders"]
    assert "extra_table_0" not in b2["gap"]["reason"]
    assert "MAX(order_date)" in b2["gap"]["reason"]
    assert "sales_revenue" in b2["gap"]["reason"]
