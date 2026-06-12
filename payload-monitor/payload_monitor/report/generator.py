"""Generate HTML dashboard report from monitor data."""

from __future__ import annotations

from typing import Optional


import json
import logging
import re
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader

from ..models import (
    AttemptAnalysis,
    ComponentRegression,
    DeepAnalysis,
    EscalationRisk,
    FailingTest,
    JobResult,
    JobRun,
    JobType,
    JiraBug,
    MonitorReport,
    Payload,
    PayloadStatus,
    PreviousAttempt,
    Regression,
    StreamReport,
    SuggestedBug,
)
import dataclasses
from .timing_section import render_timing_section

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
_JINJA_ENV = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=True,
)

_TAG_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})-(\d{2})(\d{2})(\d{2})$")


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
    failure_details = []
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
                if job.failing_tests:
                    failure_details.append(item)

    # Sort: blocking first, then escalation-risk (unstable), then the rest
    escalation_risk_names = {er.job_name for er in report.escalation_risks}

    def _fail_sort_key(x):
        if x["job"].job_type == JobType.BLOCKING:
            return 0
        if x["job"].name in escalation_risk_names:
            return 1
        return 2

    all_failing.sort(key=_fail_sort_key)

    # Build blocking job summaries: versions, test names, topology per unique job
    blocking_job_versions: dict[str, list[str]] = {}
    blocking_job_tests: dict[str, list[str]] = {}
    for item in all_failing:
        if item["job"].job_type == JobType.BLOCKING:
            name = item["job"].name
            ver = item["version"]
            if name not in blocking_job_versions:
                blocking_job_versions[name] = []
                blocking_job_tests[name] = []
            if ver not in blocking_job_versions[name]:
                blocking_job_versions[name].append(ver)
            for ft in item["job"].failing_tests:
                if ft.name not in blocking_job_tests[name]:
                    blocking_job_tests[name].append(ft.name)

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

    # Collect all regressions
    all_regressions = []
    for stream in report.streams:
        for r in stream.regressions:
            if not r.version:
                r.version = stream.version
            all_regressions.append(r)

    # Unique topology names and versions for filters
    topologies = sorted(set(
        job["job"].topology for job in all_failing if job["job"].topology
    ))
    versions = sorted(set(stream.version for stream in report.streams))

    # Generate timing HTML if available
    timing_html = ""
    if report.timing_report and not report.skip_timing:
        timing_html = render_timing_section(report.timing_report)

    # Map blocking job name -> first index in all_failing for stable detail links
    blocking_job_first_idx = {}
    for idx, item in enumerate(all_failing, 1):
        if item["job"].job_type == JobType.BLOCKING:
            if item["job"].name not in blocking_job_first_idx:
                blocking_job_first_idx[item["job"].name] = idx

    return {
        "report": report,
        "all_failing": all_failing,
        "failure_details": failure_details,
        "all_regressions": sorted(
            all_regressions,
            key=lambda r: r.sample_pass_rate - r.basis_pass_rate,
        ),
        "component_regressions": sorted(
            report.component_regressions,
            key=lambda cr: cr.sample_pass_rate - cr.base_pass_rate,
        ),
        "cr_platforms": sorted(set(
            cr.variants.get("Platform", "") for cr in report.component_regressions
            if cr.variants.get("Platform")
        )),
        "cr_versions": sorted(set(
            cr.version for cr in report.component_regressions
        )),
        "cr_comparisons": sorted(set(
            cr.comparison for cr in report.component_regressions
            if cr.comparison
        )),
        "topologies": topologies,
        "versions": versions,
        "reg_topologies": sorted(set(
            r.topology for r in all_regressions if r.topology
        )),
        "reg_versions": sorted(set(
            r.version for r in all_regressions if r.version
        )),
        "timing_html": timing_html,
        "failure_counts": report.failure_counts,
        "persistent_count": sum(1 for c in report.failure_counts.values() if c >= report.persistent_threshold),
        "recurring_threshold": report.recurring_threshold,
        "persistent_threshold": report.persistent_threshold,
        "escalation_risks": report.escalation_risks,
        "escalation_risk_jobs": set(er.job_name for er in report.escalation_risks),
        "cross_topology": report.cross_topology,
        "jira_matches_by_job": report.jira_matches,
        "suggested_bugs_by_job": {b.job_name: b for b in report.suggested_bugs},
        "blocking_job_versions": blocking_job_versions,
        "blocking_job_tests": blocking_job_tests,
        "blocking_job_first_idx": blocking_job_first_idx,
        "retried_successes": retried_successes,
    }


def generate_html(report: MonitorReport, output_path: Optional[Path] = None) -> str:
    """Generate a self-contained HTML report.

    Returns the HTML content as a string. If output_path is provided,
    also writes to that file.
    """
    # Load CSS and JS to inline
    css = (TEMPLATES_DIR / "styles.css").read_text()
    js = (TEMPLATES_DIR / "scripts.js").read_text()

    template = _JINJA_ENV.get_template("dashboard.html")
    context = _build_template_context(report)
    context["css"] = css
    context["js"] = js

    rendered_html = template.render(**context)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered_html)
        logger.info(f"Report written to {output_path}")

    return rendered_html


def generate_json(report: MonitorReport, output_path: Path) -> None:
    """Export report data as JSON for external consumption."""
    data = {
        "generated_at": report.generated_at,
        "streams": [],
        "jira_bugs": [asdict(b) for b in report.jira_bugs],
        "suggested_bugs": [asdict(b) for b in report.suggested_bugs],
        "component_regressions": [asdict(cr) for cr in report.component_regressions],
        "failure_counts": report.failure_counts,
        "jira_matches": {
            name: [asdict(b) for b in bugs]
            for name, bugs in report.jira_matches.items()
        },
        "escalation_risks": [asdict(er) for er in report.escalation_risks],
        "cross_topology": report.cross_topology,
        "jira_errors": report.jira_errors,
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


def _render_analysis_card(da: dict, attempt_count: int = 1) -> str:
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
    )


def patch_analysis_html(html_path: Path, analysis_path: Path) -> None:
    """Patch AI analysis cards directly into an existing HTML report.

    Uses data attributes placed in the template for reliable injection:
    - ``data-prow-url`` on ``.claude-suggestion`` divs marks where cards go
    - ``data-attempt-count`` carries the retry count for scope notes
    - ``data-prow-url`` on ``<details>`` elements identifies where to add badges
    - ``data-enrichment-status="data-only"`` marks the header status div
    """
    with open(analysis_path) as f:
        data = json.load(f)

    by_url = data.get("by_prow_url", {})
    if not by_url:
        logger.info("No analysis entries to patch")
        return

    content = html_path.read_text()
    patched = 0
    badge_html = '<span class="badge ai-analyzed">AI Analyzed</span>'

    def _insert_badge(m):
        summary_content = m.group(2)
        if "ai-analyzed" in summary_content:
            return m.group(0)
        return f'{m.group(1)}\n  <summary>{summary_content} {badge_html}{m.group(3)}'

    for prow_url, da in by_url.items():
        escaped_url = re.escape(prow_url)

        # Find the claude-suggestion div with matching data-prow-url
        suggestion_pattern = (
            rf'<div class="claude-suggestion" data-prow-url="{escaped_url}"'
            r' data-attempt-count="(\d+)">'
            r'.*?</div>\s*</div>'
        )
        m = re.search(suggestion_pattern, content, flags=re.DOTALL)
        if not m:
            continue

        attempt_count = int(m.group(1))
        card_html = _render_analysis_card(da, attempt_count=attempt_count)

        content = content[:m.start()] + card_html + content[m.end():]
        patched += 1

        # Add "AI Analyzed" badge to the <details> element for this job
        details_pattern = (
            rf'(<details [^>]*data-prow-url="{escaped_url}"[^>]*>)\s*<summary>'
            r'(.*?)'
            r'(</summary>)'
        )
        content = re.sub(details_pattern, _insert_badge, content, count=1, flags=re.DOTALL)

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

    The analysis JSON is keyed by prow_url:
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
    with open(analysis_path) as f:
        data = json.load(f)

    by_url = data.get("by_prow_url", {})
    merged = 0
    for stream in report.streams:
        for payload in stream.payloads:
            for job in payload.jobs:
                if job.prow_url in by_url:
                    job.deep_analysis = _build_deep_analysis(by_url[job.prow_url])
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
    with open(json_path) as f:
        data = json.load(f)

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
            ))

        streams.append(StreamReport(
            stream=s.get("stream", ""),
            version=s.get("version", ""),
            payloads=payloads,
            regressions=regressions,
        ))

    jira_bugs = [
        _safe_dataclass_init(JiraBug, b) for b in data.get("jira_bugs", [])
    ]
    suggested_bugs = [
        _safe_dataclass_init(SuggestedBug, b) for b in data.get("suggested_bugs", [])
    ]
    comp_regressions = [
        _safe_dataclass_init(ComponentRegression, cr) for cr in data.get("component_regressions", [])
    ]

    # Deserialize jira_matches (job_name -> list[JiraBug])
    jira_matches = {}
    for name, bugs_raw in data.get("jira_matches", {}).items():
        jira_matches[name] = [_safe_dataclass_init(JiraBug, b) for b in bugs_raw]

    # Deserialize escalation_risks
    escalation_risks = [
        _safe_dataclass_init(EscalationRisk, er) for er in data.get("escalation_risks", [])
    ]

    return MonitorReport(
        generated_at=data.get("generated_at", ""),
        streams=streams,
        jira_bugs=jira_bugs,
        suggested_bugs=suggested_bugs,
        component_regressions=comp_regressions,
        failure_counts=data.get("failure_counts", {}),
        jira_matches=jira_matches,
        escalation_risks=escalation_risks,
        cross_topology=data.get("cross_topology", {}),
        jira_errors=data.get("jira_errors", []),
        recurring_threshold=data.get("recurring_threshold", 2),
        persistent_threshold=data.get("persistent_threshold", 3),
    )
