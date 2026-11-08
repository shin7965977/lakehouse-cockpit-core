"""Unit tests for lakehouse-cockpit-core microkernel contracts, SQL sandbox, and KPI Tooltips."""

from __future__ import annotations

import os
import sys
from datetime import date
from typing import Any

import pandas as pd
import pytest

COCKPIT_CORE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if COCKPIT_CORE_DIR not in sys.path:
    sys.path.insert(0, COCKPIT_CORE_DIR)

from adapter import (
    BaseCockpitAdapter,
    KPICardSpec,
    MetricComponent,
)
from components.kpi_grid import _render_formula_tag


class DummyAdapter(BaseCockpitAdapter):
    @property
    def project_id(self) -> str:
        return "test-proj"

    @property
    def brand_title(self) -> str:
        return "Test Cockpit"

    @property
    def brand_icon(self) -> str:
        return "🧪"

    @property
    def brand_badge_text(self) -> str:
        return "Test"

    @property
    def kpi_cards(self) -> list[KPICardSpec]:
        return []

    def load_domain_data(
        self, start_date: date | None = None, end_date: date | None = None
    ) -> dict[str, pd.DataFrame]:
        return {"test_table": pd.DataFrame({"id": [1, 2, 3], "val": [10.0, 20.0, 30.0]})}


def test_kpi_card_readcall_formula_tag_and_tooltip() -> None:
    """Verify formula tag renders numerator and denominator with hover tooltips (excluding unit)."""
    card = KPICardSpec(
        key="return_rate",
        title_en="Unit Return Rate",
        metric_formula="returned_units / sold_units",
        target_rule="<= 2.5%",
        calculate=lambda d: {"value": "2.1%", "status": "normal"},
        formula_numerator=MetricComponent(
            label="returned_units",
            role="Numerator",
            description="Total count of customer returned items.",
        ),
        formula_denominator=MetricComponent(
            label="sold_units",
            role="Denominator",
            description="Total count of successfully completed items sold.",
        ),
    )
    tag_html = _render_formula_tag(card, is_en=True)
    assert 'class="formula-tag"' in tag_html
    assert 'class="formula-term term-num"' in tag_html
    assert 'class="formula-term term-den"' in tag_html
    assert "Total count of customer returned items." in tag_html
    assert "Unit:" not in tag_html  # Exclude unit per user requirement


def test_sql_sandbox_executes_safe_queries_and_blocks_destructive_ddl() -> None:
    """Verify SQL sandbox executes safe SELECT queries and blocks destructive DDL/DML."""
    adapter = DummyAdapter()
    data = adapter.load_domain_data()

    # Valid SELECT
    df = adapter.execute_custom_sql("SELECT id, val FROM test_table WHERE val > 15", data)
    assert len(df) == 2
    assert list(df["id"]) == [2, 3]

    # Destructive DROP should be blocked
    with pytest.raises(ValueError, match="Security Alert"):
        adapter.execute_custom_sql("DROP TABLE test_table", data)


def test_gemini_key_validation_and_ae_workflow_flag() -> None:
    """Verify Gemini API key preflight validation and adapter AE schema designer capability."""
    from components.ai_drawer import _validate_gemini_key

    # Invalid keys
    assert _validate_gemini_key("") is False
    assert _validate_gemini_key("your_api_key_here") is False
    assert _validate_gemini_key("short") is False

    # Valid Google AI Studio format
    assert _validate_gemini_key("AIzaSyD-1234567890abcdefghijklmnop") is True

    adapter = DummyAdapter()
    assert adapter.supports_schema_designer is True


def test_ae_workflow_synthesizes_dynamic_context_for_sales() -> None:
    """Verify that inputting sales goal synthesizes revenue metric and orders contract (NOT returns)."""
    from components.ae_workflow import synthesize_ae_lifecycle

    # User input: 我想知道這一季產品的銷售額
    sales_state = synthesize_ae_lifecycle("我想知道這一季產品的銷售額")
    assert sales_state["intent"] == "sales_revenue"
    assert sales_state["metric_name"] == "net_revenue_eur"
    assert "gross_revenue_eur" in sales_state["formula"]
    assert "orders" in sales_state["contract_file"]
    assert "raw_shopware_orders" in sales_state["upstream_table"]
    assert "total_net_revenue" in sales_state["lookml_view"]

    # User input: 1kg 腰果退貨
    return_state = synthesize_ae_lifecycle("1kg 腰果退貨")
    assert return_state["intent"] == "bulk_returns"
    assert return_state["metric_name"] == "return_rate_pct"
    assert "returned_units" in return_state["formula"]
    assert "returns.yaml" in return_state["contract_file"]


def test_step6_looker_studio_embed_removes_plotly() -> None:
    """Verify that ae_workflow file does not import plotly.express and references Looker Studio."""
    import inspect
    from components import ae_workflow

    source = inspect.getsource(ae_workflow)
    assert "plotly.express" not in source, "Step 6 must not use Plotly Express facade!"
    assert "lookerstudio.google.com" in source
    assert "components.iframe" in source

