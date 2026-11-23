"""Vertical-slice TDD tests for Seam 3: Bounded SQL Auto-Repair with Security Re-Validation."""

from __future__ import annotations

import pandas as pd
import pytest

from services.query_spec import DataSourceRef, QuerySpec
from services.sql_repair import RepairError, repair_sql_bounded
from services.warehouse_adapters import DuckDBAdapter


def _fixture_adapter() -> DuckDBAdapter:
    df = pd.DataFrame(
        {
            "tenant_id": [1, 1, 2],
            "year_month": ["2026-01", "2026-02", "2026-01"],
            "sales_amount": [1200.0, 1800.0, 9000.0],
        }
    )
    return DuckDBAdapter(dataframes={"fct_monthly_sales": df})


def _confirmed_spec() -> QuerySpec:
    return QuerySpec(
        spec_id="qs_rep_001",
        question="Monthly sales",
        data_sources=[DataSourceRef(relation="fct_monthly_sales")],
        projection=["year_month"],
        dialect="duckdb",
    ).confirm_intent(confirmed_by="admin")


def test_repair_fails_loud_without_api_key() -> None:
    with pytest.raises(RepairError) as exc_info:
        repair_sql_bounded(
            "SELECT year_mnth FROM fct_monthly_sales",
            "Binder Error: Referenced column 'year_mnth' not found",
            base_spec=_confirmed_spec(),
            allowed_relations={"fct_monthly_sales"},
            adapter=_fixture_adapter(),
            server_tenant_id=1,
            tenant_scoped_relations={"fct_monthly_sales"},
            api_key="",
        )
    assert exc_info.value.code == "API_KEY_MISSING"


def test_repair_fixes_column_typo_emits_diff_and_clears_confirmation() -> None:
    spec = _confirmed_spec()
    assert spec.is_confirmation_valid() is True

    res = repair_sql_bounded(
        "SELECT year_mnth, SUM(sales_amount) FROM fct_monthly_sales GROUP BY year_mnth",
        "Binder Error: Referenced column 'year_mnth' not found",
        base_spec=spec,
        allowed_relations={"fct_monthly_sales"},
        adapter=_fixture_adapter(),
        server_tenant_id=1,
        tenant_scoped_relations={"fct_monthly_sales"},
        llm_callable=lambda _p: "SELECT year_month, SUM(sales_amount) AS total_sales FROM fct_monthly_sales GROUP BY year_month LIMIT 50",
    )
    assert res.repaired is True
    assert res.attempts == 1
    assert "year_month" in res.repaired_sql
    assert "---" in res.sql_diff
    assert res.reconciled_spec is not None
    assert res.reconciled_spec.is_confirmation_valid() is False


def test_repair_rejects_ai_patch_that_attempts_cross_tenant_bypass_and_caps_at_2_attempts() -> None:
    calls = {"n": 0}

    def _malicious_llm(_prompt: str) -> str:
        calls["n"] += 1
        return "SELECT year_month, sales_amount FROM fct_monthly_sales WHERE tenant_id = 999"

    res = repair_sql_bounded(
        "SELECT year_mnth FROM fct_monthly_sales",
        "Binder Error: Referenced column 'year_mnth' not found",
        base_spec=_confirmed_spec(),
        allowed_relations={"fct_monthly_sales"},
        adapter=_fixture_adapter(),
        server_tenant_id=1,
        tenant_scoped_relations={"fct_monthly_sales"},
        llm_callable=_malicious_llm,
        max_attempts=2,
    )
    assert res.repaired is False
    assert res.attempts == 2
    assert calls["n"] == 2
    assert "CROSS_TENANT_VIOLATION" in res.failure_codes
