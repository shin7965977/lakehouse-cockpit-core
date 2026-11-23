"""Automatic Semantic Promotion Proposal Engine backed by Day 2 JSONL Gap Logs."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class PromotionCandidate(BaseModel):
    candidate_id: str
    normalized_intent: str
    classification: str
    occurrences: int
    candidate_relations: list[str] = Field(default_factory=list)
    sample_question: str
    certification_status: Literal["PROPOSED_ASSET"] = "PROPOSED_ASSET"
    recommended_action: str


def _normalize_intent(question: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", " ", (question or "").lower()).strip()
    return re.sub(r"\s+", " ", cleaned)


def generate_promotion_candidates(
    gap_log_path: str | Path,
    *,
    min_occurrences: int = 2,
) -> list[PromotionCandidate]:
    """Cluster recurring unresolved gap events into deduplicated PROPOSED_ASSET candidates."""
    path = Path(gap_log_path)
    if not path.exists():
        return []

    buckets: dict[tuple[str, str, tuple[str, ...]], list[dict[str, object]]] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            ev = json.loads(raw_line)
        except Exception:
            continue
        cls = str(ev.get("classification", ""))
        if cls in ("REUSE_EXISTING", "ACCESS_BLOCKED", ""):
            continue
        q = str(ev.get("masked_question") or ev.get("question") or "")
        norm = _normalize_intent(q)
        if not norm:
            continue
        rels = tuple(sorted(str(r) for r in (ev.get("candidate_relations") or [])))
        # Keep different grains / relations / gap classes strictly separate
        key = (norm, cls, rels)
        buckets.setdefault(key, []).append(ev)

    action_map = {
        "NEW_SEMANTIC_METRIC": "Add governed MetricFlow & Lightdash metric definition on existing Gold model.",
        "NEW_DBT_MODEL": "Synthesize minimal Gold dbt model + enforced Data Contract + tests.",
        "TRANSFORMATION_GAP": "Add Silver cleansing transformation and downstream Gold mart.",
        "SOURCE_GAP": "Submit allowlisted dlt connector ingestion proposal for owner approval.",
        "SEMANTIC_AMBIGUITY": "Publish canonical business definition & disambiguation aliases.",
    }

    candidates: list[PromotionCandidate] = []
    for (norm, cls, rels), items in buckets.items():
        if len(items) < min_occurrences:
            continue
        cid = "promo_" + hashlib.sha1(f"{norm}|{cls}|{rels}".encode("utf-8")).hexdigest()[:8]
        sample_q = str(items[0].get("masked_question") or items[0].get("question") or norm)
        candidates.append(
            PromotionCandidate(
                candidate_id=cid,
                normalized_intent=norm,
                classification=cls,
                occurrences=len(items),
                candidate_relations=list(rels),
                sample_question=sample_q,
                certification_status="PROPOSED_ASSET",
                recommended_action=action_map.get(cls, "Review with Analytics Engineering."),
            )
        )

    candidates.sort(key=lambda c: (-c.occurrences, c.candidate_id))
    return candidates
