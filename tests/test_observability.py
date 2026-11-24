"""Vertical-slice TDD tests for Seam 4: Masked Langfuse Tracing & JSONL Gap Event Log."""

from __future__ import annotations

import json
from pathlib import Path

from integrations.langfuse_adapter import ObservabilityTracker, mask_sensitive_text


def test_masks_pii_and_persists_gap_events_with_honest_langfuse_status(tmp_path: Path) -> None:
    masked = mask_sensitive_text("Contact ceo@company.com or 0912-345-678 about order 99887766")
    assert "ceo@company.com" not in masked
    assert "0912-345-678" not in masked
    assert "[MASKED_EMAIL]" in masked
    assert "[MASKED_PHONE]" in masked

    gap_log = tmp_path / "gap_events.jsonl"
    tracker = ObservabilityTracker(
        gap_log_path=gap_log,
        langfuse_public_key="",
        langfuse_secret_key="",
    )
    assert tracker.langfuse_enabled is False

    rec = tracker.record_gap_event(
        question="Contact alice@example.com for customer-level order details",
        classification="SOURCE_GAP",
        candidate_relations=["fct_monthly_sales"],
        tenant_id=1,
    )
    assert rec["langfuse_enabled"] is False
    assert "alice@example.com" not in rec["masked_question"]
    assert gap_log.exists()

    lines = [json.loads(line) for line in gap_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0]["classification"] == "SOURCE_GAP"
    assert "alice@example.com" not in lines[0]["masked_question"]
