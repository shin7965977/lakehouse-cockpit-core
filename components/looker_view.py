"""Looker Enterprise View & LookML Semantic Explorer Component."""

from __future__ import annotations

import os
from typing import Any

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

try:
    from ..adapter import BaseCockpitAdapter
except ImportError:
    from adapter import BaseCockpitAdapter


def render_looker_enterprise_view(
    adapter: BaseCockpitAdapter,
    data_dict: dict[str, pd.DataFrame],
    is_en: bool = True,
) -> None:
    """Render a native enterprise Looker tab supporting live iframe embed and LookML exploration."""
    st.markdown(
        "### 📊 Looker Enterprise BI View & LookML Explorer"
        if is_en
        else "### 📊 Looker 企業級 BI 視圖與 LookML 探索器"
    )
    st.caption(
        "🌐 **Centralized Semantic Layer**: Looker directly maps BigQuery / DuckDB Gold Marts via **LookML**, "
        "enforcing a single source of truth across executive dashboards, automated pipelines, and AI copilot agents."
        if is_en
        else "🌐 **全公司單一事實來源**：Looker 透過 **LookML** 直接將金牌層模型化，"
        "為高階主管儀表板、數據管線與 AI 助理提供統一一致的指標定義。"
    )

    # Allow custom embed URL injection or environment default
    default_embed = getattr(adapter, "looker_embed_url", "")
    embed_url = st.text_input(
        "Looker Embed URL (Optional — Paste your Looker / Looker Studio Dashboard link):"
        if is_en
        else "Looker 嵌入網址（選填 — 可貼上您的 Looker 或 Looker Studio 報表連結）：",
        value=st.session_state.get("custom_looker_url", default_embed),
        placeholder="https://lookerstudio.google.com/embed/reporting/...",
        help="Supports Looker Signed SSO Embed or Looker Studio Public Embed URLs",
    )
    if embed_url:
        st.session_state["custom_looker_url"] = embed_url

    if embed_url.strip():
        st.info(f"🔗 Embedding Looker dashboard from `{embed_url.strip()}`...")
        components.iframe(src=embed_url.strip(), height=750, scrolling=True)
    else:
        # High-Fidelity Looker Dual-Panel Preview (Tabular Visualization + LookML Definition)
        l_col1, l_col2 = st.columns([6, 4])
        with l_col1:
            st.markdown(
                "#### 🏛️ Looker Tabular View: `mart_product_performance`"
                if is_en
                else "#### 🏛️ Looker 語意表格視圖：`mart_product_performance`"
            )
            df = data_dict.get("mart_product_performance", pd.DataFrame())
            if not df.empty:
                cols_to_show = [
                    c
                    for c in [
                        "product_name",
                        "category_name",
                        "packaging_size",
                        "sold_units",
                        "returned_units",
                        "return_rate_pct",
                        "net_revenue_eur",
                        "net_margin_pct",
                    ]
                    if c in df.columns
                ]
                looker_df = df[cols_to_show].copy()
                st.dataframe(looker_df, use_container_width=True, height=420)
            else:
                st.info("No data available.")

        with l_col2:
            st.markdown(
                "#### 📜 LookML Semantic Code"
                if is_en
                else "#### 📜 LookML 語意模型原始碼"
            )
            lookml_code = getattr(adapter, "lookml_view_definition", "")
            if not lookml_code:
                lookml_code = (
                    "view: mart_product_performance {\n"
                    "  sql_table_name: `koro_marts.mart_product_performance` ;;\n\n"
                    "  dimension: product_id { type: string primary_key: yes }\n"
                    "  dimension: product_name { type: string }\n"
                    "  measure: net_revenue { type: sum sql: ${TABLE}.net_revenue_eur ;; }\n"
                    "  measure: return_rate { type: number sql: ${returned_units} / ${sold_units} ;; }\n"
                    "}"
                )
            st.code(lookml_code, language="yaml")
