"""Shared DuckDB read-only execution & Kimball schema reflection service."""

from __future__ import annotations

import json
import re
import time
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


def _is_transient_gemini_error(exc: Exception) -> bool:
    """Return True only for temporary Gemini overload/rate-limit errors worth a short retry."""
    msg = str(exc).upper()
    return any(tok in msg for tok in ("503", "UNAVAILABLE", "HIGH DEMAND", "429", "RESOURCE_EXHAUSTED"))


def generate_gemini_text(
    api_key: str,
    prompt: str,
    model: str = "gemini-3.5-flash-lite",
    max_retries: int = 2,
    retry_base_delay_s: float = 1.0,
) -> str:
    """Call Google Gemini using google-genai SDK or direct REST API if SDK is not installed in environment.

    Retries up to ``max_retries`` times with short exponential backoff ONLY on transient
    503/429 overload responses, and remains strictly Fail-Loud if all attempts fail.
    """
    clean_key = (api_key or "").strip()
    if not clean_key:
        raise ValueError("Gemini API Key is empty.")

    def _invoke_once() -> str:
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

    attempts = max(1, int(max_retries) + 1)
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return _invoke_once()
        except Exception as exc:
            last_exc = exc
            if attempt < attempts - 1 and _is_transient_gemini_error(exc):
                time.sleep(retry_base_delay_s * (2 ** attempt))
                continue
            raise
    assert last_exc is not None
    raise last_exc


def generate_gemini_structured(
    api_key: str,
    prompt: str,
    response_model: type[Any],
    model: str = "gemini-3.5-flash-lite",
    max_retries: int = 2,
    retry_base_delay_s: float = 1.0,
) -> Any:
    """Call Google Gemini with native Pydantic Structured Output (response_schema).

    Uses ``google-genai`` SDK ``GenerateContentConfig(response_mime_type="application/json",
    response_schema=response_model)`` when available, falling back to JSON extraction +
    Pydantic validation via REST. Strictly Fail-Loud if all attempts fail.
    """
    clean_key = (api_key or "").strip()
    if not clean_key:
        raise ValueError("Gemini API Key is empty.")

    def _invoke_once() -> Any:
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=clean_key)
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=response_model,
                ),
            )
            parsed = getattr(resp, "parsed", None)
            if parsed is not None:
                return response_model.model_validate(parsed)
            raw_text = (resp.text or "").strip()
            if not raw_text:
                raise RuntimeError("Gemini returned empty structured response.")
            return response_model.model_validate_json(raw_text)
        except ImportError:
            schema_hint = json.dumps(response_model.model_json_schema(), ensure_ascii=False)
            raw = generate_gemini_text(
                api_key=clean_key,
                prompt=f"{prompt}\n\nReturn ONLY valid JSON matching this schema:\n{schema_hint}",
                model=model,
                max_retries=0,
            )
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE)
            return response_model.model_validate_json(cleaned)

    attempts = max(1, int(max_retries) + 1)
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return _invoke_once()
        except Exception as exc:
            last_exc = exc
            if attempt < attempts - 1 and _is_transient_gemini_error(exc):
                time.sleep(retry_base_delay_s * (2 ** attempt))
                continue
            raise
    assert last_exc is not None
    raise last_exc
