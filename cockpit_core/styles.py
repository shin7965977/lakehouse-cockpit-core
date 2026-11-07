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
    overflow: hidden;
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
    background: rgba(128, 128, 128, 0.12);
}
</style>
"""
