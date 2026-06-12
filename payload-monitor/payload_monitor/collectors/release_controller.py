"""Fetch nightly payload data from the amd64 release controller."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests

from ..config import Config
from .http import create_session
from ..models import (
    JobResult,
    JobRun,
    JobType,
    Payload,
    PayloadStatus,
    PreviousAttempt,
    StreamReport,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://amd64.ocp.releases.ci.openshift.org"
TAGS_URL = f"{BASE_URL}/api/v1/releasestream/{{stream}}/tags"
RELEASE_URL = f"{BASE_URL}/api/v1/releasestream/{{stream}}/release/{{tag}}"
_session = create_session()

_NIGHTLY_SUFFIX = ".0-0.nightly"


def _stream_name(version: str) -> str:
    return f"{version}{_NIGHTLY_SUFFIX}"


def version_from_stream(stream: str) -> str:
    return stream.split(_NIGHTLY_SUFFIX)[0]


def _parse_job_result(state: str) -> JobResult:
    state_lower = state.lower()
    if state_lower == "succeeded":
        return JobResult.SUCCESS
    if state_lower == "failed":
        return JobResult.FAILURE
    if state_lower in ("pending", "triggered"):
        return JobResult.PENDING
    return JobResult.UNKNOWN


def _parse_phase(phase: str) -> PayloadStatus:
    phase_lower = phase.lower()
    if phase_lower == "accepted":
        return PayloadStatus.ACCEPTED
    if phase_lower == "rejected":
        return PayloadStatus.REJECTED
    return PayloadStatus.PENDING


def _parse_jobs(
    jobs_dict: dict, job_type: JobType, config: Config
) -> list[JobRun]:
    runs = []
    for name, info in jobs_dict.items():
        topology = config.classify_topology(name)
        if topology is None:
            continue
        raw_retries = info.get("retries")
        try:
            retries = int(raw_retries) if raw_retries is not None else 0
        except (TypeError, ValueError):
            retries = 0
        raw_urls = info.get("previousAttemptURLs")
        urls = raw_urls if isinstance(raw_urls, list) else []
        runs.append(JobRun(
            name=name,
            prow_url=info.get("url", ""),
            result=_parse_job_result(info.get("state", "")),
            job_type=job_type,
            topology=topology,
            retries=retries,
            previous_attempts=[
                PreviousAttempt(prow_url=url, result=JobResult.FAILURE)
                for url in urls
                if url
            ],
        ))
    return runs



def discover_streams(config: Config) -> list[str]:
    """Return list of nightly stream names to monitor."""
    return [_stream_name(v) for v in config.versions]


def fetch_tags(stream: str, limit: int = 5) -> list[dict]:
    """Fetch recent payload tags for a stream.

    Only returns Accepted or Rejected payloads (skips Ready/Pending).
    Fetches extra tags to ensure we get enough terminal payloads.
    """
    url = TAGS_URL.format(stream=stream)
    logger.info(f"Fetching tags from {url}")
    try:
        resp = _session.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch tags for {stream}: {e}")
        return []

    terminal = []
    for tag in data.get("tags", []):
        phase = tag.get("phase", "").lower()
        if phase in ("accepted", "rejected"):
            terminal.append(tag)
            if len(terminal) >= limit:
                break
    return terminal


def fetch_release_detail(stream: str, tag: str) -> Optional[dict]:
    """Fetch detailed job results for a specific release tag."""
    url = RELEASE_URL.format(stream=stream, tag=tag)
    logger.debug(f"Fetching release detail from {url}")
    try:
        resp = _session.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch detail for {tag}: {e}")
        return None


def fetch_payload(stream: str, tag_data: dict, config: Config) -> Payload:
    """Fetch full payload data including job results."""
    tag = tag_data["name"]
    version = version_from_stream(stream)

    detail = fetch_release_detail(stream, tag)
    jobs = []
    if detail and "results" in detail:
        results = detail["results"]
        blocking = results.get("blockingJobs", {})
        informing = results.get("informingJobs", {})
        jobs.extend(_parse_jobs(blocking, JobType.BLOCKING, config))
        jobs.extend(_parse_jobs(informing, JobType.INFORMING, config))

    release_url = f"{BASE_URL}/releasestream/{stream}/release/{tag}"

    return Payload(
        tag=tag,
        stream=stream,
        version=version,
        status=_parse_phase(tag_data.get("phase", "")),
        url=release_url,
        jobs=jobs,
    )


def _collect_stream(stream: str, config: Config) -> StreamReport:
    """Collect payload data for a single stream, parallelizing tag fetches."""
    version = version_from_stream(stream)
    logger.info(f"Collecting payloads for {stream}")
    tags = fetch_tags(stream, limit=config.payloads_per_stream)

    if not tags:
        logger.warning(f"No payloads found for {stream} -- stream may not exist or has no accepted/rejected payloads")
        return StreamReport(stream=stream, version=version, payloads=[])

    # Fetch all tag details in parallel
    payloads = []
    with ThreadPoolExecutor(max_workers=len(tags) or 1) as pool:
        futures = {
            pool.submit(fetch_payload, stream, tag_data, config): tag_data
            for tag_data in tags
        }
        for future in as_completed(futures):
            tag_data = futures[future]
            try:
                payload = future.result()
                payloads.append(payload)
            except Exception as e:
                logger.error(f"Failed to fetch payload {tag_data.get('name', '?')}: {e}")
                continue

    # Restore chronological order (tags are newest-first from the API)
    tag_order = {t["name"]: i for i, t in enumerate(tags)}
    payloads.sort(key=lambda p: tag_order.get(p.tag, 0))

    for payload in payloads:
        if payload.jobs:
            failing = payload.failing_edge_jobs
            logger.info(
                f"  {payload.tag}: {payload.status.value} — "
                f"{len(payload.jobs)} edge jobs, {len(failing)} failing"
            )

    return StreamReport(stream=stream, version=version, payloads=payloads)


def collect(config: Config, streams: Optional[list[str]] = None) -> list[StreamReport]:
    """Collect payload data for all configured streams in parallel."""
    if streams is None:
        streams = discover_streams(config)

    with ThreadPoolExecutor(max_workers=min(len(streams) or 1, 10)) as pool:
        futures = {
            pool.submit(_collect_stream, stream, config): stream
            for stream in streams
        }
        reports = {}
        for future in as_completed(futures):
            stream = futures[future]
            try:
                reports[stream] = future.result()
            except Exception as e:
                logger.error(f"Failed to collect stream {stream}: {e}")
                version = version_from_stream(stream)
                reports[stream] = StreamReport(stream=stream, version=version, payloads=[])

    # Preserve original stream order
    return [reports[s] for s in streams]
