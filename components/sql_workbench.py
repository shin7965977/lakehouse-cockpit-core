"""Unified SQL & Lineage Workbench (Dynamic dbt Manifest Explorer + Read-Only DuckDB Sandbox)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

try:
    from ..adapter import BaseCockpitAdapter
    from ..services.duckdb_executor import execute_readonly_duckdb
except (ImportError, ValueError):
    try:
        from adapter import BaseCockpitAdapter
        from services.duckdb_executor import execute_readonly_duckdb
    except (ImportError, ValueError):
        from cockpit_core.adapter import BaseCockpitAdapter
        from cockpit_core.services.duckdb_executor import execute_readonly_duckdb


def load_dbt_manifest_models(
    manifest_path: str | Path | None,
    layers: tuple[str, ...] = ("silver", "bronze"),
) -> list[dict[str, Any]]:
    """Parse dbt target/manifest.json and return Silver/Bronze models with compiled SQL & upstream lineage."""
    if not manifest_path:
        return []
    path_obj = Path(manifest_path)
    if not path_obj.exists():
        return []

    try:
        payload = json.loads(path_obj.read_text(encoding="utf-8"))
    except Exception:
        return []

    nodes = payload.get("nodes", {})
    if not isinstance(nodes, dict):
        return []

    models: list[dict[str, Any]] = []
    for uid, node in nodes.items():
        if not isinstance(node, dict) or node.get("resource_type") != "model":
            continue
        schema_name = str(node.get("schema", "")).lower()
        if layers and not any(layer in schema_name for layer in layers):
            continue
        raw_deps = node.get("depends_on", {}).get("nodes", [])
        clean_deps = [str(dep).split(".")[-1] for dep in raw_deps]
        models.append(
            {
                "unique_id": uid,
                "name": str(node.get("name", "")),
                "schema": str(node.get("schema", "")),
                "description": str(node.get("description", "")),
                "depends_on": clean_deps,
                "compiled_sql": str(node.get("compiled_code") or node.get("raw_code") or ""),
            }
        )

    # Sort Silver models before Bronze models for executive clarity
    models.sort(key=lambda m: (0 if "silver" in m["schema"].lower() else 1, m["name"]))
    return models


def render_sql_snippet_module(
    module_id: str,
    title_en: str,
    source_table_label: str,
    sql_template: str,
    df_preview: pd.DataFrame,
    start_date: date | None = None,
    end_date: date | None = None,
    title_zh: str = "",
    is_en: bool = True,
) -> None:
    """Render a collapsible expander containing syntax-highlighted SQL with dynamic date injection."""
    display_title = title_en if is_en else (title_zh or title_en or module_id)
    expander_title = f"📄 {display_title}"

    start_str = start_date.isoformat() if start_date else "2026-08-03"
    end_str = end_date.isoformat() if end_date else "2026-10-31"

    formatted_sql = sql_template.format(
        start_str=start_str,
        end_str=end_str,
    )

    with st.expander(expander_title, expanded=False):
        st.markdown(f"**Source Table**: `{source_table_label}`")
        st.caption("🔍 **Executable SQL Query**")
        st.code(formatted_sql, language="sql")

        st.caption("📊 **Table Data Snippet (100% Aligned with SQL Projection)**")
        if df_preview is not None and not df_preview.empty:
            st.dataframe(df_preview, use_container_width=True)
        else:
            st.info("No data returned for the selected filter.")


def _run_sandbox_query(
    adapter: BaseCockpitAdapter | None,
    sql_query: str,
    data_dict: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """Execute query via adapter if implemented, otherwise fall back to shared DuckDB executor."""
    if adapter is not None and hasattr(adapter, "execute_custom_sql"):
        return adapter.execute_custom_sql(sql_query=sql_query, data_dict=data_dict)
    res_df, err = execute_readonly_duckdb(sql_query, data_dict)
    if err:
        raise ValueError(err)
    return res_df


def render_sql_sandbox(
    adapter: BaseCockpitAdapter,
    data_dict: dict[str, pd.DataFrame],
    start_date: date | None = None,
    end_date: date | None = None,
    is_en: bool = True,
) -> None:
    """Render an interactive read-only SQL query box allowing stakeholders to verify data and export CSV."""
    title = (
        "🧪 SQL Verification Sandbox & Raw Data Explorer"
        if is_en
        else "🧪 互動式 SQL 驗證沙盒與原始數據探查器"
    )

    with st.expander(title, expanded=False):
        st.caption(
            "🔒 **FinOps Safe & Read-Only**: Enter custom SQL queries against BigQuery / DuckDB Gold & Silver tables. "
            "Verify calculation accuracy directly and export audit-ready CSV records."
            if is_en
            else "🔒 **唯讀與成本安全保證**：可手動輸入 SQL 針對金牌與銀牌層表格進行自由探查，親自驗證數據真實性，並將結果匯出為 CSV 稽核清單。"
        )

        col_input, col_ctrl = st.columns([8, 2])
        default_query = getattr(
            adapter,
            "default_sandbox_sql",
            "SELECT * FROM mart_product_performance LIMIT 10;",
        )

        with col_input:
            query_text = st.text_area(
                "SQL Query:" if is_en else "SQL 查詢語法：",
                value=st.session_state.get("sandbox_sql_text", default_query),
                height=120,
                key="sandbox_sql_textarea",
                help=(
                    "Type standard read-only SQL queries against registered data tables."
                    if is_en
                    else "針對已註冊的湖倉資料表輸入唯讀 SQL 查詢。"
                ),
            )

        with col_ctrl:
            st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
            btn_run_label = "▶️ Run Query" if is_en else "▶️ 執行查詢"
            btn_reset_label = "🔄 Reset SQL" if is_en else "🔄 重置 SQL"
            run_clicked = st.button(btn_run_label, type="primary", use_container_width=True)
            if st.button(btn_reset_label, use_container_width=True):
                st.session_state["sandbox_sql_text"] = default_query
                st.session_state.pop("sandbox_query_result", None)
                st.rerun()

        if run_clicked and query_text.strip():
            spinner_label = (
                "Executing query against in-memory DuckDB engine..."
                if is_en
                else "正在透過記憶體 DuckDB 引擎執行查詢..."
            )
            with st.spinner(spinner_label):
                try:
                    res_df = _run_sandbox_query(
                        adapter=adapter,
                        sql_query=query_text.strip(),
                        data_dict=data_dict,
                    )
                    st.session_state["sandbox_query_result"] = res_df
                    st.session_state["sandbox_query_error"] = None
                except Exception as exc:
                    st.session_state["sandbox_query_result"] = None
                    st.session_state["sandbox_query_error"] = str(exc)

        err_msg = st.session_state.get("sandbox_query_error")
        if err_msg:
            st.error(f"❌ SQL Execution Error: {err_msg}")

        result_df: pd.DataFrame | None = st.session_state.get("sandbox_query_result")
        if result_df is not None:
            st.success(
                f"✅ Query executed successfully! Returned `{len(result_df)}` rows."
                if is_en
                else f"✅ 查詢執行成功！共檢索出 `{len(result_df)}` 筆紀錄。"
            )
            st.dataframe(result_df, use_container_width=True, height=280)

            csv_data = result_df.to_csv(index=False).encode("utf-8")
            download_label = (
                f"⬇️ Download Results as CSV ({len(result_df)} records)"
                if is_en
                else f"⬇️ 下載查詢結果為 CSV（共 {len(result_df)} 筆）"
            )
            st.download_button(
                label=download_label,
                data=csv_data,
                file_name="lakehouse_sql_export.csv",
                mime="text/csv",
                use_container_width=True,
            )


def render_sql_workbench(
    adapter: BaseCockpitAdapter,
    data_dict: dict[str, pd.DataFrame],
    start_date: date | None = None,
    end_date: date | None = None,
    is_en: bool = True,
    scope_key: str = "dbt_explorer",
) -> None:
    """Render dynamic Silver/Bronze model selector from dbt manifest.json,

    side-by-side Table Preview + Compiled SQL, and the merged SQL Verification Sandbox.
    """
    st.subheader(
        "🥈 Silver / Bronze SQL & Lineage Workbench"
        if is_en
        else "🥈 Silver / Bronze 動態 SQL 與血緣工作台"
    )
    st.caption(
        "Auto-discovered from `dbt manifest.json`: inspect upstream lineage, live table preview, compiled SQL, and run custom queries below."
        if is_en
        else "自動解析 `dbt manifest.json`：動態檢視上游血緣、即時資料表預覽與編譯後生產 SQL，並可直接於下方 SQL 沙盒自由驗證。"
    )

    manifest_path = getattr(adapter, "dbt_manifest_path", None)
    models = load_dbt_manifest_models(manifest_path)

    if not models:
        st.info(
            "ℹ️ No `dbt manifest.json` configured on adapter; displaying available `data_dict` tables."
            if is_en
            else "ℹ️ 尚未設定 `dbt_manifest_path`，改以 `data_dict` 內現有資料表動態呈現。"
        )
        models = [
            {
                "unique_id": f"table.{tbl}",
                "name": tbl,
                "schema": "main_silver" if "raw" not in tbl else "main_bronze",
                "description": "In-memory Lakehouse table from domain adapter.",
                "depends_on": [],
                "compiled_sql": f"SELECT * FROM {tbl} LIMIT 50;",
            }
            for tbl in data_dict
            if isinstance(data_dict[tbl], pd.DataFrame)
        ]

    if not models:
        st.warning("No Silver or Bronze models available.")
        return

    model_options = {f"{m['schema']}.{m['name']}": m for m in models}
    selected_label = st.selectbox(
        "Select Silver / Bronze Model to Inspect:"
        if is_en
        else "選擇要檢視的 Silver / Bronze 模型：",
        options=list(model_options.keys()),
        key=f"dbt_model_selector_{scope_key}",
    )
    active_model = model_options[selected_label]

    deps_str = (
        " , ".join(f"`{d}`" for d in active_model["depends_on"])
        if active_model["depends_on"]
        else "`source_system`"
    )
    with st.container(border=True):
        layer_badge = "🥈 SILVER" if "silver" in active_model["schema"].lower() else "🥉 BRONZE"
        st.markdown(f"**{layer_badge} · `{selected_label}`**")
        if active_model["description"]:
            st.caption(active_model["description"])
        st.markdown(
            f"**Upstream Lineage (`depends_on`)**: {deps_str}"
            if is_en
            else f"**上游血緣依賴 (`depends_on`)**：{deps_str}"
        )

    col_tbl, col_sql = st.columns([1.05, 0.95])
    model_name = active_model["name"]

    with col_tbl:
        st.markdown(
            "##### 📊 Live Table Preview (`LIMIT 50`)"
            if is_en
            else "##### 📊 即時資料表預覽 (`LIMIT 50`)"
        )
        try:
            preview_df = _run_sandbox_query(
                adapter=adapter,
                sql_query=f"SELECT * FROM {model_name} LIMIT 50;",
                data_dict=data_dict,
            )
        except Exception:
            preview_df = data_dict.get(model_name, pd.DataFrame()).head(50)

        if preview_df is not None and not preview_df.empty:
            st.dataframe(preview_df, use_container_width=True, hide_index=True)
        else:
            st.info("No rows available for preview.")

    with col_sql:
        st.markdown(
            "##### 📜 Compiled dbt Production SQL"
            if is_en
            else "##### 📜 dbt 編譯後生產級 SQL"
        )
        st.code(active_model["compiled_sql"], language="sql")

    st.markdown("---")
    render_sql_sandbox(
        adapter=adapter,
        data_dict=data_dict,
        start_date=start_date,
        end_date=end_date,
        is_en=is_en,
    )


# Backward-compatible alias for callers expecting the previous function name
render_dynamic_dbt_explorer_with_sandbox = render_sql_workbench
