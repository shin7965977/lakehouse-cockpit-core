"""Vertical-slice TDD tests for Seam 2: Read-Only Warehouse Adapters (DuckDB + Fail-Loud Postgres)."""

from __future__ import annotations

import pandas as pd
import pytest

from services.warehouse_adapters import (
    DuckDBAdapter,
    PostgresAdapter,
    WarehouseAdapterError,
)


def test_duckdb_adapter_explain_and_execute_readonly_with_row_limit() -> None:
    df = pd.DataFrame(
        {
            "tenant_id": [1, 1, 2],
            "year_month": ["2026-01", "2026-02", "2026-01"],
            "sales_amount": [1000.0, 2000.0, 9999.0],
        }
    )
    adapter = DuckDBAdapter(dataframes={"fct_monthly_sales": df})
    assert adapter.dialect == "duckdb"

    plan = adapter.explain("SELECT year_month, sales_amount FROM fct_monthly_sales WHERE tenant_id = 1")
    assert plan and isinstance(plan, str)

    res = adapter.execute_readonly(
        "SELECT year_month, sales_amount FROM fct_monthly_sales WHERE tenant_id = 1",
        max_rows=1,
    )
    assert len(res.rows) == 1
    assert res.columns == ["year_month", "sales_amount"]
    assert res.rows[0]["sales_amount"] == 1000.0


def test_postgres_adapter_transpiles_detects_unsupported_functions_and_fails_loud_without_db() -> None:
    pg = PostgresAdapter(database_url="")
    assert pg.dialect == "postgres"

    transpiled, unsupported = pg.transpile_and_check(
        "SELECT year_month, SUM(sales_amount) AS rev FROM fct_monthly_sales GROUP BY year_month"
    )
    assert "SUM" in transpiled.upper()
    assert unsupported == []

    _, bad_funcs = pg.transpile_and_check(
        "SELECT strftime(event_date, '%Y-%m') FROM fct_monthly_sales"
    )
    assert "strftime" in bad_funcs

    # Fail-Loud when DATABASE_URL or psycopg is missing (never silently falls back to DuckDB)
    with pytest.raises(WarehouseAdapterError) as exc_info:
        pg.execute_readonly("SELECT 1")
    assert exc_info.value.code in ("POSTGRES_NOT_CONFIGURED", "POSTGRES_DRIVER_MISSING")
