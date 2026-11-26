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
    # 1. Inspect every comparison involving `tenant_id` anywhere in the AST
    # NOTE: In SQLGlot, `exp.And` inherits from `exp.Binary`. Skip `exp.And` so
    # compound predicates like `WHERE order_date >= ... AND tenant_id = 1` are
    # evaluated at their leaf comparison nodes instead of falsely blocked.
    for comp in tree.find_all(exp.Binary, exp.In):
        if isinstance(comp, exp.And):
            continue
        cols = [c.name.lower() for c in comp.find_all(exp.Column)]
        if "tenant_id" not in cols:
            continue
        if isinstance(comp, exp.EQ):
            lits = [str(_extract_literal_value(lit)) for lit in comp.find_all(exp.Literal)]
            if not lits or any(v != expected_str for v in lits):
                return (
                    False,
                    f"Cross-tenant filter detected: expected tenant_id = {expected_str}.",
                    tree,
                )
        else:
            # Any !=, <, >, IN, OR predicate on tenant_id is a potential bypass
            return (
                False,
                f"Unsafe tenant_id predicate '{comp.sql()}' is blocked; only server-scoped equality is allowed.",
                tree,
            )

    # Reject `NOT` wrapping tenant_id (e.g. `NOT tenant_id = 1`)
    for not_node in tree.find_all(exp.Not):
        if any(c.name.lower() == "tenant_id" for c in not_node.find_all(exp.Column)):
            return (
                False,
                "NOT-negated tenant_id filter is blocked to prevent tenant isolation bypass.",
                tree,
            )

    # Reject `OR` sibling combined with tenant_id equality (classic SQLi bypass)
    for or_node in tree.find_all(exp.Or):
        if any(c.name.lower() == "tenant_id" for c in or_node.find_all(exp.Column)):
            return (
                False,
                "OR-combined tenant_id filter is blocked to prevent tenant isolation bypass.",
                tree,
            )

    # 2. Auto-scope tables declared as tenant-scoped if query is a top-level Select
    scoped_low = {t.lower() for t in (tenant_scoped_relations or set())}
    needs_scope = any(t.lower() in scoped_low for t in referenced_tables)
    if needs_scope and isinstance(tree, exp.Select):
        has_eq_tenant = any(
            isinstance(eq, exp.EQ)
            and any(c.name.lower() == "tenant_id" for c in eq.find_all(exp.Column))
            for eq in tree.find_all(exp.EQ)
        )
        if not has_eq_tenant:
            tenant_expr = (
                exp.Literal.number(server_tenant_id)
                if isinstance(server_tenant_id, int)
                else exp.Literal.string(str(server_tenant_id))
            )
            scoped_tree = tree.copy()
            # When joining multiple tables, qualify tenant_id with the primary scoped table to avoid
            # PostgreSQL "column reference 'tenant_id' is ambiguous" errors.
            table_qualifier: str | None = None
            if len(referenced_tables) > 1:
                from_clause = scoped_tree.args.get("from_") or scoped_tree.args.get("from")
                if from_clause and isinstance(from_clause.this, exp.Table):
                    if from_clause.this.name.lower() in scoped_low:
                        table_qualifier = from_clause.this.alias_or_name
                if not table_qualifier:
                    for t_node in scoped_tree.find_all(exp.Table):
                        if t_node.name.lower() in scoped_low:
                            table_qualifier = t_node.alias_or_name
                            break
            tenant_col = (
                exp.column("tenant_id", table=table_qualifier, quoted=True)
                if table_qualifier
                else exp.column("tenant_id")
            )
            scoped_tree.where(exp.EQ(this=tenant_col, expression=tenant_expr), copy=False)
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
    if from_clause and isinstance(from_clause.this, exp.Table):
        base_tbl = from_clause.this.name.lower()

    pair_set = {(a.lower(), b.lower()) for a, b in one_to_many_joins}
    fanout_parents: set[str] = set()
    for j in joins:
        if isinstance(j.this, exp.Table):
            right_tbl = j.this.name.lower()
            if (base_tbl, right_tbl) in pair_set:
                fanout_parents.add(base_tbl)
            elif (right_tbl, base_tbl) in pair_set:
                fanout_parents.add(right_tbl)

    if not fanout_parents:
        return True, ""

    for agg in tree.find_all(exp.Sum, exp.Avg):
        for col in agg.find_all(exp.Column):
            tbl_ref = (col.table or "").lower()
            if tbl_ref in fanout_parents or (not tbl_ref and base_tbl in fanout_parents):
                return (
                    False,
                    f"Potential 1:N join fanout: aggregating '{col.sql()}' across a one-to-many join without pre-aggregation.",
                )
    return True, ""


def _try_reconcile_simple_select(
    tree: exp.Expression,
    base_spec: QuerySpec,
    referenced_tables: list[str],
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
            projection.append(target.name)
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
                group_by.append(g_expr.name)

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

    return base_spec.with_updates(
        data_sources=[DataSourceRef(relation=base_rel, catalog_ref=f"dbt://model/{base_rel}")],
        projection=projection,
        aggregations=aggregations,
        group_by=group_by,
        filters=filters,
        joins=joins,
        max_rows=max_rows,
        raw_sql_override=None,
    )


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
    reconciled = _try_reconcile_simple_select(tree, base_spec, referenced_tables)
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
