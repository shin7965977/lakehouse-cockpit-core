"""Unit & live smoke tests for services/langfuse_tracer.py (Langfuse v5 OTel integration)."""

from __future__ import annotations

import os
from dotenv import load_dotenv

from services.langfuse_tracer import (
    get_langfuse_status,
    is_langfuse_configured,
    record_trace_score,
    redact_for_trace,
    trace_generation,
    trace_span,
    trace_turn,
)

load_dotenv(".env")


def test_redact_for_trace_strips_secrets_and_pii() -> None:
    payload = {
        "api_key": "AQ.Ab8RN6_secret_value",
        "database_url": "postgresql://user:pass@ep-host.neon.tech/neondb",
        "customer_email": "alice@example.com",
        "sql": "SELECT customer_tier, SUM(total_revenue) FROM mart_customer_value GROUP BY 1",
        "note": "leaked sk-lf-1234567890abcdef and pk-lf-abcdef123456 and alice@example.com",
    }
    redacted = redact_for_trace(payload)
    assert redacted["api_key"] == "<REDACTED>"
    assert redacted["database_url"] == "<REDACTED>"
    assert redacted["customer_email"] == "<REDACTED>"
    assert "sk-lf-" not in redacted["note"]
    assert "pk-lf-" not in redacted["note"]
    assert "alice@example.com" not in redacted["note"]
    assert "SELECT customer_tier" in redacted["sql"]


def test_tracer_is_safe_noop_when_unconfigured(monkeypatch) -> None:
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)

    st = get_langfuse_status()
    assert st["status"] == "NOT_CONFIGURED"
    assert st["enabled"] is False

    with trace_turn(name="noop_turn", session_id="s1", input_data={"q": "hi"}) as obs:
        with trace_generation(name="noop_gen", model="gemini-3.7-flash", prompt_text="hello") as gen:
            gen.update(output="ok")
        with trace_span(name="noop_tool", as_type="tool") as sp:
            sp.update(output={"rows": 1})
        record_trace_score(name="sql_first_pass_ok", value=1)
        obs.update(output={"done": True})


def test_live_langfuse_cloud_auth_and_trace_smoke() -> None:
    if not is_langfuse_configured():
        return
    st = get_langfuse_status()
    assert st["status"] == "CONNECTED", f"Expected CONNECTED, got {st}"

    with trace_turn(
        name="workbench_langfuse_smoke_test",
        session_id="pytest_smoke_session",
        input_data={"question": "Langfuse v5 integration smoke check"},
        metadata={"model": "gemini-3.7-flash", "source": "pytest"},
        tags=["smoke_test", "langfuse_v5"],
    ) as obs:
        with trace_generation(
            name="gemini_smoke_gen",
            model="gemini-3.7-flash",
            prompt_text="Reply OK",
        ) as gen:
            gen.update(output="OK", usage_details={"input": 4, "output": 1, "total": 5})
        record_trace_score(
            name="sql_first_pass_ok",
            value=1,
            data_type="NUMERIC",
            comment="Langfuse v5 live smoke verified",
        )
        obs.update(output={"status": "ok"})
