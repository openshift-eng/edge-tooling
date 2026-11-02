"""Generate HTML dashboard report from monitor data."""

from __future__ import annotations

from typing import Optional


import html as html_mod
import json
import logging
import re
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader
from markupsafe import escape

from ..job_health import priority_sippy_findings, sippy_findings
from ..models import (
    AttemptAnalysis,
    ComponentRegression,
    DeepAnalysis,
    EscalationRisk,
    FailingTest,
    JobResult,
    JobRun,
    JobType,
    MonitorReport,
    Payload,
    PayloadStatus,
    PreviousAttempt,
    Regression,
    StreamReport,
    SuggestedBug,
    sort_versions,
)
import dataclasses
from .timing_section import render_timing_section

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
def _js_attr(value):
    """Escape a value for use inside an HTML-attribute JavaScript context.

    Produces a JSON-quoted string (JS-safe) with double quotes HTML-escaped
    to &quot; so it nests safely inside onclick="..." attributes.
    """
    return escape(json.dumps(str(value)))

_JINJA_ENV = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=True,
)
_JINJA_ENV.filters["js_attr"] = _js_attr

_TAG_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})-(\d{2})(\d{2})(\d{2})$")


def _load_json(path: Path, description: str) -> dict:
    """Load JSON from *path*, raising a clear, path-aware error on failure."""
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        raise FileNotFoundError(f"{description} not found: {path}") from None
    except json.JSONDecodeError as e:
        raise ValueError(f"{description} is not valid JSON ({path}): {e}") from e


def _extract_date(tag: str) -> str:
    """Extract date and time from a payload tag like '4.19.0-0.nightly-2026-03-25-085944'."""
    m = _TAG_DATE_RE.search(tag)
    if not m:
        return ""
    return f"{m.group(1)} {m.group(2)}:{m.group(3)}:{m.group(4)}"


def _build_template_context(report: MonitorReport) -> dict:
    """Build the Jinja2 template context from a MonitorReport."""
    # Collect all failing edge jobs with their context
    all_failing = []
    for stream in report.streams:
        for payload in stream.payloads:
            for job in payload.failing_edge_jobs:
                item = {
                    "job": job,
                    "version": stream.version,
                    "payload_tag": payload.tag,
                    "payload_url": payload.url,
                    "date": _extract_date(payload.tag),
                }
                all_failing.append(item)

    # Sort: blocking first, then escalation-risk (unstable), then the rest
    escalation_risk_names = {er.job_name for er in report.escalation_risks}

    def _fail_sort_key(x):
        if x["job"].job_type == JobType.BLOCKING:
            return 0
        if x["job"].name in escalation_risk_names:
            return 1
        return 2

    all_failing.sort(key=_fail_sort_key)

    # Build blocking job summaries and findings-summary counts in one pass
    blocking_job_versions: dict[str, list[str]] = {}
    blocking_job_tests: dict[str, list[str]] = {}
    blocking_failures = 0
    informing_failures = 0
    blocking_jobs_seen: set[str] = set()
    blocking_jobs_list: list[dict] = []
    for item in all_failing:
        if item["job"].job_type == JobType.BLOCKING:
            blocking_failures += 1
            name = item["job"].name
            ver = item["version"]
            if name not in blocking_jobs_seen:
                blocking_jobs_seen.add(name)
                blocking_jobs_list.append(item)
            if name not in blocking_job_versions:
                blocking_job_versions[name] = []
                blocking_job_tests[name] = []
            if ver not in blocking_job_versions[name]:
                blocking_job_versions[name].append(ver)
            for ft in item["job"].failing_tests:
                if ft.name not in blocking_job_tests[name]:
                    blocking_job_tests[name].append(ft.name)
        elif item["job"].job_type == JobType.INFORMING:
            informing_failures += 1

    total_payloads = sum(len(stream.payloads) for stream in report.streams)
    rejected_payloads = sum(
        1 for stream in report.streams for p in stream.payloads
        if p.status == PayloadStatus.REJECTED
    )

    # Collect blocking jobs that succeeded only after retry (flaky passes)
    retried_successes = []
    retried_seen = set()
    for stream in report.streams:
        for payload in stream.payloads:
            for job in payload.jobs:
                if (job.job_type == JobType.BLOCKING
                        and job.result == JobResult.SUCCESS
                        and job.retries > 0
                        and job.name not in retried_seen):
                    retried_seen.add(job.name)
                    retried_successes.append({
                        "job": job,
                        "version": stream.version,
                        "payload_tag": payload.tag,
                        "payload_url": payload.url,
                    })

    all_regressions = sippy_findings(report)
    priority_sippy = priority_sippy_findings(all_regressions)
    all_regressions.sort(
        key=lambda r: r.relative_change if r.relative_change is not None else float("inf"),
    )
    priority_keys = {r.analysis_key for r in priority_sippy[:5]}

    # Per-stream views: affected topologies + trend
    stream_views = []
    for stream in report.streams:
        affected = sorted({
            j.topology for p in stream.payloads for j in p.failing_edge_jobs if j.topology
        })

        # Trend is a per-payload failure rate (not a raw count) so an uneven
        # recent/older split (e.g. 2 vs 3 payloads) can't tip the label on
        # its own. Blocking failures are weighted 3x informing ones since
        # they're what actually reject a payload. trend_signal gates whether
        # an arrow is shown at all, so a single low-severity blip renders as
        # stable rather than a false worsening/improving signal.
        mid = max(len(stream.payloads) // 2, 1)
        recent, older = stream.payloads[:mid], stream.payloads[mid:]

        def _weighted(ps):
            return sum(3 * len(p.blocking_edge_failures) + len(p.informing_edge_failures) for p in ps)

        recent_fails = _weighted(recent) / len(recent) if recent else 0.0
        older_fails = _weighted(older) / len(older) if older else 0.0
        trend_signal = _weighted(stream.payloads)

        stream_views.append({
            "stream": stream,
            "affected_topologies": affected,
            "recent_fails": recent_fails,
            "older_fails": older_fails,
            "trend_signal": trend_signal,
        })

    # Unique topology names and versions for filters
    topologies = sorted(set(
        job["job"].topology for job in all_failing if job["job"].topology
    ))
    affected_topologies = sorted(set(topologies) | {
        r.topology for r in all_regressions if r.topology
    })
    versions = sort_versions(set(stream.version for stream in report.streams))

    # Generate timing HTML if available
    timing_html = ""
    if report.timing_report and not report.skip_timing:
        timing_html = render_timing_section(report.timing_report)
    timing_unavailable = not report.skip_timing and not timing_html
    timing_errors = [e for e in report.data_errors if e.startswith("Timing:")] if timing_unavailable else []

    return {
        "report": report,
        "all_failing": all_failing,
        "priority_sippy": priority_sippy[:5],
        "priority_sippy_count": len(priority_sippy),
        "sippy_analysis_ids": {
            r.analysis_key: f"sippy-row-{index}"
            for index, r in enumerate(all_regressions, 1) if r.analysis_key in priority_keys
        },
        "sippy_analysis_cards": {
            r.analysis_key: _render_analysis_card(asdict(r.deep_analysis), job_name=r.test_name)
            for r in priority_sippy[:5] if r.deep_analysis
        },
        "blocking_job_pairs": {(item["version"], item["job"].name) for item in all_failing
                               if item["job"].job_type == JobType.BLOCKING},
        "all_regressions": all_regressions,
        "component_regressions": sorted(
            report.component_regressions,
            key=lambda cr: cr.sample_pass_rate - cr.base_pass_rate,
        ),
        "cr_platforms": sorted(set(
            cr.variants.get("Platform", "") for cr in report.component_regressions
            if cr.variants.get("Platform")
        )),
        "cr_versions": sort_versions(set(
            cr.version for cr in report.component_regressions
        )),
        "cr_comparisons": sorted(set(
            cr.comparison for cr in report.component_regressions
            if cr.comparison
        )),
        "topologies": topologies,
        "affected_topologies": affected_topologies,
        "versions": versions,
        "reg_topologies": sorted(set(
            r.topology for r in all_regressions if r.topology
        )),
        "reg_versions": sort_versions(set(
            r.version for r in all_regressions if r.version
        )),
        "timing_html": timing_html,
        "timing_unavailable": timing_unavailable,
        "timing_errors": timing_errors,
        "failure_counts": report.failure_counts,
        "recurring_threshold": report.recurring_threshold,
        "persistent_threshold": report.persistent_threshold,
        "escalation_risks": report.escalation_risks,
        "escalation_risk_jobs": set(er.job_name for er in report.escalation_risks),
        "escalation_risk_by_job": {er.job_name: er for er in report.escalation_risks},
        "cross_topology": report.cross_topology,
        "suggested_bugs_by_job": {b.job_name: b for b in report.suggested_bugs},
        "blocking_job_versions": blocking_job_versions,
        "blocking_job_tests": blocking_job_tests,
        "retried_successes": retried_successes,
        "stream_views": stream_views,
        "blocking_failures": blocking_failures,
        "informing_failures": informing_failures,
        "blocking_jobs_list": blocking_jobs_list,
        "total_payloads": total_payloads,
        "rejected_payloads": rejected_payloads,
    }


def generate_html(report: MonitorReport, output_path: Optional[Path] = None) -> str:
    """Generate a self-contained HTML report.

    Returns the HTML content as a string. If output_path is provided,
    also writes to that file.
    """
    # Load CSS and JS to inline
    try:
        css = (TEMPLATES_DIR / "styles.css").read_text()
        js = (TEMPLATES_DIR / "scripts.js").read_text()
    except FileNotFoundError as e:
        raise FileNotFoundError(
            f"Report template asset missing ({e.filename}) - "
            "reinstall payload_monitor or check TEMPLATES_DIR"
        ) from e

    template = _JINJA_ENV.get_template("dashboard.html")
    context = _build_template_context(report)
    context["css"] = css
    context["js"] = js

    rendered_html = template.render(**context)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered_html)
        _write_ai_targets(report, context["priority_sippy"], output_path)
        logger.info(f"Report written to {output_path}")

    return rendered_html


def _write_ai_targets(report: MonitorReport, findings: list[Regression], html_path: Path) -> None:
    """Export exactly the visible Sippy findings, with known runs for evidence reuse."""
    targets = []
    for r in findings:
        runs = {}
        for stream in report.streams:
            if stream.version != r.version:
                continue
            for payload in stream.payloads:
                for job in payload.failing_edge_jobs:
                    if job.name == r.test_name and job.prow_url:
                        runs[job.prow_url] = {
                            "prow_url": job.prow_url,
                            "job_type": job.job_type.value,
                            "previous_attempt_urls": [p.prow_url for p in job.previous_attempts],
                        }
        targets.append({
            "analysis_key": r.analysis_key,
            "job_name": r.test_name,
            "version": r.version,
            "topology": r.topology,
            "triage_url": r.triage_url,
            "current_pass_rate": r.sample_pass_rate,
            "previous_pass_rate": r.basis_pass_rate,
            "relative_change": r.relative_change,
            "current_runs": r.current_runs,
            "known_failing_runs": list(runs.values()),
        })
    html_path.with_suffix(".ai-targets.json").write_text(json.dumps({"sippy_jobs": targets}, indent=2))


def generate_json(report: MonitorReport, output_path: Path) -> None:
    """Export report data as JSON for external consumption."""
    data = {
        "generated_at": report.generated_at,
        "streams": [],
        "suggested_bugs": [asdict(b) for b in report.suggested_bugs],
        "component_regressions": [asdict(cr) for cr in report.component_regressions],
        "failure_counts": report.failure_counts,
        "escalation_risks": [asdict(er) for er in report.escalation_risks],
        "cross_topology": report.cross_topology,
        "recurring_threshold": report.recurring_threshold,
        "persistent_threshold": report.persistent_threshold,
    }

    for stream in report.streams:
        stream_data = {
            "stream": stream.stream,
            "version": stream.version,
            "total_edge_failures": stream.total_edge_failures,
            "payloads": [],
            "regressions": [asdict(r) for r in stream.regressions],
        }
        for payload in stream.payloads:
            payload_data = {
                "tag": payload.tag,
                "status": payload.status.value,
                "url": payload.url,
                "edge_jobs": [],
            }
            for j in payload.jobs:
                payload_data["edge_jobs"].append({
                    "name": j.name,
                    "result": j.result.value,
                    "job_type": j.job_type.value,
                    "topology": j.topology,
                    "prow_url": j.prow_url,
                    "error_summary": j.error_summary,
                    "failing_tests": [
                        {"name": t.name, "error": t.error_message}
                        for t in j.failing_tests
                    ],
                    "deep_analysis": asdict(j.deep_analysis) if j.deep_analysis else None,
                    "retries": j.retries,
                    "previous_attempts": [
                        {
                            "prow_url": pa.prow_url,
                            "result": pa.result.value,
                            "failing_tests": [
                                {"name": t.name, "error": t.error_message}
                                for t in pa.failing_tests
                            ],
                            "error_summary": pa.error_summary,
                        }
                        for pa in j.previous_attempts
                    ],
                })
            stream_data["payloads"].append(payload_data)
        data["streams"].append(stream_data)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2))
    logger.info(f"JSON data written to {output_path}")



def _safe_urls(urls: list[str]) -> list[str]:
    """Filter URLs to only allow the https scheme."""
    safe = []
    for url in urls:
        parsed = urlparse(url)
        if parsed.scheme == "https" and parsed.netloc:
            safe.append(url)
    return safe


def _render_analysis_card(da: dict, attempt_count: int = 1, job_name: str = "") -> str:
    """Render an AI analysis card as an HTML string."""
    template = _JINJA_ENV.get_template("_analysis_card.html")
    return template.render(
        root_cause=da.get("root_cause", ""),
        failure_type=da.get("failure_type", ""),
        impact=da.get("impact", ""),
        suspect_prs=_safe_urls(da.get("suspect_prs", [])),
        recommendation=da.get("recommendation", ""),
        same_root_cause=da.get("same_root_cause", True),
        attempt_analyses=da.get("attempt_analyses", []),
        attempt_count=attempt_count,
        job_name=job_name,
    )


def patch_analysis_html(html_path: Path, analysis_path: Path) -> None:
    """Patch AI analysis cards directly into an existing HTML report.

    Uses data attributes placed in the template for reliable injection:
    - ``data-prow-url`` on ``.claude-suggestion`` divs marks where cards go
    - ``data-attempt-count`` carries the retry count for scope notes
    - ``data-prow-url`` on ``<details>`` elements identifies where to add badges
    - ``data-sippy-key`` on ``.sippy-analysis`` divs identifies job/release findings
    - ``data-enrichment-status="data-only"`` marks the header status div
    """
    data = _load_json(analysis_path, "Analysis JSON")

    by_url = data.get("by_prow_url", {})
    by_sippy_job = data.get("by_sippy_job", {})
    if not by_url and not by_sippy_job:
        logger.info("No analysis entries to patch")
        return

    content = html_path.read_text()
    patched = 0
    badge_html = '<span class="badge ai-analyzed">AI Analyzed</span>'

    _suggestion_re = re.compile(
        r'<div class="claude-suggestion" data-prow-url="([^"]*)"'
        r' data-attempt-count="(\d+)">'
        r'.*?<!-- /claude-suggestion -->',
        re.DOTALL,
    )
    _details_re = re.compile(
        r'(<details [^>]*data-(?:prow-url|sippy-key)="([^"]*)"[^>]*>)\s*<summary>'
        r'(.*?)'
        r'(</summary>)',
        re.DOTALL,
    )

    patched_urls: set[str] = set()
    patched_sippy_keys: set[str] = set()

    def _replace_suggestion(m: re.Match) -> str:
        nonlocal patched
        prow_url = html_mod.unescape(m.group(1))
        da = by_url.get(prow_url)
        if da is None:
            return m.group(0)
        attempt_count = int(m.group(2))
        patched += 1
        patched_urls.add(prow_url)
        return _render_analysis_card(da, attempt_count=attempt_count, job_name=da.get("job_name", ""))

    content = _suggestion_re.sub(_replace_suggestion, content)

    def _replace_sippy(m: re.Match) -> str:
        nonlocal patched
        key = html_mod.unescape(m.group(2))
        da = by_sippy_job.get(key)
        if da is None:
            return m.group(0)
        patched += 1
        patched_sippy_keys.add(key)
        card = _render_analysis_card(da, job_name=da.get("job_name", ""))
        return f'{m.group(1)}{card}</div><!-- /sippy-analysis -->'

    content = re.sub(
        r'(<div class="sippy-analysis" data-sippy-key="([^"]*)">)'
        r'.*?</div><!-- /sippy-analysis -->',
        _replace_sippy, content, flags=re.DOTALL,
    )
    def _enable_sippy_row(m: re.Match) -> str:
        if html_mod.unescape(m.group(2)) not in patched_sippy_keys:
            return m.group(0)
        row = m.group(0)
        row = row.replace('<div ', '<details ', 1)
        row = row.replace('<div class="sippy-job-summary">', '<summary>', 1)
        row = row.replace('</div><!-- /sippy-job-summary -->', '</summary><!-- /sippy-job-summary -->', 1)
        row = row.replace('<div class="content sippy-job-analysis" hidden>', '<div class="content sippy-job-analysis">', 1)
        return row.removesuffix('</div><!-- /sippy-job -->') + '</details><!-- /sippy-job -->'

    content = re.sub(
        r'(<div id="sippy-row-\d+"[^>]*data-sippy-key="([^"]*)"[^>]*>)'
        r'.*?</div><!-- /sippy-job -->',
        _enable_sippy_row, content, flags=re.DOTALL,
    )

    def _insert_badge(m: re.Match) -> str:
        if html_mod.unescape(m.group(2)) not in patched_urls | patched_sippy_keys:
            return m.group(0)

        def _mark_analyzed(status: re.Match) -> str:
            values = status.group(1).split()
            if "ai-analyzed" not in values:
                values.append("ai-analyzed")
            return f'data-status="{" ".join(values)}"'

        details_tag = re.sub(r'data-status="([^"]*)"', _mark_analyzed, m.group(1))
        details_tag = re.sub(r' hidden>$', '>', details_tag)
        summary_content = m.group(3)
        if "ai-analyzed" in summary_content:
            return f'{details_tag}\n  <summary>{summary_content}{m.group(4)}'
        sippy_name_end = '</span><!-- /sippy-job-name -->'
        if sippy_name_end in summary_content:
            summary_content = summary_content.replace(sippy_name_end, sippy_name_end + badge_html, 1)
            return f'{details_tag}\n  <summary>{summary_content}{m.group(4)}'
        # Grid layout: insert badge inside the job cell before its closing </span>
        job_anchor = 'class="detail-cell-job"'
        job_pos = summary_content.find(job_anchor)
        if job_pos >= 0:
            open_gt = summary_content.find('>', job_pos)
            if open_gt >= 0:
                close_pos = summary_content.find('</span>', open_gt)
                if close_pos >= 0:
                    summary_content = (
                        summary_content[:close_pos]
                        + badge_html
                        + summary_content[close_pos:]
                    )
                    return f'{details_tag}\n  <summary>{summary_content}{m.group(4)}'
        # Legacy flat layout fallback
        for anchor in ('class="detail-date"', 'class="detail-version"'):
            anchor_pos = summary_content.find(anchor)
            if anchor_pos < 0:
                continue
            span_start = summary_content.rfind('<span', 0, anchor_pos)
            if span_start < 0:
                continue
            last_close = summary_content.rfind('>', 0, span_start)
            if last_close >= 0:
                ins = last_close + 1
                while ins < span_start and summary_content[ins] in ' \t\n':
                    ins += 1
                summary_content = (
                    summary_content[:ins]
                    + badge_html + '\n    '
                    + summary_content[ins:]
                )
            else:
                summary_content = badge_html + ' ' + summary_content
            break
        else:
            summary_content = summary_content + ' ' + badge_html
        return f'{details_tag}\n  <summary>{summary_content}{m.group(4)}'

    content = _details_re.sub(_insert_badge, content)

    # Update header: replace "Data only" status using data attribute
    if patched > 0:
        new_status = (
            '<div class="meta" style="margin-top:4px">'
            '<span style="color:var(--green)">● AI-enriched via Claude</span>'
            '</div>'
        )
        content = re.sub(
            r'<div class="meta"[^>]*data-enrichment-status="data-only"[^>]*>.*?</div>',
            new_status,
            content,
            count=1,
            flags=re.DOTALL,
        )

    html_path.write_text(content)
    logger.info(f"Patched {patched} analysis card(s) into {html_path}")


def merge_analysis(report: MonitorReport, analysis_path: Path) -> None:
    """Merge a small analysis-only JSON into a MonitorReport.

    Blocking results use ``by_prow_url``. Sippy job-health results use
    ``by_sippy_job`` keyed by Regression.analysis_key, with the same card fields:
    {
      "by_prow_url": {
        "https://prow.ci.../123": {
          "root_cause": "...",
          "failure_type": "...",
          "impact": "...",
          "suspect_prs": [],
          "recommendation": "..."
        }
      }
    }
    """
    data = _load_json(analysis_path, "Analysis JSON")

    by_url = data.get("by_prow_url", {})
    by_sippy_job = data.get("by_sippy_job", {})
    merged = 0
    for stream in report.streams:
        for payload in stream.payloads:
            for job in payload.jobs:
                if job.prow_url in by_url:
                    job.deep_analysis = _build_deep_analysis(by_url[job.prow_url])
                    merged += 1

    for regression in sippy_findings(report):
        if regression.analysis_key in by_sippy_job:
            regression.deep_analysis = _build_deep_analysis(by_sippy_job[regression.analysis_key])
            merged += 1

    logger.info(f"Merged deep analysis for {merged} job(s)")


def _build_deep_analysis(da_raw: dict) -> DeepAnalysis:
    """Build a DeepAnalysis from a raw dict, sanitizing URLs."""
    return DeepAnalysis(
        root_cause=da_raw.get("root_cause", ""),
        failure_type=da_raw.get("failure_type", ""),
        impact=da_raw.get("impact", ""),
        suspect_prs=_safe_urls(da_raw.get("suspect_prs", [])),
        recommendation=da_raw.get("recommendation", ""),
        same_root_cause=da_raw.get("same_root_cause", True),
        attempt_analyses=[
            AttemptAnalysis(
                prow_url=aa["prow_url"],
                root_cause=aa.get("root_cause", ""),
                failure_type=aa.get("failure_type", ""),
            )
            for aa in da_raw.get("attempt_analyses", [])
            if aa.get("prow_url")
        ],
    )


def _safe_dataclass_init(cls, data: dict):
    """Create a dataclass instance, ignoring unknown keys for forward compatibility."""
    field_names = {f.name for f in dataclasses.fields(cls)}
    filtered = {k: v for k, v in data.items() if k in field_names}
    return cls(**filtered)


def load_json(json_path: Path) -> MonitorReport:
    """Load a MonitorReport from a JSON file (potentially enriched with deep analysis)."""
    data = _load_json(json_path, "Report JSON")

    streams = []
    for s in data.get("streams", []):
        payloads = []
        for p in s.get("payloads", []):
            jobs = []
            for j in p.get("edge_jobs", []):
                da_raw = j.get("deep_analysis")
                deep_analysis = None
                if da_raw:
                    deep_analysis = _build_deep_analysis(da_raw)
                failing_tests = [
                    FailingTest(name=t.get("name", ""), error_message=t.get("error", ""))
                    for t in j.get("failing_tests", [])
                ]
                prev_attempts = [
                    PreviousAttempt(
                        prow_url=pa.get("prow_url", ""),
                        result=JobResult(pa.get("result", "F")),
                        failing_tests=[
                            FailingTest(name=t.get("name", ""), error_message=t.get("error", ""))
                            for t in pa.get("failing_tests", [])
                        ],
                        error_summary=pa.get("error_summary", ""),
                    )
                    for pa in j.get("previous_attempts", [])
                ]
                jobs.append(JobRun(
                    name=j.get("name", ""),
                    prow_url=j.get("prow_url", ""),
                    result=JobResult(j.get("result", "U")),
                    job_type=JobType(j.get("job_type", "informing")),
                    topology=j.get("topology"),
                    failing_tests=failing_tests,
                    error_summary=j.get("error_summary", ""),
                    deep_analysis=deep_analysis,
                    retries=j.get("retries", 0),
                    previous_attempts=prev_attempts,
                ))
            payloads.append(Payload(
                tag=p.get("tag", ""),
                stream=s.get("stream", ""),
                version=s.get("version", ""),
                status=PayloadStatus(p.get("status", "Pending")),
                url=p.get("url", ""),
                jobs=jobs,
            ))

        regressions = []
        for r in s.get("regressions", []):
            regressions.append(Regression(
                test_name=r.get("test_name", ""),
                test_id=r.get("test_id", ""),
                component=r.get("component", ""),
                capability=r.get("capability", ""),
                basis_pass_rate=r.get("basis_pass_rate", 0),
                sample_pass_rate=r.get("sample_pass_rate", 0),
                version=r.get("version", s.get("version", "")),
                topology=r.get("topology", ""),
                triage_url=r.get("triage_url", ""),
                jira_bug=r.get("jira_bug", ""),
                current_runs=r.get("current_runs", 0),
                deep_analysis=_build_deep_analysis(r["deep_analysis"]) if r.get("deep_analysis") else None,
            ))

        streams.append(StreamReport(
            stream=s.get("stream", ""),
            version=s.get("version", ""),
            payloads=payloads,
            regressions=regressions,
        ))

    suggested_bugs = [
        _safe_dataclass_init(SuggestedBug, b) for b in data.get("suggested_bugs", [])
    ]
    comp_regressions = [
        _safe_dataclass_init(ComponentRegression, cr) for cr in data.get("component_regressions", [])
    ]

    # Deserialize escalation_risks
    escalation_risks = [
        _safe_dataclass_init(EscalationRisk, er) for er in data.get("escalation_risks", [])
    ]

    return MonitorReport(
        generated_at=data.get("generated_at", ""),
        streams=streams,
        suggested_bugs=suggested_bugs,
        component_regressions=comp_regressions,
        failure_counts=data.get("failure_counts", {}),
        escalation_risks=escalation_risks,
        cross_topology=data.get("cross_topology", {}),
        recurring_threshold=data.get("recurring_threshold", 2),
        persistent_threshold=data.get("persistent_threshold", 3),
    )
