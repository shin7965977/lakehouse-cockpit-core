"""Guarded SQLGlot AST Validator, Tenant Isolation, Fanout Gate & QuerySpec Reconciler."""

from __future__ import annotations

from typing import Any, Literal

import sqlglot
from pydantic import BaseModel, Field
from sqlglot import exp

try:
    from .query_spec import (
        AggregationSpec,
        DataSourceRef,
        FilterCondition,
        JoinSpec,
        QuerySpec,
        diff_sql,
    )
except (ImportError, ValueError):
    try:
        from services.query_spec import (
            AggregationSpec,
            DataSourceRef,
            FilterCondition,
            JoinSpec,
            QuerySpec,
            diff_sql,
        )
    except (ImportError, ValueError):
        from cockpit_core.services.query_spec import (
            AggregationSpec,
            DataSourceRef,
            FilterCondition,
            JoinSpec,
            QuerySpec,
            diff_sql,
        )

ValidatorStatus = Literal["VALID", "REQUIRES_AE_REVIEW", "BLOCKED"]

_FORBIDDEN_FUNCTIONS = {
    "read_csv",
    "read_csv_auto",
    "read_parquet",
    "read_json",
    "read_json_auto",
    "read_text",
    "read_blob",
    "glob",
    "http_get",
    "pg_read_file",
    "pg_ls_dir",
    "dblink",
}

_AGG_EXPR_MAP: dict[type[exp.Expression], str] = {
    exp.Sum: "SUM",
    exp.Avg: "AVG",
    exp.Count: "COUNT",
    exp.Min: "MIN",
    exp.Max: "MAX",
}

_OP_EXPR_MAP: dict[type[exp.Expression], str] = {
    exp.EQ: "=",
    exp.NEQ: "!=",
    exp.GT: ">",
    exp.GTE: ">=",
    exp.LT: "<",
    exp.LTE: "<=",
}


class SqlValidationResult(BaseModel):
    status: ValidatorStatus
    reason_codes: list[str] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)
    reconciled_spec: QuerySpec | None = None
    executable_sql: str = ""
    sql_diff: str = ""
    reconciled_from_ast: bool = False


def _extract_literal_value(node: exp.Expression) -> Any:
    if isinstance(node, exp.Literal):
        if node.is_int:
            return int(node.this)
        if node.is_number:
            return float(node.this)
        return str(node.this)
    if isinstance(node, exp.Boolean):
        return bool(node.this)
    return node.sql()


_UNSAFE_TENANT_PREDICATE_NODES = (
    exp.NEQ,
    exp.GT,
    exp.GTE,
    exp.LT,
    exp.LTE,
    exp.In,
    exp.Between,
    exp.Like,
    exp.ILike,
)


def _is_tenant_column(node: exp.Expression | None) -> bool:
    return isinstance(node, exp.Column) and node.name.lower() == "tenant_id"


def _is_tenant_literal_eq(eq: exp.EQ, expected_str: str) -> bool:
    """Return True iff `eq` is strictly `tenant_id = <expected_literal>` (either side)."""
    left, right = eq.this, eq.expression
    if _is_tenant_column(left) and isinstance(right, exp.Literal):
        return str(_extract_literal_value(right)) == expected_str
    if _is_tenant_column(right) and isinstance(left, exp.Literal):
        return str(_extract_literal_value(left)) == expected_str
    return False


def _direct_select_tables(select_node: exp.Select) -> list[exp.Table]:
    """Return exp.Table nodes directly in FROM or JOIN clauses of this SELECT (excluding CTEs/subqueries)."""
    tables: list[exp.Table] = []
    from_clause = select_node.args.get("from_") or select_node.args.get("from")
    if from_clause and isinstance(from_clause.this, exp.Table):
        tables.append(from_clause.this)
    for j in select_node.args.get("joins") or []:
        if isinstance(j.this, exp.Table):
            tables.append(j.this)
    return tables


def _select_has_literal_tenant_scope(select_node: exp.Select, expected_str: str) -> bool:
    """Check if this SELECT's own WHERE clause already enforces `tenant_id = <expected_str>`."""
    where = select_node.args.get("where")
    if not where:
        return False
    return any(_is_tenant_literal_eq(eq, expected_str) for eq in where.find_all(exp.EQ))


def _inject_tenant_scope_on_select(
    select_node: exp.Select,
    scoped_low: set[str],
    tenant_expr: exp.Expression,
    expected_str: str,
) -> bool:
    """Inject `WHERE [<qualifier>.]tenant_id = <tenant_id>` on a SELECT that directly reads a scoped table."""
    direct_tables = _direct_select_tables(select_node)
    scoped_direct = [t for t in direct_tables if t.name.lower() in scoped_low]
    if not scoped_direct:
        return False
    if _select_has_literal_tenant_scope(select_node, expected_str):
        return False

    table_qualifier = scoped_direct[0].alias_or_name if len(direct_tables) > 1 else None
    tenant_col = (
        exp.column("tenant_id", table=table_qualifier, quoted=True)
        if table_qualifier
        else exp.column("tenant_id")
    )
    select_node.where(exp.EQ(this=tenant_col, expression=tenant_expr.copy()), copy=False)
    return True


def _check_tenant_isolation(
    tree: exp.Expression,
    referenced_tables: list[str],
    server_tenant_id: int | str | None,
    tenant_scoped_relations: set[str] | None,
) -> tuple[bool, str, exp.Expression]:
    """Verify no cross-tenant predicate exists and enforce server_tenant_id scoping."""
    if server_tenant_id is None:
        return True, "", tree

    expected_str = str(server_tenant_id)

    # 1a. Inspect explicit equality nodes involving `tenant_id`
    for eq in tree.find_all(exp.EQ):
        cols = [c.name.lower() for c in eq.find_all(exp.Column)]
        if "tenant_id" not in cols:
            continue
        # Allow safe multi-tenant JOIN equality: `a.tenant_id = b.tenant_id`
        if _is_tenant_column(eq.this) and _is_tenant_column(eq.expression):
            continue
        # Otherwise, must strictly equal the server-scoped tenant literal
        if not _is_tenant_literal_eq(eq, expected_str):
            return (
                False,
                f"Cross-tenant filter detected: expected tenant_id = {expected_str}.",
                tree,
            )

    # 1b. Explicitly reject unsafe comparison / set predicates on `tenant_id`
    for comp in tree.find_all(*_UNSAFE_TENANT_PREDICATE_NODES):
        if any(c.name.lower() == "tenant_id" for c in comp.find_all(exp.Column)):
            return (
                False,
                f"Unsafe tenant_id predicate '{comp.sql()}' is blocked; only server-scoped equality is allowed.",
                tree,
            )

    # 1c. Reject `NOT` wrapping tenant_id (e.g. `NOT tenant_id = 1`)
    for not_node in tree.find_all(exp.Not):
        if any(c.name.lower() == "tenant_id" for c in not_node.find_all(exp.Column)):
            return (
                False,
                "NOT-negated tenant_id filter is blocked to prevent tenant isolation bypass.",
                tree,
            )

    # 1d. Reject `OR` sibling combined with tenant_id equality (classic SQLi bypass)
    for or_node in tree.find_all(exp.Or):
        if any(c.name.lower() == "tenant_id" for c in or_node.find_all(exp.Column)):
            return (
                False,
                "OR-combined tenant_id filter is blocked to prevent tenant isolation bypass.",
                tree,
            )

    # 2. Auto-scope SELECTs referencing tenant-scoped tables (CTE-safe; never tricked by column-to-column JOINs)
    scoped_low = {t.lower() for t in (tenant_scoped_relations or set())}
    needs_scope = any(t.lower() in scoped_low for t in referenced_tables)
    if needs_scope and isinstance(tree, exp.Select):
        tenant_expr = (
            exp.Literal.number(server_tenant_id)
            if isinstance(server_tenant_id, int)
            else exp.Literal.string(str(server_tenant_id))
        )
        scoped_tree = tree.copy()
        injected_any = False

        # First try injecting on the top-level SELECT if it directly reads a scoped table
        if _inject_tenant_scope_on_select(scoped_tree, scoped_low, tenant_expr, expected_str):
            injected_any = True

        # Also scope any CTE inner SELECTs that directly read a scoped table without literal tenant filter
        with_clause = scoped_tree.args.get("with_") or scoped_tree.args.get("with")
        if with_clause:
            for cte in with_clause.find_all(exp.CTE):
                if isinstance(cte.this, exp.Select):
                    if _inject_tenant_scope_on_select(cte.this, scoped_low, tenant_expr, expected_str):
                        injected_any = True

        if injected_any:
            return True, "", scoped_tree

    return True, "", tree


def _check_join_fanout(
    tree: exp.Expression,
    one_to_many_joins: set[tuple[str, str]] | None,
) -> tuple[bool, str]:
    """Block SUM/AVG aggregation over a parent table when joined 1:N to a child table without pre-aggregation."""
    if not one_to_many_joins or not isinstance(tree, exp.Select):
        return True, ""

    joins = list(tree.args.get("joins") or [])
    if not joins:
        return True, ""

    from_clause = tree.args.get("from_") or tree.args.get("from")
    base_tbl = ""
    alias_to_table: dict[str, str] = {}
    if from_clause and isinstance(from_clause.this, exp.Table):
        base_tbl = from_clause.this.name.lower()
        alias_to_table[from_clause.this.alias_or_name.lower()] = base_tbl

    pair_set = {(a.lower(), b.lower()) for a, b in one_to_many_joins}
    fanout_parents: set[str] = set()
    for j in joins:
        if isinstance(j.this, exp.Table):
            right_tbl = j.this.name.lower()
            alias_to_table[j.this.alias_or_name.lower()] = right_tbl
            if (base_tbl, right_tbl) in pair_set:
                fanout_parents.add(base_tbl)
            elif (right_tbl, base_tbl) in pair_set:
                fanout_parents.add(right_tbl)

    if not fanout_parents:
        return True, ""

    for agg in tree.find_all(exp.Sum, exp.Avg):
        for col in agg.find_all(exp.Column):
            raw_ref = (col.table or "").lower()
            tbl_ref = alias_to_table.get(raw_ref, raw_ref)
            if tbl_ref in fanout_parents or (not tbl_ref and base_tbl in fanout_parents):
                return (
                    False,
                    f"Potential 1:N join fanout: aggregating '{col.sql()}' across a one-to-many join without pre-aggregation.",
                )
    return True, ""


def extract_output_group_by_columns(sql: str, dialect: str = "postgres") -> list[str]:
    """Extract normalized output column names (alias-first, no table prefixes) for GROUP BY from SQL AST."""
    raw = (sql or "").strip()
    if not raw:
        return []
    try:
        tree = sqlglot.parse_one(raw, read=dialect)
    except Exception:
        return []
    if not isinstance(tree, exp.Select):
        return []

    alias_by_qual: dict[tuple[str, str], str] = {}
    alias_by_name: dict[str, str] = {}
    for expr in tree.expressions:
        alias = ""
        target = expr
        if isinstance(expr, exp.Alias):
            alias = expr.alias
            target = expr.this
        if isinstance(target, exp.Column):
            out_name = alias or target.name
            tbl = (target.table or "").lower()
            col = target.name.lower()
            if tbl:
                alias_by_qual[(tbl, col)] = out_name
            alias_by_name.setdefault(col, out_name)

    grp = tree.args.get("group")
    if not grp:
        return []

    result: list[str] = []
    for g_expr in grp.expressions:
        if isinstance(g_expr, exp.Column):
            tbl = (g_expr.table or "").lower()
            col = g_expr.name.lower()
            resolved = alias_by_qual.get((tbl, col)) or alias_by_name.get(col) or g_expr.name
            if resolved and resolved not in result:
                result.append(resolved)
    return result


def _try_reconcile_simple_select(
    tree: exp.Expression,
    base_spec: QuerySpec,
    referenced_tables: list[str],
    *,
    scoped_sql: str | None = None,
) -> QuerySpec | None:
    """Reconcile a single-level SELECT into QuerySpec; return None if CTE/Window/Subquery."""
    if not isinstance(tree, exp.Select):
        return None
    if tree.args.get("with_") or tree.args.get("with"):
        return None
    if any(True for _ in tree.find_all(exp.Window, exp.Subquery)):
        return None

    projection: list[str] = []
    aggregations: list[AggregationSpec] = []
    alias_by_qual: dict[tuple[str, str], str] = {}
    alias_by_name: dict[str, str] = {}
    has_projection_alias = False
    for expr in tree.expressions:
        alias = ""
        target = expr
        if isinstance(expr, exp.Alias):
            alias = expr.alias
            target = expr.this

        matched_agg = next(
            (func_name for cls_t, func_name in _AGG_EXPR_MAP.items() if isinstance(target, cls_t)),
            None,
        )
        if matched_agg:
            if isinstance(target, exp.Count) and isinstance(target.this, exp.Distinct):
                inner_cols = list(target.this.find_all(exp.Column))
                field = inner_cols[0].name if inner_cols else "*"
                aggregations.append(AggregationSpec(field=field, func="COUNT_DISTINCT", alias=alias))
            else:
                inner_cols = list(target.find_all(exp.Column))
                field = inner_cols[0].name if inner_cols else "*"
                aggregations.append(AggregationSpec(field=field, func=matched_agg, alias=alias))  # type: ignore[arg-type]
        elif isinstance(target, exp.Column):
            out_name = alias or target.name
            if alias and alias != target.name:
                has_projection_alias = True
            tbl = (target.table or "").lower()
            col = target.name.lower()
            if tbl:
                alias_by_qual[(tbl, col)] = out_name
            alias_by_name.setdefault(col, out_name)
            projection.append(out_name)
        elif isinstance(target, exp.Star):
            continue
        else:
            # Complex computed expression -> treat as non-reconcilable (REQUIRES_AE_REVIEW)
            return None

    group_by: list[str] = []
    grp = tree.args.get("group")
    if grp:
        for g_expr in grp.expressions:
            if isinstance(g_expr, exp.Column):
                tbl = (g_expr.table or "").lower()
                col = g_expr.name.lower()
                resolved = alias_by_qual.get((tbl, col)) or alias_by_name.get(col) or g_expr.name
                if resolved not in group_by:
                    group_by.append(resolved)

    filters: list[FilterCondition] = []
    where = tree.args.get("where")
    if where:
        if any(True for _ in where.find_all(exp.Or)):
            return None
        for comp in where.find_all(exp.Binary):
            op_str = next(
                (sym for cls_t, sym in _OP_EXPR_MAP.items() if isinstance(comp, cls_t)),
                None,
            )
            if op_str and isinstance(comp.this, exp.Column):
                filters.append(
                    FilterCondition(
                        field=comp.this.name,
                        op=op_str,  # type: ignore[arg-type]
                        value=_extract_literal_value(comp.expression),
                    )
                )

    joins: list[JoinSpec] = []
    from_clause = tree.args.get("from_") or tree.args.get("from")
    base_rel = (
        from_clause.this.name
        if from_clause and isinstance(from_clause.this, exp.Table)
        else referenced_tables[0]
    )
    for j in tree.args.get("joins") or []:
        if isinstance(j.this, exp.Table):
            on_expr = j.args.get("on")
            j_side = (j.side or "").upper()
            j_type = j_side if j_side in ("LEFT", "RIGHT") else "INNER"
            joins.append(
                JoinSpec(
                    left_relation=base_rel,
                    right_relation=j.this.name,
                    on=on_expr.sql() if on_expr else "",
                    join_type=j_type,  # type: ignore[arg-type]
                )
            )

    max_rows = base_spec.output.max_rows
    lim = tree.args.get("limit")
    if lim and lim.expression and isinstance(lim.expression, exp.Literal) and lim.expression.is_int:
        max_rows = max(1, min(10000, int(lim.expression.this)))

    # Preserve verified executable SQL when JOINs, table aliases, or column aliases are present
    # so downstream Build / compile_sql() NEVER strips table qualifiers/aliases and breaks execution.
    has_table_aliases = any(bool(t.alias) for t in _direct_select_tables(tree))
    preserve_override = scoped_sql if (joins or has_table_aliases or has_projection_alias) else None

    return base_spec.with_updates(
        data_sources=[DataSourceRef(relation=base_rel, catalog_ref=f"dbt://model/{base_rel}")],
        projection=projection,
        aggregations=aggregations,
        group_by=group_by,
        filters=filters,
        joins=joins,
        max_rows=max_rows,
        raw_sql_override=preserve_override,
    )


_AVG_OF_AVG_EXACT = {"aov", "average", "avg", "rate", "ratio", "pct", "percent"}
_AVG_OF_AVG_PREFIXES = ("average_", "avg_", "mean_")
_AVG_OF_AVG_SUFFIXES = ("_avg", "_average", "_aov", "_rate", "_ratio", "_pct", "_percent")


def _check_average_of_averages(tree: exp.Expression) -> tuple[bool, str]:
    """Flag AVG(...) applied to pre-computed average/ratio/rate columns (average-of-averages trap)."""
    offenders: list[str] = []
    for avg_node in tree.find_all(exp.Avg):
        for col in avg_node.find_all(exp.Column):
            cname = (col.name or "").strip().lower()
            if (
                cname in _AVG_OF_AVG_EXACT
                or cname.startswith(_AVG_OF_AVG_PREFIXES)
                or cname.endswith(_AVG_OF_AVG_SUFFIXES)
            ):
                if col.name not in offenders:
                    offenders.append(col.name)
    if offenders:
        cols_str = ", ".join(offenders)
        return (
            False,
            f"Average-of-averages risk detected: AVG({cols_str}) re-aggregates pre-computed average/ratio column(s). "
            "Compute a weighted ratio (SUM(numerator) / NULLIF(SUM(denominator), 0)) or select directly from the "
            "pre-aggregated mart at its native grain without redundant GROUP BY.",
        )
    return True, ""


def validate_and_reconcile_sql(
    sql: str,
    base_spec: QuerySpec,
    *,
    allowed_relations: set[str],
    server_tenant_id: int | str | None = None,
    tenant_scoped_relations: set[str] | None = None,
    one_to_many_joins: set[tuple[str, str]] | None = None,
) -> SqlValidationResult:
    """Validate SQL via SQLGlot AST, enforce tenant/fanout policies, and reconcile QuerySpec."""
    raw_sql = (sql or "").strip()
    if not raw_sql:
        return SqlValidationResult(
            status="BLOCKED",
            reason_codes=["EMPTY_SQL"],
            messages=["SQL query cannot be empty."],
        )

    dialect = "postgres" if base_spec.dialect == "postgres" else "duckdb"
    try:
        statements = [s for s in sqlglot.parse(raw_sql, read=dialect) if s is not None]
    except Exception as exc:
        return SqlValidationResult(
            status="BLOCKED",
            reason_codes=["SYNTAX_ERROR"],
            messages=[f"SQLGlot parse error: {exc}"],
        )

    if len(statements) != 1:
        return SqlValidationResult(
            status="BLOCKED",
            reason_codes=["MULTIPLE_STATEMENTS"],
            messages=["Only a single read-only SELECT statement is permitted."],
        )

    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.Union)):
        return SqlValidationResult(
            status="BLOCKED",
            reason_codes=["FORBIDDEN_STATEMENT"],
            messages=[f"Statement type '{type(tree).__name__}' is forbidden; only SELECT is allowed."],
        )

    for fn in tree.find_all(exp.Anonymous, exp.Func):
        fn_name = (
            fn.name.lower()
            if isinstance(fn, exp.Anonymous)
            else fn.sql_name().lower()
        )
        if fn_name in _FORBIDDEN_FUNCTIONS:
            return SqlValidationResult(
                status="BLOCKED",
                reason_codes=["FORBIDDEN_FUNCTION"],
                messages=[f"Function '{fn_name}' is forbidden in read-only sandbox."],
            )

    cte_names = {cte.alias.lower() for cte in tree.find_all(exp.CTE) if cte.alias}
    allowed_low = {r.lower() for r in allowed_relations}
    referenced_tables: list[str] = []
    for tbl in tree.find_all(exp.Table):
        tname = (tbl.name or "").strip()
        if not tname or tname.lower() in cte_names:
            continue
        if tname.lower() not in allowed_low:
            return SqlValidationResult(
                status="BLOCKED",
                reason_codes=["UNLISTED_RELATION"],
                messages=[f"Table '{tname}' is not in the authorized catalog allowlist."],
            )
        referenced_tables.append(tname)

    if not referenced_tables:
        return SqlValidationResult(
            status="BLOCKED",
            reason_codes=["NO_RELATION"],
            messages=["Query must reference at least one authorized catalog table."],
        )

    # Tenant Isolation Gate
    tenant_ok, tenant_msg, scoped_tree = _check_tenant_isolation(
        tree, referenced_tables, server_tenant_id, tenant_scoped_relations
    )
    if not tenant_ok:
        return SqlValidationResult(
            status="BLOCKED",
            reason_codes=["CROSS_TENANT_VIOLATION"],
            messages=[tenant_msg],
        )

    # 1:N Join Fanout Gate
    fanout_ok, fanout_msg = _check_join_fanout(tree, one_to_many_joins)
    if not fanout_ok:
        return SqlValidationResult(
            status="BLOCKED",
            reason_codes=["JOIN_FANOUT_RISK"],
            messages=[fanout_msg],
        )

    prior_sql = base_spec.compile_sql() if base_spec.data_sources else ""
    exec_sql = scoped_tree.sql(dialect=dialect)
    patch = diff_sql(prior_sql, raw_sql)

    # Bidirectional AST -> QuerySpec Reconciliation
    reconciled = _try_reconcile_simple_select(tree, base_spec, referenced_tables, scoped_sql=exec_sql)

    # Average-of-Averages Analytical Quality Gate
    avg_ok, avg_msg = _check_average_of_averages(tree)
    if not avg_ok:
        override_spec = reconciled or base_spec.with_updates(raw_sql_override=raw_sql)
        return SqlValidationResult(
            status="REQUIRES_AE_REVIEW",
            reason_codes=["AVERAGE_OF_AVERAGES_RISK"],
            messages=[avg_msg],
            reconciled_spec=override_spec,
            executable_sql=exec_sql,
            sql_diff=patch,
            reconciled_from_ast=reconciled is not None,
        )

    if reconciled is not None:
        return SqlValidationResult(
            status="VALID",
            reconciled_spec=reconciled,
            executable_sql=exec_sql,
            sql_diff=patch,
            reconciled_from_ast=True,
        )

    # Valid read-only complex SQL (CTE / Window / Subquery) -> REQUIRES_AE_REVIEW
    override_spec = base_spec.with_updates(raw_sql_override=raw_sql)
    return SqlValidationResult(
        status="REQUIRES_AE_REVIEW",
        reason_codes=["COMPLEX_SQL_REQUIRES_AE_REVIEW"],
        messages=["Valid read-only SQL exceeds structured QuerySpec subset; preserving raw_sql_override for AE review."],
        reconciled_spec=override_spec,
        executable_sql=exec_sql,
        sql_diff=patch,
        reconciled_from_ast=False,
    )
