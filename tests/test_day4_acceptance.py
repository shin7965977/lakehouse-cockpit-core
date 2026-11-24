"""Day 4 Acceptance Testing Matrix, X-Request-Id Audit & Conditional Local ERP Smoke Suite.

No private company schema or table names are hardcoded in this file.
Set `ERP_REPO_DIR` locally to run the live read-only smoke test against real ERP dbt artifacts.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from integrations.langfuse_adapter import ObservabilityTracker
from services.dbt_catalog import DbtMetadataCatalog
from services.warehouse_adapters import DuckDBAdapter
from tests.test_dbt_catalog_and_gap import _build_synthetic_catalog

TOKEN = "day4-acceptance-token"


def test_request_id_is_propagated_in_headers_and_recorded_in_gap_log(tmp_path: Path) -> None:
    gap_log = tmp_path / "gap_events.jsonl"
    tracker = ObservabilityTracker(gap_log_path=gap_log, langfuse_public_key="", langfuse_secret_key="")
    app = create_app(catalog=_build_synthetic_catalog(), internal_token=TOKEN, tracker=tracker)
    client = TestClient(app)

    # 1. Explicit X-Request-Id is echoed back and persisted in JSONL
    resp = client.post(
        "/v1/query-spec/draft-and-classify",
        json={"question": "請提供各月營業收入"},
        headers={"X-Internal-Token": TOKEN, "X-Request-Id": "req-day4-trace-001"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("x-request-id") == "req-day4-trace-001"

    records = [json.loads(line) for line in gap_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(records) == 1
    assert records[0].get("request_id") == "req-day4-trace-001"

    # 2. Missing X-Request-Id gets auto-generated
    health_resp = client.get("/health")
    assert health_resp.status_code == 200
    assert health_resp.headers.get("x-request-id")


def test_acceptance_matrix_security_and_governance_negatives(tmp_path: Path) -> None:
    """Consolidated MD Section 15 security, fanout, fail-loud, and honest-status acceptance checks."""
    df = pd.DataFrame(
        {
            "tenant_id": [1, 1, 2],
            "year_month": ["2026-01", "2026-02", "2026-01"],
            "ledger_amount": [1000.0, 2000.0, 88888.0],
            "order_volume": [5, 10, 50],
        }
    )
    tracker = ObservabilityTracker(
        gap_log_path=tmp_path / "gap.jsonl", langfuse_public_key="", langfuse_secret_key=""
    )
    app = create_app(
        catalog=_build_synthetic_catalog(),
        internal_token=TOKEN,
        warehouse_adapter=DuckDBAdapter(dataframes={"fct_monthly_ledger": df}),
        tracker=tracker,
    )
    client = TestClient(app)
    headers = {"X-Internal-Token": TOKEN}

    # Unauthenticated / wrong token denied
    assert client.post("/v1/catalog/search", json={"query": "ledger"}).status_code == 401

    # DDL / DML / file-I/O denied
    for bad_sql in (
        "DROP TABLE fct_monthly_ledger",
        "DELETE FROM fct_monthly_ledger",
        "SELECT * FROM read_csv('passwords.csv')",
    ):
        r = client.post(
            "/v1/sql/validate-and-reconcile",
            json={"sql": bad_sql, "tenant_id": 1},
            headers=headers,
        )
        assert r.status_code == 200
        assert r.json()["status"] == "BLOCKED"

    # Cross-tenant denied
    r_cross = client.post(
        "/v1/sql/validate-and-reconcile",
        json={"sql": "SELECT * FROM fct_monthly_ledger WHERE tenant_id = 2", "tenant_id": 1},
        headers=headers,
    )
    assert r_cross.json()["status"] == "BLOCKED"
    assert "CROSS_TENANT_VIOLATION" in r_cross.json()["reason_codes"]

    # Ambiguous business term requires clarification
    r_ambig = client.post(
        "/v1/query-spec/draft-and-classify",
        json={"question": "這個月營收多少"},
        headers=headers,
    )
    assert r_ambig.json()["gap"]["classification"] == "SEMANTIC_AMBIGUITY"

    # Missing customer-level source -> SOURCE_GAP (never fabricated)
    r_src = client.post(
        "/v1/query-spec/draft-and-classify",
        json={"question": "列出每位客戶每筆訂單明細"},
        headers=headers,
    )
    assert r_src.json()["gap"]["classification"] == "SOURCE_GAP"

    # Unapproved external ingestion URL -> BLOCKED
    r_ing = client.post(
        "/v1/ingestion/propose",
        json={
            "source_type": "external_crawler",
            "source_locator": "https://untrusted.example/export",
            "target_bronze_table": "stg_untrusted",
        },
        headers=headers,
    )
    assert r_ing.json()["status"] == "BLOCKED"


def test_conditional_real_local_erp_smoke_integration() -> None:
    """Conditional end-to-end smoke test against local ERP dbt target & read-only DuckDB.

    Skipped automatically in CI when ERP_REPO_DIR is not set; zero private table names hardcoded.
    """
    erp_dir_raw = os.getenv("ERP_REPO_DIR", "").strip()
    if not erp_dir_raw:
        pytest.skip("ERP_REPO_DIR not set; skipping local live ERP smoke test.")

    erp_root = Path(erp_dir_raw)
    target_dir = erp_root / "dataops" / "dbt" / "target"
    duckdb_path = erp_root / "dataops" / "dbt" / "erp_analytics.duckdb"
    assert target_dir.exists(), f"Missing dbt target dir: {target_dir}"
    assert duckdb_path.exists(), f"Missing local DuckDB file: {duckdb_path}"

    catalog = DbtMetadataCatalog.from_target_dir(target_dir)
    assert catalog.summary()["relation_count"] > 0
    assert catalog.summary()["metric_count"] > 0

    # Dynamically pick the first Gold relation that has a tenant_id column (no hardcoded private names)
    gold_rel = next(
        (r for r in catalog.relations if r.layer == "gold" and any(c.name.lower() == "tenant_id" for c in r.columns)),
        None,
    )
    assert gold_rel is not None

    app = create_app(
        catalog=catalog,
        internal_token=TOKEN,
        warehouse_adapter=DuckDBAdapter(db_path=duckdb_path),
    )
    client = TestClient(app)
    headers = {"X-Internal-Token": TOKEN}

    # 1. Cross-tenant query blocked against real ERP table
    cross = client.post(
        "/v1/sql/validate-and-reconcile",
        json={"sql": f'SELECT * FROM "{gold_rel.name}" WHERE tenant_id = 999999', "tenant_id": 1},
        headers=headers,
    )
    assert cross.status_code == 200
    assert cross.json()["status"] == "BLOCKED"
    assert "CROSS_TENANT_VIOLATION" in cross.json()["reason_codes"]

    # 2. Valid read-only query reconciled, confirmed, and executed against real read_only=True DuckDB
    val = client.post(
        "/v1/sql/validate-and-reconcile",
        json={"sql": f'SELECT * FROM "{gold_rel.name}" LIMIT 5', "tenant_id": 1},
        headers=headers,
    )
    assert val.status_code == 200
    val_body = val.json()
    assert val_body["status"] == "VALID"

    spec = val_body["reconciled_spec"]
    spec["review"]["intent_confirmed"] = True
    exec_resp = client.post(
        "/v1/query-spec/execute",
        json={"query_spec": spec, "tenant_id": 1},
        headers=headers,
    )
    assert exec_resp.status_code == 200
    assert exec_resp.json()["dialect"] == "duckdb"
    assert isinstance(exec_resp.json()["rows"], list)

