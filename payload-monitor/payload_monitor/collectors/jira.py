"""Generate suggested JIRA bugs for edge topology failures."""

from __future__ import annotations

import urllib.parse

from ..config import Config
from ..models import JobRun, SuggestedBug

JIRA_BASE = "https://redhat.atlassian.net"


def create_bug_url(
    title: str,
    description: str,
    config: Config,
    component: str = "",
) -> str:
    """Generate a JIRA create-issue URL with pre-populated fields."""
    params = {
        "project.key": config.jira_project,
        "issuetype.name": "Bug",
        "summary": title,
        "description": description,
    }
    if component:
        params["component"] = component
    return f"{JIRA_BASE}/secure/CreateIssue!default.jspa?{urllib.parse.urlencode(params)}"


def suggest_bug(
    job: JobRun,
    versions: list[str],
    config: Config,
    component: str = "",
) -> SuggestedBug:
    """Generate a suggested JIRA bug for a failing job."""
    failing_test_names = [t.name for t in job.failing_tests[:5]]

    versions_str = ", ".join(versions)
    title = f"[{job.topology}] {job.name} failing in {versions_str} nightly"

    # Short description for URL (avoids browser URL length limits)
    short_lines = [
        f"*Job*: [{job.name}|{job.prow_url}]",
        f"*Topology*: {job.topology}",
        f"*Versions*: {versions_str}",
        f"*Job Type*: {job.job_type.value}",
    ]
    short_description = "\n".join(short_lines)

    # Full description for clipboard copy
    full_lines = list(short_lines)
    full_lines.append("")
    full_lines.append("*Failing Tests*:")
    for t in job.failing_tests[:5]:
        full_lines.append(f"- {t.name}")
        if t.error_message:
            err = t.error_message[:200].replace("\n", " ")
            full_lines.append(f"  {{noformat}}{err}{{noformat}}")

    if job.error_summary:
        full_lines.extend(["", f"*Error Summary*: {job.error_summary}"])

    full_description = "\n".join(full_lines)

    return SuggestedBug(
        title=title,
        description=short_description,
        job_name=job.name,
        topology=job.topology or "",
        versions=versions,
        failing_tests=failing_test_names,
        create_url=create_bug_url(title, short_description, config, component=component),
        prow_url=job.prow_url,
        full_description=full_description,
    )
