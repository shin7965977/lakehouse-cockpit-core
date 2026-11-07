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
    """Verify Gemini API key preflight validation and adapter AE workflow capability."""
    from components.ai_drawer import _validate_gemini_key

    # Invalid keys
    assert _validate_gemini_key("") is False
    assert _validate_gemini_key("your_api_key_here") is False
    assert _validate_gemini_key("short") is False

    # Valid Google AI Studio format
    assert _validate_gemini_key("AIzaSyD-1234567890abcdefghijklmnop") is True

    adapter = DummyAdapter()
    assert adapter.supports_ae_workflow is True
