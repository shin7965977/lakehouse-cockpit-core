# Lakehouse Cockpit Core — Agent Instructions

## Adding or Modifying UI Components (`components/*.py`)

 Mode A (Integrated Cockpit) and Mode B (Component Storybook) in `examples/showcase_app.py` render from a single source of truth: `build_component_registry()`.

When adding or updating a module under `components/`:

1. **Dual-Mode Compatible Imports**: Wrap internal package imports in `try / except (ImportError, ValueError)` so the module resolves both when mounted as a `cockpit_core` submodule and when executed directly from the repository root.
2. **Register in `build_component_registry()`**: Add or update the corresponding `ShowcaseComponentSpec` entry in `examples/showcase_app.py` with:
   - `source_files`: relative path(s) matching `components/<filename>.py`.
   - `placement_in_mode_a`: `"top_kpi"`, `"workspace_tab"`, `"right_drawer"`, or `"left_sidebar"`.
   - `render`: zero-argument callable used identically by Mode A and Mode B.
3. **Verify Parity Contract**: Run `pytest -v tests/test_showcase.py`. Completion requires `test_component_registry_covers_all_files_in_components_dir`, `test_showcase_mode_a_and_offline_ai_reply`, and `test_showcase_mode_b_storybook_catalog` to pass.
