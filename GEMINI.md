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
4. **Strict Prohibition of Silent Fallbacks (Fail-Loud Policy)**:
   - **Never swallow errors**: Do not use `except Exception: pass` or silently substitute unrelated default queries/tables when an LLM API call, SQL execution, or intent parser fails.
   - **Explicit UI alerts**: Whenever a query cannot be parsed in offline mode, an API key is missing for an unsupported custom prompt, or DuckDB/LLM execution raises an error, immediately display an explicit `st.error` or `st.warning` with the exact reason.
   - **Truthful chart/table titles**: Never label a fallback chart or table with an unparsed user prompt that does not match the actual SQL projection.
