"""Vertical-slice TDD tests for Seam 1: SQLGlot AST Validator, Tenant Isolation & Join Fanout."""

from __future__ import annotations

from services.query_spec import DataSourceRef, QuerySpec
from services.sql_validator import validate_and_reconcile_sql


def _base_spec() -> QuerySpec:
    return QuerySpec(
        spec_id="qs_val_001",
        question="Monthly sales",
        data_sources=[DataSourceRef(relation="fct_monthly_sales", grain=["tenant_id", "year_month"])],
        projection=["year_month"],
        dialect="duckdb",
    )


def test_blocks_ddl_dml_multistatement_file_io_and_unlisted_tables() -> None:
    allowed = {"fct_monthly_sales", "dim_supplier", "fct_orders", "fct_order_items"}
    spec = _base_spec()

    unsafe_queries = [
        ("DROP TABLE fct_monthly_sales", "FORBIDDEN_STATEMENT"),
        ("DELETE FROM fct_monthly_sales WHERE 1=1", "FORBIDDEN_STATEMENT"),
        ("SELECT * FROM fct_monthly_sales; DROP TABLE fct_monthly_sales", "MULTIPLE_STATEMENTS"),
        ("SELECT * FROM read_csv('secret.csv')", "FORBIDDEN_FUNCTION"),
        ("SELECT * FROM pg_shadow", "UNLISTED_RELATION"),
    ]

    for sql, expected_code in unsafe_queries:
        res = validate_and_reconcile_sql(
            sql,
            spec,
            allowed_relations=allowed,
            server_tenant_id=1,
        )
        assert res.status == "BLOCKED", f"Expected BLOCKED for: {sql}"
        assert expected_code in res.reason_codes, f"Expected {expected_code} in {res.reason_codes} for: {sql}"


def test_enforces_server_tenant_isolation_and_blocks_cross_tenant_bypass() -> None:
    allowed = {"fct_monthly_sales"}
    spec = _base_spec()

    # Cross-tenant literal -> BLOCKED
    r_cross = validate_and_reconcile_sql(
        "SELECT year_month, sales_amount FROM fct_monthly_sales WHERE tenant_id = 999",
        spec,
        allowed_relations=allowed,
        server_tenant_id=1,
        tenant_scoped_relations={"fct_monthly_sales"},
    )
    assert r_cross.status == "BLOCKED"
    assert "CROSS_TENANT_VIOLATION" in r_cross.reason_codes

    # Bypass via != -> BLOCKED
    r_neq = validate_and_reconcile_sql(
        "SELECT year_month FROM fct_monthly_sales WHERE tenant_id != 1",
        spec,
        allowed_relations=allowed,
        server_tenant_id=1,
        tenant_scoped_relations={"fct_monthly_sales"},
    )
    assert r_neq.status == "BLOCKED"
    assert "CROSS_TENANT_VIOLATION" in r_neq.reason_codes

    # Clean query without tenant_id -> auto-scoped to server_tenant_id = 1
    r_ok = validate_and_reconcile_sql(
        "SELECT year_month, SUM(sales_amount) AS total_sales FROM fct_monthly_sales GROUP BY year_month LIMIT 20",
        spec,
        allowed_relations=allowed,
        server_tenant_id=1,
        tenant_scoped_relations={"fct_monthly_sales"},
    )
    assert r_ok.status == "VALID"
    assert "tenant_id = 1" in r_ok.executable_sql.lower()


def test_blocks_one_to_many_join_fanout_aggregation() -> None:
    allowed = {"fct_orders", "fct_order_items"}
    spec = _base_spec()
    fanout_sql = (
        "SELECT fct_orders.region, SUM(fct_orders.total_amount) AS rev "
        "FROM fct_orders JOIN fct_order_items ON fct_orders.order_id = fct_order_items.order_id "
        "GROUP BY fct_orders.region"
    )
    res = validate_and_reconcile_sql(
        fanout_sql,
        spec,
        allowed_relations=allowed,
        server_tenant_id=1,
        one_to_many_joins={("fct_orders", "fct_order_items")},
    )
    assert res.status == "BLOCKED"
    assert "JOIN_FANOUT_RISK" in res.reason_codes


def test_reconciles_supported_select_and_flags_cte_window_as_requires_ae_review() -> None:
    allowed = {"fct_monthly_sales"}
    confirmed = _base_spec().confirm_intent(confirmed_by="admin")
    assert confirmed.is_confirmation_valid() is True

    # 1. Supported single-level SELECT -> reconciles into QuerySpec & invalidates prior confirmation
    edited_sql = (
        "SELECT year_month, SUM(sales_amount) AS total_sales "
        "FROM fct_monthly_sales WHERE sales_amount > 500 GROUP BY year_month LIMIT 25"
    )
    r_simple = validate_and_reconcile_sql(
        edited_sql,
        confirmed,
        allowed_relations=allowed,
        server_tenant_id=1,
    )
    assert r_simple.status == "VALID"
    assert r_simple.reconciled_from_ast is True
    assert r_simple.reconciled_spec is not None
    assert r_simple.reconciled_spec.is_confirmation_valid() is False
    assert r_simple.reconciled_spec.raw_sql_override is None
    assert r_simple.reconciled_spec.output.max_rows == 25
    assert r_simple.reconciled_spec.aggregations[0].func == "SUM"

    # 2. Valid CTE / Window Function -> REQUIRES_AE_REVIEW & keeps raw_sql_override
    cte_sql = (
        "WITH base AS (SELECT year_month, sales_amount FROM fct_monthly_sales) "
        "SELECT year_month, SUM(sales_amount) OVER (ORDER BY year_month) AS running_total FROM base"
    )
    r_cte = validate_and_reconcile_sql(
        cte_sql,
        confirmed,
        allowed_relations=allowed,
        server_tenant_id=1,
    )
    assert r_cte.status == "REQUIRES_AE_REVIEW"
    assert r_cte.reconciled_from_ast is False
    assert r_cte.reconciled_spec is not None
    assert r_cte.reconciled_spec.raw_sql_override == cte_sql
    assert r_cte.reconciled_spec.is_confirmation_valid() is False

