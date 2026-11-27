"""Contract & regression tests for examples/workbench_demo.py.

Prevents silent/runtime AttributeError mismatches between the Streamlit UI glue
layer and backend Pydantic/dataclass models (e.g., gap.rationale vs gap.reason,
prop.dlt_pipeline_code vs prop.dlt_scaffold_py).
"""

from __future__ import annotations

import ast
import dataclasses
import importlib.util
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
