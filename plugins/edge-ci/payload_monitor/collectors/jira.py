"""Search JIRA for existing bugs related to edge topology failures."""

import logging
import os
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests

from ..config import Config
from .http import create_session
from ..models import JiraBug, JobRun, SuggestedBug

logger = logging.getLogger(__name__)

JIRA_BASE = "https://redhat.atlassian.net"
SEARCH_URL = f"{JIRA_BASE}/rest/api/3/search"

_session = create_session()


def _get_headers() -> dict:
    """Get request headers with auth if available.

    Uses JIRA_TOKEN as a Bearer token (Atlassian Cloud PAT).
    """
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    token = os.environ.get("JIRA_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def has_auth() -> bool:
    return bool(os.environ.get("JIRA_TOKEN"))


def search_bugs(
    job_name: str,
    config: Config,
    topology: Optional[str] = None,
    max_results: int = 5,
) -> list[JiraBug]:
    """Search JIRA for bugs matching a failing job name."""
    if not has_auth():
        logger.debug("JIRA_TOKEN not set, skipping JIRA search")
        return []

    # Search by job name in summary or description
    # Escape special JQL characters
    escaped = job_name.replace('"', '\\"')
    component = config.jira_component_for(topology) if topology else ""
    component_clause = f'AND component = "{component}" ' if component else ""
    jql = (
        f'project = {config.jira_project} '
        f'{component_clause}'
        f'AND (summary ~ "{escaped}" OR description ~ "{escaped}") '
        f'AND status not in (Closed, "Release Pending") '
        f'ORDER BY updated DESC'
    )

    try:
        resp = _session.get(
            SEARCH_URL,
            params={"jql": jql, "maxResults": max_results, "fields": "summary,status,assignee,priority,components"},
            headers=_get_headers(),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.warning(f"JIRA search failed for {job_name}: {e}")
        return []

    bugs = []
    for issue in data.get("issues", []):
        fields = issue.get("fields", {})
        assignee = fields.get("assignee") or {}
        status = fields.get("status") or {}
        priority = fields.get("priority") or {}
        components = fields.get("components", [])

        bugs.append(JiraBug(
            key=issue["key"],
            summary=fields.get("summary", ""),
            status=status.get("name", ""),
            assignee=assignee.get("displayName", "Unassigned"),
            priority=priority.get("name", ""),
            url=f"{JIRA_BASE}/browse/{issue['key']}",
            component=components[0].get("name", "") if components else "",
        ))

    return bugs


def search_bugs_for_jobs(
    failing_jobs: list[JobRun],
    config: Config,
    max_workers: int = 4,
) -> dict[str, list[JiraBug]]:
    """Search JIRA for bugs matching each failing job.

    Returns a dict mapping job name -> list of matching bugs.
    """
    if not has_auth():
        logger.info("JIRA_TOKEN not set — JIRA bug matching disabled")
        return {}

    results = {}

    def _search(job: JobRun) -> tuple[str, list[JiraBug]]:
        return job.name, search_bugs(job.name, config, topology=job.topology)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_search, job) for job in failing_jobs]
        for future in as_completed(futures):
            name, bugs = future.result()
            if bugs:
                logger.info(f"  Found {len(bugs)} JIRA bugs for {name}")
                results[name] = bugs

    return results


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
    """Generate a suggested JIRA bug for an unmatched failing job."""
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
