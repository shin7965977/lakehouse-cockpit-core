"""Langfuse v5 OpenTelemetry Observability Wrapper with PII/Secret Redaction.

Best-practice instrumentation for the AI Analytics Workbench:
- Syncs Streamlit Secrets or `.env` into process environment before initializing Langfuse.
- Redacts API keys, database URLs, and PII fields before any observation leaves the process.
- Uses Langfuse v5 native APIs (`get_client`, `start_as_current_observation`,
  `propagate_attributes`, `score_current_trace`, `flush`).
- Non-blocking & Fail-Honest: never crashes user workflows when Langfuse is unconfigured
  or unreachable, while surfacing an honest status (`CONNECTED` / `NOT_CONFIGURED` / `DEGRADED`).
"""

from __future__ import annotations

from contextlib import contextmanager
import os
import re
from typing import Any, Callable, Iterator, Literal

_SECRET_PATTERNS = [
    re.compile(r"(sk-lf-[A-Za-z0-9\-_]+)"),
    re.compile(r"(pk-lf-[A-Za-z0-9\-_]+)"),
    re.compile(r"(AQ\.[A-Za-z0-9\-_.]+)"),
    re.compile(r"(AIza[A-Za-z0-9\-_]{20,})"),
    re.compile(r"(postgres(?:ql)?://[^\s\"']+)"),
    re.compile(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"),
]

_SENSITIVE_KEY_TOKENS = (
    "api_key",
    "secret",
    "password",
    "token",
    "database_url",
    "email",
    "phone",
    "ssn",
    "address",
)

_MAX_STR_LEN = 4000


def redact_for_trace(value: Any, *, _depth: int = 0) -> Any:
    """Recursively redact secrets and PII before sending payloads to Langfuse."""
    if _depth > 6:
        return "<TRUNCATED_DEPTH>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        out = value
        for pat in _SECRET_PATTERNS:
            out = pat.sub("<REDACTED>", out)
        if len(out) > _MAX_STR_LEN:
            out = out[:_MAX_STR_LEN] + "...<TRUNCATED>"
        return out
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for k, v in value.items():
            k_low = str(k).lower()
            if any(tok in k_low for tok in _SENSITIVE_KEY_TOKENS):
                cleaned[str(k)] = "<REDACTED>"
            else:
                cleaned[str(k)] = redact_for_trace(v, _depth=_depth + 1)
        return cleaned
    if isinstance(value, (list, tuple)):
        items = [redact_for_trace(item, _depth=_depth + 1) for item in value[:25]]
        if len(value) > 25:
            items.append(f"...<{len(value) - 25} more items>")
        return items
    return redact_for_trace(str(value), _depth=_depth + 1)


def sync_langfuse_env(secret_resolver: Callable[[str], str] | None = None) -> None:
    """Ensure LANGFUSE_* variables from Streamlit Secrets or resolver are present in os.environ."""
    keys = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL", "LANGFUSE_HOST")
    if secret_resolver is not None:
        for k in keys:
            if not os.getenv(k):
                val = (secret_resolver(k) or "").strip()
                if val:
                    os.environ[k] = val
    # Keep LANGFUSE_BASE_URL and LANGFUSE_HOST mutually synced
    base = (os.getenv("LANGFUSE_BASE_URL") or os.getenv("LANGFUSE_HOST") or "").strip()
    if base:
        os.environ.setdefault("LANGFUSE_BASE_URL", base)
        os.environ.setdefault("LANGFUSE_HOST", base)


def is_langfuse_configured() -> bool:
    return bool(
        (os.getenv("LANGFUSE_PUBLIC_KEY") or "").strip()
        and (os.getenv("LANGFUSE_SECRET_KEY") or "").strip()
    )


def _get_client_or_none():
    if not is_langfuse_configured():
        return None
    try:
        from langfuse import get_client

        return get_client()
    except Exception:
        return None


def get_langfuse_status() -> dict[str, Any]:
    """Return honest Langfuse connectivity status without leaking secrets."""
    base_url = (
        os.getenv("LANGFUSE_BASE_URL")
        or os.getenv("LANGFUSE_HOST")
        or "https://cloud.langfuse.com"
    ).strip()
    if not is_langfuse_configured():
        return {
            "enabled": False,
            "status": "NOT_CONFIGURED",
            "base_url": base_url,
            "message": "Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY to enable tracing.",
        }
    client = _get_client_or_none()
    if client is None:
        return {
            "enabled": False,
            "status": "DEGRADED",
            "base_url": base_url,
            "message": "Langfuse SDK import or client initialization failed.",
        }
    try:
        ok = bool(client.auth_check())
        return {
            "enabled": ok,
            "status": "CONNECTED" if ok else "DEGRADED",
            "base_url": base_url,
            "message": "Authenticated with Langfuse Cloud." if ok else "Langfuse auth_check returned False.",
        }
    except Exception as exc:
        return {
            "enabled": False,
            "status": "DEGRADED",
            "base_url": base_url,
            "message": f"Langfuse auth_check failed: {redact_for_trace(str(exc))}",
        }


class _NoopObservation:
    """Lightweight no-op handle returned when Langfuse is disabled."""

    trace_id: str | None = None

    def update(self, **kwargs: Any) -> None:
        return None


@contextmanager
def trace_turn(
    *,
    name: str,
    session_id: str,
    input_data: Any = None,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    environment: str = "workbench_demo",
) -> Iterator[Any]:
    """Root trace context for a user interaction turn (auto-flushes on exit)."""
    client = _get_client_or_none()
    if client is None:
        yield _NoopObservation()
        return

    from langfuse import propagate_attributes

    clean_meta = {str(k): str(redact_for_trace(v))[:200] for k, v in (metadata or {}).items()}
    clean_tags = [str(t)[:64] for t in (tags or []) if t]
    try:
        with client.start_as_current_observation(
            name=name,
            as_type="agent",
            input=redact_for_trace(input_data),
            metadata=clean_meta,
        ) as obs:
            with propagate_attributes(
                session_id=session_id,
                trace_name=name,
                environment=environment,
                metadata=clean_meta,
                tags=clean_tags,
            ):
                yield obs
    finally:
        try:
            client.flush()
        except Exception:
            pass


@contextmanager
def trace_generation(
    *,
    name: str,
    model: str,
    prompt_text: str,
    metadata: dict[str, Any] | None = None,
) -> Iterator[Any]:
    """Nested LLM generation observation capturing prompt, output, tokens, and errors."""
    client = _get_client_or_none()
    if client is None:
        yield _NoopObservation()
        return

    clean_meta = redact_for_trace(metadata or {})
    with client.start_as_current_observation(
        name=name,
        as_type="generation",
        model=model,
        input=redact_for_trace(prompt_text),
        metadata=clean_meta,
    ) as gen:
        try:
            yield gen
        except Exception as exc:
            try:
                gen.update(level="ERROR", status_message=redact_for_trace(str(exc)))
            except Exception:
                pass
            raise


@contextmanager
def trace_span(
    *,
    name: str,
    as_type: Literal["span", "tool", "guardrail", "chain"] = "tool",
    input_data: Any = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[Any]:
    """Nested tool / guardrail / chain span."""
    client = _get_client_or_none()
    if client is None:
        yield _NoopObservation()
        return

    with client.start_as_current_observation(
        name=name,
        as_type=as_type,
        input=redact_for_trace(input_data),
        metadata=redact_for_trace(metadata or {}),
    ) as span:
        try:
            yield span
        except Exception as exc:
            try:
                span.update(level="ERROR", status_message=redact_for_trace(str(exc)))
            except Exception:
                pass
            raise


def record_trace_score(
    *,
    name: str,
    value: float | int | str,
    data_type: Literal["NUMERIC", "BOOLEAN", "CATEGORICAL"] = "NUMERIC",
    comment: str = "",
    trace_id: str | None = None,
) -> None:
    """Attach a programmatic evaluation score to the active or specified trace."""
    client = _get_client_or_none()
    if client is None:
        return
    try:
        if trace_id:
            client.create_score(
                trace_id=trace_id,
                name=name,
                value=value,
                data_type=data_type,
                comment=redact_for_trace(comment) if comment else None,
            )
        else:
            client.score_current_trace(
                name=name,
                value=value,
                data_type=data_type,
                comment=redact_for_trace(comment) if comment else None,
            )
    except Exception:
        pass
