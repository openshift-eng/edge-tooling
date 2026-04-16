"""Analyze collected payload data to identify patterns and suggest actions."""

import logging
from collections import defaultdict

from .config import Config
from .models import (
    JobResult,
    JobRun,
    MonitorReport,
    StreamReport,
)
from .collectors import jira as jira_collector
from .collectors.jira import has_auth as jira_has_auth

logger = logging.getLogger(__name__)


def _find_recurring_failures(streams: list[StreamReport]) -> dict[str, int]:
    """Count how many payloads each edge job has failed in."""
    failure_counts: dict[str, int] = defaultdict(int)
    for stream in streams:
        for payload in stream.payloads:
            for job in payload.failing_edge_jobs:
                failure_counts[job.name] += 1
    return dict(failure_counts)


def _find_unmatched_jobs(
    streams: list[StreamReport],
    matched_jobs: set[str],
) -> list[tuple[JobRun, list[str]]]:
    """Find failing edge jobs without JIRA matches, grouped by job name.

    Returns list of (job, versions) tuples where versions contains all
    affected versions. This avoids suggesting duplicate bugs when the
    same job fails across multiple versions.
    """
    # Group by job name, collecting all affected versions
    by_name: dict[str, tuple[JobRun, list[str]]] = {}
    for stream in streams:
        for payload in stream.payloads:
            for job in payload.failing_edge_jobs:
                if job.name in matched_jobs:
                    continue
                if job.name not in by_name:
                    by_name[job.name] = (job, [])
                versions = by_name[job.name][1]
                if stream.version not in versions:
                    versions.append(stream.version)
    return list(by_name.values())


def analyze(
    report: MonitorReport,
    config: Config,
) -> None:
    """Analyze the report data and populate JIRA bugs and suggested bugs.

    Mutates the report in place.
    """
    report.skip_jira = not jira_has_auth()
    # Find all unique failing edge jobs across all streams
    all_failing: list[JobRun] = []
    for stream in report.streams:
        for payload in stream.payloads:
            all_failing.extend(payload.failing_edge_jobs)

    if not all_failing:
        logger.info("No failing edge jobs found across any payload")
        return

    # Count recurring failures
    failure_counts = _find_recurring_failures(report.streams)
    recurring = {
        name: count for name, count in failure_counts.items() if count > 1
    }
    if recurring:
        logger.info("Recurring edge failures (appeared in >1 payload):")
        for name, count in sorted(recurring.items(), key=lambda x: -x[1]):
            logger.info(f"  {name}: {count} payloads")

    # Search JIRA for existing bugs
    unique_failing = {j.name: j for j in all_failing}
    jira_matches = jira_collector.search_bugs_for_jobs(
        list(unique_failing.values()), config
    )

    # Flatten JIRA bugs into the report
    seen_keys = set()
    for bugs in jira_matches.values():
        for bug in bugs:
            if bug.key not in seen_keys:
                seen_keys.add(bug.key)
                report.jira_bugs.append(bug)

    # Suggest bugs for unmatched failures
    matched_job_names = set(jira_matches.keys())
    unmatched = _find_unmatched_jobs(report.streams, matched_job_names)

    for job, versions in unmatched:
        component = config.jira_component_for(job.topology) if job.topology else ""
        suggested = jira_collector.suggest_bug(job, versions, config, component=component)
        report.suggested_bugs.append(suggested)

    logger.info(
        f"Analysis complete: {len(report.jira_bugs)} existing JIRA bugs, "
        f"{len(report.suggested_bugs)} suggested new bugs"
    )
