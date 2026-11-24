"""Bounded SQL Auto-Repair Loop with Mandatory Security Re-Validation & Re-Approval."""

from __future__ import annotations

import re
from typing import Callable

from pydantic import BaseModel, Field

try:
    from .duckdb_executor import generate_gemini_text
    from .query_spec import QuerySpec, diff_sql
    from .sql_validator import validate_and_reconcile_sql
    from .warehouse_adapters import WarehouseAdapter, WarehouseAdapterError
except (ImportError, ValueError):
    try:
        from services.duckdb_executor import generate_gemini_text
        from services.query_spec import QuerySpec, diff_sql
        from services.sql_validator import validate_and_reconcile_sql
        from services.warehouse_adapters import WarehouseAdapter, WarehouseAdapterError
    except (ImportError, ValueError):
        from cockpit_core.services.duckdb_executor import generate_gemini_text
        from cockpit_core.services.query_spec import QuerySpec, diff_sql
        from cockpit_core.services.sql_validator import validate_and_reconcile_sql
        from cockpit_core.services.warehouse_adapters import WarehouseAdapter, WarehouseAdapterError


class RepairError(RuntimeError):
    """Explicit Fail-Loud error when bounded SQL repair cannot run."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SqlRepairResult(BaseModel):
    repaired: bool
    attempts: int
    error_class: str
    original_sql: str
    repaired_sql: str = ""
    sql_diff: str = ""
    reconciled_spec: QuerySpec | None = None
    failure_codes: list[str] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)


def classify_sql_error(error_message: str) -> str:
    low = (error_message or "").lower()
    if "binder error" in low or "column" in low and ("not found" in low or "does not exist" in low):
        return "UNKNOWN_COLUMN"
    if "syntax" in low or "parser error" in low:
        return "SYNTAX_ERROR"
    if "function" in low and ("not exist" in low or "unsupported" in low):
        return "UNSUPPORTED_DIALECT_FUNCTION"
    return "EXECUTION_ERROR"


def _strip_sql_fences(raw: str) -> str:
    cleaned = (raw or "").strip()
    m = re.search(r"```(?:sql)?\s*(.*?)```", cleaned, re.DOTALL | re.IGNORECASE)
    if m:
        cleaned = m.group(1).strip()
    return cleaned.rstrip(";").strip()


def repair_sql_bounded(
    broken_sql: str,
    error_message: str,
    *,
    base_spec: QuerySpec,
    allowed_relations: set[str],
    adapter: WarehouseAdapter,
    server_tenant_id: int | str | None = None,
    tenant_scoped_relations: set[str] | None = None,
    one_to_many_joins: set[tuple[str, str]] | None = None,
    schema_hint: str = "",
    api_key: str = "",
    llm_callable: Callable[[str], str] | None = None,
    max_attempts: int = 2,
) -> SqlRepairResult:
    """Attempt at most `max_attempts` (capped at 2) bounded SQL repairs with full security re-validation."""
    if llm_callable is None and not (api_key or "").strip():
        raise RepairError(
            "API_KEY_MISSING",
            "⚠️ Bounded SQL Auto-Repair requires a Gemini API Key (silent offline repair is disabled).",
        )

    bounded_attempts = max(1, min(2, int(max_attempts)))
    err_class = classify_sql_error(error_message)
    current_sql = (broken_sql or "").strip()
    current_err = error_message
    failure_codes: list[str] = []
    messages: list[str] = []

    for attempt in range(1, bounded_attempts + 1):
        prompt = (
            "You are a Read-Only SQL Repair Engineer.\n"
            f"Dialect: {base_spec.dialect}\n"
            f"Error Class: {err_class}\n"
            f"Error Message: {current_err}\n"
            f"Allowed Tables: {sorted(allowed_relations)}\n"
            f"Schema Hint:\n{schema_hint or '(use only verified columns)'}\n\n"
            f"Broken SQL:\n{current_sql}\n\n"
            "Rules:\n"
            "1. Return ONLY a single valid read-only SELECT statement.\n"
            "2. NEVER remove, weaken, or alter tenant_id restrictions.\n"
            "3. Preserve original business intent and grain.\n"
        )
        try:
            raw_reply = (
                llm_callable(prompt)
                if llm_callable is not None
                else generate_gemini_text(api_key=api_key, prompt=prompt)
            )
        except Exception as exc:
            raise RepairError("LLM_CALL_FAILED", f"Gemini SQL repair call failed: {exc}") from exc

        candidate_sql = _strip_sql_fences(raw_reply)
        val = validate_and_reconcile_sql(
            candidate_sql,
            base_spec,
            allowed_relations=allowed_relations,
            server_tenant_id=server_tenant_id,
            tenant_scoped_relations=tenant_scoped_relations,
            one_to_many_joins=one_to_many_joins,
        )
        if val.status == "BLOCKED":
            failure_codes.extend(val.reason_codes)
            messages.append(f"Attempt {attempt} blocked by security gate: {val.reason_codes}")
            current_err = "; ".join(val.messages)
            continue

        try:
            adapter.explain(val.executable_sql)
        except WarehouseAdapterError as exc:
            failure_codes.append(exc.code)
            messages.append(f"Attempt {attempt} EXPLAIN failed: {exc.message}")
            current_sql = candidate_sql
            current_err = exc.message
            continue

        # Ensure confirmation is explicitly cleared so user must review diff & re-confirm
        reconciled = val.reconciled_spec
        if reconciled is not None and reconciled.is_confirmation_valid():
            reconciled = reconciled.with_updates(raw_sql_override=candidate_sql)

        return SqlRepairResult(
            repaired=True,
            attempts=attempt,
            error_class=err_class,
            original_sql=broken_sql,
            repaired_sql=candidate_sql,
            sql_diff=diff_sql(broken_sql, candidate_sql),
            reconciled_spec=reconciled,
            messages=[f"Repaired and verified via EXPLAIN on attempt {attempt}. Re-confirmation required."],
        )

    return SqlRepairResult(
        repaired=False,
        attempts=bounded_attempts,
        error_class=err_class,
        original_sql=broken_sql,
        failure_codes=list(dict.fromkeys(failure_codes)) or ["REPAIR_EXHAUSTED"],
        messages=messages or ["Bounded SQL repair exhausted maximum attempts."],
    )
