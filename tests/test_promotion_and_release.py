"""Vertical-slice TDD tests for Seam 4: Semantic Promotion, Draft PR Orchestrator & Lightdash Adapter."""

from __future__ import annotations

import json
from pathlib import Path

from integrations.lightdash_adapter import LightdashAdapter
from services.release_orchestrator import orchestrate_draft_pr
from services.semantic_promotion import generate_promotion_candidates


def test_promotion_clusters_recurring_gaps_by_grain_and_never_auto_certifies(tmp_path: Path) -> None:
    gap_log = tmp_path / "gap_events.jsonl"
    events = [
        {"masked_question": "Monthly average order value", "classification": "NEW_SEMANTIC_METRIC", "candidate_relations": ["fct_monthly_sales"]},
        {"masked_question": "Monthly average order value", "classification": "NEW_SEMANTIC_METRIC", "candidate_relations": ["fct_monthly_sales"]},
        {"masked_question": "Daily average order value", "classification": "NEW_DBT_MODEL", "candidate_relations": ["int_daily_sales"]},
        {"masked_question": "Daily average order value", "classification": "NEW_DBT_MODEL", "candidate_relations": ["int_daily_sales"]},
        {"masked_question": "One-off random question", "classification": "SOURCE_GAP", "candidate_relations": []},
    ]
    gap_log.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

    candidates = generate_promotion_candidates(gap_log, min_occurrences=2)
    assert len(candidates) == 2
    assert all(c.certification_status == "PROPOSED_ASSET" for c in candidates)
    assert all(c.occurrences >= 2 for c in candidates)


def test_draft_pr_orchestrator_returns_honest_dry_run_bundle_without_github_token() -> None:
    res = orchestrate_draft_pr(
        model_name="mart_monthly_sales_summary",
        question="Monthly revenue summary",
        files={
            "models/gold/mart_monthly_sales_summary.sql": "SELECT 1",
            "models/gold/schema.yml": "version: 2",
        },
        validation_summary="4/4 isolated checks passed",
        github_token="",
        github_repo="",
    )
    assert res.pr_mode == "DRY_RUN_BUNDLE"
    assert res.pr_url is None
    assert "mart_monthly_sales_summary" in res.branch_name
    assert "Rollback" in res.body_markdown
    assert len(res.files) == 2


def test_lightdash_adapter_reports_staging_local_only_when_unconfigured() -> None:
    ld = LightdashAdapter(lightdash_url="", api_key="", project_uuid="")
    status = ld.check_status()
    assert status["lightdash_status"] == "STAGING_LOCAL_ONLY"
    assert status["connected"] is False
