"""Analyze collected payload data to identify patterns and suggest actions."""

from __future__ import annotations

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
from .collectors.sippy import job_analysis_url

logger = logging.getLogger(__name__)


def _find_recurring_failures(streams: list[StreamReport]) -> dict[str, int]:
    """Count how many payloads each edge job has failed in."""
    failure_counts: dict[str, int] = defaultdict(int)
    for stream in streams:
        for payload in stream.payloads:
            for job in payload.failing_edge_jobs:
                failure_counts[job.name] += 1
    return dict(failure_counts)


def _find_unique_failing_jobs(
    streams: list[StreamReport],
) -> list[tuple[JobRun, list[str]]]:
    """Find failing edge jobs grouped by job name, for bug suggestions.

    Returns list of (job, versions) tuples where versions contains all
    affected versions. This avoids suggesting duplicate bugs when the
    same job fails across multiple versions.
    """
    # Group by job name, collecting all affected versions
    by_name: dict[str, tuple[JobRun, list[str]]] = {}
    for stream in streams:
        for payload in stream.payloads:
            for job in payload.failing_edge_jobs:
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
        # Payloads are stored newest-first (collectors.release_controller
        # sorts them that way, matching StreamReport.latest_payload), so no
        # reversal is needed to walk from most-recent to oldest.
        newest_first_payloads = stream.payloads

        # Collect all unique informing job names seen in this stream
        informing_jobs: dict[str, str] = {}  # name -> topology
        for payload in stream.payloads:
            for job in payload.jobs:
                if job.job_type == JobType.INFORMING and job.topology:
                    informing_jobs[job.name] = job.topology

        for job_name, topology in informing_jobs.items():
            consecutive = 0
            latest_prow_url = ""
            latest_failure_seen = False
            streak_runs: list[dict] = []
            for payload in newest_first_payloads:
                job_in_payload = None
                for job in payload.jobs:
                    if job.name == job_name:
                        job_in_payload = job
                        break
                if job_in_payload is None:
                    break
                if job_in_payload.result == JobResult.FAILURE:
                    consecutive += 1
                    streak_runs.append({
                        "payload_tag": payload.tag,
                        "prow_url": job_in_payload.prow_url,
                    })
                    if not latest_failure_seen:
                        latest_prow_url = job_in_payload.prow_url
                        latest_failure_seen = True
                else:
                    break

            if consecutive >= config.escalation_threshold:
                risks.append(EscalationRisk(
                    job_name=job_name,
                    topology=topology,
                    version=stream.version,
                    consecutive_failures=consecutive,
                    prow_url=latest_prow_url,
                    triage_url=job_analysis_url(stream.version, job_name),
                    failing_runs=streak_runs,
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
    """Analyze the report data and populate suggested bugs.

    Mutates the report in place.
    """
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

    try:
        report.escalation_risks = _find_escalation_risks(report.streams, config)
    except Exception as e:
        logger.error(f"Escalation risk analysis failed: {e}")
        report.escalation_risks = []
        report.data_errors.append(f"Escalation risk analysis: {e}")

    # Suggest bugs only for failures worth filing: blocking failures, jobs
    # that have failed persistently, or jobs flagged as escalation risks.
    # Otherwise a single flake in an accepted stream generates a bug card.
    escalation_risk_names = {er.job_name for er in report.escalation_risks}
    unique_failing = _find_unique_failing_jobs(report.streams)

    for job, versions in unique_failing:
        is_blocking = job.job_type == JobType.BLOCKING
        is_persistent = failure_counts.get(job.name, 0) >= config.persistent_threshold
        is_unstable = job.name in escalation_risk_names
        if not (is_blocking or is_persistent or is_unstable):
            continue
        component = config.jira_component_for(job.topology) if job.topology else ""
        suggested = jira_collector.suggest_bug(job, versions, config, component=component)
        report.suggested_bugs.append(suggested)

    logger.info(f"Analysis complete: {len(report.suggested_bugs)} suggested bugs")

    try:
        report.cross_topology = _correlate_cross_topology(report.streams, config)
    except Exception as e:
        logger.error(f"Cross-topology correlation failed: {e}")
        report.cross_topology = {}
        report.data_errors.append(f"Cross-topology correlation: {e}")
