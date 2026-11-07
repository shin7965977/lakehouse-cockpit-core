"""Modern BI Design System & Responsive CSS Styles for Streamlit Cockpit."""

MODERN_COCKPIT_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}

/* Metric Cards - Adaptive to Light and Dark Mode */
.metric-card {
    background: var(--secondary-background-color, #ffffff);
    border: 1px solid rgba(128, 128, 128, 0.2);
    border-radius: 14px;
    padding: 1.25rem 1.5rem;
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.05);
    margin-bottom: 1rem;
    position: relative;
    overflow: visible; /* Allows tooltips to pop out without clipping */
    transition: transform 0.2s ease, box-shadow 0.2s ease;
}
.metric-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 24px rgba(99, 102, 241, 0.18);
    border-color: rgba(99, 102, 241, 0.4);
}
.metric-card-accent {
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 4px;
    border-top-left-radius: 14px;
    border-top-right-radius: 14px;
}
.metric-label {
    font-size: 0.825rem;
    font-weight: 600;
    color: var(--text-color, #4b5563);
    margin-bottom: 0.35rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    display: flex;
    justify-content: space-between;
    align-items: center;
}
.metric-value {
    font-size: 2.1rem;
    font-weight: 700;
    color: var(--text-color, #111827);
    line-height: 1.2;
    margin-bottom: 0.35rem;
}
.metric-sub {
    font-size: 0.775rem;
    color: #6b7280;
    display: flex;
    align-items: center;
    gap: 0.4rem;
    flex-wrap: wrap;
}
.status-pill {
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 0.72rem;
    font-weight: 600;
}
.status-normal {
    background-color: rgba(16, 185, 129, 0.15);
    color: #10b981;
    border: 1px solid rgba(16, 185, 129, 0.3);
}
.status-warning {
    background-color: rgba(239, 68, 68, 0.15);
    color: #ef4444;
    border: 1px solid rgba(239, 68, 68, 0.3);
}
.code-tag {
    font-family: monospace;
    font-size: 0.75rem;
    padding: 1px 5px;
    border-radius: 4px;
    background: rgba(128, 128, 128, 0.1);
    color: var(--text-color, #374151);
}

/* ========================================================================= */
/* ReadCall-style Formula Tag & Hover Tooltip System (Excluding Unit)       */
/* ========================================================================= */
.formula-tag {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.75rem;
    background: rgba(128, 128, 128, 0.08);
    border: 1px solid rgba(128, 128, 128, 0.2);
    color: var(--text-color, #334155);
    padding: 0.22rem 0.55rem;
    border-radius: 6px;
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    margin-top: 0.25rem;
}
.formula-term {
    position: relative;
    cursor: help;
    font-weight: 700;
    color: #2563eb;
    border-bottom: 1.5px dotted #3b82f6;
    padding: 0 2px;
    display: inline-block;
    transition: all 0.15s ease-in-out;
}
.formula-term:hover {
    color: #1d4ed8;
    background: rgba(59, 130, 246, 0.12);
    border-radius: 3px;
    border-bottom-style: solid;
}
.formula-term .tooltip-box {
    visibility: hidden;
    opacity: 0;
    position: absolute;
    bottom: calc(100% + 8px);
    background: #0f172a;
    color: #f8fafc;
    padding: 0.55rem 0.75rem;
    border-radius: 8px;
    font-size: 0.73rem;
    font-family: 'Inter', sans-serif;
    font-weight: 400;
    line-height: 1.4;
    width: 240px;
    max-width: 260px;
    box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);
    z-index: 99999;
    pointer-events: none !important;
    transition: opacity 0.2s ease, transform 0.2s ease;
    text-align: left;
    white-space: normal;
    word-break: break-word;
}
.formula-term.term-num .tooltip-box {
    left: 0;
    right: auto;
    transform: translateY(0);
}
.formula-term.term-num .tooltip-box::after {
    content: "";
    position: absolute;
    top: 100%;
    left: 18px;
    border-width: 5px;
    border-style: solid;
    border-color: #0f172a transparent transparent transparent;
}
.formula-term.term-den .tooltip-box {
    right: 0;
    left: auto;
    transform: translateY(0);
}
.formula-term.term-den .tooltip-box::after {
    content: "";
    position: absolute;
    top: 100%;
    right: 18px;
    border-width: 5px;
    border-style: solid;
    border-color: #0f172a transparent transparent transparent;
}
.formula-term:hover .tooltip-box {
    visibility: visible;
    opacity: 1;
    transform: translateY(-2px);
}
.tooltip-header {
    font-weight: 700;
    color: #38bdf8;
    font-size: 0.74rem;
    margin-bottom: 0.25rem;
}
.tooltip-body {
    color: #e2e8f0;
    font-size: 0.72rem;
}
</style>
"""
