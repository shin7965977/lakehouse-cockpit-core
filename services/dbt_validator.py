"""Isolated dbt / DuckDB Asset Materialization, Contract Assertions & Golden SQL Parity."""

from __future__ import annotations

import math
from pathlib import Path

import duckdb
import pandas as pd
from pydantic import BaseModel, Field


class AssetValidationCheck(BaseModel):
    name: str
    passed: bool
    detail: str


class IsolatedValidationResult(BaseModel):
    model_name: str
    passed: bool
    materialized_rows: int = 0
    columns: list[str] = Field(default_factory=list)
    checks: list[AssetValidationCheck] = Field(default_factory=list)
    preview_rows: list[dict[str, object]] = Field(default_factory=list)


def validate_isolated_dbt_asset(
    *,
    model_name: str,
    candidate_sql: str,
    source_dataframes: dict[str, pd.DataFrame] | None = None,
    source_duckdb_path: str | Path | None = None,
    grain: list[str] | None = None,
    not_null_columns: list[str] | None = None,
    golden_sql: str | None = None,
    reconcile_column: str | None = None,
    tolerance: float = 0.01,
) -> IsolatedValidationResult:
    """Materialize candidate SQL inside an isolated in-memory DuckDB and run contract + Golden SQL checks."""
    checks: list[AssetValidationCheck] = []
    con = duckdb.connect(":memory:")
    try:
        # Attach read-only source DuckDB (if provided) without ever writing to it
        if source_duckdb_path and Path(source_duckdb_path).exists():
            con.execute(f"ATTACH '{Path(source_duckdb_path)}' AS _src (READ_ONLY)")
            tables = con.execute(
                "SELECT table_schema, table_name FROM information_schema.tables WHERE table_catalog = '_src'"
            ).fetchall()
            for sch, tbl in tables:
                con.execute(f'CREATE OR REPLACE VIEW "{tbl}" AS SELECT * FROM _src."{sch}"."{tbl}"')

        for tbl_name, df in (source_dataframes or {}).items():
            if isinstance(df, pd.DataFrame):
                con.register(tbl_name, df)

        clean_sql = (candidate_sql or "").strip().rstrip(";")
        try:
            con.execute(f'CREATE TABLE "{model_name}" AS {clean_sql}')
            row_count = int(con.execute(f'SELECT COUNT(*) FROM "{model_name}"').fetchone()[0])
            desc = con.execute(f'DESCRIBE "{model_name}"').fetchall()
            cols = [str(r[0]) for r in desc]
            checks.append(
                AssetValidationCheck(
                    name="isolated_materialization",
                    passed=row_count > 0,
                    detail=f"Materialized {row_count} rows in isolated sandbox."
                    if row_count > 0
                    else "Materialized 0 rows (Fail-Loud).",
                )
            )
        except Exception as exc:
            checks.append(
                AssetValidationCheck(
                    name="isolated_materialization",
                    passed=False,
                    detail=f"Materialization failed: {exc}",
                )
            )
            return IsolatedValidationResult(model_name=model_name, passed=False, checks=checks)

        # 2. not_null contract checks
        for col in not_null_columns or []:
            if col not in cols:
                checks.append(
                    AssetValidationCheck(
                        name=f"not_null:{col}",
                        passed=False,
                        detail=f"Required column '{col}' missing from materialized relation.",
                    )
                )
                continue
            null_cnt = int(
                con.execute(f'SELECT COUNT(*) FROM "{model_name}" WHERE "{col}" IS NULL').fetchone()[0]
            )
            checks.append(
                AssetValidationCheck(
                    name=f"not_null:{col}",
                    passed=null_cnt == 0,
                    detail=f"{null_cnt} NULL values found in '{col}'.",
                )
            )

        # 3. Composite grain uniqueness check
        if grain:
            missing_grain = [g for g in grain if g not in cols]
            if missing_grain:
                checks.append(
                    AssetValidationCheck(
                        name="grain_uniqueness",
                        passed=False,
                        detail=f"Grain column(s) missing: {missing_grain}",
                    )
                )
            else:
                grp = ", ".join(f'"{g}"' for g in grain)
                dup_groups = int(
                    con.execute(
                        f'SELECT COUNT(*) FROM (SELECT {grp} FROM "{model_name}" GROUP BY {grp} HAVING COUNT(*) > 1) _d'
                    ).fetchone()[0]
                )
                checks.append(
                    AssetValidationCheck(
                        name="grain_uniqueness",
                        passed=dup_groups == 0,
                        detail=(
                            f"Grain ({', '.join(grain)}) is 100% unique."
                            if dup_groups == 0
                            else f"Found {dup_groups} duplicate group(s) violating grain ({', '.join(grain)})."
                        ),
                    )
                )

        # 4. Golden SQL numeric parity check
        if golden_sql and reconcile_column:
            try:
                cand_sum = con.execute(
                    f'SELECT COALESCE(SUM(CAST("{reconcile_column}" AS DOUBLE)), 0) FROM "{model_name}"'
                ).fetchone()[0]
                gold_clean = golden_sql.strip().rstrip(";")
                gold_sum = con.execute(
                    f'SELECT COALESCE(SUM(CAST("{reconcile_column}" AS DOUBLE)), 0) FROM ({gold_clean}) _g'
                ).fetchone()[0]
                diff_val = abs(float(cand_sum) - float(gold_sum))
                parity_ok = math.isclose(float(cand_sum), float(gold_sum), abs_tol=tolerance)
                checks.append(
                    AssetValidationCheck(
                        name="golden_sql_parity",
                        passed=parity_ok,
                        detail=f"candidate={cand_sum:.2f}, golden={gold_sum:.2f}, diff={diff_val:.4f}",
                    )
                )
            except Exception as exc:
                checks.append(
                    AssetValidationCheck(
                        name="golden_sql_parity",
                        passed=False,
                        detail=f"Golden SQL parity check failed: {exc}",
                    )
                )

        preview_df = con.execute(f'SELECT * FROM "{model_name}" LIMIT 10').df()
        safe_preview = preview_df.where(pd.notnull(preview_df), None).to_dict(orient="records")
        all_passed = all(c.passed for c in checks)
        return IsolatedValidationResult(
            model_name=model_name,
            passed=all_passed,
            materialized_rows=row_count,
            columns=cols,
            checks=checks,
            preview_rows=safe_preview,
        )
    finally:
        con.close()
