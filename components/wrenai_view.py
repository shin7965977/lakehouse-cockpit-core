"""WrenAI Embedded Generative BI (Ad-Hoc Text-to-Chart) View Component."""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

try:
    from ..adapter import BaseCockpitAdapter
except (ImportError, ValueError):
    try:
        from adapter import BaseCockpitAdapter
    except (ImportError, ValueError):
        from cockpit_core.adapter import BaseCockpitAdapter


def render_wrenai_view(
    adapter: BaseCockpitAdapter | None = None,
    data_dict: dict[str, pd.DataFrame] | None = None,
    is_en: bool = True,
) -> None:
    """Render an embedded WrenAI GenBI iframe for ad-hoc natural language charting."""
    st.markdown(
        "### 📊 WrenAI Generative BI (Ad-Hoc Text-to-Chart)"
        if is_en
        else "### 📊 WrenAI 生成式 BI（Ad-Hoc 自然語言生圖）"
    )
    st.caption(
        "Delegate ad-hoc multi-table charting to open-source WrenAI (Apache-2.0) backed by your dbt-synced MDL semantic layer."
        if is_en
        else "將臨時多表查詢與自然語言生圖委派給開源 WrenAI（Apache-2.0），並由 dbt 同步的 MDL 語意層提供精準指標口徑。"
    )

    default_url = (
        getattr(adapter, "wrenai_embed_url", "")
        or os.getenv("WRENAI_EMBED_URL", "")
        or ""
    ).strip()

    wren_url = st.text_input(
        "WrenAI Embed / Self-Hosted URL (Optional — paste your WrenAI Embedded Thread or Docker UI URL):"
        if is_en
        else "WrenAI 嵌入或自架網址（選填 — 可貼上 WrenAI Embedded Thread 或自架 Docker UI 網址）：",
        value=default_url,
        placeholder="https://cloud-st.getwren.ai/iframe/<project-id>  or  http://localhost:3000",
        key="wrenai_embed_url_input",
    ).strip()

    if wren_url:
        if hasattr(st, "iframe"):
            st.iframe(wren_url, height=720)
        else:
            components.iframe(wren_url, height=720, scrolling=True)
        return

    # Helpful setup & dbt sync guide when no live WrenAI endpoint is configured yet
    st.info(
        "No WrenAI URL configured yet. Paste a WrenAI Embedded Thread URL or local Docker URL above to enable live ad-hoc charting."
        if is_en
        else "尚未填入 WrenAI 網址。請於上方貼上 WrenAI Embedded Thread 或本地自架 Docker 網址，即可立即啟用 Ad-Hoc 自然語言生圖。"
    )

    col_setup, col_sync = st.columns(2)
    with col_setup:
        st.markdown(
            "##### 🐳 1. Self-Host WrenAI (Free / Apache-2.0)"
            if is_en
            else "##### 🐳 1. 免費自架 WrenAI（Apache-2.0 開源）"
        )
        st.code(
            "# Download & launch WrenAI via official launcher\n"
            "curl -L https://github.com/Canner/WrenAI/releases/latest/download/wren-launcher-darwin.tar.gz | tar -xz\n"
            "./wren-launcher\n"
            "# Default Web UI: http://localhost:3000",
            language="bash",
        )

    with col_sync:
        st.markdown(
            "##### 🔗 2. Sync dbt Models into WrenAI MDL"
            if is_en
            else "##### 🔗 2. 從 dbt 單向同步至 WrenAI MDL 語意層"
        )
        st.code(
            "# Generate dbt manifest.json & catalog.json\n"
            "dbt build && dbt docs generate\n\n"
            "# Compile dbt models directly into WrenAI MDL\n"
            "wren dbt create   # initial sync\n"
            "wren dbt update   # CI incremental sync",
            language="bash",
        )

    if data_dict:
        table_names = [
            k for k, v in data_dict.items() if isinstance(v, pd.DataFrame) and not v.empty
        ]
        if table_names:
            st.caption(
                f"Active Lakehouse tables ready for WrenAI MDL sync ({len(table_names)}): "
                + ", ".join(f"`{t}`" for t in table_names)
                if is_en
                else f"目前湖倉可同步至 WrenAI MDL 的資料表（共 {len(table_names)} 張）："
                + "、".join(f"`{t}`" for t in table_names)
            )
