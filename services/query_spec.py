"""Versioned QuerySpec & AnalyticsAssetContract domain models with canonical SHA-256 hashing."""

from __future__ import annotations

import difflib
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

CertificationLabel = Literal[
    "CERTIFIED",
    "VALIDATED_EXPLORATORY",
    "NEEDS_CLARIFICATION",
    "BLOCKED",
    "PROPOSED_ASSET",
    "APPROVED_ASSET",
]
QueryPurpose = Literal["ad_hoc", "long_term"]
QueryDialect = Literal["duckdb", "postgres"]
ValidationStatus = Literal["pending", "valid", "invalid", "blocked"]
AggFunc = Literal["SUM", "AVG", "COUNT", "COUNT_DISTINCT", "MIN", "MAX"]
FilterOp = Literal[
    "=",
    "!=",
    ">",
    ">=",
    "<",
    "<=",
    "IN",
    "NOT IN",
    "LIKE",
    "IS NULL",
    "IS NOT NULL",
    "LAST_N_DAYS",
    "LAST_N_MONTHS",
]
JoinType = Literal["INNER", "LEFT", "RIGHT"]


class DataSourceRef(BaseModel):
    relation: str
    catalog_ref: str = ""
    grain: list[str] = Field(default_factory=list)


class AggregationSpec(BaseModel):
    field: str
    func: AggFunc = "SUM"
    alias: str = ""


class FilterCondition(BaseModel):
    field: str
    op: FilterOp = "="
    value: Any = None


class JoinSpec(BaseModel):
    left_relation: str
    right_relation: str
    on: str
    join_type: JoinType = "INNER"
    cardinality: str = "unknown"


class ValidationState(BaseModel):
    status: ValidationStatus = "pending"
    reason_codes: list[str] = Field(default_factory=list)


class ReviewState(BaseModel):
    intent_confirmed: bool = False
    spec_hash: str | None = None
    confirmed_by: str | None = None
    confirmed_at: str | None = None


class OutputSpec(BaseModel):
    max_rows: int = Field(default=100, ge=1, le=10000)
    certification: CertificationLabel = "VALIDATED_EXPLORATORY"


class QuerySpec(BaseModel):
    """Single auditable specification shared by Ad-hoc Exploration and Long-term Asset Design."""

    spec_id: str = "qs_draft_001"
    version: int = Field(default=1, ge=1)
    purpose: QueryPurpose = "ad_hoc"
    question: str = ""
    semantic_match: str | None = None
    data_sources: list[DataSourceRef] = Field(default_factory=list)
    projection: list[str] = Field(default_factory=list)
    aggregations: list[AggregationSpec] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    filters: list[FilterCondition] = Field(default_factory=list)
    joins: list[JoinSpec] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    dialect: QueryDialect = "duckdb"
    raw_sql_override: str | None = None
    validation: ValidationState = Field(default_factory=ValidationState)
    review: ReviewState = Field(default_factory=ReviewState)
    output: OutputSpec = Field(default_factory=OutputSpec)

    def canonical_payload(self) -> dict[str, Any]:
        """Return the exact semantic payload bound to the immutable SHA-256 spec_hash."""
        return {
            "purpose": self.purpose,
            "semantic_match": self.semantic_match,
            "data_sources": [s.model_dump(mode="json") for s in self.data_sources],
            "projection": list(self.projection),
            "aggregations": [a.model_dump(mode="json") for a in self.aggregations],
            "group_by": list(self.group_by),
            "filters": [f.model_dump(mode="json") for f in self.filters],
            "joins": [j.model_dump(mode="json") for j in self.joins],
            "metrics": list(self.metrics),
            "assumptions": list(self.assumptions),
            "dialect": self.dialect,
            "raw_sql_override": (
                self.raw_sql_override.strip() if self.raw_sql_override else None
            ),
            "output_max_rows": self.output.max_rows,
        }

    def compute_spec_hash(self) -> str:
        """Compute deterministic SHA-256 hex digest of all semantic fields."""
        encoded = json.dumps(
            self.canonical_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def model_post_init(self, __context: Any) -> None:
        current_hash = self.compute_spec_hash()
        if self.review.spec_hash is None:
            self.review.spec_hash = current_hash
        elif self.review.spec_hash != current_hash:
            # Semantic drift detected: invalidate prior confirmation and validation
            self.review.intent_confirmed = False
            self.review.confirmed_by = None
            self.review.confirmed_at = None
            self.review.spec_hash = current_hash
            self.validation.status = "pending"
            self.validation.reason_codes = ["SPEC_HASH_CHANGED"]

    def with_updates(self, **changes: Any) -> QuerySpec:
        """Return an updated QuerySpec, incrementing version and invalidating confirmation if semantics changed."""
        old_hash = self.compute_spec_hash()
        data = self.model_dump(mode="python")
        for key, value in changes.items():
            if key == "max_rows":
                data["output"]["max_rows"] = value
            else:
                data[key] = value
        updated = QuerySpec.model_validate(data)
        new_hash = updated.compute_spec_hash()
        if new_hash != old_hash:
            updated.version = self.version + 1
            updated.review.intent_confirmed = False
            updated.review.confirmed_by = None
            updated.review.confirmed_at = None
            updated.review.spec_hash = new_hash
            updated.validation.status = "pending"
            updated.validation.reason_codes = ["SPEC_HASH_CHANGED"]
        return updated

    def confirm_intent(self, confirmed_by: str = "analyst") -> QuerySpec:
        """Bind explicit user intent confirmation to the exact current spec_hash."""
        current_hash = self.compute_spec_hash()
        data = self.model_dump(mode="python")
        data["review"] = {
            "intent_confirmed": True,
            "spec_hash": current_hash,
            "confirmed_by": confirmed_by,
            "confirmed_at": datetime.now(timezone.utc).isoformat(),
        }
        return QuerySpec.model_validate(data)

    def is_confirmation_valid(self) -> bool:
        """Return True only if intent is confirmed AND spec_hash matches current semantics."""
        return bool(
            self.review.intent_confirmed
            and self.review.spec_hash == self.compute_spec_hash()
        )

    def compile_sql(self) -> str:
        """Deterministically compile structured QuerySpec fields into read-only SELECT SQL (or return raw_sql_override)."""
        if self.raw_sql_override and self.raw_sql_override.strip():
            return self.raw_sql_override.strip()

        if not self.data_sources:
            raise ValueError("Cannot compile QuerySpec without at least one data_source.")

        def _q(ident: str) -> str:
            clean = ident.strip().strip('"')
            if "." in clean:
                return ".".join(f'"{part}"' for part in clean.split("."))
            return f'"{clean}"'

        def _fmt_val(val: Any) -> str:
            if val is None:
                return "NULL"
            if isinstance(val, bool):
                return "TRUE" if val else "FALSE"
            if isinstance(val, (int, float)):
                return str(val)
            if isinstance(val, (list, tuple)):
                return "(" + ", ".join(_fmt_val(v) for v in val) + ")"
            escaped = str(val).replace("'", "''")
            return f"'{escaped}'"

        select_parts: list[str] = []
        for col in self.projection:
            select_parts.append(_q(col))
        for agg in self.aggregations:
            alias = agg.alias or f"{agg.func.lower()}_{agg.field.split('.')[-1]}"
            if agg.func == "COUNT_DISTINCT":
                expr = f"COUNT(DISTINCT {_q(agg.field)})"
            else:
                expr = f"{agg.func}({_q(agg.field)})"
            select_parts.append(f"{expr} AS {_q(alias)}")
        if not select_parts:
            select_parts.append("*")

        base_rel = _q(self.data_sources[0].relation)
        lines = [f"SELECT {', '.join(select_parts)}", f"FROM {base_rel}"]

        for j in self.joins:
            lines.append(f"{j.join_type} JOIN {_q(j.right_relation)} ON {j.on}")

        if self.filters:
            where_clauses: list[str] = []
            for f in self.filters:
                if f.op in ("IS NULL", "IS NOT NULL"):
                    where_clauses.append(f"{_q(f.field)} {f.op}")
                elif f.op in ("LAST_N_DAYS", "LAST_N_MONTHS"):
                    try:
                        n = int(f.value)
                    except (TypeError, ValueError) as exc:
                        raise ValueError(
                            f"Filter op '{f.op}' requires a positive integer value, got {f.value!r}."
                        ) from exc
                    if n < 1 or n > 3650:
                        raise ValueError(
                            f"Filter op '{f.op}' value must be between 1 and 3650, got {n}."
                        )
                    unit = "days" if f.op == "LAST_N_DAYS" else "months"
                    where_clauses.append(
                        f"CAST({_q(f.field)} AS DATE) >= CAST(CURRENT_DATE - INTERVAL '{n} {unit}' AS DATE)"
                    )
                else:
                    if isinstance(f.value, str):
                        val_up = f.value.upper()
                        if any(tok in val_up for tok in ("NOW(", "CURRENT_DATE", "INTERVAL ")):
                            raise ValueError(
                                f"Raw SQL date expression {f.value!r} cannot be passed as a literal filter value; "
                                "use structured op 'LAST_N_DAYS' or 'LAST_N_MONTHS' with an integer value instead."
                            )
                    where_clauses.append(f"{_q(f.field)} {f.op} {_fmt_val(f.value)}")
            lines.append("WHERE " + " AND ".join(where_clauses))

        if self.group_by:
            lines.append("GROUP BY " + ", ".join(_q(g) for g in self.group_by))

        lines.append(f"LIMIT {self.output.max_rows}")
        return "\n".join(lines)


def diff_sql(before_sql: str, after_sql: str) -> str:
    """Return a readable unified diff between two SQL strings."""
    before_lines = (before_sql or "").strip().splitlines()
    after_lines = (after_sql or "").strip().splitlines()
    diff = list(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile="compiled_sql",
            tofile="edited_sql",
            lineterm="",
        )
    )
    return "\n".join(diff)


class AnalyticsAssetContract(BaseModel):
    """Long-term governed asset contract promoted from a validated QuerySpec."""

    asset_id: str
    asset_version: int = Field(default=1, ge=1)
    model_name: str
    business_owner: str = "analytics-engineering"
    query_spec: QuerySpec
    metric_expression: str = ""
    grain: list[str] = Field(default_factory=list)
    additive_behavior: Literal["additive", "semi_additive", "non_additive"] = "additive"
    freshness_sla: str = "24h"
    lightdash_explore: str = ""
    certification_status: CertificationLabel = "PROPOSED_ASSET"
    rollback_reference: str = ""
