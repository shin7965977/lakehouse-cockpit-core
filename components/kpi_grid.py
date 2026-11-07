"""Responsive 4-Card Executive KPI Grid Component with ReadCall-style Formula Tooltips."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from ..adapter import KPICardSpec, MetricComponent


def _render_formula_tag(card: KPICardSpec, is_en: bool) -> str:
    """Generate HTML for formula tag with interactive hover tooltips (excluding Unit)."""
    num: MetricComponent | None = card.formula_numerator
    den: MetricComponent | None = card.formula_denominator

    if num and den:
        num_desc = num.description if is_en else (num.description_zh or num.description)
        den_desc = den.description if is_en else (den.description_zh or den.description)
        num_role = num.role if is_en else "分子"
        den_role = den.role if is_en else "分母"

        num_html = (
            f'<span class="formula-term term-num">'
            f"{num.label}"
            f'<span class="tooltip-box">'
            f'<div class="tooltip-header">{num.label} ({num_role})</div>'
            f'<div class="tooltip-body">{num_desc}</div>'
            f"</span>"
            f"</span>"
        )
        den_html = (
            f'<span class="formula-term term-den">'
            f"{den.label}"
            f'<span class="tooltip-box">'
            f'<div class="tooltip-header">{den.label} ({den_role})</div>'
            f'<div class="tooltip-body">{den_desc}</div>'
            f"</span>"
            f"</span>"
        )
        return f'<div class="formula-tag">{num_html} <span style="color:#64748b;font-weight:700;">/</span> {den_html}</div>'

    # Fallback to standard monospace code-tag
    return f'<span class="code-tag">{card.metric_formula}</span>'


def render_kpi_grid(
    cards: list[KPICardSpec],
    data_dict: dict[str, pd.DataFrame],
    is_en: bool = True,
) -> None:
    """Render a responsive 4-column KPI grid with glassmorphism cards and interactive hover tooltips."""
    if not cards:
        return

    cols = st.columns(len(cards))
    for idx, card in enumerate(cards):
        with cols[idx]:
            try:
                res: dict[str, Any] = card.calculate(data_dict)
            except Exception as e:
                res = {
                    "value": "N/A",
                    "status": "warning",
                    "caption": f"Error: {e}",
                }

            val_str = str(res.get("value", "--"))
            status = res.get("status", "normal")
            status_label = "Normal" if status == "normal" else "Warning"
            status_cls = "status-normal" if status == "normal" else "status-warning"
            title = (
                card.title_en
                if is_en
                else (card.title_zh or card.title_en or card.key)
            )
            accent = getattr(card, "accent_color", "#6366f1")
            formula_tag_html = _render_formula_tag(card, is_en=is_en)

            st.markdown(
                f"""
                <div class="metric-card">
                    <div class="metric-card-accent" style="background-color: {accent};"></div>
                    <div class="metric-label">
                        <span>{title}</span>
                        <span class="status-pill {status_cls}">{status_label}</span>
                    </div>
                    <div class="metric-value">{val_str}</div>
                    <div class="metric-sub">
                        {formula_tag_html}
                        <span>{card.target_rule}</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
