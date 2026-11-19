"""Natural Language AI Smart Chart Generator (Text-to-Chart) Component.

Ported and generalized from `data-pipeline-consulting-demo/chart_generator.py`
for the reusable `lakehouse-cockpit-core` microkernel.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

try:
    from ..adapter import BaseCockpitAdapter
except (ImportError, ValueError):
    try:
        from adapter import BaseCockpitAdapter
    except (ImportError, ValueError):
        from cockpit_core.adapter import BaseCockpitAdapter


BUSINESS_CHART_KEYWORDS = (
    "銷售",
    "營收",
    "gmv",
    "訂單",
    "商品",
    "客戶",
    "業績",
    "退貨",
    "退款",
    "排行",
    "金額",
    "單價",
    "aov",
    "品類",
    "區域",
    "地區",
    "數據",
    "指標",
    "圖",
    "走勢",
    "趨勢",
    "圓餅",
    "長條",
    "柱狀",
    "佔比",
    "比較",
    "分析",
    "chart",
    "plot",
    "revenue",
    "sales",
    "order",
    "product",
    "category",
    "region",
    "return",
    "trend",
    "bar",
    "pie",
    "line",
)


def generate_chart_from_nl(
    prompt: str,
    data_dict: dict[str, pd.DataFrame],
    is_en: bool = False,
) -> tuple[go.Figure | None, str, str, pd.DataFrame]:
    """Parse a natural-language prompt and synthesize an interactive Plotly chart, title, insight, and DataFrame."""
    clean_prompt = prompt.strip().lower()

    if not any(kw in clean_prompt for kw in BUSINESS_CHART_KEYWORDS):
        if is_en:
            return (
                None,
                "⚠️ Domain Scope Guardrail",
                (
                    f'Your query *"{prompt}"* is unrelated to Lakehouse business metrics, orders, categories, or revenue.\n\n'
                    "💡 **Suggested prompts:**\n"
                    '- 📈 *"Show daily GMV revenue trend"*\n'
                    '- 🏆 *"Plot category GMV comparison bar chart"*\n'
                    '- ⚠️ *"Compare return rate by category"*\n'
                    '- 🥧 *"Show revenue share by region in a pie chart"*'
                ),
                pd.DataFrame(),
            )
        return (
            None,
            "⚠️ 業務範疇約束提醒",
            (
                f"您輸入的提問 *「{prompt}」* 與營運銷售、品類、訂單或退貨指標無關，因此無法生成圖表。\n\n"
                "💡 **建議您可以嘗試以下指令：**\n"
                "- 📈 *「請畫出每日 GMV 營收趨勢走勢圖」*\n"
                "- 🏆 *「用長條圖呈現各品類 GMV 銷售額排行」*\n"
                "- ⚠️ *「比較各品類的退貨率表現」*\n"
                "- 🥧 *「用圓餅圖呈現各區域營收佔比」*"
            ),
            pd.DataFrame(),
        )

    fact_df = data_dict.get("fact_orders", pd.DataFrame())
    mart_df = data_dict.get("mart_product_performance", pd.DataFrame())

    # 1. Pie / Share by Region or Category
    if any(kw in clean_prompt for kw in ("圓餅", "佔比", "地區", "區域", "pie", "region", "share")):
        if not fact_df.empty and {"region", "gross_amount"}.issubset(fact_df.columns):
            reg_df = (
                fact_df.groupby("region", as_index=False)["gross_amount"]
                .sum()
                .sort_values("gross_amount", ascending=False)
            )
            title = "Revenue Share by Region" if is_en else "各區域 GMV 營收貢獻佔比（圓餅圖）"
            fig = px.pie(
                reg_df,
                names="region",
                values="gross_amount",
                hole=0.4,
                title=title,
                template="plotly_dark",
            )
            fig.update_layout(height=360, margin={"l": 20, "r": 20, "t": 45, "b": 20})
            top_row = reg_df.iloc[0]
            total = reg_df["gross_amount"].sum()
            share = (top_row["gross_amount"] / total * 100) if total > 0 else 0.0
            insight = (
                f"Top region is **{top_row['region']}** (${top_row['gross_amount']:,.0f}, {share:.1f}% of total GMV)."
                if is_en
                else f"營收貢獻最高區域為 **{top_row['region']}**（累計 ${top_row['gross_amount']:,.0f}，佔全區總額 {share:.1f}%）。"
            )
            return fig, title, insight, reg_df

    # 2. Return Rate / Refund monitoring
    if any(kw in clean_prompt for kw in ("退貨", "退款", "異常", "return", "refund")):
        if not mart_df.empty and {"category", "return_rate_pct"}.issubset(mart_df.columns):
            sub_df = mart_df.sort_values("return_rate_pct", ascending=False)
            title = "Return Rate (%) by Category" if is_en else "各品類退貨率監控比較（%）"
            fig = px.bar(
                sub_df,
                x="category",
                y="return_rate_pct",
                color="return_rate_pct",
                color_continuous_scale="Reds",
                title=title,
                template="plotly_dark",
            )
            fig.update_layout(height=360, margin={"l": 20, "r": 20, "t": 45, "b": 20})
            worst = sub_df.iloc[0]
            insight = (
                f"Highest return rate is **{worst['category']}** at {worst['return_rate_pct']:.1f}%. Prioritize SKU quality review."
                if is_en
                else f"**{worst['category']}** 品類退貨率最高（達 {worst['return_rate_pct']:.1f}%），建議優先檢視該品類高單價商品規格與包裝。"
            )
            return fig, title, insight, sub_df

    # 3. Category / Product Bar Chart
    if any(kw in clean_prompt for kw in ("品類", "商品", "長條", "柱狀", "排行", "category", "product", "bar")):
        if not mart_df.empty and {"category", "total_gmv"}.issubset(mart_df.columns):
            sub_df = mart_df.sort_values("total_gmv", ascending=False)
            title = "GMV Ranking by Category ($)" if is_en else "各品類 GMV 銷售額排行（長條圖）"
            fig = px.bar(
                sub_df,
                x="category",
                y="total_gmv",
                color="category",
                title=title,
                template="plotly_dark",
            )
            fig.update_layout(height=360, margin={"l": 20, "r": 20, "t": 45, "b": 20})
            top_cat = sub_df.iloc[0]
            insight = (
                f"**{top_cat['category']}** leads with ${top_cat['total_gmv']:,.0f} across {int(top_cat['order_count'])} orders."
                if is_en
                else f"**{top_cat['category']}** 穩居銷售冠軍，累計 GMV 達 ${top_cat['total_gmv']:,.0f}（共 {int(top_cat['order_count'])} 筆訂單）。"
            )
            return fig, title, insight, sub_df

    # 4. Default: Daily GMV & Order Trend
    if not fact_df.empty and {"event_date", "gross_amount"}.issubset(fact_df.columns):
        daily_df = (
            fact_df.groupby("event_date", as_index=False)
            .agg(daily_gmv=("gross_amount", "sum"), orders=("order_id", "count"))
            .sort_values("event_date")
        )
        title = "Daily GMV Revenue Trend ($)" if is_en else "每日 GMV 營收趨勢走勢圖 ($)"
        fig = px.area(
            daily_df,
            x="event_date",
            y="daily_gmv",
            markers=True,
            title=title,
            template="plotly_dark",
        )
        fig.update_layout(height=360, margin={"l": 20, "r": 20, "t": 45, "b": 20})
        total_gmv = daily_df["daily_gmv"].sum()
        peak_row = daily_df.loc[daily_df["daily_gmv"].idxmax()]
        insight = (
            f"Cumulative GMV reached **${total_gmv:,.0f}**, peaking on `{peak_row['event_date']}` at **${peak_row['daily_gmv']:,.0f}**."
            if is_en
            else f"期間累計 GMV 達 **${total_gmv:,.0f}**，單日營收高峰出現在 `{peak_row['event_date']}`（達 **${peak_row['daily_gmv']:,.0f}**）。"
        )
        return fig, title, insight, daily_df

    return None, "No Data Available", "Dataset is empty.", pd.DataFrame()


def render_text_to_chart_module(
    adapter: BaseCockpitAdapter | None = None,
    data_dict: dict[str, pd.DataFrame] | None = None,
    is_en: bool = True,
    scope_key: str = "default",
) -> None:
    """Render the interactive Natural Language AI Smart Chart Generator (Text-to-Chart) widget."""
    st.subheader(
        "✨ Natural Language AI Chart Generator (Text-to-Chart)"
        if is_en
        else "✨ 自然語言 AI 智能圖表生成器 (Text-to-Chart)"
    )
    st.caption(
        "🤖 Enter any business chart request in natural language to synthesize an interactive Plotly visualization and consulting takeaway."
        if is_en
        else "🤖 支援自然語言輸入圖表需求，AI 即時解析意圖、選取指標欄位並繪製互動式 Plotly 圖表與顧問洞察摘要。"
    )

    state_query_key = f"nl_chart_query_{scope_key}"
    input_widget_key = f"nl_chart_input_{scope_key}"

    default_prompt = (
        "Show daily GMV revenue trend"
        if is_en
        else "請畫出每日 GMV 營收趨勢走勢圖"
    )
    if state_query_key not in st.session_state:
        st.session_state[state_query_key] = default_prompt

    def _set_preset_query(q: str) -> None:
        st.session_state[state_query_key] = q
        st.session_state[input_widget_key] = q

    st.markdown("##### 💡 Click an example prompt:" if is_en else "##### 💡 點擊範例指令立即生圖：")
    presets = (
        [
            ("📈 Daily GMV Trend", "Show daily GMV revenue trend"),
            ("🏆 Category GMV Bar Chart", "Plot category GMV comparison bar chart"),
            ("⚠️ Category Return Rate", "Compare return rate by category"),
            ("🥧 Regional Share Pie Chart", "Show revenue share by region in a pie chart"),
        ]
        if is_en
        else [
            ("📈 每日 GMV 趨勢走勢", "請畫出每日 GMV 營收趨勢走勢圖"),
            ("🏆 各品類銷售額長條圖", "用長條圖呈現各品類 GMV 銷售額排行"),
            ("⚠️ 各品類退貨率監控", "比較各品類的退貨率表現"),
            ("🥧 各區域營收佔比圓餅圖", "用圓餅圖呈現各區域營收佔比"),
        ]
    )

    cols = st.columns(len(presets))
    for idx, (btn_label, preset_prompt) in enumerate(presets):
        with cols[idx]:
            if st.button(
                btn_label,
                key=f"btn_nl_preset_{scope_key}_{idx}",
                use_container_width=True,
            ):
                _set_preset_query(preset_prompt)
                st.rerun()

    in_col, btn_col = st.columns([8, 2])
    with in_col:
        user_input = st.text_input(
            label="Natural Language Chart Prompt",
            value=st.session_state[state_query_key],
            placeholder=(
                "e.g., Compare return rate by category..."
                if is_en
                else "例如：用長條圖呈現各品類 GMV 銷售額排行..."
            ),
            label_visibility="collapsed",
            key=input_widget_key,
        )
    with btn_col:
        gen_label = "🚀 Generate" if is_en else "🚀 立即生圖"
        if st.button(
            gen_label,
            key=f"btn_nl_generate_{scope_key}",
            type="primary",
            use_container_width=True,
        ):
            if user_input and user_input.strip():
                st.session_state[state_query_key] = user_input.strip()
                st.rerun()

    active_prompt = (user_input or st.session_state[state_query_key]).strip() or default_prompt

    # Allow domain adapter override if implemented, otherwise use built-in engine
    custom_fn = getattr(adapter, "generate_nl_chart", None) if adapter is not None else None
    if callable(custom_fn):
        fig, chart_title, insight_text, raw_df = custom_fn(
            prompt=active_prompt,
            data_dict=data_dict or {},
            is_en=is_en,
        )
    else:
        fig, chart_title, insight_text, raw_df = generate_chart_from_nl(
            prompt=active_prompt,
            data_dict=data_dict or {},
            is_en=is_en,
        )

    st.markdown("---")
    if fig is None:
        st.warning(f"### {chart_title}")
        st.markdown(insight_text)
    else:
        st.markdown(f"#### 📊 {chart_title}")
        st.info(f"💡 **AI Takeaway**: {insight_text}")
        st.plotly_chart(fig, use_container_width=True)
        if isinstance(raw_df, pd.DataFrame) and not raw_df.empty:
            with st.expander(
                "📋 Underlying Chart Data" if is_en else "📋 檢視圖表底層數據明細",
                expanded=False,
            ):
                st.dataframe(raw_df, use_container_width=True)


# ==============================================================================
# Shared Schema-Agnostic Gemini AI NL2Chart Core Engine
# ==============================================================================
def _discover_gemini_flash_models(client) -> list[str]:
    """Return ordered cascade list of available Gemini Flash models."""
    import re

    discovered: list[str] = []
    try:
        for m in client.models.list():
            m_name = m.name.replace("models/", "")
            if "flash" in m_name.lower() and not any(
                bad in m_name.lower()
                for bad in ("omni", "embed", "imagen", "tts", "stt", "realtime")
            ):
                discovered.append(m_name)

        def _score(name: str) -> float:
            nums = re.findall(r"(\d+(?:\.\d+)?)", name)
            return float(nums[0]) if nums else 0.0

        discovered.sort(key=_score, reverse=True)
    except Exception:  # noqa: BLE001
        pass

    fallbacks = ["gemini-2.5-flash", "gemini-2.0-flash"]
    candidates = discovered if discovered else list(fallbacks)
    for fb in fallbacks:
        if fb not in candidates:
            candidates.append(fb)
    return candidates


def generate_ai_chart_config(
    query: str,
    dataset_schemas: dict[str, dict[str, str]],
    api_key: str | None = None,
    domain_context: str = "",
    fallback_fn=None,
) -> dict:
    """Schema-agnostic Gemini AI NL2Chart engine with strict column whitelist validation.

    Shared across all Lakehouse Cockpit projects (ReadCall, Consulting Demo, etc.).
    """
    import json
    import os

    clean_key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()
    if clean_key and dataset_schemas:
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=clean_key)
            candidates = _discover_gemini_flash_models(client)
            schema_doc = json.dumps(dataset_schemas, ensure_ascii=False, indent=2)
            ds_list = " | ".join(f'"{k}"' for k in dataset_schemas)

            system_prompt = (
                f"You are a Product & Business Analytics NL2Chart engine. {domain_context}\n"
                "Given a user's chart request in Chinese or English, return ONLY a valid JSON object:\n"
                "{\n"
                f'  "dataset": {ds_list},\n'
                '  "chart_type": "line" | "bar" | "horizontal_bar",\n'
                '  "x": "<exact column name from chosen dataset>",\n'
                '  "y": ["<exact column name(s) from chosen dataset>"],\n'
                '  "title": "<concise chart title matching the user language>",\n'
                '  "takeaway": "<1-sentence explanation of what this chart measures, matching user language>",\n'
                '  "limit": 12\n'
                "}\n\n"
                "Rules:\n"
                "1. Use ONLY exact column names from the chosen dataset in DATASET_SCHEMAS. Never invent columns.\n"
                "2. For time-series datasets containing 'event_date', set x='event_date' and y to 1-3 numeric columns.\n"
                "3. For ranking/top-N categorical datasets, set chart_type='horizontal_bar' and 'limit' to requested top-N.\n"
                "4. For sparse daily conversion/failure rates, prefer chart_type='bar' or 'line' depending on user intent.\n\n"
                f"DATASET_SCHEMAS:\n{schema_doc}"
            )

            for model_name in candidates:
                try:
                    resp = client.models.generate_content(
                        model=model_name,
                        contents=f"User chart request: {query}",
                        config=types.GenerateContentConfig(
                            system_instruction=system_prompt,
                            temperature=0.0,
                            response_mime_type="application/json",
                        ),
                    )
                    if not resp or not resp.text:
                        continue
                    parsed = json.loads(resp.text.strip())
                    ds = str(parsed.get("dataset", "")).strip().lower()
                    if ds not in dataset_schemas:
                        continue

                    valid_cols = set(dataset_schemas[ds].keys())
                    ctype = str(parsed.get("chart_type", "line")).strip().lower()
                    if ctype not in ("line", "bar", "horizontal_bar"):
                        ctype = "line"

                    if ctype == "horizontal_bar" and "event_name" in valid_cols:
                        limit_val = max(1, min(int(parsed.get("limit", 12)), 32))
                        return {
                            "dataset": ds,
                            "chart_type": "horizontal_bar",
                            "x": "total_event_count"
                            if "total_event_count" in valid_cols
                            else next(iter(valid_cols)),
                            "y": "event_name",
                            "color": "module_category"
                            if "module_category" in valid_cols
                            else None,
                            "title": str(parsed.get("title") or query),
                            "takeaway": str(parsed.get("takeaway") or ""),
                            "limit": limit_val,
                            "query": query,
                            "generated_by": f"gemini:{model_name}",
                        }

                    x_col = str(parsed.get("x", "event_date")).strip()
                    if x_col not in valid_cols:
                        x_col = (
                            "event_date"
                            if "event_date" in valid_cols
                            else next(iter(valid_cols))
                        )

                    raw_y = parsed.get("y", [])
                    if isinstance(raw_y, str):
                        raw_y = [raw_y]
                    y_cols = [
                        c
                        for c in raw_y
                        if isinstance(c, str) and c in valid_cols and c != x_col
                    ]
                    if not y_cols:
                        continue

                    return {
                        "dataset": ds,
                        "chart_type": ctype,
                        "x": x_col,
                        "y": y_cols[:3],
                        "title": str(parsed.get("title") or query),
                        "takeaway": str(parsed.get("takeaway") or ""),
                        "query": query,
                        "generated_by": f"gemini:{model_name}",
                    }
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass

    if callable(fallback_fn):
        return fallback_fn(query)
    first_ds = next(iter(dataset_schemas)) if dataset_schemas else "kpi"
    return {
        "dataset": first_ds,
        "chart_type": "line",
        "x": "event_date",
        "y": [],
        "title": query,
        "query": query,
        "generated_by": "fallback_rules",
    }


def render_ai_dynamic_chart(config: dict, df: pd.DataFrame) -> go.Figure | None:
    """Shared Plotly renderer with percentage formatting for rate metrics & semantic failure coloring."""
    if df is None or df.empty or not config:
        return None

    ctype = config.get("chart_type", "line")
    title = config.get("title", "AI Generated Chart")

    if ctype == "horizontal_bar" and "event_name" in df.columns:
        limit_n = max(1, min(int(config.get("limit", 12)), 32))
        x_val = config.get("x", "total_event_count")
        if x_val not in df.columns:
            return None
        sub = df.sort_values(x_val, ascending=False).head(limit_n).copy()
        color_col = config.get("color")
        fig = px.bar(
            sub,
            x=x_val,
            y="event_name",
            color=color_col if color_col and color_col in sub.columns else None,
            orientation="h",
            title=title,
            template="plotly_white",
        )
        fig.update_layout(
            yaxis={"categoryorder": "total ascending"},
            height=420,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        return fig

    x_col = config.get("x", "event_date")
    y_cols = config.get("y", [])
    if isinstance(y_cols, str):
        y_cols = [y_cols]
    avail_y = [c for c in y_cols if c in df.columns]
    if not avail_y or x_col not in df.columns:
        return None

    is_rate_chart = all(
        any(tok in c.lower() for tok in ("rate", "ratio", "pct", "share"))
        for c in avail_y
    )

    sub = df.sort_values(x_col).copy()
    fig = go.Figure()
    palette = ["#2563eb", "#10b981", "#f59e0b", "#8b5cf6"]
    for idx, col in enumerate(avail_y):
        is_neg = any(
            neg in col.lower() for neg in ("fail", "error", "return", "churn", "drop")
        )
        color = "#ef4444" if is_neg else palette[idx % len(palette)]
        if ctype == "bar":
            fig.add_trace(
                go.Bar(x=sub[x_col], y=sub[col], name=col, marker_color=color)
            )
        else:
            fig.add_trace(
                go.Scatter(
                    x=sub[x_col],
                    y=sub[col],
                    mode="lines+markers",
                    name=col,
                    line={
                        "color": color,
                        "width": 2.5,
                        "shape": "linear" if is_rate_chart else "spline",
                    },
                )
            )

    layout_kwargs = {
        "barmode": "group",
        "title": title,
        "template": "plotly_white",
        "height": 390,
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "hovermode": "x unified",
        "legend": {"orientation": "h", "y": 1.1},
    }
    if is_rate_chart:
        layout_kwargs["yaxis"] = {"tickformat": ".0%"}
    fig.update_layout(**layout_kwargs)
    return fig


def refine_ai_chart_config(
    current_config: dict,
    user_message: str,
    chat_history: list[dict[str, str]],
    dataset_schemas: dict[str, dict[str, str]],
    api_key: str | None = None,
    domain_context: str = "",
    is_en: bool = False,
) -> tuple[dict, str]:
    """Refine an existing chart config or answer user questions in a multi-turn HITL loop."""
    import json
    import os
    import re

    updated = dict(current_config)
    clean_key = (api_key or os.getenv("GEMINI_API_KEY", "")).strip()

    if clean_key and dataset_schemas:
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=clean_key)
            candidates = _discover_gemini_flash_models(client)
            schema_doc = json.dumps(dataset_schemas, ensure_ascii=False, indent=2)
            hist_text = "\n".join(
                f"{m['role'].upper()}: {m['content']}" for m in chat_history[-8:]
            )
            system_prompt = (
                f"You are a Human-in-the-Loop BI Chart Spec Assistant. {domain_context}\n"
                "The user is inspecting a pending Plotly chart specification BEFORE rendering it.\n"
                "They may ask a question (e.g. why a column was chosen, what a metric means) OR ask to modify parameters (e.g. switch to bar chart, change X/Y columns, change top-N limit).\n"
                "Return ONLY a valid JSON object:\n"
                "{\n"
                '  "reply": "<concise helpful answer/confirmation in the user language>",\n'
                '  "config": {\n'
                '    "dataset": "<valid dataset key>",\n'
                '    "chart_type": "line" | "bar" | "horizontal_bar",\n'
                '    "x": "<valid column>",\n'
                '    "y": ["<valid column(s)>"],\n'
                '    "color": "<optional valid column or null>",\n'
                '    "limit": 12,\n'
                '    "title": "<chart title>",\n'
                '    "takeaway": "<1-sentence takeaway>"\n'
                "  }\n"
                "}\n"
                "Rules: Only use exact columns from DATASET_SCHEMAS. Preserve unchanged fields from current_config.\n\n"
                f"DATASET_SCHEMAS:\n{schema_doc}"
            )
            contents = (
                f"Current Chart Spec:\n{json.dumps(current_config, ensure_ascii=False, indent=2)}\n\n"
                f"Conversation History:\n{hist_text}\n\n"
                f"New User Message: {user_message}"
            )

            for model_name in candidates:
                try:
                    resp = client.models.generate_content(
                        model=model_name,
                        contents=contents,
                        config=types.GenerateContentConfig(
                            system_instruction=system_prompt,
                            temperature=0.1,
                            response_mime_type="application/json",
                        ),
                    )
                    if not resp or not resp.text:
                        continue
                    parsed = json.loads(resp.text.strip())
                    reply = str(parsed.get("reply") or "").strip()
                    new_cfg = parsed.get("config") or {}
                    ds = str(new_cfg.get("dataset", updated.get("dataset", ""))).strip().lower()
                    if ds in dataset_schemas:
                        valid_cols = set(dataset_schemas[ds].keys())
                        updated["dataset"] = ds
                        ctype = str(
                            new_cfg.get("chart_type", updated.get("chart_type", "line"))
                        ).strip().lower()
                        if ctype in ("line", "bar", "horizontal_bar"):
                            updated["chart_type"] = ctype
                        x_c = str(new_cfg.get("x", updated.get("x", ""))).strip()
                        if x_c in valid_cols:
                            updated["x"] = x_c
                        raw_y = new_cfg.get("y", updated.get("y", []))
                        if isinstance(raw_y, str):
                            raw_y = [raw_y]
                        valid_y = [c for c in raw_y if isinstance(c, str) and c in valid_cols]
                        if valid_y:
                            updated["y"] = (
                                valid_y[0]
                                if updated.get("chart_type") == "horizontal_bar"
                                and "event_name" in valid_y
                                else valid_y[:3]
                            )
                        col_c = new_cfg.get("color")
                        if col_c in valid_cols:
                            updated["color"] = col_c
                        elif col_c is None:
                            updated.pop("color", None)
                        if "limit" in new_cfg and new_cfg["limit"]:
                            updated["limit"] = max(1, min(int(new_cfg["limit"]), 32))
                        if new_cfg.get("title"):
                            updated["title"] = str(new_cfg["title"])
                        if new_cfg.get("takeaway"):
                            updated["takeaway"] = str(new_cfg["takeaway"])
                        updated["generated_by"] = f"gemini:{model_name}"
                    return updated, reply or (
                        "Updated chart specification above."
                        if is_en
                        else "已依據您的補充更新上方圖表參數。"
                    )
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass

    # Deterministic offline refinement fallback (no API key needed for simple tweaks)
    msg_l = user_message.strip().lower()
    changes: list[str] = []
    if any(k in msg_l for k in ("長條", "柱狀", "bar")):
        if updated.get("chart_type") != "horizontal_bar":
            updated["chart_type"] = "bar"
            changes.append("圖表類型 ➔ 長條圖 (bar)" if not is_en else "chart_type -> bar")
    elif any(k in msg_l for k in ("折線", "趨勢線", "line")):
        updated["chart_type"] = "line"
        changes.append("圖表類型 ➔ 折線圖 (line)" if not is_en else "chart_type -> line")

    m_top = re.search(r"(?:top|前)\s*(\d+)", msg_l)
    if m_top:
        n_lim = max(1, min(int(m_top.group(1)), 32))
        updated["limit"] = n_lim
        changes.append(f"筆數上限 ➔ Top {n_lim}" if not is_en else f"limit -> Top {n_lim}")

    if changes:
        reply_txt = (
            ("Updated: " + ", ".join(changes) + ". Click 'Render Chart' when ready.")
            if is_en
            else ("已套用調整：" + "、".join(changes) + "。確認無誤後可點擊下方「產生圖表」。")
        )
    else:
        reply_txt = (
            "Tip: You can directly adjust X/Y columns or chart type in the form above, or bind your Gemini API Key on the right for full AI conversational Q&A."
            if is_en
            else "提示：您可以直接在上方表單手動調整 X/Y 軸與圖表類型；若於右側綁定 Gemini API Key，即可進行完整 AI 疑問解答與自然語言微調。"
        )
    return updated, reply_txt


def _format_col_label(col: str, schema_map: dict[str, str]) -> str:
    desc = schema_map.get(col, "")
    return f"{col}（{desc}）" if desc else col


def _render_nl2chart_studio_body(
    dataset_schemas: dict[str, dict[str, str]],
    dataset_getters: dict[str, Any],
    api_key: str = "",
    domain_context: str = "",
    presets: list[dict[str, str]] | None = None,
    fallback_fn=None,
    is_en: bool = False,
    state_prefix: str = "nl2chart_hitl",
) -> None:
    """Core body of the 2-step Human-in-the-Loop NL2Chart Studio."""
    k_query = f"{state_prefix}_query"
    k_draft = f"{state_prefix}_draft_cfg"
    k_chat = f"{state_prefix}_chat_hist"
    k_rendered = f"{state_prefix}_rendered_cfg"

    if k_query not in st.session_state:
        st.session_state[k_query] = (
            presets[0]["query"] if presets else "Show daily active users trend"
        )
    if k_chat not in st.session_state:
        st.session_state[k_chat] = []

    # Optional preset quick buttons
    if presets:
        cols_p = st.columns(len(presets))
        for idx, p in enumerate(presets):
            if cols_p[idx].button(
                p["label"],
                key=f"{state_prefix}_preset_{idx}",
                use_container_width=True,
            ):
                st.session_state[k_query] = p["query"]
                cfg = generate_ai_chart_config(
                    query=p["query"],
                    dataset_schemas=dataset_schemas,
                    api_key=api_key,
                    domain_context=domain_context,
                    fallback_fn=fallback_fn,
                )
                st.session_state[k_draft] = cfg
                st.session_state[k_chat] = [
                    {
                        "role": "assistant",
                        "content": (
                            f"Drafted chart spec for **{p['query']}**. Review or refine parameters below, then click **Render Chart**."
                            if is_en
                            else f"已為「**{p['query']}**」解析出圖表參數規格！請確認下方 X/Y 軸與其他參數，若有疑問可直接補充，確認無誤後再點擊「**產生圖表**」。"
                        ),
                    }
                ]

    # Step 1: Natural Language Request Input
    st.markdown(
        f"**{'Step 1 · Enter chart request:' if is_en else '步驟 1 · 輸入圖表需求：'}**"
    )
    c_in, c_btn = st.columns([4.6, 1.4])
    with c_in:
        nl_q = st.text_input(
            "Enter chart request:" if is_en else "輸入圖表需求：",
            key=k_query,
            label_visibility="collapsed",
        )
    with c_btn:
        btn_analyze = st.button(
            "🔍 " + ("Analyze Spec" if is_en else "解析圖表參數"),
            key=f"{state_prefix}_btn_analyze",
            type="primary",
            use_container_width=True,
        )

    if btn_analyze and nl_q.strip():
        with st.spinner(
            "🤖 Analyzing chart parameters..."
            if is_en
            else "🤖 正在解析 X/Y 軸與圖表參數規格..."
        ):
            cfg = generate_ai_chart_config(
                query=nl_q.strip(),
                dataset_schemas=dataset_schemas,
                api_key=api_key,
                domain_context=domain_context,
                fallback_fn=fallback_fn,
            )
        st.session_state[k_draft] = cfg
        ds_k = cfg.get("dataset", "")
        x_v = cfg.get("x", "")
        y_v = cfg.get("y", [])
        y_str = ", ".join(y_v) if isinstance(y_v, list) else str(y_v)
        st.session_state[k_chat] = [
            {
                "role": "assistant",
                "content": (
                    f"Proposed spec from `{ds_k}`: X=`{x_v}`, Y=`{y_str}`, Type=`{cfg.get('chart_type')}`. "
                    "Ask any question or request edits below before rendering!"
                    if is_en
                    else f"已從 `{ds_k}` 規劃初步規格：X軸=`{x_v}`、Y軸=`{y_str}`、類型=`{cfg.get('chart_type')}`。若對欄位或計算方式有疑問，可直接在下方對話補充，確認後再按「產生圖表」！"
                ),
            }
        ]

    # Step 2: Interactive Chart Spec Confirmation Form + Accumulating Q&A Box
    draft_cfg = st.session_state.get(k_draft)
    if draft_cfg:
        ds_keys = list(dataset_schemas.keys())
        cur_ds = draft_cfg.get("dataset", ds_keys[0])
        if cur_ds not in dataset_schemas:
            cur_ds = ds_keys[0]
        cur_schema = dataset_schemas[cur_ds]
        all_cols = list(cur_schema.keys())

        with st.container(border=True):
            st.markdown(
                "#### 📋 "
                + (
                    "Step 2 · Review Chart Parameters & Refine (Before Plotting)"
                    if is_en
                    else "步驟 2 · 圖表參數確認與補充對話（確認後才繪圖）"
                )
            )

            # Readable summary banner matching user's exact requested format
            raw_y_disp = draft_cfg.get("y", [])
            y_list_disp = [raw_y_disp] if isinstance(raw_y_disp, str) else list(raw_y_disp)
            x_label = _format_col_label(str(draft_cfg.get("x", "")), cur_schema)
            y_label = "、".join(_format_col_label(c, cur_schema) for c in y_list_disp)
            other_parts = [
                f"資料表={cur_ds}" if not is_en else f"Dataset={cur_ds}",
                f"類型={draft_cfg.get('chart_type', 'line')}"
                if not is_en
                else f"Type={draft_cfg.get('chart_type', 'line')}",
            ]
            if draft_cfg.get("color"):
                other_parts.append(
                    f"顏色分組={draft_cfg['color']}"
                    if not is_en
                    else f"Color={draft_cfg['color']}"
                )
            if draft_cfg.get("chart_type") == "horizontal_bar":
                other_parts.append(
                    f"顯示筆數=Top {draft_cfg.get('limit', 12)}"
                    if not is_en
                    else f"Limit=Top {draft_cfg.get('limit', 12)}"
                )
            is_rate_preview = bool(y_list_disp) and all(
                any(t in c.lower() for t in ("rate", "ratio", "pct", "share"))
                for c in y_list_disp
            )
            if is_rate_preview:
                other_parts.append("Y軸單位=百分比 (%)" if not is_en else "Y-Axis=%")

            if is_en:
                st.info(
                    f"**X-Axis:** ({x_label}) , **Y-Axis:** ({y_label})\n\n"
                    f"**Other Parameters:** ({' ｜ '.join(other_parts)})"
                )
            else:
                st.info(
                    f"**X軸：** ({x_label}) ， **Y軸：** ({y_label})\n\n"
                    f"**其他欄位或資訊：** ({' ｜ '.join(other_parts)})"
                )

            # Editable Parameter Controls (Instant manual adjustment without spending LLM tokens)
            r1_c1, r1_c2, r1_c3 = st.columns(3)
            with r1_c1:
                sel_ds = st.selectbox(
                    "資料表 (Dataset)" if not is_en else "Dataset",
                    options=ds_keys,
                    index=ds_keys.index(cur_ds),
                    key=f"{state_prefix}_sel_ds",
                )
            if sel_ds != cur_ds:
                cur_ds = sel_ds
                cur_schema = dataset_schemas[cur_ds]
                all_cols = list(cur_schema.keys())
                draft_cfg["dataset"] = cur_ds

            chart_types = ["line", "bar", "horizontal_bar"]
            cur_ctype = draft_cfg.get("chart_type", "line")
            if cur_ctype not in chart_types:
                cur_ctype = "line"
            with r1_c2:
                sel_ctype = st.selectbox(
                    "圖表類型 (Chart Type)" if not is_en else "Chart Type",
                    options=chart_types,
                    index=chart_types.index(cur_ctype),
                    key=f"{state_prefix}_sel_ctype",
                )
            draft_cfg["chart_type"] = sel_ctype

            with r1_c3:
                if sel_ctype == "horizontal_bar":
                    sel_limit = st.number_input(
                        "顯示筆數上限 (Top N)" if not is_en else "Top N Limit",
                        min_value=1,
                        max_value=32,
                        value=int(draft_cfg.get("limit", 12)),
                        key=f"{state_prefix}_sel_limit",
                    )
                    draft_cfg["limit"] = int(sel_limit)
                else:
                    color_opts = ["(None)"] + all_cols
                    cur_color = draft_cfg.get("color")
                    color_idx = color_opts.index(cur_color) if cur_color in color_opts else 0
                    sel_color = st.selectbox(
                        "顏色分組 (Color)" if not is_en else "Color Group",
                        options=color_opts,
                        index=color_idx,
                        key=f"{state_prefix}_sel_color",
                    )
                    if sel_color == "(None)":
                        draft_cfg.pop("color", None)
                    else:
                        draft_cfg["color"] = sel_color

            r2_c1, r2_c2 = st.columns([1.2, 2.0])
            cur_x = draft_cfg.get("x", all_cols[0])
            if cur_x not in all_cols:
                cur_x = "event_date" if "event_date" in all_cols else all_cols[0]
            with r2_c1:
                sel_x = st.selectbox(
                    "X 軸欄位 (X-Axis)" if not is_en else "X-Axis Column",
                    options=all_cols,
                    index=all_cols.index(cur_x),
                    format_func=lambda c: _format_col_label(c, cur_schema),
                    key=f"{state_prefix}_sel_x_{cur_ds}",
                )
            draft_cfg["x"] = sel_x

            raw_y_cur = draft_cfg.get("y", [])
            y_cur_list = [raw_y_cur] if isinstance(raw_y_cur, str) else list(raw_y_cur)
            valid_y_defaults = [c for c in y_cur_list if c in all_cols]
            if not valid_y_defaults and len(all_cols) > 1:
                valid_y_defaults = [all_cols[1]]
            with r2_c2:
                sel_y = st.multiselect(
                    "Y 軸欄位 (Y-Axis)" if not is_en else "Y-Axis Columns",
                    options=all_cols,
                    default=valid_y_defaults,
                    format_func=lambda c: _format_col_label(c, cur_schema),
                    key=f"{state_prefix}_sel_y_{cur_ds}",
                )
            if sel_y:
                draft_cfg["y"] = (
                    sel_y[0]
                    if sel_ctype == "horizontal_bar" and "event_name" in sel_y
                    else sel_y
                )

            st.session_state[k_draft] = draft_cfg

            # Accumulating Q&A / Refinement Chat Box (no full-page reload!)
            st.markdown(
                "**💬 "
                + (
                    "Follow-up Q&A & Parameter Refinement (Accumulates without reloading page):"
                    if is_en
                    else "疑問補充對話視窗（對話持續累加，不刷新頁面）："
                )
                + "**"
            )
            if st.session_state[k_chat]:
                with st.container(height=180, border=True):
                    for msg in st.session_state[k_chat]:
                        avatar = "🤖" if msg["role"] == "assistant" else "👤"
                        with st.chat_message(msg["role"], avatar=avatar):
                            st.markdown(msg["content"])

            with st.form(key=f"{state_prefix}_refine_form", clear_on_submit=True):
                q_col, b_col = st.columns([4.6, 1.4])
                with q_col:
                    followup_txt = st.text_input(
                        "疑問或補充說明" if not is_en else "Ask or refine parameters...",
                        placeholder=(
                            "例：改成柱狀圖、拿掉某個欄位、為什麼這個指標會破 100%？"
                            if not is_en
                            else "e.g., switch to bar chart, remove a column, why is this rate > 100%?"
                        ),
                        label_visibility="collapsed",
                        key=f"{state_prefix}_followup_input",
                    )
                with b_col:
                    btn_send_followup = st.form_submit_button(
                        "💬 " + ("Send" if is_en else "送出補充"),
                        use_container_width=True,
                    )

            if btn_send_followup and followup_txt.strip():
                st.session_state[k_chat].append(
                    {"role": "user", "content": followup_txt.strip()}
                )
                with st.spinner(
                    "🤖 Refining spec..." if is_en else "🤖 正在依據您的補充調整規格..."
                ):
                    new_cfg, ai_reply = refine_ai_chart_config(
                        current_config=dict(st.session_state[k_draft]),
                        user_message=followup_txt.strip(),
                        chat_history=st.session_state[k_chat],
                        dataset_schemas=dataset_schemas,
                        api_key=api_key,
                        domain_context=domain_context,
                        is_en=is_en,
                    )
                st.session_state[k_draft] = new_cfg
                st.session_state[k_chat].append(
                    {"role": "assistant", "content": ai_reply}
                )
                st.rerun(scope="fragment") if hasattr(st, "fragment") else st.rerun()

            # Action Row: Render Chart vs Reset
            act_c1, act_c2 = st.columns([3.5, 1.5])
            with act_c1:
                if st.button(
                    "🚀 "
                    + (
                        "Confirm & Generate Chart"
                        if is_en
                        else "確認參數，產生圖表"
                    ),
                    key=f"{state_prefix}_btn_render_final",
                    type="primary",
                    use_container_width=True,
                ):
                    st.session_state[k_rendered] = dict(st.session_state[k_draft])
            with act_c2:
                if st.button(
                    "🔄 " + ("Reset" if is_en else "清除重來"),
                    key=f"{state_prefix}_btn_reset",
                    use_container_width=True,
                ):
                    st.session_state.pop(k_draft, None)
                    st.session_state[k_chat] = []
                    st.rerun(scope="fragment") if hasattr(st, "fragment") else st.rerun()

    # Step 3: Render Confirmed Plotly Chart (stays untouched during Q&A until user clicks Generate!)
    rendered_cfg = st.session_state.get(k_rendered)
    if rendered_cfg:
        st.markdown("---")
        r_ds = rendered_cfg.get("dataset", "kpi")
        getter = dataset_getters.get(r_ds) or next(iter(dataset_getters.values()))
        target_df = getter() if callable(getter) else getter
        fig_out = render_ai_dynamic_chart(rendered_cfg, target_df)

        gen_by = rendered_cfg.get("generated_by", "fallback_rules")
        if gen_by.startswith("gemini:"):
            m_tag = gen_by.split(":", 1)[1]
            st.caption(
                f"✨ **AI-Generated Chart** (`{m_tag}` via `cockpit_core`) · Dataset: `{r_ds}` · X: `{rendered_cfg.get('x')}` · Y: `{rendered_cfg.get('y')}`"
                if is_en
                else f"✨ **Gemini AI 圖表**（`{m_tag}` · `cockpit_core`）· 資料表：`{r_ds}` · X軸：`{rendered_cfg.get('x')}` · Y軸：`{rendered_cfg.get('y')}`"
            )
        else:
            st.caption(
                "ℹ️ **Rule-based preview** (bind your Gemini API Key on the right for full AI chart Q&A)."
                if is_en
                else "ℹ️ **離線規則預覽**（於右側綁定 Gemini API Key 可解鎖完整 AI 對話微調）。"
            )

        if rendered_cfg.get("takeaway"):
            st.info(f"💡 **AI Takeaway**: {rendered_cfg['takeaway']}")

        if fig_out:
            st.plotly_chart(fig_out, use_container_width=True)
        else:
            st.warning(
                "⚠️ No matching data rows for selected columns."
                if is_en
                else "⚠️ 所選欄位在目前日期範圍內無對應數據。"
            )


if hasattr(st, "fragment"):

    @st.fragment
    def render_interactive_nl2chart_studio(
        dataset_schemas: dict[str, dict[str, str]],
        dataset_getters: dict[str, Any],
        api_key: str = "",
        domain_context: str = "",
        presets: list[dict[str, str]] | None = None,
        fallback_fn=None,
        is_en: bool = False,
        state_prefix: str = "nl2chart_hitl",
    ) -> None:
        """Fragment-isolated 2-step Human-in-the-Loop NL2Chart Studio (zero full-page reloads)."""
        _render_nl2chart_studio_body(
            dataset_schemas=dataset_schemas,
            dataset_getters=dataset_getters,
            api_key=api_key,
            domain_context=domain_context,
            presets=presets,
            fallback_fn=fallback_fn,
            is_en=is_en,
            state_prefix=state_prefix,
        )
else:
    render_interactive_nl2chart_studio = _render_nl2chart_studio_body
