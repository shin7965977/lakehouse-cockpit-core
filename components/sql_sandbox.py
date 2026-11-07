"""Interactive SQL Verification Sandbox & CSV Exporter Component."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import streamlit as st

try:
    from ..adapter import BaseCockpitAdapter
except (ImportError, ValueError):
    try:
        from adapter import BaseCockpitAdapter
    except (ImportError, ValueError):
        from cockpit_core.adapter import BaseCockpitAdapter


def render_sql_sandbox(
    adapter: BaseCockpitAdapter,
    data_dict: dict[str, pd.DataFrame],
    start_date: date | None = None,
    end_date: date | None = None,
    is_en: bool = True,
) -> None:
    """Render an interactive SQL query box allowing stakeholders to verify real data and download CSV."""
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
                "SQL Query:",
                value=st.session_state.get("sandbox_sql_text", default_query),
                height=120,
                key="sandbox_sql_textarea",
                help="Type standard SQL queries against registered data tables.",
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
            with st.spinner("Executing query against in-memory DuckDB engine..."):
                try:
                    res_df = adapter.execute_custom_sql(
                        sql_query=query_text.strip(),
                        data_dict=data_dict,
                    )
                    st.session_state["sandbox_query_result"] = res_df
                    st.session_state["sandbox_query_error"] = None
                except Exception as e:
                    st.session_state["sandbox_query_result"] = None
                    st.session_state["sandbox_query_error"] = str(e)

        # Display Error if any
        err_msg = st.session_state.get("sandbox_query_error")
        if err_msg:
            st.error(f"❌ SQL Execution Error: {err_msg}")

        # Display Result and Download Button
        result_df: pd.DataFrame | None = st.session_state.get("sandbox_query_result")
        if result_df is not None:
            st.success(
                f"✅ Query executed successfully! Returned `{len(result_df)}` rows."
                if is_en
                else f"✅ 查詢執行成功！共檢索出 `{len(result_df)}` 筆紀錄。"
            )
            st.dataframe(result_df, use_container_width=True, height=280)

            # CSV Download Button
            csv_data = result_df.to_csv(index=False).encode("utf-8")
            download_label = (
                f"⬇️ Download Results as CSV ({len(result_df)} records)"
                if is_en
                else f"⬇️ 下載查詢結果為 CSV（共 {len(result_df)} 筆）"
            )
            st.download_button(
                label=download_label,
                data=csv_data,
                file_name="koro_custom_sql_export.csv",
                mime="text/csv",
                use_container_width=True,
            )
