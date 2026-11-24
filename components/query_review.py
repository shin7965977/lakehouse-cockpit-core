"""Shared Query Review Gate & Advanced SQL Editor Component.

Provides an editable QuerySpec review gate shared by Ad-hoc Exploration and Long-term
Analytics Asset Design:
- Catalog search & 7-state semantic gap classification evidence
- Structured editing (relation, columns, aggregations, row limit, dialect)
- Advanced SQL editor with raw_sql_override + unified SQL diff
- Immutable canonical SHA-256 spec_hash that invalidates confirmation on any edit
"""

from __future__ import annotations

import os
from typing import Any

import pandas as pd
import streamlit as st

try:
    from ..services.dbt_catalog import DbtMetadataCatalog
    from ..services.query_spec import (
        AggregationSpec,
        DataSourceRef,
        QuerySpec,
        diff_sql,
    )
    from ..services.requirement_engine import (
        RequirementDraftError,
        draft_and_classify_query_spec,
    )
except (ImportError, ValueError):
    try:
        from services.dbt_catalog import DbtMetadataCatalog
        from services.query_spec import (
            AggregationSpec,
            DataSourceRef,
            QuerySpec,
            diff_sql,
        )
        from services.requirement_engine import (
            RequirementDraftError,
            draft_and_classify_query_spec,
        )
    except (ImportError, ValueError):
        from cockpit_core.services.dbt_catalog import DbtMetadataCatalog
        from cockpit_core.services.query_spec import (
            AggregationSpec,
            DataSourceRef,
            QuerySpec,
            diff_sql,
        )
        from cockpit_core.services.requirement_engine import (
            RequirementDraftError,
            draft_and_classify_query_spec,
        )


def _resolve_api_key() -> str:
    return (
        st.session_state.get("user_gemini_key")
        or st.session_state.get("sidebar_gemini_api_key")
        or st.session_state.get("gemini_api_key")
        or os.getenv("GEMINI_API_KEY", "")
    ).strip()


def render_query_review_gate(
    data_dict: dict[str, pd.DataFrame] | None = None,
    catalog: DbtMetadataCatalog | None = None,
    is_en: bool = True,
    scope_key: str = "default",
) -> None:
    """Render the shared QuerySpec Review Gate & Advanced SQL Editor."""
    active_catalog = catalog or DbtMetadataCatalog.from_target_dir()
    data_dict = data_dict or {}

    st.subheader(
        "🛡️ QuerySpec Review Gate & Advanced SQL Editor"
        if is_en
        else "🛡️ QuerySpec 審核閘門與進階 SQL 編輯器"
    )
    st.caption(
        "Inspect dbt-native catalog evidence, classify semantic gaps, edit structured QuerySpec or raw SQL with live diff, and confirm exact `spec_hash`."
        if is_en
        else "檢視 dbt 原生 Catalog 證據、7 狀態語意缺口分診、結構化欄位或進階 SQL 編輯與 Diff，並以不可變 `spec_hash` 確認意圖。"
    )

    k_spec = f"qr_spec_{scope_key}"
    k_gap = f"qr_gap_{scope_key}"
    k_base_sql = f"qr_base_sql_{scope_key}"

    # 1. Catalog Search + Gap Classification Bar
    q_col, btn_col = st.columns([4, 1.4])
    with q_col:
        question = st.text_input(
            "Analytical Question / Semantic Lookup:"
            if is_en
            else "輸入分析問題或查詢既有指標 / Catalog：",
            key=f"qr_question_{scope_key}",
            placeholder=(
                "e.g. Monthly revenue by category, or search a table/column..."
                if is_en
                else "例如：查詢各月營業收入、或搜尋資料表與欄位..."
            ),
        )
    with btn_col:
        st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
        run_classify = st.button(
            "🔍 Classify & Draft" if is_en else "🔍 分診並草擬規格",
            type="primary",
            use_container_width=True,
            key=f"qr_btn_classify_{scope_key}",
        )

    if run_classify and question.strip():
        try:
            resp = draft_and_classify_query_spec(
                question.strip(),
                active_catalog,
                api_key=_resolve_api_key(),
            )
            st.session_state[k_gap] = resp.gap.model_dump(mode="json")
            if resp.query_spec is not None:
                st.session_state[k_spec] = resp.query_spec.model_dump(mode="json")
                st.session_state[k_base_sql] = resp.compiled_sql
            elif resp.clarification_prompt:
                st.warning(resp.clarification_prompt)
        except RequirementDraftError as exc:
            st.error(f"[{exc.code}] {exc.message}")

    # Show Catalog & Gap Evidence when available
    if st.session_state.get(k_gap):
        gap_data: dict[str, Any] = st.session_state[k_gap]
        st.info(
            f"**Gap State**: `{gap_data['classification']}` — {gap_data['reason']}"
            if is_en
            else f"**語意分診狀態**：`{gap_data['classification']}` — {gap_data['reason']}"
        )

    # Initialize a clean structured QuerySpec from available tables if none drafted yet
    if k_spec not in st.session_state:
        available_tables = list(data_dict.keys()) or [r.name for r in active_catalog.relations]
        default_tbl = available_tables[0] if available_tables else "fact_orders"
        default_cols: list[str] = []
        if default_tbl in data_dict and not data_dict[default_tbl].empty:
            default_cols = list(data_dict[default_tbl].columns[:2])
        init_spec = QuerySpec(
            spec_id="qs_workbench_001",
            question=question or "Exploratory review",
            data_sources=[DataSourceRef(relation=default_tbl, catalog_ref=f"dbt://model/{default_tbl}")],
            projection=default_cols[:1],
            aggregations=(
                [AggregationSpec(field=default_cols[1], func="SUM", alias=f"sum_{default_cols[1]}")]
                if len(default_cols) > 1
                else []
            ),
            group_by=default_cols[:1],
            dialect="duckdb",
        )
        st.session_state[k_spec] = init_spec.model_dump(mode="json")
        st.session_state[k_base_sql] = init_spec.compile_sql()

    current_spec = QuerySpec.model_validate(st.session_state[k_spec])

    # 2. Structured QuerySpec Editor
    table_options = list(dict.fromkeys(
        list(data_dict.keys())
        + [r.name for r in active_catalog.relations]
        + [s.relation for s in current_spec.data_sources]
    ))
    c1, c2, c3 = st.columns([2, 1.2, 1.2])
    with c1:
        cur_rel = current_spec.data_sources[0].relation if current_spec.data_sources else table_options[0]
        sel_rel = st.selectbox(
            "Source Relation:" if is_en else "資料來源表：",
            options=table_options,
            index=table_options.index(cur_rel) if cur_rel in table_options else 0,
            key=f"qr_rel_{scope_key}",
        )
    with c2:
        sel_dialect = st.selectbox(
            "SQL Dialect:" if is_en else "SQL 方言：",
            options=["duckdb", "postgres"],
            index=0 if current_spec.dialect == "duckdb" else 1,
            key=f"qr_dialect_{scope_key}",
        )
    with c3:
        sel_limit = st.number_input(
            "Row Limit:" if is_en else "筆數上限：",
            min_value=1,
            max_value=10000,
            value=int(current_spec.output.max_rows),
            key=f"qr_limit_{scope_key}",
        )

    # Apply structured changes -> recompute spec_hash & invalidate confirmation if changed
    if (
        sel_rel != cur_rel
        or sel_dialect != current_spec.dialect
        or int(sel_limit) != int(current_spec.output.max_rows)
    ):
        updated_spec = current_spec.with_updates(
            data_sources=[DataSourceRef(relation=sel_rel, catalog_ref=f"dbt://model/{sel_rel}")],
            dialect=sel_dialect,
            max_rows=int(sel_limit),
            raw_sql_override=None,
        )
        st.session_state[k_spec] = updated_spec.model_dump(mode="json")
        st.session_state[k_base_sql] = updated_spec.compile_sql()
        current_spec = updated_spec

    # 3. Advanced SQL Editor (raw_sql_override + diff + hash invalidation)
    compiled_sql = current_spec.compile_sql()
    edited_sql = st.text_area(
        "Advanced SQL Editor (manual edits set `raw_sql_override` and invalidate prior confirmation):"
        if is_en
        else "進階 SQL 編輯器（手動修改將啟用 `raw_sql_override` 並自動清除舊確認）：",
        value=compiled_sql,
        height=140,
        key=f"qr_sql_editor_{scope_key}",
    )

    if edited_sql.strip() != compiled_sql.strip():
        current_spec = current_spec.with_updates(raw_sql_override=edited_sql.strip())
        st.session_state[k_spec] = current_spec.model_dump(mode="json")

    sql_diff_text = diff_sql(st.session_state.get(k_base_sql, ""), current_spec.compile_sql())
    if sql_diff_text:
        with st.expander(
            "🧾 SQL Diff (`raw_sql_override` active)"
            if is_en
            else "🧾 SQL 差異比對（已啟用 `raw_sql_override`）",
            expanded=True,
        ):
            st.code(sql_diff_text, language="diff")

    # 4. Immutable spec_hash & Confirmation Gate
    spec_hash = current_spec.compute_spec_hash()
    is_confirmed = current_spec.is_confirmation_valid()
    status_badge = (
        f"✅ Confirmed (`{spec_hash[:12]}`)"
        if is_confirmed
        else f"⏳ Pending Confirmation (`{spec_hash[:12]}`)"
    ) if is_en else (
        f"✅ 已確認意圖 (`{spec_hash[:12]}`)"
        if is_confirmed
        else f"⏳ 待確認意圖 (`{spec_hash[:12]}`)"
    )

    st.caption(
        f"**Version**: v{current_spec.version} | **Certification**: `{current_spec.output.certification}` | **Status**: {status_badge}"
        if is_en
        else f"**版本**：v{current_spec.version} ｜ **認證標籤**：`{current_spec.output.certification}` ｜ **狀態**：{status_badge}"
    )

    if st.button(
        "✅ Confirm Intent for Current spec_hash"
        if is_en
        else "✅ 確認目前 spec_hash 之分析意圖",
        use_container_width=True,
        key=f"qr_confirm_{scope_key}",
    ):
        confirmed_spec = current_spec.confirm_intent(confirmed_by="workbench_user")
        st.session_state[k_spec] = confirmed_spec.model_dump(mode="json")
        st.success(
            f"Intent confirmed for `spec_hash={confirmed_spec.review.spec_hash[:12]}`."
            if is_en
            else f"已確認意圖，綁定雜湊 `spec_hash={confirmed_spec.review.spec_hash[:12]}`。"
        )
