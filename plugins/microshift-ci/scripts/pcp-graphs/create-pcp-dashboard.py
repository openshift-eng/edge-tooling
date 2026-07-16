#!/usr/bin/env python3
"""Assemble a self-contained interactive PCP performance dashboard HTML.

Reads parsed JSON metric files from <workdir>/pcp-dashboard/<build_id>/<scenario>/
and scenario metadata from <workdir>/pcp-dashboard/scenarios.json.
Embeds Chart.js and all data inline — no external dependencies at runtime.

Usage:
    create-pcp-dashboard.py --workdir DIR [--timezone TZ]
"""

import argparse
import html
import json
import os
import sys


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
VENDOR_DIR = os.path.join(SCRIPT_DIR, "vendor")

CSS = """\
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f0f2f5; color: #333; display: flex; height: 100vh; overflow: hidden; }
.sidebar { width: 300px; min-width: 300px; background: #1a1a2e; color: #e0e0e0; overflow-y: auto; padding: 16px 0; display: flex; flex-direction: column; }
.sidebar h1 { font-size: 1.1em; padding: 0 16px 12px; border-bottom: 1px solid #2a2a4e; color: #fff; }
.sidebar .build-group { margin-top: 12px; }
.sidebar .build-label { font-size: 0.75em; text-transform: uppercase; color: #8888aa; padding: 0 16px 4px; letter-spacing: 0.5px; }
.sidebar .scenario-item { padding: 8px 16px; cursor: pointer; font-size: 0.88em; display: flex; align-items: center; gap: 8px; transition: background 0.15s; border-left: 3px solid transparent; }
.sidebar .scenario-item:hover { background: #2a2a4e; }
.sidebar .scenario-item.active { background: #2a2a4e; border-left-color: #e94560; color: #fff; }
.sidebar .status-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.sidebar .status-dot.pass { background: #28a745; }
.sidebar .status-dot.fail { background: #dc3545; }
.sidebar .status-dot.unknown { background: #6c757d; }
.sidebar .scenario-name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sidebar .scenario-meta { font-size: 0.78em; color: #8888aa; margin-left: auto; white-space: nowrap; }
.main { flex: 1; overflow-y: auto; padding: 20px; }
.main h2 { font-size: 1.3em; color: #1a1a2e; margin-bottom: 4px; }
.scenario-info { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 16px; }
.info-card { background: #fff; border-radius: 8px; padding: 10px 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); display: flex; flex-direction: column; align-items: center; min-width: 100px; }
.info-card .value { font-size: 1.4em; font-weight: 700; }
.info-card .label { font-size: 0.78em; color: #6c757d; text-transform: uppercase; }
.info-card .value.pass { color: #28a745; }
.info-card .value.fail { color: #dc3545; }
.chart-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px; }
.chart-card { background: #fff; border-radius: 8px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
.chart-card h3 { font-size: 0.95em; color: #1a1a2e; margin-bottom: 8px; }
.chart-card canvas { width: 100% !important; height: 280px !important; }
.chart-card:fullscreen { display: flex; flex-direction: column; justify-content: center; padding: 32px; }
.chart-card:fullscreen canvas { height: 70vh !important; }
.pcp-fs-btn { background: none; border: 1px solid #ddd; border-radius: 4px; cursor: pointer; font-size: 1.1em; color: #6c757d; padding: 2px 6px; line-height: 1; }
.pcp-fs-btn:hover { background: #f0f0f0; color: #333; }
.stats-row { display: flex; gap: 16px; flex-wrap: wrap; margin-top: 8px; font-size: 0.78em; color: #6c757d; }
.stats-row span { white-space: nowrap; }
.stats-row .val { font-weight: 600; color: #333; }
.vm-tabs { display: flex; gap: 0; margin-bottom: 16px; border-bottom: 2px solid #e0e0e0; }
.vm-tab { padding: 8px 16px; cursor: pointer; font-size: 0.88em; color: #6c757d; border: none; background: none; border-bottom: 2px solid transparent; margin-bottom: -2px; transition: color 0.15s, border-color 0.15s; }
.vm-tab:hover { color: #333; }
.vm-tab.active { color: #1a1a2e; border-bottom-color: #e94560; font-weight: 600; }
.empty-state { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 60vh; color: #6c757d; }
.empty-state h2 { font-size: 1.3em; margin-bottom: 8px; }
@media (max-width: 900px) {
    .chart-grid { grid-template-columns: 1fr; }
    .sidebar { width: 220px; min-width: 220px; }
}
"""

JS_APP = """\
(function() {
    const DATA = JSON.parse(document.getElementById('pcp-data').textContent);
    const SCENARIOS = JSON.parse(document.getElementById('pcp-scenarios').textContent);

    const sidebar = document.getElementById('sidebar-items');
    const mainArea = document.getElementById('main-area');
    let activeCharts = [];
    let activeKey = null;

    // Build sidebar
    const buildIds = Object.keys(DATA).sort();
    buildIds.forEach(function(bid) {
        const group = document.createElement('div');
        group.className = 'build-group';
        const label = document.createElement('div');
        label.className = 'build-label';
        label.textContent = 'Build ' + bid;
        group.appendChild(label);

        const scenarios = Object.keys(DATA[bid]).sort();
        scenarios.forEach(function(sc) {
            const meta = (SCENARIOS[bid] || {})[sc] || {};
            const item = document.createElement('div');
            item.className = 'scenario-item';
            item.dataset.key = bid + '/' + sc;

            const dot = document.createElement('span');
            dot.className = 'status-dot ' + (meta.status || 'unknown');
            item.appendChild(dot);

            const name = document.createElement('span');
            name.className = 'scenario-name';
            name.textContent = sc;
            name.title = sc;
            item.appendChild(name);

            if (meta.tests !== undefined) {
                const info = document.createElement('span');
                info.className = 'scenario-meta';
                info.textContent = meta.tests + 't';
                if (meta.failures > 0) info.textContent += ' ' + meta.failures + 'f';
                item.appendChild(info);
            }

            item.addEventListener('click', function() { selectScenario(bid, sc); });
            group.appendChild(item);
        });
        sidebar.appendChild(group);
    });

    // Select first scenario by default
    if (buildIds.length > 0) {
        const firstBid = buildIds[0];
        const firstSc = Object.keys(DATA[firstBid]).sort()[0];
        if (firstSc) selectScenario(firstBid, firstSc);
    }

    function selectScenario(bid, sc) {
        const key = bid + '/' + sc;
        if (key === activeKey) return;
        activeKey = key;

        document.querySelectorAll('.scenario-item').forEach(function(el) {
            el.classList.toggle('active', el.dataset.key === key);
        });

        activeCharts.forEach(function(c) { c.destroy(); });
        activeCharts = [];

        mainArea.innerHTML = '';

        const vms = DATA[bid][sc];
        const vmNames = Object.keys(vms).sort();
        const meta = (SCENARIOS[bid] || {})[sc] || {};

        // Header
        var h2 = document.createElement('h2');
        h2.textContent = sc;
        mainArea.appendChild(h2);

        // Info cards
        var infoRow = document.createElement('div');
        infoRow.className = 'scenario-info';
        infoRow.appendChild(infoCard(meta.status === 'pass' ? 'PASS' : meta.status === 'fail' ? 'FAIL' : '?', 'Status', meta.status || 'unknown'));
        infoRow.appendChild(infoCard(meta.tests !== undefined ? String(meta.tests) : '-', 'Tests', ''));
        infoRow.appendChild(infoCard(meta.failures !== undefined ? String(meta.failures) : '-', 'Failures', meta.failures > 0 ? 'fail' : ''));
        infoRow.appendChild(infoCard(meta.duration_sec !== undefined ? formatDuration(meta.duration_sec) : '-', 'Duration', ''));
        infoRow.appendChild(infoCard(vmNames.join(', '), 'VMs', ''));
        mainArea.appendChild(infoRow);

        // VM tabs + chart area
        var chartArea = document.createElement('div');
        chartArea.id = 'chart-area';
        if (vmNames.length > 1) {
            var tabs = document.createElement('div');
            tabs.className = 'vm-tabs';
            vmNames.forEach(function(vm) {
                var btn = document.createElement('button');
                btn.className = 'vm-tab';
                btn.textContent = vm;
                btn.addEventListener('click', function() { renderVm(vms, vm, chartArea, tabs); });
                tabs.appendChild(btn);
            });
            mainArea.appendChild(tabs);
        }
        mainArea.appendChild(chartArea);

        renderVm(vms, vmNames[0], chartArea, mainArea.querySelector('.vm-tabs'));
    }

    function renderVm(vms, vm, chartArea, tabBar) {
        activeCharts.forEach(function(c) { c.destroy(); });
        activeCharts = [];
        chartArea.innerHTML = '';

        if (tabBar) {
            tabBar.querySelectorAll('.vm-tab').forEach(function(btn) {
                btn.classList.toggle('active', btn.textContent === vm);
            });
        }

        var metrics = vms[vm];
        var grid = document.createElement('div');
        grid.className = 'chart-grid';
        chartArea.appendChild(grid);

        if (metrics.cpu) pcpCharts.renderCpu(grid, metrics.cpu);
        if (metrics.mem) pcpCharts.renderMem(grid, metrics.mem);
        if (metrics.io) pcpCharts.renderIo(grid, metrics.io);
        if (metrics.disk) pcpCharts.renderDisk(grid, metrics.disk);

        if (!metrics.cpu && !metrics.mem && !metrics.io && !metrics.disk) {
            var empty = document.createElement('div');
            empty.className = 'chart-card';
            empty.style.cssText = 'grid-column:1/-1;text-align:center;padding:40px;color:#6c757d;';
            empty.textContent = 'No PCP metric data available for this VM.';
            grid.appendChild(empty);
        }
    }

    function infoCard(value, label, cls) {
        var card = document.createElement('div');
        card.className = 'info-card';
        var valSpan = document.createElement('span');
        valSpan.className = 'value' + (cls ? ' ' + cls : '');
        valSpan.textContent = value;
        card.appendChild(valSpan);
        var lblSpan = document.createElement('span');
        lblSpan.className = 'label';
        lblSpan.textContent = label;
        card.appendChild(lblSpan);
        return card;
    }

    function formatDuration(sec) {
        if (sec < 60) return sec + 's';
        var m = Math.floor(sec / 60);
        var s = Math.round(sec % 60);
        if (m < 60) return m + 'm ' + s + 's';
        var h = Math.floor(m / 60);
        m = m % 60;
        return h + 'h ' + m + 'm';
    }

    pcpCharts.init({ onChart: function(c) { activeCharts.push(c); } });
})();
"""


def load_chartjs():
    """Load the vendored Chart.js UMD bundle."""
    path = os.path.join(VENDOR_DIR, "chart.umd.min.js")
    if not os.path.isfile(path):
        print(f"ERROR: Chart.js not found at {path}", file=sys.stderr)
        print("Run: curl -sL https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js "
              f"-o {path}", file=sys.stderr)
        sys.exit(1)
    with open(path) as f:
        return f.read()


def load_pcp_charts_js():
    """Load the shared PCP chart rendering functions."""
    path = os.path.join(SCRIPT_DIR, "pcp-charts.js")
    if not os.path.isfile(path):
        print(f"ERROR: pcp-charts.js not found at {path}", file=sys.stderr)
        sys.exit(1)
    with open(path) as f:
        return f.read()


def load_vm_metrics(vm_path):
    """Load metric JSON files from a single VM directory."""
    metrics = {}
    for name, key in [("cpu.json", "cpu"), ("mem.json", "mem"),
                       ("io.json", "io"), ("disk.json", "disk")]:
        fpath = os.path.join(vm_path, name)
        if os.path.isfile(fpath):
            try:
                with open(fpath) as f:
                    metrics[key] = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
    return metrics


def load_metrics(dashboard_dir):
    """Load all parsed metric JSON files, grouped by build_id, scenario, and VM."""
    data = {}
    if not os.path.isdir(dashboard_dir):
        return data

    for build_id in sorted(os.listdir(dashboard_dir)):
        build_path = os.path.join(dashboard_dir, build_id)
        if not os.path.isdir(build_path) or build_id == "scenarios.json":
            continue
        for scenario in sorted(os.listdir(build_path)):
            scenario_path = os.path.join(build_path, scenario)
            if not os.path.isdir(scenario_path):
                continue

            vms = {}
            for vm in sorted(os.listdir(scenario_path)):
                vm_path = os.path.join(scenario_path, vm)
                if not os.path.isdir(vm_path):
                    continue
                vms[vm] = load_vm_metrics(vm_path)

            if vms:
                data.setdefault(build_id, {})[scenario] = vms

    return data


def load_scenarios(dashboard_dir):
    """Load scenario metadata."""
    path = os.path.join(dashboard_dir, "scenarios.json")
    if os.path.isfile(path):
        with open(path) as f:
            return json.load(f)
    return {}


def escape_json_for_script(json_str):
    """Neutralize sequences that could break out of a script tag."""
    return json_str.replace("</", "<\\/").replace("<!--", "<\\!--")


def build_html(chartjs_src, pcp_charts_src, data_json, scenarios_json, timezone,
               title="PCP Performance Dashboard"):
    """Assemble the complete HTML document."""
    safe_data = escape_json_for_script(data_json)
    safe_scenarios = escape_json_for_script(scenarios_json)
    safe_tz = html.escape(timezone)
    safe_title = html.escape(title)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{safe_title}</title>
<style>
{CSS}
</style>
</head>
<body>
<div class="sidebar">
    <h1>{safe_title}</h1>
    <div style="padding:4px 16px;font-size:0.75em;color:#8888aa;">Timezone: {safe_tz}</div>
    <div id="sidebar-items"></div>
</div>
<div class="main" id="main-area">
    <div class="empty-state">
        <h2>Select a scenario</h2>
        <p>Choose a scenario from the sidebar to view performance metrics.</p>
    </div>
</div>
<script type="application/json" id="pcp-data">{safe_data}</script>
<script type="application/json" id="pcp-scenarios">{safe_scenarios}</script>
<script>
{chartjs_src}
</script>
<script>
{pcp_charts_src}
</script>
<script>
{JS_APP}
</script>
</body>
</html>"""


def main():
    """Parse arguments, load metric data, and write the dashboard HTML."""
    parser = argparse.ArgumentParser(
        description="Generate interactive PCP performance dashboard HTML")
    parser.add_argument("--workdir", required=True,
                        help="Work directory with pcp-dashboard/ subdirectory")
    parser.add_argument("--timezone", default="UTC",
                        help="Timezone label for display (default: UTC)")
    parser.add_argument("--output",
                        help="Output HTML file (default: <workdir>/pcp-dashboard.html)")
    parser.add_argument("--title", default="PCP Performance Dashboard",
                        help="HTML <title> for the dashboard")
    args = parser.parse_args()

    dashboard_dir = os.path.join(args.workdir, "pcp-dashboard")
    output = args.output or os.path.join(args.workdir, "pcp-dashboard.html")

    chartjs_src = load_chartjs()
    pcp_charts_src = load_pcp_charts_js()
    data = load_metrics(dashboard_dir)
    scenarios = load_scenarios(dashboard_dir)

    if not data:
        print("WARNING: No PCP metric data found in "
              f"{dashboard_dir}", file=sys.stderr)

    data_json = json.dumps(data, separators=(",", ":"))
    scenarios_json = json.dumps(scenarios, separators=(",", ":"))

    html = build_html(chartjs_src, pcp_charts_src, data_json, scenarios_json, args.timezone,
                       title=args.title)

    with open(output, "w") as f:
        f.write(html)

    total_scenarios = sum(len(v) for v in data.values())
    total_vms = sum(len(vms) for bld in data.values() for vms in bld.values())
    print(f"Dashboard generated: {output} "
          f"({total_scenarios} scenarios, {total_vms} VMs, {len(html)//1024}KB)",
          file=sys.stderr)


if __name__ == "__main__":
    main()
