"""Smoke, interaction, and Mode A/B synchronization contract tests for the Showcase app."""

from __future__ import annotations

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
    valid_placements = {"top_kpi", "workspace_tab", "right_drawer"}
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
    for cid in ["kpi_grid", "sql_expander", "on_demand_panel", "looker_view", "schema_designer"]:
        catalog_radio.set_value(cid).run(timeout=15)
        assert not at.exception, f"Mode B component '{cid}' raised an exception: {at.exception}"
