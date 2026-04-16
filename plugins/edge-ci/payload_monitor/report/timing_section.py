"""Generate HTML for the Install & Upgrade Timing dashboard section."""

import html
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from ..collectors.timing import compute_stats
from ..models import TimingReport, TimingRun


def _fmt_duration(seconds: float) -> str:
    """Format seconds as 'Xm Ys' or 'Xh Ym'."""
    if seconds <= 0:
        return "N/A"
    minutes = seconds / 60
    if minutes >= 60:
        h = int(minutes // 60)
        m = int(minutes % 60)
        return f"{h}h {m}m"
    return f"{minutes:.0f}m"


def _group_runs(
    runs: list[TimingRun],
) -> dict[tuple[str, str], list[TimingRun]]:
    """Group runs by (topology, run_type)."""
    groups: dict[tuple[str, str], list[TimingRun]] = defaultdict(list)
    for r in runs:
        groups[(r.topology, r.run_type)].append(r)
    return dict(groups)


def _variant_key(run: TimingRun) -> str:
    """Build a human-readable variant key from a run's variant dict."""
    v = run.variant
    parts = [
        v.get("network", "ipv4"),
        v.get("install_method", "metal"),
    ]
    if v.get("feature") == "techpreview":
        parts.append("techpreview")
    if v.get("scenario") not in (None, "standard"):
        parts.append(v["scenario"])
    return " / ".join(parts)


# ---------------------------------------------------------------------------
# SVG chart colours per topology+type
# ---------------------------------------------------------------------------

_COLORS = {
    ("TNA", "install"): "#58a6ff",
    ("TNA", "upgrade"): "#bc8cff",
    ("TNF", "install"): "#db6d28",
    ("TNF", "upgrade"): "#d29922",
}


def _color_for(topology: str, run_type: str) -> str:
    return _COLORS.get((topology, run_type), "#8b949e")


# ---------------------------------------------------------------------------
# Sub-section renderers
# ---------------------------------------------------------------------------

def render_summary_table(report: TimingReport) -> str:
    """Render the summary statistics table."""
    all_runs = list(report.runs.values())
    runs = report.successful_runs
    if not all_runs:
        return '<div class="empty-state">No timing runs collected.</div>'

    # Find groups with runs but zero successes
    all_groups = _group_runs(all_runs)
    success_groups = _group_runs(runs) if runs else {}

    rows = []
    for (topo, rtype) in sorted(all_groups.keys()):
        total = len(all_groups[(topo, rtype)])
        if (topo, rtype) in success_groups:
            stats = compute_stats(success_groups[(topo, rtype)])
            rows.append(
                f'<tr class="timing-row" data-ttopology="{html.escape(topo)}" data-ttype="{html.escape(rtype)}">'
                f'<td><span class="badge {topo.lower()}">{html.escape(topo)}</span></td>'
                f'<td>{html.escape(rtype.capitalize())}</td>'
                f'<td>{stats["count"]}</td>'
                f'<td>{_fmt_duration(stats["avg"])}</td>'
                f'<td>{_fmt_duration(stats["median"])}</td>'
                f'<td>{_fmt_duration(stats["p90"])}</td>'
                f'<td>{_fmt_duration(stats["p95"])}</td>'
                f'<td>{_fmt_duration(stats["p99"])}</td>'
                f'<td>{_fmt_duration(stats["min"])}</td>'
                f'<td>{_fmt_duration(stats["max"])}</td>'
                f'<td>{stats["cv"]}%</td>'
                f'</tr>'
            )
        else:
            rows.append(
                f'<tr class="timing-row" data-ttopology="{html.escape(topo)}" data-ttype="{html.escape(rtype)}" style="opacity:0.6">'
                f'<td><span class="badge {topo.lower()}">{html.escape(topo)}</span></td>'
                f'<td>{html.escape(rtype.capitalize())}</td>'
                f'<td>0 / {total}</td>'
                f'<td colspan="8" style="color:var(--text-muted);font-style:italic">'
                f'{total} run{"s" if total != 1 else ""} collected but none successful'
                f'</td>'
                f'</tr>'
            )

    return (
        '<table class="timing-summary">'
        '<thead><tr>'
        '<th>Topology</th><th>Type</th><th>Runs</th>'
        '<th>Avg</th><th>Median</th><th>P90</th><th>P95</th><th>P99</th>'
        '<th>Min</th><th>Max</th><th>CV</th>'
        '</tr></thead>'
        '<tbody>' + "\n".join(rows) + '</tbody>'
        '</table>'
    )


def render_variant_table(report: TimingReport) -> str:
    """Render the infrastructure variant breakdown table."""
    runs = report.successful_runs
    if not runs:
        return ""

    # Group by (topology, run_type, variant_key)
    variant_groups: dict[tuple[str, str, str], list[TimingRun]] = defaultdict(list)
    for r in runs:
        vk = _variant_key(r)
        variant_groups[(r.topology, r.run_type, vk)].append(r)

    # Only show if there's more than one variant
    if len(variant_groups) <= 1:
        return ""

    rows = []
    for (topo, rtype, vk) in sorted(variant_groups.keys()):
        group = variant_groups[(topo, rtype, vk)]
        stats = compute_stats(group)
        rows.append(
            f'<tr class="timing-row" data-ttopology="{html.escape(topo)}" data-ttype="{html.escape(rtype)}">'
            f'<td><span class="badge {topo.lower()}">{html.escape(topo)}</span></td>'
            f'<td>{html.escape(rtype.capitalize())}</td>'
            f'<td style="font-size:13px;color:var(--text-muted)">{html.escape(vk)}</td>'
            f'<td>{stats["count"]}</td>'
            f'<td>{_fmt_duration(stats["avg"])}</td>'
            f'<td>{_fmt_duration(stats["median"])}</td>'
            f'<td>{_fmt_duration(stats["p95"])}</td>'
            f'<td>{stats["cv"]}%</td>'
            f'</tr>'
        )

    return (
        '<h3>Infrastructure Variant Comparison</h3>'
        '<table class="timing-variants">'
        '<thead><tr>'
        '<th>Topology</th><th>Type</th><th>Variant</th><th>Runs</th>'
        '<th>Avg</th><th>Median</th><th>P95</th><th>CV</th>'
        '</tr></thead>'
        '<tbody>' + "\n".join(rows) + '</tbody>'
        '</table>'
    )


def render_phase_table(report: TimingReport) -> str:
    """Render the per-phase duration breakdown table."""
    if not report.phase_durations:
        return ""

    # Collect phases and topologies
    # phase_durations keys are "version:phase_name", values are {date: minutes}
    # We want to show the latest average per phase
    phase_avgs: dict[str, dict[str, float]] = defaultdict(dict)
    for key, daily in report.phase_durations.items():
        parts = key.split(":", 1)
        if len(parts) != 2:
            continue
        version, phase = parts
        values = [v for v in daily.values() if v > 0]
        if values:
            phase_avgs[phase][version] = sum(values) / len(values)

    if not phase_avgs:
        # All zeros — show N/A with note
        phases = sorted(set(
            k.split(":", 1)[1] for k in report.phase_durations.keys()
            if ":" in k
        ))
        if not phases:
            return ""
        rows = []
        for phase in phases:
            short = phase.replace("install should succeed: ", "")
            rows.append(f'<tr><td>{html.escape(short)}</td><td>N/A*</td></tr>')
        return (
            '<h3>Install Phase Breakdown</h3>'
            '<table class="timing-phases">'
            '<thead><tr><th>Phase</th><th>Avg Duration</th></tr></thead>'
            '<tbody>' + "\n".join(rows) + '</tbody>'
            '</table>'
            '<div style="font-size:11px;color:var(--text-muted);margin-top:4px">'
            '* Sippy <code>/api/tests/durations</code> currently returns 0 for '
            '<code>cluster install</code> suite tests — data will populate once the upstream bug is fixed.'
            '</div>'
        )

    # Has real data
    rows = []
    for phase in sorted(phase_avgs.keys()):
        short = phase.replace("install should succeed: ", "")
        avg_mins = sum(phase_avgs[phase].values()) / len(phase_avgs[phase])
        rows.append(
            f'<tr><td>{html.escape(short)}</td>'
            f'<td>{avg_mins:.1f}m</td></tr>'
        )

    return (
        '<h3>Install Phase Breakdown</h3>'
        '<table class="timing-phases">'
        '<thead><tr><th>Phase</th><th>Avg Duration</th></tr></thead>'
        '<tbody>' + "\n".join(rows) + '</tbody>'
        '</table>'
    )


def render_trend_svg(report: TimingReport) -> str:
    """Render an inline SVG line chart of daily average duration."""
    runs = report.successful_runs
    if len(runs) < 2:
        return ""

    # Group by (topology, run_type, date)
    daily: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for r in runs:
        try:
            dt = datetime.fromisoformat(r.start_time.replace("Z", "+00:00"))
            date_str = dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
        daily[(r.topology, r.run_type)][date_str].append(r.duration_seconds)

    # Compute daily averages
    series: dict[tuple[str, str], list[tuple[str, float]]] = {}
    all_dates = set()
    for key, date_map in daily.items():
        points = []
        for date_str, durations in sorted(date_map.items()):
            avg = sum(durations) / len(durations)
            points.append((date_str, avg))
            all_dates.add(date_str)
        series[key] = points

    if not all_dates:
        return ""

    sorted_dates = sorted(all_dates)
    date_to_x = {d: i for i, d in enumerate(sorted_dates)}
    n_dates = len(sorted_dates)

    # SVG dimensions
    w, h = 700, 280
    pad_l, pad_r, pad_t, pad_b = 60, 20, 20, 50

    chart_w = w - pad_l - pad_r
    chart_h = h - pad_t - pad_b

    # Find y range (in minutes)
    all_vals = [v / 60 for pts in series.values() for _, v in pts]
    y_min = 0
    y_max = max(all_vals) * 1.1 if all_vals else 60

    def tx(i: int) -> float:
        if n_dates <= 1:
            return pad_l + chart_w / 2
        return pad_l + (i / (n_dates - 1)) * chart_w

    def ty(minutes: float) -> float:
        if y_max == y_min:
            return pad_t + chart_h / 2
        return pad_t + (1 - (minutes - y_min) / (y_max - y_min)) * chart_h

    parts = [
        f'<svg class="timing-trend-chart" viewBox="0 0 {w} {h}" '
        f'xmlns="http://www.w3.org/2000/svg" style="width:100%;max-width:{w}px;height:auto">',
        f'<rect width="{w}" height="{h}" fill="var(--surface)" rx="8"/>',
    ]

    # Grid lines
    n_grid = 5
    for i in range(n_grid + 1):
        yv = y_min + (y_max - y_min) * i / n_grid
        yp = ty(yv)
        parts.append(
            f'<line x1="{pad_l}" y1="{yp}" x2="{w - pad_r}" y2="{yp}" '
            f'stroke="var(--border)" stroke-width="0.5"/>'
        )
        parts.append(
            f'<text x="{pad_l - 8}" y="{yp + 4}" fill="var(--text-muted)" '
            f'font-size="11" text-anchor="end">{yv:.0f}m</text>'
        )

    # X-axis labels (show every Nth date to avoid overlap)
    step = max(1, n_dates // 7)
    for i, d in enumerate(sorted_dates):
        if i % step == 0 or i == n_dates - 1:
            xp = tx(i)
            label = d[5:]  # MM-DD
            parts.append(
                f'<text x="{xp}" y="{h - 10}" fill="var(--text-muted)" '
                f'font-size="11" text-anchor="middle">{label}</text>'
            )

    # Series lines
    for (topo, rtype), points in sorted(series.items()):
        color = _color_for(topo, rtype)
        coords = []
        for date_str, val in points:
            x = tx(date_to_x[date_str])
            y = ty(val / 60)
            coords.append(f"{x:.1f},{y:.1f}")

        if len(coords) >= 2:
            parts.append(
                f'<polyline points="{" ".join(coords)}" '
                f'fill="none" stroke="{color}" stroke-width="2"/>'
            )
        for date_str, val in points:
            x = tx(date_to_x[date_str])
            y = ty(val / 60)
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}">'
                f'<title>{topo} {rtype}: {_fmt_duration(val)} ({date_str})</title>'
                f'</circle>'
            )

    # Legend
    legend_x = pad_l + 8
    legend_y = pad_t + 8
    for i, (key, _) in enumerate(sorted(series.items())):
        topo, rtype = key
        color = _color_for(topo, rtype)
        lx = legend_x + i * 130
        parts.append(
            f'<rect x="{lx}" y="{legend_y}" width="12" height="12" rx="2" fill="{color}"/>'
        )
        parts.append(
            f'<text x="{lx + 16}" y="{legend_y + 10}" fill="var(--text)" '
            f'font-size="11">{topo} {rtype}</text>'
        )

    parts.append('</svg>')
    return "\n".join(parts)


def render_dow_heatmap(report: TimingReport) -> str:
    """Render a day-of-week heatmap table showing average duration by day."""
    runs = report.successful_runs
    if len(runs) < 3:
        return ""

    # Group by (topology, run_type, day_of_week)
    dow_groups: dict[tuple[str, str, int], list[float]] = defaultdict(list)
    for r in runs:
        try:
            dt = datetime.fromisoformat(r.start_time.replace("Z", "+00:00"))
            dow_groups[(r.topology, r.run_type, dt.weekday())].append(
                r.duration_seconds
            )
        except ValueError:
            continue

    if not dow_groups:
        return ""

    # Collect unique (topology, run_type) combos
    combos = sorted(set((t, rt) for t, rt, _ in dow_groups.keys()))
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    # Compute averages per cell
    avgs: dict[tuple[str, str, int], float] = {}
    all_avgs = []
    for (topo, rtype, dow), durations in dow_groups.items():
        avg = sum(durations) / len(durations)
        avgs[(topo, rtype, dow)] = avg
        all_avgs.append(avg)

    if not all_avgs:
        return ""

    overall_avg = sum(all_avgs) / len(all_avgs)

    def cell_color(val: float) -> str:
        """Return CSS color based on how far from overall average."""
        if overall_avg == 0:
            return "var(--text)"
        ratio = val / overall_avg
        if ratio <= 0.9:
            return "var(--green)"
        elif ratio >= 1.1:
            return "var(--red)"
        elif ratio >= 1.0:
            return "var(--yellow)"
        return "var(--text)"

    # Header
    header_cells = "<th>Day</th>"
    for topo, rtype in combos:
        header_cells += f"<th>{html.escape(topo)} {html.escape(rtype.capitalize())}</th>"

    rows = []
    for dow in range(7):
        cells = f"<td>{day_names[dow]}</td>"
        for topo, rtype in combos:
            val = avgs.get((topo, rtype, dow))
            if val is not None:
                color = cell_color(val)
                cells += (
                    f'<td style="color:{color};font-weight:600">'
                    f'{_fmt_duration(val)}</td>'
                )
            else:
                cells += '<td style="color:var(--text-muted)">—</td>'
        rows.append(f"<tr>{cells}</tr>")

    return (
        '<h3>Day-of-Week Heatmap</h3>'
        '<div style="font-size:11px;color:var(--text-muted);margin-bottom:6px">'
        'Helps distinguish code regressions from CI infrastructure congestion patterns. '
        '<span style="color:var(--green)">Green</span> = faster than average, '
        '<span style="color:var(--red)">Red</span> = slower than average.'
        '</div>'
        '<table class="timing-phases">'
        f'<thead><tr>{header_cells}</tr></thead>'
        '<tbody>' + "\n".join(rows) + '</tbody>'
        '</table>'
    )


def render_version_comparison(report: TimingReport) -> str:
    """Render version-over-version duration comparison table."""
    runs = report.successful_runs
    if not runs:
        return ""

    # Group by (topology, run_type, release)
    ver_groups: dict[tuple[str, str, str], list[TimingRun]] = defaultdict(list)
    for r in runs:
        ver_groups[(r.topology, r.run_type, r.release)].append(r)

    # Only show if we have data for multiple versions
    versions = sorted(set(r.release for r in runs))
    if len(versions) < 2:
        return ""

    combos = sorted(set((t, rt) for t, rt, _ in ver_groups.keys()))

    rows = []
    for topo, rtype in combos:
        cells = (
            f'<td><span class="badge {topo.lower()}">{html.escape(topo)}</span></td>'
            f'<td>{html.escape(rtype.capitalize())}</td>'
        )
        prev_avg = None
        for ver in versions:
            group = ver_groups.get((topo, rtype, ver), [])
            if group:
                stats = compute_stats(group)
                avg = stats["avg"]
                avg_str = _fmt_duration(avg)
                p95_str = _fmt_duration(stats["p95"])

                # Delta from previous version
                if prev_avg is not None and prev_avg > 0:
                    delta = (avg - prev_avg) / prev_avg * 100
                    delta_color = "var(--red)" if delta > 5 else "var(--green)" if delta < -5 else "var(--text-muted)"
                    delta_str = f'<span style="font-size:11px;color:{delta_color}">({delta:+.0f}%)</span>'
                else:
                    delta_str = ""

                cells += f'<td>{avg_str} {delta_str}</td><td>{p95_str}</td>'
                prev_avg = avg
            else:
                cells += '<td style="color:var(--text-muted)">—</td><td>—</td>'
                prev_avg = None
        rows.append(f'<tr class="timing-row" data-ttopology="{html.escape(topo)}" data-ttype="{html.escape(rtype)}">{cells}</tr>')

    # Build header
    header = "<th>Topology</th><th>Type</th>"
    for ver in versions:
        header += f"<th>{html.escape(ver)} Avg</th><th>{html.escape(ver)} P95</th>"

    return (
        '<h3>Version-over-Version Comparison</h3>'
        '<table class="timing-variants">'
        f'<thead><tr>{header}</tr></thead>'
        '<tbody>' + "\n".join(rows) + '</tbody>'
        '</table>'
    )


def render_anomaly_flags(report: TimingReport) -> str:
    """Render anomaly flags for runs exceeding P95 threshold."""
    runs = report.successful_runs
    if len(runs) < 5:
        return ""

    groups = _group_runs(runs)
    anomalies = []

    for (topo, rtype), group in groups.items():
        stats = compute_stats(group)
        p95 = stats["p95"]
        if p95 <= 0:
            continue
        for r in group:
            if r.duration_seconds > p95:
                excess = (r.duration_seconds - p95) / p95 * 100
                anomalies.append((r, topo, rtype, p95, excess))

    if not anomalies:
        return ""

    # Sort by excess percentage descending
    anomalies.sort(key=lambda x: -x[4])

    rows = []
    for r, topo, rtype, p95, excess in anomalies:
        rows.append(
            f'<tr class="timing-row" data-ttopology="{html.escape(topo)}" data-ttype="{html.escape(rtype)}" data-tversion="{html.escape(r.release)}">'
            f'<td><span class="badge {topo.lower()}">{html.escape(topo)}</span></td>'
            f'<td>{html.escape(rtype.capitalize())}</td>'
            f'<td style="font-size:12px">{html.escape(r.job_name)}</td>'
            f'<td>{_fmt_duration(r.duration_seconds)}</td>'
            f'<td>{_fmt_duration(p95)}</td>'
            f'<td style="color:var(--red);font-weight:600">+{excess:.0f}%</td>'
            f'<td style="font-size:12px;color:var(--text-muted)">{html.escape(r.start_time[:10])}</td>'
            f'</tr>'
        )

    return (
        '<h3>Anomaly Flags</h3>'
        '<div style="font-size:11px;color:var(--text-muted);margin-bottom:6px">'
        'Runs where duration exceeded the P95 threshold for their topology/type group.'
        '</div>'
        '<table class="timing-variants">'
        '<thead><tr>'
        '<th>Topology</th><th>Type</th><th>Job</th>'
        '<th>Duration</th><th>P95</th><th>Above P95</th><th>Date</th>'
        '</tr></thead>'
        '<tbody>' + "\n".join(rows) + '</tbody>'
        '</table>'
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _render_filter_bar(report: TimingReport) -> str:
    """Render the filter buttons for the timing section."""
    all_runs = list(report.runs.values())
    topologies = sorted(set(r.topology for r in all_runs))
    types = sorted(set(r.run_type for r in all_runs))
    versions = sorted(set(r.release for r in all_runs))

    parts = ['<div class="filters" id="timing-filters">']

    # Topology filter
    parts.append(
        '<button class="filter-btn active" '
        'data-group="ttopology" data-value="all">All Topologies</button>'
    )
    for topo in topologies:
        parts.append(
            f'<button class="filter-btn" '
            f'data-group="ttopology" data-value="{html.escape(topo)}">'
            f'{html.escape(topo)}</button>'
        )

    parts.append('<span style="margin:0 4px"></span>')

    # Type filter
    parts.append(
        '<button class="filter-btn active" '
        'data-group="ttype" data-value="all">All Types</button>'
    )
    for rtype in types:
        parts.append(
            f'<button class="filter-btn" '
            f'data-group="ttype" data-value="{html.escape(rtype)}">'
            f'{html.escape(rtype.capitalize())}</button>'
        )

    if len(versions) > 1:
        parts.append('<span style="margin:0 4px"></span>')

        # Version filter
        parts.append(
            '<button class="filter-btn active" '
            'data-group="tversion" data-value="all">All Versions</button>'
        )
        for ver in versions:
            parts.append(
                f'<button class="filter-btn" '
                f'data-group="tversion" data-value="{html.escape(ver)}">'
                f'{html.escape(ver)}</button>'
            )

    parts.append('</div>')
    return "\n".join(parts)


def render_timing_section(report: TimingReport) -> str:
    """Assemble the full timing HTML section."""
    if not report or not report.runs:
        return ""

    parts = [
        '<div class="timing-section">',
        '<h2>Install &amp; Upgrade Timing</h2>',
        '<div class="meta" style="margin-bottom:8px">'
        'Duration statistics for SNO/TNA/TNF CI jobs. '
        'Only successful runs are included in statistics. '
        f'Last updated: {html.escape(report.last_updated)}'
        '</div>',
        _render_filter_bar(report),
    ]

    # Summary table
    parts.append(render_summary_table(report))

    # Trend chart
    trend = render_trend_svg(report)
    if trend:
        parts.append('<h3>Duration Trend</h3>')
        parts.append(trend)

    # Variant table
    variant = render_variant_table(report)
    if variant:
        parts.append(variant)

    # Phase table
    phase = render_phase_table(report)
    if phase:
        parts.append(phase)

    # Day-of-week heatmap
    dow = render_dow_heatmap(report)
    if dow:
        parts.append(dow)

    # Version-over-version comparison
    ver_cmp = render_version_comparison(report)
    if ver_cmp:
        parts.append(ver_cmp)

    # Anomaly flags
    anomalies = render_anomaly_flags(report)
    if anomalies:
        parts.append(anomalies)

    parts.append('</div>')
    return "\n".join(parts)
