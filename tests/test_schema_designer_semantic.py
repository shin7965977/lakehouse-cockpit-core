"""Unit tests for Semantic-First Schema Designer & Guardrails."""

import pandas as pd

from components.schema_designer import (
    DatabaseReflectionService,
    _build_governed_semantic_artifacts,
    synthesize_dynamic_dbt_model,
)


def test_semantic_guardrails_block_ids_ranks_and_unit_price():
    """Ensure IDs, ranks, percentages, and unit_price are never summed as measures."""
    preview_df = pd.DataFrame(
        {
            "product_id": [101, 102],
            "product_title": ["Shoes A", "Bag B"],
            "category_name": ["Shoes", "Accessories"],
            "unit_price": [120.0, 80.0],
            "units_sold": [10, 15],
            "net_sales_amount": [1200.0, 1200.0],
            "net_sales_contribution_percentage": [50.0, 50.0],
            "profitability_rank": [1, 2],
        }
    )
    yaml_str, lookml_str = _build_governed_semantic_artifacts(
        spec_id="SPEC-C008",
        model_name="mart_product_profitability",
        target_grain="Product / Category (1 row per product)",
        preview_df=preview_df,
    )

    # Model ref must point to the newly synthesized model
    assert "model: ref('mart_product_profitability')" in yaml_str
    # Primary entity must be product_id, NOT product_title
    assert "- name: product_id\n        type: primary" in yaml_str
    # Primary KPI must prioritize net_sales_amount, NOT product_id
    assert "name: net_sales_amount_kpi" in yaml_str
    assert "product_id_kpi" not in yaml_str
    # Non-additive fields must NOT appear as agg: sum measures
    assert "- name: product_id\n        agg: sum" not in yaml_str
    assert "- name: profitability_rank\n        agg: sum" not in yaml_str
    assert "- name: unit_price\n        agg: sum" not in yaml_str
    assert "view: mart_product_profitability" in lookml_str


def test_synthesize_dynamic_dbt_model_untruncated_and_missing_cost_warning():
    """Verify physical dbt SQL has no LIMIT 25 truncation and warns on missing COGS."""
    mock_data = {
        "mart_product_performance": pd.DataFrame(
            {
                "product_id": list(range(1, 31)),
                "product_title": [f"Item {i}" for i in range(1, 31)],
                "category_name": ["Shoes"] * 30,
                "unit_price": [50.0] * 30,
                "units_sold": [10] * 30,
                "completed_sales_amount": [500.0] * 30,
                "refunded_units": [1] * 30,
            }
        )
    }
    reflection = DatabaseReflectionService(mock_data)
    res = synthesize_dynamic_dbt_model(
        user_goal="我想要分析產品利潤",
        decisions={},
        reflection=reflection,
        api_key="",
    )

    assert not res.is_rejected
    # Physical Mart SQL must NOT have LIMIT truncation
    assert "LIMIT 25" not in res.dbt_sql.upper()
    # Preview DataFrame is safely bounded to <= 25 rows
    assert len(res.data_preview) <= 25
    # Missing COGS warning must be surfaced
    assert "Cost" in res.missing_fields_warning or "COGS" in res.missing_fields_warning
    # Semantic YAML must reference the synthesized model
    assert "model: ref('mart_product_profitability')" in res.semantic_yaml
