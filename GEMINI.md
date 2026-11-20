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
4. **Strict Prohibition of Silent Fallbacks**:
   - **Never swallow errors**: Do not use `except Exception: pass` or silently substitute unrelated default queries/tables when an LLM API call, SQL execution, or intent parser fails.
   - **Truthful chart/table titles**: Never label a fallback chart or table with an unparsed user prompt that does not match the actual SQL projection.
5. **Mandatory Fail-Loud Policy (一定要 Fail-Loud)**:
   - **Halt and surface every failure explicitly**: Any failure in Gemini API calls, JSON parsing, DuckDB SQL compilation/execution, empty (0-row) dry-run results, or out-of-domain prompts MUST immediately halt the pipeline and render a visible `st.error` / `st.warning` showing the exact error reason (and the failed SQL for debugging whenever applicable).
   - **No fake green status or dummy rows**: Never display a green `PASSED` / `Compiled` status badge or substitute `.head()` / mock rows from an unrelated table when a step fails.
   - **Strict API-key gating for AI-only features**: Features that require live LLM reasoning (e.g., Chart Logic Audit, Schema Designer custom synthesis) must explicitly block and prompt for the Gemini API Key when missing—never silently degrade to keyword heuristics.
