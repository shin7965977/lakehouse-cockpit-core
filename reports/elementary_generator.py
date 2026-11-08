"""Elementary Observability Report Generator.

Synthesizes the official standalone single-file Elementary HTML report containing:
- Left sidebar navigation: Dashboard, Lineage, Catalog, Results, Test Execution History, Model Duration.
- Top KPI Donut charts: Test results breakdown, Tables health, Monitored tables.
- Metric anomaly cards: Freshness, Volume, Schema changes, dbt tests, Model runs.
- Interactive drill-down tables with test failures, sample rows, and models.
"""

from pathlib import Path
import json


def get_elementary_report_html() -> str:
    """Generate self-contained Elementary Observability Report HTML."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Elementary Data Observability | platzi_transform (prod)</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    :root {
      --primary: #f43f5e;
      --primary-light: #fff1f2;
      --sidebar-bg: #ffffff;
      --main-bg: #f8fafc;
      --card-bg: #ffffff;
      --text-main: #0f172a;
      --text-muted: #64748b;
      --border-color: #e2e8f0;
      --success: #10b981;
      --warning: #f59e0b;
      --danger: #ef4444;
      --info: #0284c7;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
    body { background-color: var(--main-bg); color: var(--text-main); display: flex; height: 100vh; overflow: hidden; }

    /* Top Navbar */
    .top-header {
      position: fixed; top: 0; left: 0; right: 0; height: 52px; background: #ffffff; border-bottom: 1px solid var(--border-color);
      display: flex; align-items: center; justify-content: space-between; padding: 0 20px; z-index: 100;
    }
    .brand-section { display: flex; align-items: center; gap: 12px; }
    .brand-logo { width: 32px; height: 32px; background: #e11d48; border-radius: 6px; display: flex; align-items: center; justify-content: center; color: white; font-weight: bold; font-size: 18px; }
    .env-badge { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 500; color: #334155; }
    .env-dot { width: 8px; height: 8px; border-radius: 50%; background: #3b82f6; }
    .header-time { font-size: 12px; color: var(--text-muted); }

    /* Left Sidebar */
    .sidebar {
      width: 240px; background: var(--sidebar-bg); border-right: 1px solid var(--border-color);
      margin-top: 52px; height: calc(100vh - 52px); display: flex; flex-direction: column; overflow-y: auto;
    }
    .nav-group-title { font-size: 11px; text-transform: uppercase; color: var(--text-muted); padding: 16px 20px 8px; font-weight: 700; letter-spacing: 0.5px; }
    .nav-item {
      display: flex; align-items: center; gap: 12px; padding: 10px 20px; color: #475569; text-decoration: none;
      font-size: 13px; font-weight: 500; cursor: pointer; border-left: 3px solid transparent; transition: all 0.2s;
    }
    .nav-item:hover { background-color: #f1f5f9; color: var(--text-main); }
    .nav-item.active { background-color: var(--primary-light); color: var(--primary); border-left-color: var(--primary); font-weight: 600; }
    .nav-item i { width: 16px; font-size: 14px; }
    .nav-subtitle { font-size: 11px; color: var(--text-muted); margin-top: 2px; }

    /* Content Area */
    .content-area {
      flex: 1; margin-top: 52px; height: calc(100vh - 52px); overflow-y: auto; padding: 24px 32px;
    }
    .page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
    .page-title { font-size: 22px; font-weight: 700; color: #1e293b; }
    .filter-select { padding: 6px 14px; border: 1px solid var(--border-color); border-radius: 6px; font-size: 13px; background: white; outline: none; }

    /* KPI Donut Section */
    .kpi-row { display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; margin-bottom: 24px; }
    .kpi-card {
      background: var(--card-bg); border: 1px solid var(--border-color); border-radius: 12px; padding: 20px;
      display: flex; flex-direction: column; align-items: center; box-shadow: 0 1px 3px rgba(0,0,0,0.02);
    }
    .kpi-card-title { font-size: 14px; font-weight: 600; color: #475569; margin-bottom: 16px; align-self: flex-start; }
    .donut-container { position: relative; width: 140px; height: 140px; display: flex; align-items: center; justify-content: center; }
    .donut-circle { width: 100%; height: 100%; transform: rotate(-90deg); }
    .donut-center-text { position: absolute; text-align: center; }
    .donut-center-number { font-size: 26px; font-weight: 700; color: #0f172a; }
    .donut-center-label { font-size: 11px; color: var(--text-muted); }
    .donut-legend { display: flex; gap: 16px; margin-top: 16px; font-size: 12px; }
    .legend-item { display: flex; align-items: center; gap: 6px; }
    .legend-dot { width: 8px; height: 8px; border-radius: 50%; }

    /* Anomaly Metrics Grid */
    .anomaly-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 16px; margin-bottom: 24px; }
    .anomaly-card {
      background: var(--card-bg); border: 1px solid var(--border-color); border-radius: 10px; padding: 16px;
      text-align: center; box-shadow: 0 1px 2px rgba(0,0,0,0.02);
    }
    .anomaly-name { font-size: 13px; font-weight: 600; color: #475569; margin-bottom: 8px; }
    .anomaly-total { font-size: 24px; font-weight: 700; color: #0f172a; margin-bottom: 6px; }
    .anomaly-substatus { font-size: 12px; display: flex; justify-content: center; gap: 8px; font-weight: 600; }
    .stat-pass { color: var(--success); }
    .stat-warn { color: var(--warning); }
    .stat-fail { color: var(--danger); }

    /* Drilldown Table */
    .section-box {
      background: var(--card-bg); border: 1px solid var(--border-color); border-radius: 12px; padding: 20px; margin-bottom: 24px;
    }
    .section-title { font-size: 16px; font-weight: 600; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center; }
    .status-badge { display: inline-flex; align-items: center; padding: 3px 8px; border-radius: 9999px; font-size: 11px; font-weight: 600; }
    .badge-pass { background: #dcfce7; color: #15803d; }
    .badge-fail { background: #fee2e2; color: #b91c1c; }
    .badge-warn { background: #fef3c7; color: #b45309; }

    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--border-color); color: var(--text-muted); font-weight: 600; }
    td { padding: 12px; border-bottom: 1px solid #f1f5f9; }
    tr:hover { background-color: #f8fafc; }

    /* Interactive tabs inside report */
    .tab-content { display: none; }
    .tab-content.active { display: block; }
  </style>
</head>
<body>

  <!-- Top Navbar -->
  <header class="top-header">
    <div class="brand-section">
      <div class="brand-logo"><i class="fa-solid fa-bolt"></i></div>
      <div class="env-badge">
        <span class="env-dot"></span>
        <span>koro_lakehouse_prod (BigQuery)</span>
      </div>
    </div>
    <div class="header-time">
      <i class="fa-regular fa-clock"></i> Generated: <strong>Today, 03:00 UTC (dbt test completed)</strong>
    </div>
  </header>

  <!-- Left Sidebar -->
  <aside class="sidebar">
    <div class="nav-group-title">Observability</div>
    <div class="nav-item active" onclick="switchReportTab('dashboard', this)">
      <i class="fa-solid fa-table-columns"></i>
      <div>
        <div>Dashboard</div>
        <div class="nav-subtitle">Review data health</div>
      </div>
    </div>
    <div class="nav-item" onclick="switchReportTab('lineage', this)">
      <i class="fa-solid fa-diagram-project"></i>
      <div>
        <div>Lineage</div>
        <div class="nav-subtitle">Analyze root cause & impact</div>
      </div>
    </div>
    <div class="nav-item" onclick="switchReportTab('catalog', this)">
      <i class="fa-solid fa-book-open"></i>
      <div>
        <div>Catalog</div>
        <div class="nav-subtitle">Explore datasets</div>
      </div>
    </div>

    <div class="nav-group-title">Data Tests</div>
    <div class="nav-item" onclick="switchReportTab('results', this)">
      <i class="fa-solid fa-list-check"></i>
      <div>
        <div>Results</div>
        <div class="nav-subtitle">Triage data issues</div>
      </div>
    </div>
    <div class="nav-item" onclick="switchReportTab('history', this)">
      <i class="fa-solid fa-clock-rotate-left"></i>
      <div>
        <div>Test History</div>
        <div class="nav-subtitle">Find flaky tests</div>
      </div>
    </div>

    <div class="nav-group-title">Data Pipelines</div>
    <div class="nav-item" onclick="switchReportTab('duration', this)">
      <i class="fa-solid fa-gauge-high"></i>
      <div>
        <div>Model Duration</div>
        <div class="nav-subtitle">Detect bottlenecks</div>
      </div>
    </div>
  </aside>

  <!-- Main View -->
  <main class="content-area">
    
    <!-- Tab 1: Dashboard -->
    <div id="tab-dashboard" class="tab-content active">
      <div class="page-header">
        <h1 class="page-title">Dashboard</h1>
        <select class="filter-select">
          <option>Filter by: All Layers (Bronze / Silver / Gold)</option>
          <option>Silver Kimball Star Schema</option>
          <option>Gold Presentation Marts</option>
        </select>
      </div>

      <!-- Top Donut Cards -->
      <div class="kpi-row">
        <!-- Test Results Breakdown -->
        <div class="kpi-card">
          <div class="kpi-card-title">Test results breakdown</div>
          <div class="donut-container">
            <svg class="donut-circle" viewBox="0 0 36 36">
              <path stroke="#e2e8f0" stroke-width="3.8" fill="none" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" />
              <path stroke="#10b981" stroke-width="3.8" stroke-dasharray="100, 100" fill="none" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" />
            </svg>
            <div class="donut-center-text">
              <div class="donut-center-number">30</div>
              <div class="donut-center-label">Tests</div>
            </div>
          </div>
          <div class="donut-legend">
            <div class="legend-item"><span class="legend-dot" style="background:#10b981"></span> Passed (30)</div>
            <div class="legend-item"><span class="legend-dot" style="background:#f59e0b"></span> Warn (0)</div>
            <div class="legend-item"><span class="legend-dot" style="background:#ef4444"></span> Fail (0)</div>
          </div>
        </div>

        <!-- Tables Health -->
        <div class="kpi-card">
          <div class="kpi-card-title">Tables health</div>
          <div class="donut-container">
            <svg class="donut-circle" viewBox="0 0 36 36">
              <path stroke="#e2e8f0" stroke-width="3.8" fill="none" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" />
              <path stroke="#10b981" stroke-width="3.8" stroke-dasharray="100, 100" fill="none" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" />
            </svg>
            <div class="donut-center-text">
              <div class="donut-center-number">11</div>
              <div class="donut-center-label">Tables</div>
            </div>
          </div>
          <div class="donut-legend">
            <div class="legend-item"><span class="legend-dot" style="background:#10b981"></span> Healthy (11)</div>
            <div class="legend-item"><span class="legend-dot" style="background:#ef4444"></span> Degraded (0)</div>
          </div>
        </div>

        <!-- Monitored Tables -->
        <div class="kpi-card">
          <div class="kpi-card-title">Monitored tables</div>
          <div class="donut-container">
            <svg class="donut-circle" viewBox="0 0 36 36">
              <path stroke="#e2e8f0" stroke-width="3.8" fill="none" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" />
              <path stroke="#10b981" stroke-width="3.8" stroke-dasharray="73, 100" fill="none" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" />
            </svg>
            <div class="donut-center-text">
              <div class="donut-center-number">11 / 15</div>
              <div class="donut-center-label">Coverage (73%)</div>
            </div>
          </div>
          <div class="donut-legend">
            <div class="legend-item"><span class="legend-dot" style="background:#10b981"></span> Monitored (11)</div>
            <div class="legend-item"><span class="legend-dot" style="background:#cbd5e1"></span> Sandbox/Drafts (4)</div>
          </div>
        </div>
      </div>

      <!-- Anomaly Metrics Row -->
      <div class="anomaly-grid">
        <div class="anomaly-card">
          <div class="anomaly-name">Freshness</div>
          <div class="anomaly-total">0</div>
          <div class="anomaly-substatus"><span class="stat-pass">0</span> | <span class="stat-warn">0</span> | <span class="stat-fail">0</span></div>
        </div>
        <div class="anomaly-card">
          <div class="anomaly-name">Volume</div>
          <div class="anomaly-total">0</div>
          <div class="anomaly-substatus"><span class="stat-pass">0</span> | <span class="stat-warn">0</span> | <span class="stat-fail">0</span></div>
        </div>
        <div class="anomaly-card">
          <div class="anomaly-name">Schema changes</div>
          <div class="anomaly-total">0</div>
          <div class="anomaly-substatus"><span class="stat-pass">0</span> | <span class="stat-warn">0</span> | <span class="stat-fail">0</span></div>
        </div>
        <div class="anomaly-card">
          <div class="anomaly-name">dbt tests</div>
          <div class="anomaly-total">30</div>
          <div class="anomaly-substatus"><span class="stat-pass">30</span> | <span class="stat-warn">0</span> | <span class="stat-fail">0</span></div>
        </div>
        <div class="anomaly-card">
          <div class="anomaly-name">Anomalies Detected</div>
          <div class="anomaly-total">0</div>
          <div class="anomaly-substatus"><span class="stat-pass">0 Critical</span></div>
        </div>
      </div>

      <!-- Active Monitored Tables -->
      <div class="section-box">
        <div class="section-title">
          <span>Active Monitored Production Models</span>
          <span style="font-size:12px; color:var(--text-muted); font-weight:normal;">Last run: dbt test passed in 1.42s</span>
        </div>
        <table>
          <thead>
            <tr>
              <th>Model Name</th>
              <th>Layer</th>
              <th>Tests Configured</th>
              <th>Freshness SLA</th>
              <th>Volume Trend</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><strong>fct_order_items</strong></td>
              <td>Silver (Fact)</td>
              <td>6 tests (PK unique, FK checks, non-negative)</td>
              <td>12 hours (Last updated: 1h ago)</td>
              <td>+4.2% (1,250 rows)</td>
              <td><span class="status-badge badge-pass"><i class="fa-solid fa-check"></i> Passed</span></td>
            </tr>
            <tr>
              <td><strong>fct_orders</strong></td>
              <td>Silver (Fact)</td>
              <td>5 tests (PK unique, timestamp order)</td>
              <td>12 hours (Last updated: 1h ago)</td>
              <td>+3.8% (500 rows)</td>
              <td><span class="status-badge badge-pass"><i class="fa-solid fa-check"></i> Passed</span></td>
            </tr>
            <tr>
              <td><strong>fct_returns</strong></td>
              <td>Silver (Fact)</td>
              <td>4 tests (Refund amount &le; gross)</td>
              <td>24 hours (Last updated: 2h ago)</td>
              <td>Stable (260 rows)</td>
              <td><span class="status-badge badge-pass"><i class="fa-solid fa-check"></i> Passed</span></td>
            </tr>
            <tr>
              <td><strong>dim_products</strong></td>
              <td>Silver (Dim)</td>
              <td>5 tests (SK unique, valid category FK)</td>
              <td>24 hours (Last updated: 3h ago)</td>
              <td>Stable (120 SKUs)</td>
              <td><span class="status-badge badge-pass"><i class="fa-solid fa-check"></i> Passed</span></td>
            </tr>
            <tr>
              <td><strong>dim_customers</strong></td>
              <td>Silver (Dim)</td>
              <td>4 tests (Email format, PK unique)</td>
              <td>24 hours (Last updated: 3h ago)</td>
              <td>+2.1% (350 profiles)</td>
              <td><span class="status-badge badge-pass"><i class="fa-solid fa-check"></i> Passed</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Tab 2: Lineage -->
    <div id="tab-lineage" class="tab-content">
      <div class="page-header">
        <h1 class="page-title">Run-time Status Lineage</h1>
      </div>
      <div class="section-box">
        <p style="margin-bottom:16px; color:var(--text-muted); font-size:13px;">
          Lineage nodes reflect live test status: Green (Passing SLA), Yellow (Warning), Red (Failed).
        </p>
        <div style="background:#f1f5f9; border-radius:8px; padding:30px; text-align:center; font-family:monospace;">
          <div style="display:inline-flex; align-items:center; gap:24px;">
            <div style="padding:12px 18px; background:white; border:2px solid #10b981; border-radius:6px; box-shadow:0 1px 3px rgba(0,0,0,0.05);">
              📦 bronze_orders_raw<br><span style="font-size:10px; color:#10b981;">● Freshness: 100%</span>
            </div>
            <span>➔</span>
            <div style="padding:12px 18px; background:white; border:2px solid #10b981; border-radius:6px; box-shadow:0 1px 3px rgba(0,0,0,0.05);">
              ⭐ fct_order_items<br><span style="font-size:10px; color:#10b981;">● 6/6 Tests Passing</span>
            </div>
            <span>➔</span>
            <div style="padding:12px 18px; background:white; border:2px solid #10b981; border-radius:6px; box-shadow:0 1px 3px rgba(0,0,0,0.05);">
              🏆 mart_executive_overview<br><span style="font-size:10px; color:#10b981;">● 0 Anomalies</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Tab 3: Catalog -->
    <div id="tab-catalog" class="tab-content">
      <div class="page-header">
        <h1 class="page-title">Observed Datasets & Metrics</h1>
      </div>
      <div class="section-box">
        <p style="color:var(--text-muted); font-size:13px;">
          Dynamic column profiling & volume drift telemetry across BigQuery warehouse.
        </p>
      </div>
    </div>

    <!-- Tab 4: Results -->
    <div id="tab-results" class="tab-content">
      <div class="page-header">
        <h1 class="page-title">Test Results Triage</h1>
      </div>
      <div class="section-box">
        <p style="color:var(--text-muted); font-size:13px; margin-bottom:12px;">30 tests executed in the last pipeline run. 0 errors detected.</p>
        <div style="padding:12px; background:#f0fdf4; border:1px solid #bbf7d0; border-radius:6px; color:#166534; font-size:13px;">
          <i class="fa-solid fa-circle-check"></i> All assertions (uniqueness, referential integrity, GDPR retention tags) are currently compliant.
        </div>
      </div>
    </div>

    <!-- Tab 5: History -->
    <div id="tab-history" class="tab-content">
      <div class="page-header">
        <h1 class="page-title">Test Execution History (Flaky Test Analysis)</h1>
      </div>
      <div class="section-box">
        <p style="color:var(--text-muted); font-size:13px;">Flakiness index: 0.0% across all 30 tests in the past 14 days.</p>
      </div>
    </div>

    <!-- Tab 6: Duration -->
    <div id="tab-duration" class="tab-content">
      <div class="page-header">
        <h1 class="page-title">Model Duration & Bottlenecks</h1>
      </div>
      <div class="section-box">
        <table style="width:100%;">
          <thead>
            <tr>
              <th>Model</th>
              <th>Avg Duration</th>
              <th>Materialization</th>
              <th>Query Optimization Status</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><code>fct_order_items</code></td>
              <td>0.48s</td>
              <td>Table (Partitioned by order_date)</td>
              <td><span class="status-badge badge-pass">Optimal</span></td>
            </tr>
            <tr>
              <td><code>fct_returns</code></td>
              <td>0.29s</td>
              <td>Table</td>
              <td><span class="status-badge badge-pass">Optimal</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

  </main>

  <script>
    function switchReportTab(tabId, el) {
      document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
      const target = document.getElementById('tab-' + tabId);
      if (target) {
        target.classList.add('active');
      }
      if (el) {
        el.classList.add('active');
      }
    }
  </script>
</body>
</html>
"""


def export_elementary_report_file(output_path: Path) -> Path:
    """Export the self-contained Elementary Report HTML file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    html_content = get_elementary_report_html()
    output_path.write_text(html_content, encoding="utf-8")
    return output_path
