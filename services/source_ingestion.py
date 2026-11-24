"""Allowlisted Source Discovery, Declarative dlt Proposal & Isolated Bronze Fixture Loader."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import duckdb
import yaml
from pydantic import BaseModel, Field

APPROVED_CONNECTOR_REGISTRY: dict[str, dict[str, str]] = {
    "approved_local_csv": {
        "description": "Reviewed local CSV fixture or finance export file.",
        "extraction_method": "file_batch",
        "idempotency": "replace_by_file_hash",
    },
    "erp_readonly_api": {
        "description": "Authenticated read-only ERP REST report endpoint.",
        "extraction_method": "incremental_api",
        "idempotency": "merge_on_primary_key",
    },
}


class SourceIngestionProposal(BaseModel):
    status: Literal["PROPOSED", "BLOCKED"]
    source_type: str
    source_locator: str
    target_bronze_table: str
    reason_codes: list[str] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)
    dlt_scaffold_py: str = ""
    ingestion_contract_yaml: str = ""
    staging_dbt_sql: str = ""
    isolated_bronze_row_count: int = 0
    isolated_bronze_columns: list[str] = Field(default_factory=list)


def _safe_table_name(name: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_]+", "_", (name or "stg_custom_source").strip().lower()).strip("_")
    return clean if clean.startswith("stg_") else f"stg_{clean}"


def propose_source_ingestion(
    *,
    source_type: str,
    source_locator: str,
    target_bronze_table: str,
    owner: str = "data-engineering@company.com",
    pii_classification: str = "internal_review_required",
    execute_isolated_bronze_load: bool = False,
) -> SourceIngestionProposal:
    """Validate source against allowlisted registry, generate dlt scaffold + contract, and optionally load isolated Bronze."""
    src_type = (source_type or "").strip().lower()
    locator = (source_locator or "").strip()
    bronze_tbl = _safe_table_name(target_bronze_table)

    if src_type not in APPROVED_CONNECTOR_REGISTRY:
        return SourceIngestionProposal(
            status="BLOCKED",
            source_type=src_type,
            source_locator=locator,
            target_bronze_table=bronze_tbl,
            reason_codes=["UNAPPROVED_SOURCE_BLOCKED"],
            messages=[
                f"Connector '{src_type}' is not in the approved registry ({sorted(APPROVED_CONNECTOR_REGISTRY)})."
            ],
        )

    if src_type == "approved_local_csv" and (locator.startswith("http://") or locator.startswith("https://")):
        return SourceIngestionProposal(
            status="BLOCKED",
            source_type=src_type,
            source_locator=locator,
            target_bronze_table=bronze_tbl,
            reason_codes=["UNAPPROVED_SOURCE_BLOCKED"],
            messages=["External HTTP URLs are blocked for approved_local_csv connector."],
        )

    meta = APPROVED_CONNECTOR_REGISTRY[src_type]
    dlt_scaffold = (
        f'"""Declarative dlt pipeline scaffold for {bronze_tbl} (requires owner approval before production)."""\n\n'
        "import dlt\n"
        "import pandas as pd\n\n\n"
        f"@dlt.resource(name='{bronze_tbl}', write_disposition='replace')\n"
        f"def load_{bronze_tbl}(source_path: str = {locator!r}):\n"
        "    df = pd.read_csv(source_path)\n"
        "    yield df.to_dict(orient='records')\n\n\n"
        "def run_isolated_staging_pipeline():\n"
        f"    pipeline = dlt.pipeline(pipeline_name='{bronze_tbl}_pipeline', destination='duckdb', dataset_name='bronze_staging')\n"
        f"    return pipeline.run(load_{bronze_tbl}())\n"
    )

    contract_doc = {
        "version": 1,
        "target_bronze_table": bronze_tbl,
        "source_type": src_type,
        "extraction_method": meta["extraction_method"],
        "idempotency_policy": meta["idempotency"],
        "owner": owner,
        "pii_classification": pii_classification,
        "rollback_plan": f"DROP TABLE IF EXISTS bronze_staging.{bronze_tbl}; revert PR commit.",
        "requires_owner_authorization": True,
    }
    contract_yaml = yaml.safe_dump(contract_doc, sort_keys=False, allow_unicode=True)

    row_count = 0
    columns: list[str] = []
    if execute_isolated_bronze_load and src_type == "approved_local_csv":
        csv_path = Path(locator)
        if not csv_path.exists():
            return SourceIngestionProposal(
                status="BLOCKED",
                source_type=src_type,
                source_locator=locator,
                target_bronze_table=bronze_tbl,
                reason_codes=["FIXTURE_FILE_NOT_FOUND"],
                messages=[f"Local fixture file not found: {csv_path}"],
            )
        con = duckdb.connect(":memory:")
        try:
            con.execute(
                f'CREATE TABLE "{bronze_tbl}" AS SELECT * FROM read_csv_auto(?)',
                [str(csv_path)],
            )
            row_count = int(con.execute(f'SELECT COUNT(*) FROM "{bronze_tbl}"').fetchone()[0])
            desc = con.execute(f'DESCRIBE "{bronze_tbl}"').fetchall()
            columns = [str(r[0]) for r in desc]
        finally:
            con.close()

    col_select = ",\n    ".join(f'"{c}"' for c in columns) if columns else "*"
    staging_sql = (
        f"-- Bronze Staging Model generated from approved connector: {src_type}\n"
        "{{ config(materialized='view') }}\n\n"
        f"SELECT\n    {col_select}\n"
        f"FROM {{{{ source('bronze_staging', '{bronze_tbl}') }}}}\n"
    )

    return SourceIngestionProposal(
        status="PROPOSED",
        source_type=src_type,
        source_locator=locator,
        target_bronze_table=bronze_tbl,
        messages=[f"Generated governed dlt proposal for {bronze_tbl}."],
        dlt_scaffold_py=dlt_scaffold,
        ingestion_contract_yaml=contract_yaml,
        staging_dbt_sql=staging_sql,
        isolated_bronze_row_count=row_count,
        isolated_bronze_columns=columns,
    )
