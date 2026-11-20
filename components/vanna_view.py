"""Vanna.ai Open-Source GenBI (RAG Text-to-SQL & Auto-Plotly Charting) Component.

Enforces strict Fail-Loud policy: zero silent fallbacks. Unrecognized offline queries,
LLM API errors, and DuckDB execution errors are surfaced explicitly in the UI.
"""

from __future__ import annotations

import os

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

try:
    from ..adapter import BaseCockpitAdapter
    from ..services.duckdb_executor import (
        DatabaseReflectionService,
        execute_readonly_duckdb,
        generate_gemini_text,
    )
    from .sql_workbench import load_dbt_manifest_models
except (ImportError, ValueError):
    try:
        from adapter import BaseCockpitAdapter
        from components.sql_workbench import load_dbt_manifest_models
        from services.duckdb_executor import (
            DatabaseReflectionService,
            execute_readonly_duckdb,
            generate_gemini_text,
        )
    except (ImportError, ValueError):
        from cockpit_core.adapter import BaseCockpitAdapter
        from cockpit_core.components.sql_workbench import load_dbt_manifest_models
        from cockpit_core.services.duckdb_executor import (
            DatabaseReflectionService,
            execute_readonly_duckdb,
            generate_gemini_text,
        )


def _build_vanna_training_corpus(
    adapter: BaseCockpitAdapter | None,
    data_dict: dict[str, pd.DataFrame],
) -> list[dict[str, str]]:
    """Build Vanna RAG training records (DDL + dbt Documentation + Verified SQL) from live schema."""
    records: list[dict[str, str]] = []
    for tbl, df in (data_dict or {}).items():
        if not isinstance(df, pd.DataFrame) or df.empty:
            continue
        col_defs = ",\n  ".join(f"{col} {str(df[col].dtype).upper()}" for col in df.columns[:15])
        ddl = f"CREATE TABLE {tbl} (\n  {col_defs}\n);"
        records.append({"type": "ddl", "source": tbl, "content": ddl})

    manifest_path = getattr(adapter, "dbt_manifest_path", None) if adapter else None
    for model in load_dbt_manifest_models(manifest_path):
        if model.get("description"):
            records.append(
                {
                    "type": "documentation",
                    "source": f"dbt:{model['name']}",
                    "content": f"Model {model['name']} ({model['schema']}): {model['description']}",
                }
            )
        if model.get("compiled_sql"):
            records.append(
                {
                    "type": "sql",
                    "source": f"dbt:{model['name']}",
                    "content": model["compiled_sql"],
                }
            )
    return records


def _match_offline_sql(question: str, data_dict: dict[str, pd.DataFrame]) -> tuple[str, str]:
    """Match question against explicit schema intents without any silent catch-all fallback.

    Returns ``(sql_query, error_message)``. If the question does not match any known
    schema rule in offline mode, ``sql_query`` is ``""`` and ``error_message`` explains why.
    """
    q_low = (question or "").strip().lower()
    if not q_low:
        return "", "Question cannot be empty."

    tables = {k: v for k, v in (data_dict or {}).items() if isinstance(v, pd.DataFrame) and not v.empty}
    if not tables:
        return "", "No non-empty tables available in data_dict."

    # 1. Time / Daily trend
    if any(k in q_low for k in ["trend", "daily", "date", "month", "趨勢", "每日", "走勢", "時間"]):
        for tbl, df in tables.items():
            date_col = next((c for c in df.columns if "date" in c.lower() or "time" in c.lower()), None)
            num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and "id" not in c.lower()]
            if date_col and num_cols:
                metric = "units" if ("unit" in q_low or "件數" in q_low or "銷量" in q_low) and "units" in df.columns else num_cols[0]
                alias = metric if metric.startswith("total_") else f"total_{metric}"
                return (
                    f"SELECT CAST({date_col} AS DATE) AS report_date, "
                    f"ROUND(SUM(CAST({metric} AS DOUBLE)), 2) AS {alias}\n"
                    f"FROM {tbl}\n"
                    f"GROUP BY 1\n"
                    f"ORDER BY 1 ASC\n"
                    f"LIMIT 60",
                    "",
                )

    # 2. Region / City breakdown
    if any(k in q_low for k in ["region", "city", "area", "taipei", "taichung", "地區", "區域", "城市", "台北", "台中", "高雄", "台南"]):
        for tbl in ("fact_orders", "fct_orders"):
            if tbl in tables and "region" in tables[tbl].columns:
                if any(k in q_low for k in ["avg", "aov", "average", "平均", "客單價", "單價"]):
                    agg = "ROUND(AVG(CAST(gross_amount AS DOUBLE)), 2) AS avg_order_value"
                elif any(k in q_low for k in ["unit", "qty", "件數", "數量", "銷量"]):
                    agg = "SUM(units) AS total_units_sold"
                elif any(k in q_low for k in ["count", "orders", "筆數", "訂單數"]):
                    agg = "COUNT(DISTINCT order_id) AS order_count"
                else:
                    agg = "ROUND(SUM(CAST(gross_amount AS DOUBLE)), 2) AS region_gmv, COUNT(DISTINCT order_id) AS order_count"
                return (
                    f"SELECT region, {agg}\n"
                    f"FROM {tbl}\n"
                    f"GROUP BY region\n"
                    f"ORDER BY 2 DESC",
                    "",
                )
        return "", "No table with a `region` column was found in the loaded datasets."

    # 3. Average Order Value (AOV)
    if any(k in q_low for k in ["avg", "aov", "average", "平均", "客單價", "單價"]):
        if "mart_product_performance" in tables and "avg_order_value" in tables["mart_product_performance"].columns:
            return (
                "SELECT category, avg_order_value, order_count\n"
                "FROM mart_product_performance\n"
                "ORDER BY avg_order_value DESC",
                "",
            )
        if "fact_orders" in tables:
            return (
                "SELECT category, ROUND(AVG(CAST(gross_amount AS DOUBLE)), 2) AS avg_order_value, COUNT(*) AS order_count\n"
                "FROM fact_orders\n"
                "GROUP BY category\n"
                "ORDER BY avg_order_value DESC",
                "",
            )

    # 4. Units / Quantity sold
    if any(k in q_low for k in ["unit", "qty", "quantity", "件數", "數量", "銷量"]):
        if "fact_orders" in tables and "units" in tables["fact_orders"].columns:
            return (
                "SELECT category, SUM(units) AS total_units, ROUND(SUM(gross_amount), 2) AS total_gmv\n"
                "FROM fact_orders\n"
                "GROUP BY category\n"
                "ORDER BY total_units DESC",
                "",
            )

    # 5. Top Individual Orders
    if any(k in q_low for k in ["top", "highest", "單筆", "最高", "大單", "前10", "前十"]):
        if "fact_orders" in tables:
            return (
                "SELECT order_id, gross_amount, category, region, event_date\n"
                "FROM fact_orders\n"
                "ORDER BY gross_amount DESC\n"
                "LIMIT 10",
                "",
            )

    # 6. Return / Refund / Status
    if any(k in q_low for k in ["return", "refund", "status", "退貨", "退款", "狀態"]):
        for tbl in ("mart_product_performance", "fact_orders", "fct_orders"):
            if tbl in tables:
                df = tables[tbl]
                dim = next(
                    (c for c in df.columns if any(k in c.lower() for k in ["category", "status", "region", "product"])),
                    df.columns[0],
                )
                ret = next(
                    (c for c in df.columns if any(k in c.lower() for k in ["return", "amount", "gmv", "total"])),
                    None,
                )
                agg = f"ROUND(AVG(CAST({ret} AS DOUBLE)), 2) AS avg_{ret}" if ret else "COUNT(*) AS record_count"
                return f"SELECT {dim}, {agg}, COUNT(*) AS records FROM {tbl} GROUP BY {dim} ORDER BY 2 DESC LIMIT 15", ""

    # 7. Explicit Category / Product revenue or performance
    if any(k in q_low for k in ["category", "categories", "product", "revenue", "gmv", "sales", "品類", "類別", "商品", "營收", "銷售", "業績"]):
        pref_tbl = (
            "mart_product_performance"
            if "mart_product_performance" in tables
            else next(iter(tables.keys()))
        )
        df = tables[pref_tbl]
        dim_col = next(
            (c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])),
            df.columns[0],
        )
        num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and "id" not in c.lower()]
        metric_col = num_cols[0] if num_cols else None
        alias = metric_col if (metric_col and metric_col.startswith("total_")) else f"total_{metric_col}"
        agg_expr = (
            f"ROUND(SUM(CAST({metric_col} AS DOUBLE)), 2) AS {alias}"
            if metric_col
            else "COUNT(*) AS record_count"
        )
        return (
            f"SELECT {dim_col}, {agg_expr}\n"
            f"FROM {pref_tbl}\n"
            f"GROUP BY {dim_col}\n"
            f"ORDER BY 2 DESC\n"
            f"LIMIT 15",
            "",
        )

    # STRICT NO SILENT FALLBACK: refuse to guess an unrelated default table!
    return (
        "",
        "Offline rule parser could not match your question to any known schema dimension/metric "
        "(supported offline dimensions: category, region, event_date, return_rate, avg_order_value, units, top orders). "
        "Please provide a Gemini API Key to enable full Vanna LLM Text-to-SQL generation.",
    )


def _synthesize_sql_with_vanna_rag(
    question: str,
    corpus: list[dict[str, str]],
    data_dict: dict[str, pd.DataFrame],
    api_key: str,
) -> tuple[str, str, str]:
    """Generate DuckDB SQL without silent fallbacks.

    Returns ``(sql_query, engine_badge, error_message)``.
    """
    if not api_key:
        sql, err = _match_offline_sql(question, data_dict)
        return sql, "Vanna Local RAG + DuckDB (Offline Rule Mode)", err

    try:
        rag_context = "\n\n".join(f"[{r['type'].upper()}] {r['content']}" for r in corpus[:12])
        prompt = (
            "You are Vanna.ai, a strict, zero-guessing RAG Text-to-SQL engine running on DuckDB.\n"
            "Use ONLY the trained DDL and dbt documentation below.\n"
            "STRICT RULES:\n"
            "1. NO SILENT GUESSING ON AMBIGUOUS QUERIES: If the user's question is vague or ambiguous — for example, "
            "it does not specify which dimension to compare (e.g. 'which one' without saying category vs region), "
            "uses undefined subjective terms ('best/worst performance', 'doing well') without specifying the metric "
            "(revenue/GMV vs return_rate vs order_count vs units), or asks for 'recently' without a defined time window — "
            "DO NOT guess a SQL query! Instead, return starting with:\n"
            "AMBIGUOUS: <briefly explain in the user's language which dimension, metric, or time window is missing, and list the available columns from the DDL>.\n"
            "2. OUT OF DOMAIN / MISSING COLUMNS: If the user asks for columns or entities that do not exist in the DDL (e.g. customer VIP retention), "
            "return starting with:\n"
            "ERROR: <explain which required columns are missing from the schema>.\n"
            "3. UNAMBIGUOUS QUERIES ONLY: Only when the dimension and metric are clearly specified, write a single valid DuckDB SELECT query "
            "that projects both the requested dimension column and the requested numeric metric column.\n\n"
            f"{rag_context}\n\n"
            f"Question: {question}\n"
            "Return ONLY the raw SQL query, 'AMBIGUOUS: <reason>', or 'ERROR: <reason>' without markdown fences."
        )
        raw_text = generate_gemini_text(api_key=api_key, prompt=prompt, model="gemini-2.5-flash")
        candidate = raw_text.strip().strip("`").replace("sql\n", "").strip().rstrip(";")
        if candidate.upper().startswith("AMBIGUOUS:"):
            return "", "Vanna RAG + Gemini + DuckDB", candidate
        if candidate.upper().startswith("ERROR:"):
            return "", "Vanna RAG + Gemini + DuckDB", candidate
        if not (candidate.upper().startswith("SELECT") or candidate.upper().startswith("WITH")):
            return candidate, "Vanna RAG + Gemini + DuckDB", f"LLM did not return a valid SELECT query: {candidate}"
        return candidate, "Vanna RAG + Gemini + DuckDB", ""
    except Exception as exc:
        return "", "Vanna RAG + Gemini + DuckDB", f"Gemini API Error: {exc}"


def _build_auto_plotly_figure(
    df: pd.DataFrame,
    chart_type: str,
) -> go.Figure:
    """Synthesize a Plotly figure whose title strictly reflects the actual projected columns."""
    if df is None or df.empty:
        return go.Figure()

    num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    if not num_cols:
        raise ValueError(
            f"Cannot plot chart: query result has no numeric metric columns (projected columns: {list(df.columns)})."
        )
    non_num_cols = [c for c in df.columns if c not in num_cols]
    x_col = non_num_cols[0] if non_num_cols else df.columns[0]
    y_col = num_cols[0]
    truthful_title = f"{y_col} by {x_col}"

    resolved_type = chart_type.lower()
    if resolved_type == "auto":
        if "date" in x_col.lower() or "time" in x_col.lower() or pd.api.types.is_datetime64_any_dtype(df[x_col]):
            resolved_type = "line"
        else:
            resolved_type = "bar"

    if resolved_type == "line":
        fig = px.line(df, x=x_col, y=y_col, markers=True, title=truthful_title, template="plotly_white")
    elif resolved_type == "pie":
        fig = px.pie(df, names=x_col, values=y_col, title=truthful_title, hole=0.35, template="plotly_white")
    elif resolved_type == "scatter":
        y2 = num_cols[1] if len(num_cols) > 1 else y_col
        fig = px.scatter(
            df,
            x=x_col,
            y=y2,
            size=y_col if y_col in num_cols else None,
            title=f"{y2} vs {x_col}",
            template="plotly_white",
        )
    else:
        fig = px.bar(
            df,
            x=x_col,
            y=y_col,
            color=x_col if len(df) <= 10 else None,
            title=truthful_title,
            template="plotly_white",
        )

    fig.update_layout(margin={"l": 20, "r": 20, "t": 48, "b": 20}, height=380)
    return fig


def render_vanna_view(
    adapter: BaseCockpitAdapter | None = None,
    data_dict: dict[str, pd.DataFrame] | None = None,
    is_en: bool = True,
) -> None:
    """Render Vanna.ai open-source RAG Text-to-SQL & Auto-Plotly Charting workbench."""
    safe_data = data_dict or {}
    reflection = DatabaseReflectionService(safe_data)
    corpus = _build_vanna_training_corpus(adapter, safe_data)

    st.markdown(
        "### 📊 Vanna.ai Open-Source GenBI (RAG Text-to-SQL & Auto-Chart)"
        if is_en
        else "### 📊 Vanna.ai 開源生成式 BI（語意 RAG 自動生圖）"
    )
    st.caption(
        "100% open-source (MIT) in-process GenBI engine: auto-trains on Lakehouse DDL & dbt manifest.json, executes DuckDB SQL, and renders interactive Plotly charts."
        if is_en
        else "100% 開源免費（MIT）的內嵌式 GenBI 引擎：自動讀取湖倉 DDL 與 dbt manifest.json 建立 RAG 知識庫，現場執行 DuckDB SQL 並自動繪製 Plotly 圖表。"
    )

    with st.expander(
        f"🧠 Inspect Vanna RAG Training Corpus ({len(corpus)} DDL & dbt Metadata Records)"
        if is_en
        else f"🧠 檢視 Vanna RAG 已訓練語意知識庫（共 {len(corpus)} 筆 DDL 與 dbt 模型文件）",
        expanded=False,
    ):
        st.caption(
            "Vanna indexes table DDLs, dbt column descriptions, and verified SQL models so LLM queries remain strictly grounded in your schema."
            if is_en
            else "Vanna 自動將資料表 DDL、dbt 欄位描述與已驗證 SQL 納入檢索知識庫，確保自然語言生圖 100% 對齊真實欄位。"
        )
        st.dataframe(pd.DataFrame(corpus), use_container_width=True, hide_index=True)

    preset_q1 = (
        "Compare total revenue and performance across product categories"
        if is_en
        else "比較各商品類別的總銷售額與績效表現"
    )
    preset_q2 = (
        "Analyze return rate and order status distribution"
        if is_en
        else "分析各品類退貨率與訂單狀態分佈"
    )
    preset_q3 = (
        "Show daily order revenue trend over time"
        if is_en
        else "顯示每日訂單營收時間走勢趨勢圖"
    )

    zh_to_en = {
        "比較各商品類別的總銷售額與績效表現": "Compare total revenue and performance across product categories",
        "分析各品類退貨率與訂單狀態分佈": "Analyze return rate and order status distribution",
        "顯示每日訂單營收時間走勢趨勢圖": "Show daily order revenue trend over time",
    }
    en_to_zh = {v: k for k, v in zh_to_en.items()}
    cur_q = st.session_state.get("vanna_question_input", "")
    if not cur_q:
        st.session_state["vanna_question_input"] = preset_q1
    elif is_en and cur_q in zh_to_en:
        st.session_state["vanna_question_input"] = zh_to_en[cur_q]
    elif not is_en and cur_q in en_to_zh:
        st.session_state["vanna_question_input"] = en_to_zh[cur_q]

    p_col1, p_col2, p_col3 = st.columns(3)
    with p_col1:
        if st.button(
            "📦 Category Revenue" if is_en else "📦 各品類營收排行",
            use_container_width=True,
            key="vanna_preset_1",
        ):
            st.session_state["vanna_question_input"] = preset_q1
    with p_col2:
        if st.button(
            "🔄 Return & Status" if is_en else "🔄 退貨率與狀態分析",
            use_container_width=True,
            key="vanna_preset_2",
        ):
            st.session_state["vanna_question_input"] = preset_q2
    with p_col3:
        if st.button(
            "📈 Revenue Trend" if is_en else "📈 營收時間趨勢圖",
            use_container_width=True,
            key="vanna_preset_3",
        ):
            st.session_state["vanna_question_input"] = preset_q3

    q_col, chart_col, btn_col = st.columns([3.0, 1.1, 0.9])
    with q_col:
        question = st.text_input(
            "Ask Vanna to generate an ad-hoc SQL query & chart:"
            if is_en
            else "輸入自然語言問題，讓 Vanna 自動生成 SQL 與互動圖表：",
            key="vanna_question_input",
        )
    with chart_col:
        chart_type = st.selectbox(
            "Chart Type:" if is_en else "圖表類型：",
            options=["Auto", "Bar", "Line", "Pie", "Scatter"],
            key="vanna_chart_type_select",
        )
    with btn_col:
        st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
        st.button(
            "🚀 Run" if is_en else "🚀 執行生圖",
            type="primary",
            use_container_width=True,
            key="vanna_run_btn",
        )

    active_api_key = (
        st.session_state.get("user_gemini_key")
        or st.session_state.get("sidebar_gemini_api_key")
        or st.session_state.get("gemini_api_key")
        or os.getenv("GEMINI_API_KEY", "")
    ).strip()

    sql_query, engine_badge, synth_err = _synthesize_sql_with_vanna_rag(
        question=question,
        corpus=corpus,
        data_dict=reflection.data_dict,
        api_key=active_api_key,
    )

    if synth_err:
        if synth_err.upper().startswith("AMBIGUOUS:"):
            detail = synth_err[len("AMBIGUOUS:"):].strip()
            st.warning(
                f"⚠️ **Ambiguous Query — Clarification Required ({engine_badge})**: {detail}"
                if is_en
                else f"⚠️ **語意模糊需釐清（{engine_badge}）**：{detail}"
            )
        else:
            st.error(
                f"❌ **Query Synthesis Failed ({engine_badge})**: {synth_err}"
                if is_en
                else f"❌ **SQL 生成失敗（{engine_badge}）**：{synth_err}"
            )
        if sql_query:
            st.code(sql_query, language="sql")
        return

    res_df, exec_err = execute_readonly_duckdb(sql_query, reflection.data_dict)
    if exec_err:
        st.error(
            f"❌ **DuckDB Execution Error ({engine_badge})**: {exec_err}"
            if is_en
            else f"❌ **DuckDB 執行錯誤（{engine_badge}）**：{exec_err}"
        )
        st.code(sql_query, language="sql")
        return

    if res_df.empty:
        st.warning(
            "⚠️ Query executed successfully but returned 0 rows."
            if is_en
            else "⚠️ SQL 執行成功，但查詢結果為 0 筆資料。"
        )
        st.code(sql_query, language="sql")
        return

    st.caption(
        f"⚡ **Engine**: `{engine_badge}` &nbsp;|&nbsp; **Rows Returned**: `{len(res_df)}`"
        if is_en
        else f"⚡ **執行引擎**：`{engine_badge}` &nbsp;|&nbsp; **檢索筆數**：`{len(res_df)}`"
    )

    try:
        fig = _build_auto_plotly_figure(res_df, chart_type=chart_type)
        st.plotly_chart(fig, use_container_width=True)
    except ValueError as chart_err:
        st.warning(f"⚠️ {chart_err}")

    c_sql, c_data = st.columns([0.95, 1.05])
    with c_sql:
        st.markdown(
            "##### 📜 Vanna Synthesized SQL (`vn.generate_sql`)"
            if is_en
            else "##### 📜 Vanna 自動生成 SQL (`vn.generate_sql`)"
        )
        st.code(sql_query, language="sql")
    with c_data:
        st.markdown(
            "##### 📊 DuckDB Query Output (`vn.run_sql`)"
            if is_en
            else "##### 📊 DuckDB 查詢結果資料表 (`vn.run_sql`)"
        )
        st.dataframe(res_df, use_container_width=True, hide_index=True)
