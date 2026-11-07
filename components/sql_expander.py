"""SQL Query Expander & Table Data Snippet Contract Component."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st


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
    """Render a collapsible expander containing syntax-highlighted SQL with dynamic date injection,

    accompanied by an aligned Table Data Snippet.
    """
    display_title = (
        title_en
        if is_en
        else (title_zh or title_en or module_id)
    )
    expander_title = f"📄 {display_title}"

    # Use actual selected dates or default to full reporting window
    start_str = start_date.isoformat() if start_date else "2026-08-03"
    end_str = end_date.isoformat() if end_date else "2026-10-31"

    # Inject dynamic dates into template safely
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
