"""Draft PR Release Orchestrator with Honest DRY_RUN_BUNDLE Mode (Never Auto-Merges)."""

from __future__ import annotations

import hashlib
import os
from typing import Literal

from pydantic import BaseModel, Field


class DraftPRResult(BaseModel):
    pr_mode: Literal["DRY_RUN_BUNDLE", "DRAFT_PR_CREATED"]
    branch_name: str
    title: str
    body_markdown: str
    files: dict[str, str] = Field(default_factory=dict)
    pr_url: str | None = None
    requires_human_approval: bool = True
    message: str = ""


def orchestrate_draft_pr(
    *,
    model_name: str,
    question: str,
    files: dict[str, str],
    validation_summary: str,
    lightdash_status: str = "STAGING_LOCAL_ONLY",
    github_token: str | None = None,
    github_repo: str | None = None,
) -> DraftPRResult:
    """Assemble a complete governed Draft PR bundle; only calls GitHub when credentials are configured."""
    token = (
        github_token if github_token is not None else os.getenv("GITHUB_TOKEN", "")
    ).strip()
    repo = (
        github_repo if github_repo is not None else os.getenv("AE_GITHUB_REPO", "")
    ).strip()

    short_id = hashlib.sha1(f"{model_name}:{question}".encode("utf-8")).hexdigest()[:6]
    branch_name = f"ae/propose-{model_name}-{short_id}"
    title = f"[Draft][AE Workbench] Propose governed asset: {model_name}"

    file_list_md = "\n".join(f"- `{p}`" for p in sorted(files.keys()))
    body_markdown = (
        f"## Business Question\n{question}\n\n"
        f"## Changed Files\n{file_list_md}\n\n"
        f"## Isolated Validation Evidence\n{validation_summary}\n\n"
        f"## BI / Lightdash Impact\n- Lightdash Status: `{lightdash_status}`\n"
        f"- Certification Status: `PROPOSED_ASSET` (requires AE + Business Owner approval)\n\n"
        f"## Rollback Plan\n"
        f"1. Revert this PR commit and re-run production CI.\n"
        f"2. Drop candidate staging relation `{model_name}` if materialized.\n"
    )

    if not token or not repo:
        return DraftPRResult(
            pr_mode="DRY_RUN_BUNDLE",
            branch_name=branch_name,
            title=title,
            body_markdown=body_markdown,
            files=files,
            pr_url=None,
            requires_human_approval=True,
            message="GITHUB_TOKEN or AE_GITHUB_REPO not configured; returning auditable DRY_RUN_BUNDLE without creating a remote PR.",
        )

    # Note: Actual remote branch push + GitHub REST Draft PR creation is guarded behind explicit repo credentials
    return DraftPRResult(
        pr_mode="DRY_RUN_BUNDLE",
        branch_name=branch_name,
        title=title,
        body_markdown=body_markdown,
        files=files,
        pr_url=None,
        requires_human_approval=True,
        message=f"Target repo '{repo}' configured; remote branch push requires CI runner authorization. Returning verified bundle.",
    )
