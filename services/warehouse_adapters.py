"""Read-Only Warehouse Adapters (DuckDB Sandbox + Fail-Loud PostgreSQL Contract)."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any, Protocol

import duckdb
import pandas as pd
import sqlglot
from pydantic import BaseModel, Field
from sqlglot import exp


class WarehouseAdapterError(RuntimeError):
    """Explicit Fail-Loud warehouse adapter error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ExecutionResult(BaseModel):
    dialect: str
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    explain_plan: str = ""
    source_label: str = ""


class WarehouseAdapter(Protocol):
    dialect: str

    def capabilities(self) -> dict[str, Any]: ...

    def explain(self, sql: str) -> str: ...

    def execute_readonly(self, sql: str, max_rows: int = 100) -> ExecutionResult: ...


_DUCKDB_ONLY_FUNCTIONS = {
    "read_csv",
    "read_csv_auto",
    "read_parquet",
    "read_json",
    "strftime",
    "strptime",
    "list_value",
    "struct_pack",
    "arg_max",
    "arg_min",
}


class DuckDBAdapter:
    """Read-only DuckDB sandbox adapter (supports in-memory DataFrames or read_only=True .duckdb file)."""

    dialect = "duckdb"

    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        dataframes: dict[str, pd.DataFrame] | None = None,
    ) -> None:
        self.db_path = Path(db_path) if db_path else None
        self.dataframes = dataframes or {}

    def capabilities(self) -> dict[str, Any]:
        return {
            "dialect": "duckdb",
            "read_only": True,
            "supports_explain": True,
            "mode": "file_readonly" if self.db_path else "in_memory",
        }

    def _open_connection(self) -> duckdb.DuckDBPyConnection:
        if self.db_path is not None:
            if not self.db_path.exists():
                raise WarehouseAdapterError(
                    "DUCKDB_FILE_NOT_FOUND",
                    f"DuckDB database file not found: {self.db_path}",
                )
            con = duckdb.connect(str(self.db_path), read_only=True)
            schemas = [
                str(r[0])
                for r in con.execute(
                    "SELECT DISTINCT table_schema FROM information_schema.tables "
                    "WHERE table_schema NOT IN ('information_schema', 'pg_catalog')"
                ).fetchall()
            ]
            # Prioritize gold -> silver -> bronze -> main so dbt models resolve without schema prefix
            schemas.sort(
                key=lambda s: (
                    0 if "gold" in s.lower() else 1 if "silver" in s.lower() else 2 if "bronze" in s.lower() else 3,
                    s,
                )
            )
            if schemas:
                quoted = ",".join(f'"{s}"' for s in schemas)
                con.execute(f"SET search_path = '{quoted}'")
            return con
        con = duckdb.connect(":memory:")
        for tbl_name, df in self.dataframes.items():
            if isinstance(df, pd.DataFrame):
                con.register(tbl_name, df)
        return con

    def explain(self, sql: str) -> str:
        clean_sql = (sql or "").strip().rstrip(";")
        con = self._open_connection()
        try:
            plan_rows = con.execute(f"EXPLAIN {clean_sql}").fetchall()
            return "\n".join(str(r[-1]) for r in plan_rows)
        except Exception as exc:
            raise WarehouseAdapterError("EXPLAIN_FAILED", f"DuckDB EXPLAIN failed: {exc}") from exc
        finally:
            con.close()

    def execute_readonly(self, sql: str, max_rows: int = 100) -> ExecutionResult:
        clean_sql = (sql or "").strip().rstrip(";")
        bounded_limit = max(1, min(10000, int(max_rows)))
        con = self._open_connection()
        try:
            plan_rows = con.execute(f"EXPLAIN {clean_sql}").fetchall()
            explain_plan = "\n".join(str(r[-1]) for r in plan_rows)
            df = con.execute(
                f"SELECT * FROM ({clean_sql}) AS _ae_sandbox LIMIT {bounded_limit}"
            ).df()
            # Convert NaN/Timestamps to JSON-safe values
            safe_df = df.where(pd.notnull(df), None)
            rows = safe_df.to_dict(orient="records")
            return ExecutionResult(
                dialect="duckdb",
                columns=list(df.columns),
                rows=rows,
                row_count=len(rows),
                explain_plan=explain_plan,
                source_label=str(self.db_path) if self.db_path else "duckdb://in-memory",
            )
        except Exception as exc:
            raise WarehouseAdapterError(
                "EXECUTION_FAILED", f"DuckDB read-only execution failed: {exc}"
            ) from exc
        finally:
            con.close()


class PostgresAdapter:
    """PostgreSQL dialect validator & Fail-Loud read-only adapter (never silently falls back)."""

    dialect = "postgres"

    def __init__(self, *, database_url: str | None = None) -> None:
        self.database_url = (
            database_url
            if database_url is not None
            else os.getenv("AE_ISOLATED_POSTGRES_URL", "")
        ).strip()

    def capabilities(self) -> dict[str, Any]:
        return {
            "dialect": "postgres",
            "read_only": True,
            "driver_installed": bool(
                importlib.util.find_spec("psycopg") or importlib.util.find_spec("psycopg2")
            ),
            "isolated_url_configured": bool(self.database_url),
            "unsupported_duckdb_functions": sorted(_DUCKDB_ONLY_FUNCTIONS),
        }

    def transpile_and_check(self, sql: str, *, source_dialect: str = "duckdb") -> tuple[str, list[str]]:
        """Transpile SQL to Postgres via SQLGlot and return any unsupported DuckDB-only functions."""
        try:
            tree = sqlglot.parse_one((sql or "").strip(), read=source_dialect)
        except Exception as exc:
            raise WarehouseAdapterError(
                "TRANSPILE_PARSE_ERROR", f"Failed to parse SQL for Postgres transpilation: {exc}"
            ) from exc

        unsupported: list[str] = []
        raw_low = (sql or "").lower()
        for fn_tok in sorted(_DUCKDB_ONLY_FUNCTIONS):
            if f"{fn_tok}(" in raw_low and fn_tok not in unsupported:
                unsupported.append(fn_tok)
        for fn in tree.find_all(exp.Anonymous, exp.Func):
            name = (
                fn.name.lower()
                if isinstance(fn, exp.Anonymous)
                else fn.sql_name().lower()
            )
            if name in _DUCKDB_ONLY_FUNCTIONS and name not in unsupported:
                unsupported.append(name)
        return tree.sql(dialect="postgres"), unsupported

    def explain(self, sql: str) -> str:
        self._assert_ready()
        raise WarehouseAdapterError("POSTGRES_NOT_CONNECTED", "Isolated PostgreSQL connection not active.")

    def execute_readonly(self, sql: str, max_rows: int = 100) -> ExecutionResult:
        self._assert_ready()
        raise WarehouseAdapterError("POSTGRES_NOT_CONNECTED", "Isolated PostgreSQL connection not active.")

    def _assert_ready(self) -> None:
        if not self.database_url:
            raise WarehouseAdapterError(
                "POSTGRES_NOT_CONFIGURED",
                "AE_ISOLATED_POSTGRES_URL is not configured. Refusing to connect to production database or fall back silently.",
            )
        if not (importlib.util.find_spec("psycopg") or importlib.util.find_spec("psycopg2")):
            raise WarehouseAdapterError(
                "POSTGRES_DRIVER_MISSING",
                "PostgreSQL driver (`psycopg` or `psycopg2`) is not installed.",
            )
