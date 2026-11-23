"""Vertical-slice TDD tests for Seam 2: Isolated dbt Materialization, Contract Tests & Golden SQL Parity."""

from __future__ import annotations

import pandas as pd

from services.dbt_validator import validate_isolated_dbt_asset


def test_isolated_materialization_passes_not_null_composite_grain_and_golden_parity() -> None:
    df = pd.DataFrame(
        {
            "tenant_id": [1, 1, 2],
            "year_month": ["2026-01", "2026-02", "2026-01"],
            "sales_amount": [1000.0, 2500.0, 4000.0],
        }
    )
    candidate_sql = (
        'SELECT "tenant_id", "year_month", SUM("sales_amount") AS "total_sales" '
        'FROM "fct_monthly_sales" GROUP BY "tenant_id", "year_month"'
    )
    golden_sql = (
        'SELECT "tenant_id", "year_month", SUM("sales_amount") AS "total_sales" '
        'FROM "fct_monthly_sales" GROUP BY 1, 2'
    )

    res = validate_isolated_dbt_asset(
        model_name="mart_monthly_sales_summary",
        candidate_sql=candidate_sql,
        source_dataframes={"fct_monthly_sales": df},
        grain=["tenant_id", "year_month"],
        not_null_columns=["tenant_id", "year_month", "total_sales"],
        golden_sql=golden_sql,
        reconcile_column="total_sales",
    )
    assert res.passed is True
    assert res.materialized_rows == 3
    assert all(check.passed for check in res.checks)


def test_isolated_validator_fails_loud_on_duplicate_grain_and_golden_mismatch() -> None:
    df = pd.DataFrame(
        {
            "tenant_id": [1, 1],
            "year_month": ["2026-01", "2026-01"],
            "sales_amount": [1000.0, 2000.0],
        }
    )
    # Missing GROUP BY -> duplicate (tenant_id, year_month) grain!
    bad_sql = 'SELECT "tenant_id", "year_month", "sales_amount" AS "total_sales" FROM "fct_monthly_sales"'
    golden_sql = 'SELECT "tenant_id", "year_month", SUM("sales_amount") * 2 AS "total_sales" FROM "fct_monthly_sales" GROUP BY 1, 2'

    res = validate_isolated_dbt_asset(
        model_name="mart_bad_grain",
        candidate_sql=bad_sql,
        source_dataframes={"fct_monthly_sales": df},
        grain=["tenant_id", "year_month"],
        not_null_columns=["tenant_id", "year_month"],
        golden_sql=golden_sql,
        reconcile_column="total_sales",
    )
    assert res.passed is False
    failed_names = {c.name for c in res.checks if not c.passed}
    assert "grain_uniqueness" in failed_names
    assert "golden_sql_parity" in failed_names
