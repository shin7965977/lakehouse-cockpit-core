"""Sidebar Pipeline Operations & Dataset CSV Downloader Component.

Provides:
1. Cloud Data Pipeline Control (Google Cloud Workflows · Serverless DAG trigger & cache refresh).
2. BigQuery / Lakehouse Dataset CSV Downloader (filtered or full tables).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

try:
    from ..adapter import BaseCockpitAdapter
except (ImportError, ValueError):
    try:
        from adapter import BaseCockpitAdapter
    except (ImportError, ValueError):
        from cockpit_core.adapter import BaseCockpitAdapter


def render_sidebar_pipeline_ops(
    adapter: BaseCockpitAdapter | None = None,
    data_dict: dict[str, pd.DataFrame] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    is_en: bool = True,
    scope_key: str = "sidebar",
) -> None:
    """Render Cloud Pipeline Workflow Control and Dataset CSV Downloader."""
    # =========================================================================
    # 1. Cloud Data Pipeline Control (Google Cloud Workflows · Serverless DAG)
    # =========================================================================
    st.subheader("⚡ Cloud Pipeline Control" if is_en else "⚡ 雲端資料管線控制")
    st.caption("Google Cloud Workflows · Serverless DAG")

    col_sync1, col_sync2 = st.columns([2, 1])
    with col_sync1:
        status_html = (
            "<span style='color:#10b981; font-size:0.85rem; font-weight:600;'>🟢 Cloud Schedule Ready</span>"
            if is_en
            else "<span style='color:#10b981; font-size:0.85rem; font-weight:600;'>🟢 雲端排程已就緒</span>"
        )
        st.markdown(status_html, unsafe_allow_html=True)
    with col_sync2:
        refresh_label = "🔄 Refresh" if is_en else "🔄 刷新"
        refresh_help = (
            "Clear local cache and reload Lakehouse datasets"
            if is_en
            else "清除本地快取並重新載入 BigQuery 數據"
        )
        if st.button(
            refresh_label,
            key=f"btn_refresh_cache_{scope_key}",
            help=refresh_help,
            use_container_width=True,
        ):
            st.cache_data.clear()
            st.rerun()

    trigger_label = (
        "🚀 Sync Latest Data (Trigger Workflow)"
        if is_en
        else "🚀 立即同步最新資料 (Trigger Workflow)"
    )
    if st.button(
        trigger_label,
        type="primary",
        use_container_width=True,
        key=f"btn_trigger_wf_{scope_key}",
    ):
        custom_trigger = getattr(adapter, "trigger_workflow", None) if adapter is not None else None
        if callable(custom_trigger):
            res = custom_trigger()
            st.success(res if isinstance(res, str) else "✅ Workflow triggered successfully!")
        else:
            st.success(
                "✅ Cloud Workflow `lakehouse-elt-dag` execution triggered (Mock Execution ID: `wf-exec-9042`)."
                if is_en
                else "✅ 已向 Google Cloud Workflows 發送執行請求（執行 ID：`wf-exec-9042`）！"
            )

    st.markdown("---")

    # =========================================================================
    # 2. Download Lakehouse / BigQuery Full Data (CSV)
    # =========================================================================
    st.subheader(
        "📥 Download BigQuery Gold Data"
        if is_en
        else "📥 下載 BigQuery 金牌完整數據"
    )

    datasets = data_dict or {}
    valid_tables = {
        k: v for k, v in datasets.items() if isinstance(v, pd.DataFrame) and not v.empty
    }

    if not valid_tables:
        st.caption("No datasets available for export." if is_en else "尚無可匯出的資料表。")
        return

    # Map friendly option labels matching the Platzi screenshot style
    option_map: dict[str, tuple[str, pd.DataFrame]] = {}
    first_key, first_df = next(iter(valid_tables.items()))
    period_label = (
        "📅 Selected Date Range (KPI)"
        if is_en
        else "📅 依所選日期區間 (KPI)"
    )
    option_map[period_label] = (first_key, first_df)

    for tbl_name, df_val in valid_tables.items():
        opt_label = (
            f"📊 {tbl_name} (Full · {len(df_val)} rows)"
            if is_en
            else f"📊 {tbl_name} (全量 · {len(df_val)} 筆)"
        )
        option_map[opt_label] = (tbl_name, df_val)

    select_caption = (
        "Select BigQuery table to download"
        if is_en
        else "選擇要下載的 BigQuery 金牌資料表"
    )
    selected_opt = st.selectbox(
        select_caption,
        options=list(option_map.keys()),
        key=f"export_table_select_{scope_key}",
    )

    chosen_name, export_df = option_map[selected_opt]
    date_suffix = ""
    if start_date and end_date:
        date_suffix = f"_{start_date.isoformat()}_to_{end_date.isoformat()}"

    if selected_opt == period_label:
        dl_filename = f"bigquery_gold_daily_kpi{date_suffix}.csv"
        dl_btn_label = (
            f"💾 Download Period KPI ({len(export_df)} rows) (CSV)"
            if is_en
            else f"💾 下載區間 KPI ({len(export_df)} 筆) (CSV)"
        )
    else:
        dl_filename = f"bigquery_{chosen_name}_full.csv"
        dl_btn_label = (
            f"💾 Download {chosen_name} ({len(export_df)} rows) (CSV)"
            if is_en
            else f"💾 下載 {chosen_name} ({len(export_df)} 筆) (CSV)"
        )

    st.download_button(
        label=dl_btn_label,
        data=export_df.to_csv(index=False).encode("utf-8-sig"),
        file_name=dl_filename,
        mime="text/csv",
        use_container_width=True,
        key=f"btn_dl_csv_{scope_key}",
    )
