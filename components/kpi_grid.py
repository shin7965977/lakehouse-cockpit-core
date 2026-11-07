"""Responsive 4-Card Executive KPI Grid Component."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from ..adapter import KPICardSpec


def render_kpi_grid(
    cards: list[KPICardSpec],
    data_dict: dict[str, pd.DataFrame],
    is_en: bool = True,
) -> None:
    """Render a responsive 4-column KPI grid with glassmorphism cards and top accent highlights."""
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
            title = card.title_en or getattr(card, "title_zh", card.key)
            accent = getattr(card, "accent_color", "#6366f1")

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
                        <span class="code-tag">{card.metric_formula}</span>
                        <span>{card.target_rule}</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
