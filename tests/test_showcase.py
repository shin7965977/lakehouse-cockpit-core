"""Smoke, interaction, and Mode A/B synchronization contract tests for the Showcase app."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from examples.showcase_app import (  # noqa: E402
    ShowcaseRetailAdapter,
    build_component_registry,
)

SHOWCASE_PATH = str(REPO_ROOT / "examples" / "showcase_app.py")


def test_component_registry_covers_all_files_in_components_dir() -> None:
    """Enforce 1:1 parity: every components/*.py file must be registered in build_component_registry()."""
    adapter = ShowcaseRetailAdapter()
    registry = build_component_registry(
        adapter=adapter,
        data_dict=adapter.load_domain_data(),
        is_en=False,
        start_date=None,
        end_date=None,
    )

    registered_files = {
        file_rel_path
        for spec in registry
        for file_rel_path in spec.source_files
    }
    actual_component_files = {
        f"components/{p.name}"
        for p in (REPO_ROOT / "components").glob("*.py")
        if p.name != "__init__.py"
    }

    missing = actual_component_files - registered_files
    assert not missing, (
        f"New component file(s) {missing} found in components/ but not registered in "
        "examples/showcase_app.py:build_component_registry()! Register them so Mode A and Mode B stay in sync."
    )

    # Every registered item must have a valid Mode A placement and a callable render function
    valid_placements = {"top_kpi", "workspace_tab", "right_drawer", "left_sidebar"}
    for spec in registry:
        assert spec.placement_in_mode_a in valid_placements
        assert callable(spec.render)


def test_showcase_mode_a_and_offline_ai_reply() -> None:
    """Mode A renders all feature tabs cleanly and supports offline Mock AI replies without an API key."""
    at = AppTest.from_file(SHOWCASE_PATH).run(timeout=15)
    assert not at.exception, f"Mode A raised an exception: {at.exception}"

    # Click the first quick diagnosis question button in the AI Drawer (offline mock mode)
    quick_btns = [b for b in at.button if b.key and b.key.startswith("quick_btn_")]
    assert len(quick_btns) > 0, "Expected quick diagnosis buttons to be visible in offline mock mode"
    quick_btns[0].click().run(timeout=15)
    assert not at.exception, f"Clicking quick question raised an exception: {at.exception}"


def test_showcase_mode_b_storybook_catalog() -> None:
    """Mode B (Component Storybook) renders and switches across catalog items cleanly."""
    at = AppTest.from_file(SHOWCASE_PATH).run(timeout=15)
    at.sidebar.radio[0].set_value("mode_b").run(timeout=15)
    assert not at.exception, f"Mode B initial render raised an exception: {at.exception}"

    # Verify switching across representative Storybook catalog items
    catalog_radio = next(r for r in at.radio if r.key == "storybook_selected_component")
    for cid in [
        "kpi_grid",
        "on_demand_panel",
        "sql_workbench",
        "looker_view",
        "schema_designer",
        "data_center",
        "ai_drawer",
        "sidebar_ops",
    ]:
        catalog_radio.set_value(cid).run(timeout=15)
        assert not at.exception, f"Mode B component '{cid}' raised an exception: {at.exception}"


def test_duckdb_executor_readonly_guardrail_and_reflection() -> None:
    """Shared DuckDB service enforces read-only guardrails and reflects Kimball metadata."""
    import pandas as pd
    from services.duckdb_executor import (
        DatabaseReflectionService,
        detect_pii_columns,
        execute_readonly_duckdb,
    )

    df_orders = pd.DataFrame({"order_id": [1, 2], "amount": [100.0, 250.0]})
    df_customers = pd.DataFrame({"customer_id": [1], "email": ["a@b.com"]})
    data_dict = {"fct_orders": df_orders, "dim_customers": df_customers}

    # Read-only guardrail blocks destructive statements
    _, err = execute_readonly_duckdb("DROP TABLE fct_orders;", data_dict)
    assert "blocked" in err.lower()

    # Valid SELECT succeeds
    res_df, err = execute_readonly_duckdb("SELECT SUM(amount) AS total FROM fct_orders", data_dict)
    assert err == ""
    assert float(res_df.iloc[0]["total"]) == 350.0

    # Reflection & PII detection
    reflection = DatabaseReflectionService(data_dict)
    models = reflection.get_kimball_models()
    assert "dim_customers" in models["dimensions"]
    assert "fct_orders" in models["facts"]
    assert detect_pii_columns(df_customers) == ["email"]


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _find_cjk_leaks(at: AppTest) -> list[str]:
    """Collect any rendered UI strings containing CJK characters."""
    leaks: list[str] = []
    for attr in ("title", "header", "subheader", "caption", "markdown", "info", "warning", "error", "success"):
        for el in getattr(at, attr, []):
            val = getattr(el, "value", "") or ""
            if _CJK_RE.search(val):
                leaks.append(f"{attr}: {val[:80]}")
    for attr in ("radio", "selectbox"):
        for el in getattr(at, attr, []):
            label = getattr(el, "label", "") or ""
            if _CJK_RE.search(label):
                leaks.append(f"{attr}.label: {label[:80]}")
            for opt in getattr(el, "options", []) or []:
                opt_str = str(opt)
                if _CJK_RE.search(opt_str):
                    leaks.append(f"{attr}.option: {opt_str[:80]}")
    for el in getattr(at, "button", []):
        label = getattr(el, "label", "") or ""
        if _CJK_RE.search(label):
            leaks.append(f"button: {label[:80]}")
    for el in getattr(at, "tabs", []):
        label = getattr(el, "label", "") or ""
        if _CJK_RE.search(label):
            leaks.append(f"tab: {label[:80]}")
    return leaks


def test_english_mode_has_no_chinese_in_mode_a_and_mode_b() -> None:
    """Switching to English must eliminate Chinese UI text in both Mode A and Mode B."""
    at = AppTest.from_file(SHOWCASE_PATH).run(timeout=15)
    lang_radio = next(r for r in at.sidebar.radio if r.key == "shared_lang_selector")
    lang_radio.set_value("English").run(timeout=15)
    assert not at.exception, f"Mode A English render raised an exception: {at.exception}"

    mode_a_leaks = _find_cjk_leaks(at)
    assert not mode_a_leaks, f"Chinese text leaked in Mode A English UI: {mode_a_leaks}"

    # Switch to Mode B while staying in English
    at.sidebar.radio[0].set_value("mode_b").run(timeout=15)
    assert not at.exception, f"Mode B English render raised an exception: {at.exception}"

    catalog_radio = next(r for r in at.radio if r.key == "storybook_selected_component")
    for cid in list(catalog_radio.options):
        catalog_radio.set_value(cid).run(timeout=15)
        assert not at.exception, f"Mode B English '{cid}' raised an exception: {at.exception}"
        assert any("UX & Business Problem Solved" in (box.value or "") for box in at.info), (
            f"Mode B '{cid}' missing UX & Business Problem Solved callout"
        )
        leaks = _find_cjk_leaks(at)
        assert not leaks, f"Chinese text leaked in Mode B '{cid}' English UI: {leaks}"

