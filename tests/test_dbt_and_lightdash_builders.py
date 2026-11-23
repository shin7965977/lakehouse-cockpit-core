"""Vertical-slice TDD tests for Seam 1: Data Contract, Minimal dbt Model & Separate Lightdash Semantics."""

from __future__ import annotations

import yaml

from services.dbt_asset_builder import build_dbt_and_lightdash_bundle
from services.query_spec import AggregationSpec, DataSourceRef, QuerySpec


def _confirmed_spec() -> QuerySpec:
    return QuerySpec(
        spec_id="qs_day3_001",
        purpose="long_term",
        question="Monthly revenue by region",
        data_sources=[DataSourceRef(relation="fct_monthly_sales", grain=["tenant_id", "year_month"])],
        projection=["tenant_id", "year_month"],
        aggregations=[AggregationSpec(field="sales_amount", func="SUM", alias="total_sales")],
        group_by=["tenant_id", "year_month"],
        dialect="duckdb",
    ).confirm_intent(confirmed_by="admin")


def test_generates_enforced_contract_ref_sql_separate_lightdash_yaml_and_manifest() -> None:
    bundle = build_dbt_and_lightdash_bundle(
        query_spec=_confirmed_spec(),
        model_name="mart_monthly_sales_summary",
        grain=["tenant_id", "year_month"],
        business_owner="finance-analytics@company.com",
        column_types={"tenant_id": "bigint", "year_month": "varchar", "total_sales": "double"},
    )

    # 1. dbt SQL uses {{ ref('...') }} instead of hardcoded table name
    assert "{{ config(materialized='table') }}" in bundle.dbt_sql
    assert "{{ ref('fct_monthly_sales') }}" in bundle.dbt_sql

    # 2. Data Contract schema.yml has enforced contract, not_null & composite grain uniqueness
    schema_doc = yaml.safe_load(bundle.schema_yaml)
    model_entry = schema_doc["models"][0]
    assert model_entry["name"] == "mart_monthly_sales_summary"
    assert model_entry["config"]["contract"]["enforced"] is True
    assert model_entry["meta"]["owner"] == "finance-analytics@company.com"
    assert any("unique_combination_of_columns" in str(t) for t in model_entry.get("tests", []))

    # 3. Lightdash semantic YAML is separate (meta.metrics / meta.dimension) and NEVER copies MetricFlow syntax
    assert "semantic_models:" not in bundle.lightdash_yaml
    ld_doc = yaml.safe_load(bundle.lightdash_yaml)
    ld_cols = {c["name"]: c for c in ld_doc["models"][0]["columns"]}
    assert "metrics" in ld_cols["total_sales"]["meta"]
    assert ld_cols["year_month"]["meta"]["dimension"]["type"] in ("string", "date")

    # 4. BI Asset Manifest stays PROPOSED_ASSET and honest STAGING_LOCAL_ONLY
    assert bundle.bi_asset_manifest.certification_status == "PROPOSED_ASSET"
    assert bundle.bi_asset_manifest.lightdash_status == "STAGING_LOCAL_ONLY"
    assert bundle.bi_asset_manifest.model_name == "mart_monthly_sales_summary"
