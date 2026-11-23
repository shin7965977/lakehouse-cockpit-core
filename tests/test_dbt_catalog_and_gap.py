"""Unit tests for Ticket 01: dbt-Native Metadata Catalog & 7-State Semantic Gap Lookup.

Uses anonymized synthetic dbt payloads so no private company schema is stored in the public repo.
"""

from __future__ import annotations

import json
from pathlib import Path

from services.dbt_catalog import DbtMetadataCatalog
from services.semantic_gap_detector import GapClassification, classify_semantic_gap


def _build_synthetic_catalog() -> DbtMetadataCatalog:
    manifest = {
        "metadata": {"generated_at": "2026-11-23T00:00:00Z"},
        "nodes": {
            "model.demo.stg_raw_shipments": {
                "resource_type": "model",
                "name": "stg_raw_shipments",
                "schema": "bronze",
                "description": "Raw warehouse shipment webhook payload.",
                "columns": {
                    "shipment_ref": {"name": "shipment_ref", "description": "Raw carrier tracking code"},
                    "carrier_cost": {"name": "carrier_cost", "description": "Unnormalized shipping fee"},
                },
                "depends_on": {"nodes": []},
            },
            "model.demo.int_vendor_tiers": {
                "resource_type": "model",
                "name": "int_vendor_tiers",
                "schema": "silver",
                "description": "Cleansed vendor tier classification.",
                "columns": {
                    "vendor_code": {"name": "vendor_code", "description": "Normalized vendor code"},
                    "vendor_tier": {"name": "vendor_tier", "description": "Strategic vendor tier"},
                },
                "depends_on": {"nodes": ["model.demo.stg_raw_shipments"]},
            },
            "model.demo.fct_monthly_ledger": {
                "resource_type": "model",
                "name": "fct_monthly_ledger",
                "schema": "gold",
                "description": "Monthly general ledger summary at tenant and month grain.",
                "columns": {
                    "tenant_id": {"name": "tenant_id"},
                    "year_month": {"name": "year_month"},
                    "ledger_amount": {"name": "ledger_amount", "description": "Monthly ledger amount"},
                    "order_volume": {"name": "order_volume", "description": "Monthly order volume"},
                },
                "depends_on": {"nodes": ["model.demo.int_vendor_tiers"]},
            },
            "test.demo.not_null_fct_monthly_ledger_tenant_id": {
                "resource_type": "test",
                "name": "not_null_fct_monthly_ledger_tenant_id",
                "depends_on": {"nodes": ["model.demo.fct_monthly_ledger"]},
            },
        },
        "child_map": {
            "model.demo.stg_raw_shipments": ["model.demo.int_vendor_tiers"],
            "model.demo.int_vendor_tiers": ["model.demo.fct_monthly_ledger"],
        },
    }
    catalog = {
        "nodes": {
            "model.demo.fct_monthly_ledger": {
                "columns": {
                    "tenant_id": {"name": "tenant_id", "type": "VARCHAR"},
                    "year_month": {"name": "year_month", "type": "VARCHAR"},
                    "ledger_amount": {"name": "ledger_amount", "type": "DOUBLE"},
                    "order_volume": {"name": "order_volume", "type": "BIGINT"},
                }
            }
        }
    }
    run_results = {
        "metadata": {"generated_at": "2026-11-23T01:00:00Z"},
        "results": [
            {"unique_id": "model.demo.fct_monthly_ledger", "status": "success", "failures": 0},
            {"unique_id": "test.demo.not_null_fct_monthly_ledger_tenant_id", "status": "pass", "failures": 0},
        ],
    }
    semantic_manifest = {
        "semantic_models": [
            {
                "name": "monthly_ledger",
                "node_relation": {"alias": "fct_monthly_ledger"},
                "entities": [{"name": "tenant"}],
                "dimensions": [{"name": "year_month"}],
                "measures": [{"name": "gl_amount"}, {"name": "sales_amt"}],
            }
        ],
        "metrics": [
            {
                "name": "operating_revenue",
                "label": "Operating Revenue (營業收入)",
                "description": "Certified GL operating revenue.",
                "type": "simple",
                "type_params": {"measure": {"name": "gl_amount"}},
            },
            {
                "name": "sales_order_amount",
                "label": "Sales Order Revenue (銷售訂單金額)",
                "description": "Operational sales order revenue.",
                "type": "simple",
                "type_params": {"measure": {"name": "sales_amt"}},
            },
        ],
    }
    return DbtMetadataCatalog.from_payloads(
        manifest=manifest,
        catalog=catalog,
        run_results=run_results,
        semantic_manifest=semantic_manifest,
    )


def test_dbt_catalog_parses_all_four_artifacts_and_searches(tmp_path: Path) -> None:
    cat = _build_synthetic_catalog()
    summary = cat.summary()
    assert summary["relation_count"] == 3
    assert summary["metric_count"] == 2

    ledger = cat.get_relation("fct_monthly_ledger")
    assert ledger is not None
    assert ledger.layer == "gold"
    assert ledger.last_run_status == "success"
    assert ledger.test_failures == 0
    assert any(c.name == "ledger_amount" and c.data_type == "DOUBLE" for c in ledger.columns)

    search_res = cat.search("operating_revenue")
    assert any(m.name == "operating_revenue" for m in search_res.metrics)

    # Also verify loading from disk directory
    (tmp_path / "manifest.json").write_text(
        json.dumps({"nodes": {"model.x.fct_a": {"resource_type": "model", "name": "fct_a", "schema": "gold"}}}),
        encoding="utf-8",
    )
    disk_cat = DbtMetadataCatalog.from_target_dir(tmp_path)
    assert disk_cat.get_relation("fct_a") is not None


def test_all_seven_semantic_gap_classifications() -> None:
    cat = _build_synthetic_catalog()

    # 1. ACCESS_BLOCKED
    r_blocked = classify_semantic_gap("查詢營業收入", cat, is_authorized=False)
    assert r_blocked.classification == GapClassification.ACCESS_BLOCKED

    # 2. REUSE_EXISTING
    r_reuse = classify_semantic_gap("請提供各月營業收入趨勢", cat)
    assert r_reuse.classification == GapClassification.REUSE_EXISTING
    assert r_reuse.matched_metric is not None
    assert r_reuse.matched_metric.name == "operating_revenue"

    # 3. SEMANTIC_AMBIGUITY
    r_ambig = classify_semantic_gap("這個月營收是多少？", cat)
    assert r_ambig.classification == GapClassification.SEMANTIC_AMBIGUITY
    assert len(r_ambig.candidate_metrics) >= 2

    # 4. NEW_SEMANTIC_METRIC (Gold column exists, no certified metric)
    r_new_metric = classify_semantic_gap("計算每月的 order_volume 與平均金額", cat)
    assert r_new_metric.classification == GapClassification.NEW_SEMANTIC_METRIC
    assert "fct_monthly_ledger" in r_new_metric.candidate_relations

    # 5. NEW_DBT_MODEL (Silver column only)
    r_new_model = classify_semantic_gap("依 vendor_tier 統計數量", cat)
    assert r_new_model.classification == GapClassification.NEW_DBT_MODEL
    assert "int_vendor_tiers" in r_new_model.candidate_relations

    # 6. TRANSFORMATION_GAP (Bronze column only)
    r_transform = classify_semantic_gap("分析 carrier_cost 分布", cat)
    assert r_transform.classification == GapClassification.TRANSFORMATION_GAP
    assert "stg_raw_shipments" in r_transform.candidate_relations

    # 7. SOURCE_GAP (Fine-grained customer/order detail absent from monthly aggregate)
    r_source = classify_semantic_gap("列出每位客戶每筆訂單明細", cat)
    assert r_source.classification == GapClassification.SOURCE_GAP
    assert r_source.evidence
