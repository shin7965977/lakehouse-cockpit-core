"""SQL Query Expander & Table Data Snippet Contract Component."""

from __future__ import annotations

from datetime import date
from typing import Callable
import pandas as pd
import streamlit as st


def render_sql_snippet_module(
    module_id: str,
    title_zh: str,
    title_en: str,
    source_table_label: str,
    sql_template: str,
    df_preview: pd.DataFrame,
    start_date: date | None = None,
    end_date: date | None = None,
    is_en: bool = False,
) -> None:
    """Render a collapsible expander containing syntax-highlighted SQL with dynamic date injection,

    accompanied by an aligned Table Data Snippet.
    """
    expander_title = (
        f"📄 {title_en}" if is_en else f"📄 {title_zh}"
    )

    start_str = start_date.isoformat() if start_date else "2026-10-01"
    end_str = end_date.isoformat() if end_date else "2026-10-31"

    # Inject dynamic dates into template
    formatted_sql = sql_template.format(
        start_str=start_str,
        end_str=end_str,
    )

    with st.expander(expander_title, expanded=False):
        st.markdown(
            f"**{'Source Table' if is_en else '來源表'}**：`{source_table_label}`"
        )
        st.caption(
            "🔍 **Executable SQL Query**"
            if is_en
            else "🔍 **可直接於 BigQuery 執行的 SQL 查詢**"
        )
        st.code(formatted_sql, language="sql")

        st.caption(
            "📊 **Table Data Snippet (100% Aligned with SQL Projection)**"
            if is_en
            else "📊 **圖表數據切片 (100% 依 SQL 語法即時對齊)**"
        )
        if df_preview is not None and not df_preview.empty:
            st.dataframe(df_preview, use_container_width=True)
        else:
            st.info(
                "No data returned for the selected filter."
                if is_en
                else "目前篩選區間無對應數據。"
            )
