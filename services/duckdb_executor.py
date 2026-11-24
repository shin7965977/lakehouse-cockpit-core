"""Shared DuckDB read-only execution & Kimball schema reflection service."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

import pandas as pd

_FORBIDDEN_SQL_RE = re.compile(
    r"\b(DROP|DELETE|INSERT|UPDATE|ALTER|CREATE|TRUNCATE|ATTACH|DETACH|COPY|EXPORT|IMPORT|PRAGMA|CALL)\b",
    re.IGNORECASE,
)

_PII_KEYWORDS = ("email", "phone", "address", "password", "ssn", "credit_card", "passport")


def validate_readonly_sql(sql: str) -> str | None:
    """Validate that a SQL query is non-empty and strictly read-only.

    Returns an error string if invalid, or ``None`` if safe to execute.
    """
    cleaned = (sql or "").strip()
    if not cleaned:
        return "SQL query cannot be empty."
    match = _FORBIDDEN_SQL_RE.search(cleaned)
    if match:
        return f"Read-only guardrail blocked forbidden SQL keyword: {match.group(1).upper()}"
    return None


def detect_pii_columns(df: pd.DataFrame) -> list[str]:
    """Return column names in ``df`` that match common PII naming patterns."""
    if df is None or df.empty:
        return []
    return [c for c in df.columns if any(k in str(c).lower() for k in _PII_KEYWORDS)]


def execute_readonly_duckdb(
    sql: str,
    data_dict: dict[str, pd.DataFrame],
) -> tuple[pd.DataFrame, str]:
    """Execute a validated read-only SQL query against in-memory DataFrames via DuckDB."""
    err = validate_readonly_sql(sql)
    if err:
        return pd.DataFrame(), err

    import duckdb

    con = duckdb.connect(":memory:")
    try:
        for tbl_name, df in (data_dict or {}).items():
            if isinstance(df, pd.DataFrame) and not df.empty:
                con.register(tbl_name, df)
        res_df = con.execute(sql).df()
        return res_df, ""
    except Exception as exc:
        return pd.DataFrame(), str(exc)
    finally:
        con.close()


class DatabaseReflectionService:
    """Inspects in-memory Lakehouse DataFrames to expose Kimball metadata and column values."""

    def __init__(self, data_dict: dict[str, pd.DataFrame]) -> None:
        self.data_dict: dict[str, pd.DataFrame] = {
            k: v for k, v in (data_dict or {}).items() if isinstance(v, pd.DataFrame) and not v.empty
        }

    def get_kimball_models(self) -> dict[str, list[str]]:
        dims = [k for k in self.data_dict if k.startswith("dim_") or "product" in k or "customer" in k]
        facts = [k for k in self.data_dict if k.startswith("fct_") or k.startswith("fact_") or "order" in k]
        return {
            "dimensions": dims or ["dim_products", "dim_regions"],
            "facts": facts or ["fact_orders"],
        }

    def get_schema_summary(self) -> str:
        lines: list[str] = []
        for tbl, df in self.data_dict.items():
            cols = ", ".join(f"{c} ({df[c].dtype})" for c in df.columns[:15])
            lines.append(f"- {tbl} ({len(df)} rows): {cols}")
        return "\n".join(lines) or "- (no tables loaded)"

    def get_distinct_column_values(self, table_name: str, column_name: str) -> list[str]:
        df = self.data_dict.get(table_name)
        if df is None or column_name not in df.columns:
            return []
        return [str(v) for v in df[column_name].dropna().unique().tolist()[:50]]

    def get_table_stats(self) -> list[dict[str, Any]]:
        return [
            {"table": tbl, "rows": len(df), "columns": len(df.columns)}
            for tbl, df in self.data_dict.items()
        ]


def generate_gemini_text(
    api_key: str,
    prompt: str,
    model: str = "gemini-3.5-flash-lite",
) -> str:
    """Call Google Gemini using google-genai SDK or direct REST API if SDK is not installed in environment."""
    clean_key = (api_key or "").strip()
    if not clean_key:
        raise ValueError("Gemini API Key is empty.")

    try:
        from google import genai

        client = genai.Client(api_key=clean_key)
        resp = client.models.generate_content(model=model, contents=prompt)
        return (resp.text or "").strip()
    except ImportError:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={clean_key}"
        payload = json.dumps(
            {"contents": [{"parts": [{"text": prompt}]}]}
        ).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=25) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as http_err:
            err_detail = http_err.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Gemini REST HTTP {http_err.code}: {err_detail}") from http_err

        candidates = body.get("candidates") or []
        if not candidates:
            raise RuntimeError(f"Gemini returned no candidates: {body}")
        parts = candidates[0].get("content", {}).get("parts") or []
        return "".join(str(p.get("text", "")) for p in parts).strip()
