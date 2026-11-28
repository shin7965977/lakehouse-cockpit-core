"""Contract & regression tests for examples/workbench_demo.py.

Prevents silent/runtime AttributeError mismatches between the Streamlit UI glue
layer and backend Pydantic/dataclass models (e.g., gap.rationale vs gap.reason,
prop.dlt_pipeline_code vs prop.dlt_scaffold_py).
"""

from __future__ import annotations

import ast
import dataclasses
import importlib.util
import inspect
from pathlib import Path
import typing

import streamlit as st

from services.consultant_service import (
    answer_consultant_turn,
    draft_candidate_query,
    propose_consultant_turn,
)
from services.dbt_asset_builder import build_dbt_and_lightdash_bundle
from services.dbt_catalog import DbtMetadataCatalog
from services.neon_demo_engine import (
    build_jaffle_catalog,
    execute_neon_readonly,
    fetch_demo_state,
    reset_demo,
    run_day5_normal_refresh,
    run_day10_scd_type2,
    run_day15_dirty_data_wap,
    run_day20_semantic_drift_ci,
)
from services.query_spec import QuerySpec
from services.release_orchestrator import orchestrate_draft_pr
from services.semantic_gap_detector import (
    GapClassification,
    GapDetectionResult,
    classify_sql_semantic_gap,
)
from services.source_ingestion import propose_source_ingestion
from services.sql_repair import repair_sql_bounded
from services.sql_validator import validate_and_reconcile_sql

ROOT = Path(__file__).resolve().parents[1]
WORKBENCH_PATH = ROOT / "examples" / "workbench_demo.py"


def _valid_attrs(cls: type) -> set[str]:
    attrs = set(dir(cls))
    if hasattr(cls, "model_fields"):
        attrs |= set(cls.model_fields.keys())
    if hasattr(cls, "model_computed_fields"):
        attrs |= set(cls.model_computed_fields.keys())
    if dataclasses.is_dataclass(cls):
        attrs |= {f.name for f in dataclasses.fields(cls)}
    return attrs


def test_workbench_demo_static_model_attribute_contracts() -> None:
    """AST-verify that every backend model attribute accessed in workbench_demo.py exists."""
    ret = lambda fn: typing.get_type_hints(fn)["return"]
    call_returns: dict[str, type] = {
        "classify_sql_semantic_gap": ret(classify_sql_semantic_gap),
        "draft_candidate_query": ret(draft_candidate_query),
        "validate_and_reconcile_sql": ret(validate_and_reconcile_sql),
        "propose_consultant_turn": ret(propose_consultant_turn),
        "answer_consultant_turn": ret(answer_consultant_turn),
        "build_dbt_and_lightdash_bundle": ret(build_dbt_and_lightdash_bundle),
        "orchestrate_draft_pr": ret(orchestrate_draft_pr),
        "propose_source_ingestion": ret(propose_source_ingestion),
        "repair_sql_bounded": ret(repair_sql_bounded),
        "build_jaffle_catalog": DbtMetadataCatalog,
        "QuerySpec": QuerySpec,
    }

    tree = ast.parse(WORKBENCH_PATH.read_text(encoding="utf-8"))

    # Pass 1: discover st.session_state[key] -> model class from assignments
    ss_types: dict[str, type] = {}
    for fn_node in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        env: dict[str, type] = {}
        for n in ast.walk(fn_node):
            if not isinstance(n, ast.Assign):
                continue
            if isinstance(n.value, ast.Call):
                fname = (
                    n.value.func.id
                    if isinstance(n.value.func, ast.Name)
                    else (n.value.func.attr if isinstance(n.value.func, ast.Attribute) else None)
                )
                if fname in call_returns:
                    for t in n.targets:
                        if isinstance(t, ast.Name):
                            env[t.id] = call_returns[fname]
                        elif (
                            isinstance(t, ast.Subscript)
                            and isinstance(t.slice, ast.Constant)
                            and isinstance(t.slice.value, str)
                        ):
                            ss_types[t.slice.value] = call_returns[fname]
            elif isinstance(n.value, ast.Name) and n.value.id in env:
                for t in n.targets:
                    if (
                        isinstance(t, ast.Subscript)
                        and isinstance(t.slice, ast.Constant)
                        and isinstance(t.slice.value, str)
                    ):
                        ss_types[t.slice.value] = env[n.value.id]
            elif (
                isinstance(n.value, ast.Attribute)
                and isinstance(n.value.value, ast.Name)
                and n.value.value.id in env
            ):
                parent_cls = env[n.value.value.id]
                th = typing.get_type_hints(parent_cls) if hasattr(parent_cls, "__annotations__") else {}
                f_type = th.get(n.value.attr)
                if f_type is not None:
                    args = [a for a in typing.get_args(f_type) if a is not type(None)]
                    real_cls = args[0] if args else f_type
                    if isinstance(real_cls, type):
                        for t in n.targets:
                            if (
                                isinstance(t, ast.Subscript)
                                and isinstance(t.slice, ast.Constant)
                                and isinstance(t.slice.value, str)
                            ):
                                ss_types[t.slice.value] = real_cls

    # Pass 2: verify every attribute access on tracked model variables inside every function
    mismatches: list[str] = []
    for fn_node in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        env = {}
        if fn_node.name == "_record_gap_event":
            env["gap"] = GapDetectionResult
        for n in ast.walk(fn_node):
            if not isinstance(n, ast.Assign):
                continue
            inferred: type | None = None
            val = n.value
            if isinstance(val, ast.Call):
                fname = (
                    val.func.id
                    if isinstance(val.func, ast.Name)
                    else (val.func.attr if isinstance(val.func, ast.Attribute) else None)
                )
                if fname in call_returns:
                    inferred = call_returns[fname]
                elif (
                    fname == "get"
                    and val.args
                    and isinstance(val.args[0], ast.Constant)
                    and val.args[0].value in ss_types
                ):
                    inferred = ss_types[val.args[0].value]
            elif (
                isinstance(val, ast.Subscript)
                and isinstance(val.slice, ast.Constant)
                and val.slice.value in ss_types
            ):
                inferred = ss_types[val.slice.value]
            if inferred is not None:
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        env[t.id] = inferred

        for n in ast.walk(fn_node):
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in env:
                cls = env[n.value.id]
                if n.attr not in _valid_attrs(cls):
                    mismatches.append(
                        f"{fn_node.name}() L{n.lineno}: {n.value.id}.{n.attr} does not exist on {cls.__name__}"
                    )

    assert not mismatches, "Contract mismatches in examples/workbench_demo.py:\n  - " + "\n  - ".join(
        sorted(mismatches)
    )


def _load_workbench_module():
    spec = importlib.util.spec_from_file_location("workbench_demo_mod", WORKBENCH_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_record_gap_event_with_real_gap_detection_result(monkeypatch) -> None:
    """Regression test: _record_gap_event must read GapDetectionResult.reason without AttributeError."""
    mod = _load_workbench_module()
    fake_state: dict[str, object] = {"gap_events": []}
    monkeypatch.setattr(st, "session_state", fake_state)

    gap = GapDetectionResult(
        classification=GapClassification.NEW_SEMANTIC_METRIC,
        question="Is revenue driven by AOV or volume?",
        reason="Candidate SQL computes tier_avg_aov not yet certified in dbt semantic layer.",
        candidate_relations=["mart_customer_value"],
    )
    mod._record_gap_event(gap.question, gap)

    events = fake_state["gap_events"]
    assert isinstance(events, list) and len(events) == 1
    assert events[0]["classification"] == "NEW_SEMANTIC_METRIC"
    assert events[0]["rationale"] == gap.reason
    assert events[0]["candidate_relations"] == ["mart_customer_value"]


def test_propose_source_ingestion_exposes_dlt_scaffold_py() -> None:
    """Regression test: allowlisted SourceIngestionProposal exposes dlt_scaffold_py (not dlt_pipeline_code)."""
    prop = propose_source_ingestion(
        source_type="approved_local_csv",
        source_locator="data/marketing_campaigns_2025.csv",
        target_bronze_table="bronze_marketing_campaigns",
    )
    assert prop.status == "PROPOSED"
    assert prop.dlt_scaffold_py
    assert "@dlt.resource" in prop.dlt_scaffold_py


def test_sql_validator_flags_average_of_averages_risk() -> None:
    """Regression test: AVG(average_order_value) must NOT pass as VALID; must flag AVERAGE_OF_AVERAGES_RISK."""
    bad_sql = (
        'SELECT "mart_customer_value"."customer_name", "mart_customer_value"."customer_tier", '
        'SUM("mart_customer_value"."total_revenue") AS "total_revenue", '
        'AVG("mart_customer_value"."average_order_value") AS "aov", '
        'MAX("mart_customer_value"."max_order_amount") AS "max_order_size", '
        'SUM("mart_customer_value"."order_count") AS "order_count" '
        'FROM "mart_customer_value" '
        'GROUP BY "mart_customer_value"."customer_name", "mart_customer_value"."customer_tier" '
        'ORDER BY "total_revenue" DESC LIMIT 20'
    )
    base_spec = QuerySpec(
        spec_id="qs_test_avg_of_avg",
        question="Is our revenue mainly driven by high AOV from a few key customers?",
        dialect="postgres",
        raw_sql_override=bad_sql,
    )
    res = validate_and_reconcile_sql(
        bad_sql,
        base_spec,
        allowed_relations={"mart_customer_value"},
    )
    assert res.status == "REQUIRES_AE_REVIEW"
    assert "AVERAGE_OF_AVERAGES_RISK" in res.reason_codes


def test_dbt_asset_builder_strips_outer_order_by_and_limit_keeps_window() -> None:
    """Regression test: dbt Gold SQL must strip outer ORDER BY and LIMIT while preserving window OVER (ORDER BY)."""
    sql_with_order_limit = (
        'SELECT "customer_id", "customer_name", "total_revenue", '
        'SUM("total_revenue") OVER (ORDER BY "total_revenue" DESC) AS "running_rev" '
        'FROM "mart_customer_value" '
        'ORDER BY "total_revenue" DESC LIMIT 20'
    )
    spec = QuerySpec(
        spec_id="qs_test_order_strip",
        question="Customer revenue share",
        dialect="postgres",
        raw_sql_override=sql_with_order_limit,
    )
    bundle = build_dbt_and_lightdash_bundle(
        query_spec=spec,
        model_name="mart_customer_revenue_share",
        grain=["customer_id"],
    )
    body_after_config = bundle.dbt_sql.split("}}", 1)[-1]
    assert "LIMIT 20" not in body_after_config.upper()
    assert "OVER (ORDER BY" in body_after_config.upper()
    # Ensure no trailing outer ORDER BY remains outside OVER(...)
    tail_after_from = body_after_config.upper().split("FROM", 1)[-1]
    assert "ORDER BY" not in tail_after_from


def test_workbench_demo_tab2_cards_have_hover_tooltips() -> None:
    """Ensure key Tab 2 workflow headers provide hover tooltips (HTML title attribute)."""
    src = WORKBENCH_PATH.read_text(encoding="utf-8")
    for heading in (
        ">1. Business Consultation & Intent Framing",
        ">Candidate Governed Query",
        ">Semantic Gap Classification",
    ):
        idx = src.find(heading)
        assert idx != -1, f"Card heading '{heading}' not found in workbench_demo.py"
        window = src[max(0, idx - 320) : idx + 120]
        assert "title=" in window, f"Card heading '{heading}' is missing a hover tooltip (title=...)"


def test_workbench_demo_service_call_signatures() -> None:
    """AST-verify that every backend service call in workbench_demo.py binds cleanly to its real signature."""
    fns = {
        "answer_consultant_turn": answer_consultant_turn,
        "draft_candidate_query": draft_candidate_query,
        "propose_consultant_turn": propose_consultant_turn,
        "build_dbt_and_lightdash_bundle": build_dbt_and_lightdash_bundle,
        "build_jaffle_catalog": build_jaffle_catalog,
        "execute_neon_readonly": execute_neon_readonly,
        "fetch_demo_state": fetch_demo_state,
        "reset_demo": reset_demo,
        "run_day5_normal_refresh": run_day5_normal_refresh,
        "run_day10_scd_type2": run_day10_scd_type2,
        "run_day15_dirty_data_wap": run_day15_dirty_data_wap,
        "run_day20_semantic_drift_ci": run_day20_semantic_drift_ci,
        "orchestrate_draft_pr": orchestrate_draft_pr,
        "classify_sql_semantic_gap": classify_sql_semantic_gap,
        "propose_source_ingestion": propose_source_ingestion,
        "repair_sql_bounded": repair_sql_bounded,
        "validate_and_reconcile_sql": validate_and_reconcile_sql,
    }
    tree = ast.parse(WORKBENCH_PATH.read_text(encoding="utf-8"))
    mismatches: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fname = node.func.id if isinstance(node.func, ast.Name) else None
        if fname not in fns:
            continue
        if any(isinstance(a, ast.Starred) for a in node.args) or any(k.arg is None for k in node.keywords):
            continue
        sig = inspect.signature(fns[fname])
        pos_args = [object() for _ in node.args]
        kw_args = {kw.arg: object() for kw in node.keywords if kw.arg is not None}
        try:
            sig.bind(*pos_args, **kw_args)
        except TypeError as exc:
            mismatches.append(f"L{node.lineno}: {fname}(...) -> {exc}")

    assert not mismatches, "Service call signature mismatches in examples/workbench_demo.py:\n  - " + "\n  - ".join(
        sorted(mismatches)
    )


