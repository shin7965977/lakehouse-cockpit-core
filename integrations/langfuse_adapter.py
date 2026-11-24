"""Dual-track Masked Observability Adapter (Langfuse + Local JSONL Gap Event Log).

Masks PII (emails, phone numbers, long numeric IDs) before emitting spans.
Reports `langfuse_enabled=False` honestly when Langfuse credentials are not configured
while always persisting masked gap events locally for promotion candidate discovery.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:\+?\d{1,3}[-\s]?)?(?:09\d{2}[-\s]?\d{3}[-\s]?\d{3}|\d{2,4}[-\s]\d{3,4}[-\s]\d{3,4})")


def mask_sensitive_text(text: str) -> str:
    """Mask emails and phone numbers before logging or external tracing."""
    out = _EMAIL_RE.sub("[MASKED_EMAIL]", text or "")
    out = _PHONE_RE.sub("[MASKED_PHONE]", out)
    return out


def _mask_payload(obj: Any) -> Any:
    if isinstance(obj, str):
        return mask_sensitive_text(obj)
    if isinstance(obj, dict):
        return {k: _mask_payload(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask_payload(v) for v in obj]
    return obj


class ObservabilityTracker:
    """Tracks masked spans to Langfuse (when configured) and local JSONL gap logs."""

    def __init__(
        self,
        gap_log_path: str | Path | None = None,
        langfuse_public_key: str | None = None,
        langfuse_secret_key: str | None = None,
        langfuse_host: str | None = None,
    ) -> None:
        default_log = Path(os.getenv("AE_GAP_LOG_PATH", ".scratch/gap_events.jsonl"))
        self.gap_log_path = Path(gap_log_path) if gap_log_path else default_log

        pub = (
            langfuse_public_key
            if langfuse_public_key is not None
            else os.getenv("LANGFUSE_PUBLIC_KEY", "")
        ).strip()
        sec = (
            langfuse_secret_key
            if langfuse_secret_key is not None
            else os.getenv("LANGFUSE_SECRET_KEY", "")
        ).strip()
        host = (
            langfuse_host
            if langfuse_host is not None
            else os.getenv("LANGFUSE_HOST", "")
        ).strip()

        self._client: Any = None
        self.langfuse_enabled = False
        if pub and sec:
            try:
                from langfuse import Langfuse  # type: ignore[import-untyped]

                kwargs: dict[str, Any] = {"public_key": pub, "secret_key": sec}
                if host:
                    kwargs["host"] = host
                self._client = Langfuse(**kwargs)
                self.langfuse_enabled = True
            except Exception:
                self._client = None
                self.langfuse_enabled = False

    def record_span(
        self,
        name: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Emit a PII-masked span event and return the recorded payload."""
        masked_meta = _mask_payload(metadata or {})
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "span": name,
            "langfuse_enabled": self.langfuse_enabled,
            "metadata": masked_meta,
        }
        if self._client is not None:
            try:
                self._client.trace(name=name, metadata=masked_meta)
            except Exception:
                event["langfuse_enabled"] = False
        return event

    def record_gap_event(
        self,
        *,
        question: str,
        classification: str,
        candidate_relations: list[str] | None = None,
        tenant_id: int | str | None = None,
        spec_hash: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Mask and append a semantic gap event to the local JSONL log (and Langfuse if enabled)."""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": request_id,
            "masked_question": mask_sensitive_text(question),
            "classification": classification,
            "candidate_relations": list(candidate_relations or []),
            "tenant_id": tenant_id,
            "spec_hash": spec_hash,
            "langfuse_enabled": self.langfuse_enabled,
        }
        self.gap_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.gap_log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

        if self._client is not None:
            try:
                self._client.trace(name="semantic_gap_event", metadata=record)
            except Exception:
                record["langfuse_enabled"] = False
        return record
