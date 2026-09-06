#!/usr/bin/env python3
"""Generate a self-contained HTML report of the CVE investigation.

Groups tickets by Jira component, then by affected version, showing each
ticket's CVE ID(s) (linked back to the Jira ticket) and govulncheck scan
status - including the actual matched findings (vulnerability ID + module),
not just a pass/fail badge. No LLM involved - verdicts and grouping are
deterministic, reusing the same logic as generate_report.py's markdown report
and analyze_scan_result.py's finding formatting.

Only components we've actually mapped to a repo in config/component-repos.json
are shown - the Jira "Black CVE" filter spans hundreds of components across
the whole org, most of which aren't edge components we scan/own, so showing
all of them would bury the ones we care about. Everything else is dropped
before rendering (not just hidden), and the dropped count is reported on
stdout so it's clear this is a deliberate, deterministic filter, not missing
data.

Also renders a separate "Ad-hoc repo checks" section from any check-repo
(single-repo, outside the Jira pipeline) runs found under
<workdir>/scans/results/*/analysis.json, so a one-off validation of e.g.
openshift/microshift or openshift/lvm-operator shows up in the same report
as the bulk Jira-driven results, as long as it used the same --workdir.

Tickets flagged private (see lib.cve_extract.is_private_ticket - Jira
Security Level or a "private" label) are rendered with NOTHING but a link
back to the Jira ticket: no CVE ID, summary, or scan findings. They are
grouped under the neutral version "Withheld" so real OCP versions cannot
leak via section headings.

Usage:
    generate_html_report.py --workdir DIR
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from analyze_scan_result import finding_label  # noqa: E402
from generate_report import load_json, verdict_for_ticket  # noqa: E402

VERDICT_LABELS = {
    "affected": ("AFFECTED", "affected"),
    "not_affected": ("NOT AFFECTED", "not-affected"),
    "inconclusive": ("INCONCLUSIVE", "inconclusive"),
    "not_scanned": ("NOT SCANNED", "not-scanned"),
    "needs_review": ("NEEDS REVIEW", "not-scanned"),
}


def version_sort_key(version: str) -> tuple:
    if version in ("Unspecified", "Withheld"):
        return (1, version)
    parts = []
    for part in version.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return (0, tuple(parts))


def scan_status_badge(scan: dict) -> tuple[str, str]:
    if scan.get("scan_incomplete"):
        return f"INCOMPLETE (exit {scan.get('scan_exit_code')}, likely OOM)", "inconclusive"
    if scan.get("affected"):
        return "AFFECTED", "affected"
    return "NOT AFFECTED", "not-affected"


def load_known_components(config_path: Path) -> set[str]:
    """Component names we've mapped to a repo (config/component-repos.json)."""
    if not config_path.is_file():
        return set()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    return set(config.get("components", {}).keys())


def build_scan_index(scan_results: dict) -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = {}
    for result in scan_results.get("results", []):
        for ticket_key in result.get("ticket_keys", []):
            index.setdefault(ticket_key, []).append(result)
    return index


def group_tickets(
    parsed: dict,
    scan_index: dict[str, list[dict]],
    known_components: set[str],
) -> tuple[dict[str, dict[str, list[dict]]], int]:
    """component -> version -> list of row dicts, sorted for rendering.

    Tickets whose component isn't in `known_components` (config/component-repos.json)
    are dropped entirely rather than grouped under an "Unknown"/noise bucket -
    this report is scoped to the edge components we actually map to a repo.
    Returns the grouped dict plus how many tickets were dropped.
    """
    grouped: dict[str, dict[str, list[dict]]] = {}
    dropped = 0

    for ticket in parsed.get("tickets", []):
        component = ticket.get("component") or "Unknown"
        if known_components and component not in known_components:
            dropped += 1
            continue
        scans = scan_index.get(ticket["key"], [])
        is_private = ticket.get("is_private", False)
        # Private tickets must not be bucketed under real OCP versions.
        versions = (
            ["Withheld"]
            if is_private
            else (ticket.get("versions") or ["Unspecified"])
        )
        verdict = "private" if is_private else verdict_for_ticket(ticket, scans)

        row = {
            "key": ticket["key"],
            "url": ticket.get("url", ""),
            "is_private": is_private,
            "verdict": verdict,
            "cve_ids": [] if is_private else ticket.get("cve_ids", []),
            "summary": "" if is_private else ticket.get("summary", ""),
            "status": ticket.get("status", ""),
            "scans": [] if is_private else scans,
        }

        for version in versions:
            grouped.setdefault(component, {}).setdefault(version, []).append(row)

    return grouped, dropped


def render_findings(findings: list[dict]) -> str:
    """List what govulncheck actually matched - not just a pass/fail badge."""
    if not findings:
        return ""
    items = "".join(f"<li>{html.escape(finding_label(f))}</li>" for f in findings)
    return f'<ul class="finding-list">{items}</ul>'


def render_scan_detail(scans: list[dict]) -> str:
    if not scans:
        return ""
    items = []
    for scan in scans:
        label, css_class = scan_status_badge(scan)
        repo = html.escape(scan.get("repo_slug") or scan.get("repo_url", ""))
        ref = html.escape(scan.get("git_ref", ""))
        findings_html = render_findings(scan.get("matched_findings") or [])
        items.append(
            f'<li><code>{repo}@{ref}</code> '
            f'<span class="badge badge-{css_class}">{label}</span>'
            f"{findings_html}</li>"
        )
    return f'<ul class="scan-list">{"".join(items)}</ul>'


def render_row(row: dict) -> str:
    key = html.escape(row["key"])
    url = html.escape(row["url"])
    if row["is_private"]:
        return (
            '<tr class="private-row">'
            f'<td colspan="4">&#128274; Private ticket - details withheld. '
            f'<a href="{url}" target="_blank" rel="noopener">{key}</a></td>'
            "</tr>"
        )

    label, css_class = VERDICT_LABELS.get(row["verdict"], (row["verdict"].upper(), "not-scanned"))
    cve_links = ", ".join(
        f'<a href="{url}" target="_blank" rel="noopener">{html.escape(cve)}</a>'
        for cve in row["cve_ids"]
    ) or f'<a href="{url}" target="_blank" rel="noopener">{key}</a>'
    summary = html.escape(row["summary"])
    scan_detail = render_scan_detail(row["scans"])

    return (
        "<tr>"
        f'<td class="cve-cell">{cve_links}<div class="ticket-key">{key} &middot; {html.escape(row["status"])}</div></td>'
        f'<td class="summary-cell">{summary}</td>'
        f'<td><span class="badge badge-{css_class}">{label}</span></td>'
        f'<td>{scan_detail or "&mdash;"}</td>'
        "</tr>"
    )


def unique_rows_by_key(versions: dict[str, list[dict]]) -> list[dict]:
    """Deduplicate rows that appear under multiple version buckets.

    Tickets with several OCP versions are listed once per version section, but
    component/global totals must count each ticket key only once.
    """
    seen: dict[str, dict] = {}
    for rows in versions.values():
        for row in rows:
            key = row.get("key")
            if key is None or key in seen:
                continue
            seen[key] = row
    return list(seen.values())


def render_component(component: str, versions: dict[str, list[dict]], *, open_by_default: bool) -> str:
    version_blocks = []
    for version in sorted(versions.keys(), key=version_sort_key):
        rows = versions[version]
        rows_html = "".join(render_row(row) for row in sorted(rows, key=lambda r: r["key"]))
        version_blocks.append(
            f'<h4>{html.escape(version)}</h4>'
            '<table><thead><tr><th>CVE / Ticket</th><th>Summary</th>'
            "<th>Verdict</th><th>govulncheck</th></tr></thead>"
            f"<tbody>{rows_html}</tbody></table>"
        )

    unique_rows = unique_rows_by_key(versions)
    total = len(unique_rows)
    affected = sum(1 for row in unique_rows if row["verdict"] == "affected")
    badge = f'<span class="badge badge-affected">{affected} affected</span>' if affected else ""
    open_attr = " open" if open_by_default else ""

    return (
        f"<details{open_attr}>"
        f'<summary>{html.escape(component)} '
        f'<span class="count">({total} ticket{"s" if total != 1 else ""})</span> {badge}</summary>'
        f'<div class="component-body">{"".join(version_blocks)}</div>'
        "</details>"
    )


def render_summary(grouped: dict[str, dict[str, list[dict]]]) -> tuple[str, dict[str, int]]:
    counts = {"total": 0, "private": 0, "affected": 0, "not_affected": 0, "inconclusive": 0, "other": 0}
    seen_keys: set[str] = set()
    for versions in grouped.values():
        for row in unique_rows_by_key(versions):
            key = row["key"]
            if key in seen_keys:
                continue
            seen_keys.add(key)
            counts["total"] += 1
            verdict = row["verdict"]
            if verdict == "private":
                counts["private"] += 1
            elif verdict in ("affected", "not_affected", "inconclusive"):
                counts[verdict] += 1
            else:
                counts["other"] += 1

    cards = [
        ("Total tickets", counts["total"], "total"),
        ("Affected", counts["affected"], "affected"),
        ("Not affected", counts["not_affected"], "not-affected"),
        ("Inconclusive", counts["inconclusive"], "inconclusive"),
        ("Private (redacted)", counts["private"], "private"),
    ]
    cards_html = "".join(
        f'<div class="stat-card stat-{cls}"><div class="stat-value">{value}</div>'
        f'<div class="stat-label">{label}</div></div>'
        for label, value, cls in cards
    )
    return cards_html, counts


def load_check_repo_results(workdir: Path) -> list[dict]:
    """Ad-hoc `check-repo` results (analyze_scan_result.py's analysis.json).

    Kept separate from the Jira-driven ticket tables above since a check-repo
    run may have no corresponding Jira ticket at all (a pure "is this ref
    affected by anything right now" check) - only result.json/analysis.json
    exist for these, never a jira/cves-parsed.json entry. scan-local's plain
    result.json files (no analysis.json) are intentionally NOT picked up here
    to avoid double-reporting bulk-pipeline scans that are already covered by
    the ticket tables above.
    """
    results_dir = workdir / "scans" / "results"
    if not results_dir.is_dir():
        return []
    entries = []
    for analysis_path in sorted(results_dir.glob("*/analysis.json")):
        try:
            entries.append(json.loads(analysis_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return entries


def render_check_repo_row(entry: dict) -> str:
    verdict = entry.get("verdict", "unknown")
    label, css_class = VERDICT_LABELS.get(verdict, (verdict.upper(), "not-scanned"))
    repo = html.escape(entry.get("repo_slug") or entry.get("repo_url", ""))
    ref = html.escape(entry.get("git_ref", ""))
    cve_ids = entry.get("cve_ids") or []
    cve_text = html.escape(", ".join(cve_ids)) if cve_ids else "(any known vulnerability)"
    jira_url = entry.get("jira_url", "")
    ticket_keys = entry.get("ticket_keys") or []
    ticket_label = html.escape(", ".join(ticket_keys)) if ticket_keys else "Jira ticket"
    if jira_url:
        ticket_html = f'<a href="{html.escape(jira_url)}" target="_blank" rel="noopener">{ticket_label}</a>'
    elif ticket_keys:
        ticket_html = ticket_label
    else:
        ticket_html = "&mdash;"
    findings_html = render_findings(entry.get("matched_findings") or [])

    return (
        "<tr>"
        f'<td class="cve-cell"><code>{repo}@{ref}</code></td>'
        f"<td>{cve_text}</td>"
        f'<td><span class="badge badge-{css_class}">{label}</span></td>'
        f'<td>{findings_html or "&mdash;"}</td>'
        f"<td>{ticket_html}</td>"
        "</tr>"
    )


def render_check_repo_section(entries: list[dict]) -> str:
    if not entries:
        return ""
    rows_html = "".join(render_check_repo_row(entry) for entry in entries)
    return (
        "<h2>Ad-hoc repo checks</h2>"
        f'<p class="meta">{len(entries)} one-off <code>check-repo</code> run(s), outside the Jira-driven pipeline above.</p>'
        "<table><thead><tr><th>Repo @ ref</th><th>CVE(s) checked</th>"
        "<th>Verdict</th><th>Findings</th><th>Ticket</th></tr></thead>"
        f"<tbody>{rows_html}</tbody></table>"
    )


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Edge CVE Investigation Report</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
          margin: 0; padding: 2rem; background: #f5f6f8; color: #1a1a1a; }}
  h1 {{ margin-bottom: 0.2rem; }}
  .meta {{ color: #666; margin-bottom: 1.5rem; font-size: 0.9rem; }}
  .stats {{ display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem; }}
  .stat-card {{ background: #fff; border-radius: 8px; padding: 0.8rem 1.2rem; box-shadow: 0 1px 3px rgba(0,0,0,0.1);
                min-width: 120px; text-align: center; }}
  .stat-value {{ font-size: 1.6rem; font-weight: 700; }}
  .stat-label {{ font-size: 0.8rem; color: #666; }}
  .stat-affected .stat-value {{ color: #c0392b; }}
  .stat-not-affected .stat-value {{ color: #27ae60; }}
  .stat-inconclusive .stat-value {{ color: #d68910; }}
  .stat-private .stat-value {{ color: #7f8c8d; }}
  #filter {{ width: 100%; max-width: 420px; padding: 0.5rem 0.8rem; font-size: 1rem;
             border: 1px solid #ccc; border-radius: 6px; margin-bottom: 1.5rem; }}
  details {{ background: #fff; border-radius: 8px; margin-bottom: 0.6rem; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
  summary {{ padding: 0.8rem 1rem; cursor: pointer; font-weight: 600; list-style: none; }}
  summary::-webkit-details-marker {{ display: none; }}
  summary::before {{ content: "\\25B8"; display: inline-block; margin-right: 0.5rem; transition: transform 0.15s; }}
  details[open] summary::before {{ transform: rotate(90deg); }}
  .count {{ color: #888; font-weight: 400; font-size: 0.85rem; }}
  .component-body {{ padding: 0 1rem 1rem 1rem; }}
  h4 {{ margin: 1rem 0 0.4rem 0; color: #444; }}
  table {{ width: 100%; border-collapse: collapse; margin-bottom: 0.5rem; }}
  th, td {{ text-align: left; padding: 0.5rem 0.6rem; border-bottom: 1px solid #eee; vertical-align: top; font-size: 0.9rem; }}
  th {{ color: #888; font-weight: 600; font-size: 0.78rem; text-transform: uppercase; }}
  .ticket-key {{ color: #888; font-size: 0.78rem; margin-top: 0.2rem; }}
  .cve-cell {{ white-space: nowrap; }}
  .summary-cell {{ max-width: 480px; }}
  a {{ color: #2563eb; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .badge {{ display: inline-block; padding: 0.15rem 0.55rem; border-radius: 999px; font-size: 0.72rem;
            font-weight: 700; letter-spacing: 0.02em; white-space: nowrap; }}
  .badge-affected {{ background: #fdecea; color: #c0392b; }}
  .badge-not-affected {{ background: #eafaf1; color: #27ae60; }}
  .badge-inconclusive {{ background: #fef5e7; color: #d68910; }}
  .badge-not-scanned {{ background: #eee; color: #666; }}
  .scan-list {{ margin: 0; padding-left: 1.1rem; font-size: 0.82rem; }}
  .finding-list {{ margin: 0.15rem 0 0.15rem 1.1rem; padding-left: 1rem; font-size: 0.8rem; color: #a83232; }}
  .private-row td {{ color: #7f8c8d; font-style: italic; }}
  code {{ background: #f0f1f3; padding: 0.1rem 0.3rem; border-radius: 4px; font-size: 0.85em; }}
  .hidden {{ display: none !important; }}
  h2 {{ margin-top: 2rem; }}
  #check-repo-section table {{ background: #fff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
  #check-repo-section table th, #check-repo-section table td {{ padding: 0.6rem 0.8rem; }}
</style>
</head>
<body>
<h1>Edge CVE Investigation Report</h1>
<div class="meta">Generated {generated_at} &middot; Jira scope: {jql}</div>
<div class="meta">Components: {components_list} &middot; {dropped_note}</div>
<div class="stats">{stats_html}</div>
<input id="filter" type="text" placeholder="Filter by CVE, ticket key, or component...">
<div id="components">{components_html}</div>
<div id="check-repo-section">{check_repo_html}</div>
<script>
  const filterInput = document.getElementById('filter');
  filterInput.addEventListener('input', () => {{
    const q = filterInput.value.trim().toLowerCase();
    document.querySelectorAll('tbody tr').forEach((row) => {{
      const match = !q || row.textContent.toLowerCase().includes(q);
      row.classList.toggle('hidden', !match);
    }});
    document.querySelectorAll('#components > details').forEach((details) => {{
      const anyVisible = Array.from(details.querySelectorAll('tbody tr'))
        .some((row) => !row.classList.contains('hidden'));
      details.classList.toggle('hidden', q !== '' && !anyVisible);
      if (q && anyVisible) details.open = true;
    }});
  }});
</script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate an HTML CVE investigation report")
    parser.add_argument("--workdir", required=True)
    parser.add_argument(
        "--config",
        default="",
        help="Component mapping JSON (default: plugin config/component-repos.json)",
    )
    args = parser.parse_args()

    # Every asset this pipeline produces lives under --workdir (same convention
    # as fetch/parse/group/scan/finalize) - no --output override, so the HTML
    # report can never end up somewhere other than alongside the rest of the
    # run's data.
    workdir = Path(args.workdir)
    parsed_path = workdir / "jira" / "cves-parsed.json"
    scan_path = workdir / "scans" / "govulncheck-results.json"
    output_path = workdir / "report-cve-investigation.html"
    config_path = (
        Path(args.config) if args.config else SCRIPT_DIR.parent / "config" / "component-repos.json"
    )

    if not parsed_path.is_file():
        print(f"Error: required input missing: {parsed_path}", file=sys.stderr)
        sys.exit(1)

    parsed = load_json(parsed_path)
    scans = load_json(scan_path) if scan_path.is_file() else {"results": []}
    scan_index = build_scan_index(scans)
    known_components = load_known_components(config_path)
    if not known_components:
        print(
            f"Error: no components found in {config_path}; "
            "refusing to render all Jira components (edge-only scope requires a mapping)",
            file=sys.stderr,
        )
        sys.exit(1)

    grouped, dropped = group_tickets(parsed, scan_index, known_components)
    stats_html, counts = render_summary(grouped)

    components_html = "".join(
        render_component(
            component,
            versions,
            open_by_default=any(
                row["verdict"] == "affected" for rows in versions.values() for row in rows
            ),
        )
        for component, versions in sorted(grouped.items())
    )

    check_repo_results = load_check_repo_results(workdir)
    check_repo_html = render_check_repo_section(check_repo_results)

    jql = 'filter = "All Open CVEs" AND filter = "All Open Black CVEs"'
    components_list = ", ".join(sorted(known_components))
    dropped_note = (
        f"{dropped} ticket(s) for other components dropped" if dropped else "no other-component tickets found"
    )
    page = PAGE_TEMPLATE.format(
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        jql=html.escape(jql),
        components_list=html.escape(components_list),
        dropped_note=html.escape(dropped_note),
        stats_html=stats_html,
        components_html=components_html or "<p>No tickets found for the configured components.</p>",
        check_repo_html=check_repo_html,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(page, encoding="utf-8")

    counts["check_repo_checks"] = len(check_repo_results)
    counts["dropped_unmapped_components"] = dropped
    summary = {"output": str(output_path), "known_components": sorted(known_components), **counts}
    print(f"Written: {output_path}", file=sys.stderr)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
