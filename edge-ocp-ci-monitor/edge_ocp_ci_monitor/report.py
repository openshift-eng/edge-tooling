"""Chai rendering and in-memory merging over the unchanged payload report model."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import asdict
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from payload_monitor.job_health import sippy_findings
from payload_monitor.models import MonitorReport
from payload_monitor.report import generator as payload_report

from .analysis import build_targets
from .analysis_cards import (
    _jira_links,
    _render_analysis_card,
    _safe_attempt_analyses,
    _safe_urls,
    patch_analysis_cards,
    remove_jira_draft_links,
)
from .jira import AGENT_ISSUE_LABEL, suggested_bugs
from .security import protect_logs, redact_data, redact_token

logger = logging.getLogger(__name__)

_JINJA_ENV = Environment(
    loader=FileSystemLoader([
        str(Path(__file__).parent / "templates"),
        str(payload_report.TEMPLATES_DIR),
    ]),
    autoescape=True,
)
_JINJA_ENV.filters["js_attr"] = payload_report._js_attr
_JINJA_ENV.filters["safe_attempts"] = _safe_attempt_analyses
_JINJA_ENV.filters["jira_links"] = _jira_links


@protect_logs(lambda *args, **kwargs: os.environ.get("CHAI_MCP_TOKEN", "").strip())
def generate_html(
    report: MonitorReport, output_path: Path | None = None, *, ai_warnings: list[str] | None = None,
) -> str:
    """Render Chai's template overrides with the existing dashboard data and assets."""
    report = redact_data(report, os.environ.get("CHAI_MCP_TOKEN", "").strip())
    context = payload_report._build_template_context(report)
    jira_drafts_by_target = {}
    for bug in report.suggested_bugs:
        if _safe_urls([bug.create_url]):
            for key in bug.analysis_keys or [f"prow:{bug.prow_url}"]:
                jira_drafts_by_target.setdefault(key, []).append({"title": bug.title, "url": bug.create_url})
    context["jira_drafts_by_target"] = jira_drafts_by_target
    context["sippy_analysis_cards"] = {
        finding.analysis_key: _render_analysis_card(
            asdict(finding.deep_analysis), job_name=finding.test_name,
            jira_drafts=jira_drafts_by_target.get(f"sippy:{finding.analysis_key}", []),
        )
        for finding in context["priority_sippy"] if finding.deep_analysis
    }
    templates = Path(__file__).parent / "templates"
    context.update(
        css="\n".join(path.read_text() for path in (
            payload_report.TEMPLATES_DIR / "styles.css", templates / "jira_drafts.css", templates / "dashboard.css",
        )),
        js="\n".join(path.read_text() for path in (
            payload_report.TEMPLATES_DIR / "scripts.js", templates / "jira_drafts.js", templates / "dashboard.js",
        )),
        ai_warnings=redact_data(ai_warnings or [], os.environ.get("CHAI_MCP_TOKEN", "").strip()),
        jira_draft_label=AGENT_ISSUE_LABEL,
    )
    rendered = _JINJA_ENV.get_template("dashboard.html").render(**context)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered)
        logger.info("Report written to %s", output_path)
    return rendered


@protect_logs(lambda *args, **kwargs: os.environ.get("CHAI_MCP_TOKEN", "").strip())
def generate_json(report: MonitorReport, output_path: Path) -> None:
    """Sanitize the report before the shared serializer can write any bytes."""
    payload_report.generate_json(redact_data(report, os.environ.get("CHAI_MCP_TOKEN", "").strip()), output_path)


def merge_analysis_data(report: MonitorReport, data: dict) -> None:
    """Merge validated in-memory results without creating an analysis sidecar."""
    by_url = data.get("by_prow_url", {})
    by_sippy_job = data.get("by_sippy_job", {})
    for stream in report.streams:
        for payload in stream.payloads:
            for job in payload.jobs:
                if job.prow_url in by_url:
                    job.deep_analysis = payload_report._build_deep_analysis(by_url[job.prow_url])
    for finding in sippy_findings(report):
        if finding.analysis_key in by_sippy_job:
            finding.deep_analysis = payload_report._build_deep_analysis(by_sippy_job[finding.analysis_key])
    # Rebuild from cumulative decisions, so a later batch finding an existing
    # bug can retract an earlier draft instead of leaving a stale suggestion.
    report.suggested_bugs = suggested_bugs(build_targets(report), data)


@protect_logs(lambda *args, **kwargs: os.environ.get("CHAI_MCP_TOKEN", "").strip())
def patch_analysis_html(html_path: Path, analysis_path: Path) -> None:
    """Insert or replace keyed analysis cards using the edge-owned renderer."""
    patch_analysis_cards(html_path, analysis_path)
    content = redact_token(html_path.read_text(), os.environ.get("CHAI_MCP_TOKEN", "").strip())
    content = content.replace(
        '<span style="color:var(--green)">● AI-enriched via Claude</span>',
        '<span style="color:var(--green)">● AI-enriched via Chai</span>',
    )
    # HTML-only imports have no fresh targets or validated Jira decisions.
    # Remove all draft actions, including cards for jobs absent from this import.
    content = remove_jira_draft_links(content)
    content = re.sub(r'<div class="inline-create-bug">.*?</div>', "", content, flags=re.DOTALL)
    start = content.find("<!-- Suggested Bugs -->")
    end = content.find('</div><!-- /tab-payload-health -->', start)
    if start >= 0 and end >= 0:
        content = content[:start] + content[end:]
    content = re.sub(
        r'<section id="section-suggested-bugs"[^>]*>.*?</section>', "", content, flags=re.DOTALL,
    )
    html_path.write_text(content)
