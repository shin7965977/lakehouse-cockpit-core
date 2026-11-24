"""Vertical-slice TDD tests for Seam 3: Approved Source Registry, dlt Proposal & Isolated Bronze Load."""

from __future__ import annotations

from pathlib import Path

from services.source_ingestion import propose_source_ingestion


def test_blocks_unapproved_external_source_url() -> None:
    res = propose_source_ingestion(
        source_type="http_scrape",
        source_locator="https://evil-unverified-site.example/dump.csv",
        target_bronze_table="stg_external_dump",
    )
    assert res.status == "BLOCKED"
    assert "UNAPPROVED_SOURCE_BLOCKED" in res.reason_codes
    assert res.dlt_scaffold_py == ""


def test_approved_local_csv_generates_dlt_scaffold_contract_and_loads_isolated_bronze(
    tmp_path: Path,
) -> None:
    csv_file = tmp_path / "customer_returns_fixture.csv"
    csv_file.write_text(
        "tenant_id,order_id,customer_code,return_reason\n"
        "1,ORD-101,CUST-A,damaged_box\n"
        "1,ORD-102,CUST-B,wrong_size\n",
        encoding="utf-8",
    )

    res = propose_source_ingestion(
        source_type="approved_local_csv",
        source_locator=str(csv_file),
        target_bronze_table="stg_customer_returns",
        execute_isolated_bronze_load=True,
    )
    assert res.status == "PROPOSED"
    assert "import dlt" in res.dlt_scaffold_py
    assert "stg_customer_returns" in res.staging_dbt_sql
    assert "pii_classification" in res.ingestion_contract_yaml
    assert res.isolated_bronze_row_count == 2
    assert set(res.isolated_bronze_columns) == {
        "tenant_id",
        "order_id",
        "customer_code",
        "return_reason",
    }
