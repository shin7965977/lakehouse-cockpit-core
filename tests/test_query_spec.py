"""Unit tests for Ticket 02: Versioned QuerySpec, Canonical spec_hash & SQL Compilation."""

from __future__ import annotations

import pytest

from services.query_spec import (
    AggregationSpec,
    AnalyticsAssetContract,
    DataSourceRef,
    FilterCondition,
    JoinSpec,
    QuerySpec,
    diff_sql,
)


def _sample_spec() -> QuerySpec:
    return QuerySpec(
        spec_id="qs_test_001",
        question="Monthly revenue by category",
        data_sources=[DataSourceRef(relation="fact_orders", grain=["order_id"])],
        projection=["category"],
        aggregations=[AggregationSpec(field="gross_amount", func="SUM", alias="total_gmv")],
        group_by=["category"],
        filters=[FilterCondition(field="gross_amount", op=">", value=100)],
        dialect="duckdb",
    )


def test_canonical_spec_hash_is_deterministic_and_invalidates_on_any_semantic_edit() -> None:
    s1 = _sample_spec()
    s2 = _sample_spec()
    assert s1.compute_spec_hash() == s2.compute_spec_hash()

    confirmed = s1.confirm_intent(confirmed_by="admin@company.com")
    assert confirmed.is_confirmation_valid() is True
    old_hash = confirmed.review.spec_hash

    # Mutate filter -> hash changes, version increments, confirmation & validation reset
    edited_filter = confirmed.with_updates(
        filters=[FilterCondition(field="gross_amount", op=">", value=500)]
    )
    assert edited_filter.version == confirmed.version + 1
    assert edited_filter.compute_spec_hash() != old_hash
    assert edited_filter.is_confirmation_valid() is False
    assert edited_filter.validation.status == "pending"

    # Mutate max_rows -> also invalidates confirmation per Q8-A
    reconfirmed = edited_filter.confirm_intent()
    assert reconfirmed.is_confirmation_valid() is True
    edited_limit = reconfirmed.with_updates(max_rows=50)
    assert edited_limit.is_confirmation_valid() is False
    assert edited_limit.compute_spec_hash() != reconfirmed.compute_spec_hash()

    # Mutate raw_sql_override -> invalidates confirmation immediately
    reconfirmed2 = edited_limit.confirm_intent()
    edited_sql = reconfirmed2.with_updates(raw_sql_override="SELECT category, AVG(gross_amount) FROM fact_orders GROUP BY 1")
    assert edited_sql.is_confirmation_valid() is False
    assert edited_sql.compute_spec_hash() != reconfirmed2.compute_spec_hash()


def test_compile_sql_and_unified_diff() -> None:
    spec = _sample_spec().with_updates(
        joins=[
            JoinSpec(
                left_relation="fact_orders",
                right_relation="dim_category",
                on="fact_orders.category = dim_category.category",
                join_type="LEFT",
                cardinality="many_to_one",
            )
        ]
    )
    compiled = spec.compile_sql()
    assert 'SELECT "category", SUM("gross_amount") AS "total_gmv"' in compiled
    assert 'FROM "fact_orders"' in compiled
    assert 'LEFT JOIN "dim_category" ON fact_orders.category = dim_category.category' in compiled
    assert 'WHERE "gross_amount" > 100' in compiled
    assert 'GROUP BY "category"' in compiled
    assert "LIMIT 100" in compiled

    manual_sql = compiled.replace("SUM(", "AVG(")
    patch = diff_sql(compiled, manual_sql)
    assert "--- compiled_sql" in patch
    assert "+++ edited_sql" in patch
    assert '-SELECT "category", SUM("gross_amount")' in patch
    assert '+SELECT "category", AVG("gross_amount")' in patch


def test_compile_sql_fails_loud_without_data_sources() -> None:
    empty_spec = QuerySpec(question="Empty spec")
    with pytest.raises(ValueError, match="data_source"):
        empty_spec.compile_sql()


def test_analytics_asset_contract_serialization() -> None:
    spec = _sample_spec().confirm_intent()
    contract = AnalyticsAssetContract(
        asset_id="asset_001",
        model_name="mart_category_gmv",
        query_spec=spec,
        grain=["category"],
        lightdash_explore="mart_category_gmv",
    )
    raw_json = contract.model_dump_json()
    restored = AnalyticsAssetContract.model_validate_json(raw_json)
    assert restored.model_name == "mart_category_gmv"
    assert restored.query_spec.compute_spec_hash() == spec.compute_spec_hash()
