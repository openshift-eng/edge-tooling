"""Analyze collected payload data to identify patterns and suggest actions."""

import logging
import re
from collections import defaultdict

from .config import Config
from .models import (
    EscalationRisk,
    JobResult,
    JobRun,
    JobType,
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


def _find_escalation_risks(
    streams: list[StreamReport],
    config: Config,
) -> list[EscalationRisk]:
    """Find informing jobs with consecutive recent failures (unstable jobs).

    Iterates payloads from newest to oldest per stream. Counts consecutive
    failures starting from the most recent payload. A job that is absent
    from a payload breaks the streak.
    """
    risks: list[EscalationRisk] = []
    for stream in streams:
        # Payloads are stored oldest-first; reverse to get newest-first
        reversed_payloads = list(reversed(stream.payloads))

        # Collect all unique informing job names seen in this stream
        informing_jobs: dict[str, str] = {}  # name -> topology
        for payload in stream.payloads:
            for job in payload.jobs:
                if job.job_type == JobType.INFORMING and job.topology:
                    informing_jobs[job.name] = job.topology

        for job_name, topology in informing_jobs.items():
            consecutive = 0
            for payload in reversed_payloads:
                job_in_payload = None
                for job in payload.jobs:
                    if job.name == job_name:
                        job_in_payload = job
                        break
                if job_in_payload is None:
                    # Job absent from this payload breaks the streak
                    break
                if job_in_payload.result == JobResult.FAILURE:
                    consecutive += 1
                else:
                    break

            if consecutive >= config.escalation_threshold:
                sippy_url = f"https://sippy.dptools.openshift.org/sippy-ng/jobs/{job_name}"
                risks.append(EscalationRisk(
                    job_name=job_name,
                    topology=topology,
                    version=stream.version,
                    consecutive_failures=consecutive,
                    sippy_url=sippy_url,
                ))

    return risks


def _normalize_job_name(name: str, config: Config) -> str:
    """Normalize a job name by replacing topology-specific patterns with a placeholder.

    Uses the topology patterns from config to identify and replace topology
    markers. This groups jobs that differ only in their topology segment.
    """
    result = name
    name_lower = name.lower()
    for topo in config.topologies:
        if any(p in name_lower for p in topo.exclude_patterns):
            continue
        for pattern in topo.job_patterns:
            replaced = re.sub(
                rf'(?:^|(?<=[-_])){re.escape(pattern)}(?=[-_]|$)',
                '__TOPO__',
                result,
                flags=re.IGNORECASE,
            )
            if replaced != result:
                return replaced
    return result


def _correlate_cross_topology(
    streams: list[StreamReport],
    config: Config,
) -> dict[str, list[str]]:
    """Find jobs that fail across multiple topologies within the same version.

    Returns dict mapping job_name -> list of other topologies with the same
    base failure. Only includes jobs where 2+ topologies share the failure.
    """
    cross: dict[str, list[str]] = {}

    for stream in streams:
        # Collect all unique failing edge jobs in this stream (version)
        failing_jobs: dict[str, str] = {}  # job_name -> topology
        for payload in stream.payloads:
            for job in payload.failing_edge_jobs:
                if job.name not in failing_jobs and job.topology:
                    failing_jobs[job.name] = job.topology

        # Group by normalized name
        groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for job_name, topology in failing_jobs.items():
            normalized = _normalize_job_name(job_name, config)
            groups[normalized].append((job_name, topology))

        # For groups with 2+ different topologies, map each job to others
        for normalized, members in groups.items():
            unique_topos = set(topo for _, topo in members)
            if len(unique_topos) < 2:
                continue
            for job_name, topology in members:
                other_topos = sorted(
                    t for t in unique_topos if t != topology
                )
                if other_topos:
                    cross[job_name] = other_topos

    return cross


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
    report.failure_counts = failure_counts
    recurring = {
        name: count for name, count in failure_counts.items()
        if count >= config.recurring_threshold
    }
    if recurring:
        logger.info("Recurring edge failures (appeared in >1 payload):")
        for name, count in sorted(recurring.items(), key=lambda x: -x[1]):
            logger.info(f"  {name}: {count} payloads")

    # Search JIRA for existing bugs
    unique_failing = {j.name: j for j in all_failing}
    jira_matches, jira_errors = jira_collector.search_bugs_for_jobs(
        list(unique_failing.values()), config
    )
    report.jira_errors = jira_errors

    # Store raw JIRA matches on the report for per-job inline display
    report.jira_matches = jira_matches

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

    try:
        report.escalation_risks = _find_escalation_risks(report.streams, config)
    except Exception as e:
        logger.error(f"Escalation risk analysis failed: {e}")
        report.escalation_risks = []
        report.data_errors.append(f"Escalation risk analysis: {e}")

    try:
        report.cross_topology = _correlate_cross_topology(report.streams, config)
    except Exception as e:
        logger.error(f"Cross-topology correlation failed: {e}")
        report.cross_topology = {}
        report.data_errors.append(f"Cross-topology correlation: {e}")
