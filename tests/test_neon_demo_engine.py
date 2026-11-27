"""Unit & integration tests for the Neon Data Governance & SCD Type 2 Demo Engine."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
import pytest

from services.neon_demo_engine import (
    JAFFLE_ONE_TO_MANY_JOINS,
    _generate_baseline_data,
    build_jaffle_catalog,
    execute_neon_readonly,
    fetch_demo_state,
    reset_demo,
    run_day5_normal_refresh,
    run_day10_scd_type2,
    run_day15_dirty_data_wap,
    run_day20_semantic_drift_ci,
)
from services.query_spec import DataSourceRef, QuerySpec
from services.sql_validator import validate_and_reconcile_sql

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def test_baseline_seed_is_deterministic_and_complete() -> None:
    seed_a = _generate_baseline_data()
    seed_b = _generate_baseline_data()
    assert len(seed_a["products"]) == 12
    assert len(seed_a["customers"]) == 120
    assert len(seed_a["orders"]) == 520
    assert seed_a["orders"] == seed_b["orders"]


def test_jaffle_catalog_and_fanout_gate_block_1_to_n_join() -> None:
    catalog = build_jaffle_catalog()
    rel_names = {r.name for r in catalog.relations}
    assert "fct_orders" in rel_names
    assert "dim_customers_scd2" in rel_names

    fanout_sql = (
        "SELECT o.customer_id, SUM(o.order_total) AS bad_rev "
        "FROM fct_orders o JOIN fct_order_items i ON o.order_id = i.order_id "
        "GROUP BY o.customer_id"
    )
    spec = QuerySpec(
        spec_id="qs_test_fanout",
        data_sources=[DataSourceRef(relation="fct_orders")],
        dialect="postgres",
    )
    res = validate_and_reconcile_sql(
        fanout_sql,
        spec,
        allowed_relations=rel_names,
        server_tenant_id=None,
        one_to_many_joins=JAFFLE_ONE_TO_MANY_JOINS,
    )
    assert res.status == "BLOCKED"
    assert "JOIN_FANOUT_RISK" in res.reason_codes


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="DATABASE_URL not configured")
def test_live_neon_day0_to_day20_lifecycle_and_reset() -> None:
    r0 = reset_demo()
    assert r0["status"] == "HEALTHY"
    assert r0["orders"] == 520

    r5 = run_day5_normal_refresh()
    assert r5["published_to_gold"] is True
    assert r5["gold_order_count"] == 545

    r10 = run_day10_scd_type2()
    assert r10["upgraded_customers"] == 15
    assert r10["total_snapshot_rows"] == 135
    state10 = fetch_demo_state()
    distortions = {row["customer_tier"]: row["historical_distortion"] for row in state10["scd_comparison"]}
    assert distortions["VIP"] > 0  # Naive SCD1 inflates historical VIP revenue
    assert distortions["Standard"] < 0  # Naive SCD1 deflates historical Standard revenue

    r15 = run_day15_dirty_data_wap()
    assert r15["published_to_gold"] is False
    assert r15["lkg_preserved"] is True
    assert r15["quarantined_rows"] == 5
    assert r15["lkg_order_count"] == 560

    r20 = run_day20_semantic_drift_ci()
    assert r20["merge_allowed"] is False
    assert r20["delta_pct"] > 5.0
    assert r20["ci_gates"][0]["status"] == "PASS"  # Schema Contract passes
    assert r20["ci_gates"][1]["status"] == "BLOCKED"  # CI Data Diff catches drift

    df = execute_neon_readonly("SELECT COUNT(*) AS cnt FROM fct_orders")
    assert int(df.iloc[0]["cnt"]) == 560  # Production still intact at LKG

    # Leave Neon clean at Day 0
    r_final = reset_demo()
    assert r_final["orders"] == 520
