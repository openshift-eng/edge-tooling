"""Fresh collection orchestration for the Chai monitor using unchanged collector modules."""

from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from payload_monitor.__main__ import (
    _collect_jobs_by_type,
    _emit_job_section,
    _setup_logging,
    _write_sippy_summary,
)
from payload_monitor.analyzer import (
    _correlate_cross_topology,
    _find_escalation_risks,
    _find_recurring_failures,
)
from payload_monitor.collectors import component_readiness, prow, sippy, timing
from payload_monitor.collectors.release_controller import (
    collect as collect_payloads,
)
from payload_monitor.collectors.release_controller import (
    configured_stream_names,
    version_from_stream,
)
from payload_monitor.config import Config
from payload_monitor.models import JobType, MonitorReport

from .report import generate_html, generate_json, patch_analysis_html
from .security import protect_logs, redact_data


@dataclass
class MonitorOutput:
    report: MonitorReport | None
    html_path: Path


@protect_logs(lambda *args, **kwargs: os.environ.get("CHAI_MCP_TOKEN", "").strip())
def collect_report(
    versions, output_path, export_json, verbose, skip_prow, skip_sippy, with_timing,
    payloads, merge_analysis_path,
):
    """Edge OCP CI Monitor — monitor OpenShift nightly payloads for edge topology failures."""
    _setup_logging(verbose)
    logger = logging.getLogger(__name__)

    # Build config
    config = Config()
    if payloads is not None:
        config.payloads_per_stream = payloads

    # Determine output path
    if not output_path:
        report_dir = Path(config.report_dir)
        date_str = datetime.now().strftime("%Y-%m-%d")
        output_path = str(report_dir / f"report-{date_str}.html")
    html_path = Path(output_path)

    # Explicit local import patches existing HTML without collecting data.
    if merge_analysis_path:
        if not html_path.exists():
            logger.error(f"HTML report not found: {html_path}")
            raise SystemExit(1)
        logger.info(f"Patching analysis from {merge_analysis_path} into {html_path}")
        patch_analysis_html(html_path, Path(merge_analysis_path))
        return MonitorOutput(None, html_path)

    # Deduplicate filename if it already exists
    if html_path.exists():
        ts = datetime.now().strftime("%H%M%S")
        html_path = html_path.with_stem(f"{html_path.stem}-{ts}")

    # Override versions if specified
    if versions:
        parsed = [v.strip() for v in versions.split(",") if v.strip()]
        if not parsed:
            logger.error("--versions provided but no valid versions found")
            raise SystemExit(1)
        invalid = [v for v in parsed if not re.match(r'^\d+\.\d+$', v)]
        if invalid:
            logger.error(f"Invalid version format: {', '.join(invalid)} (expected X.Y, e.g., 4.19)")
            raise SystemExit(1)
        config.versions = parsed

    logger.info("Starting Edge OCP CI Monitor")

    # Step 1: Resolve configured release streams
    logger.info("Step 1: Resolving configured release streams...")
    stream_names = configured_stream_names(config)
    active_versions = [version_from_stream(s) for s in stream_names]
    logger.info(f"  Versions: {active_versions}")

    sippy_regressions = {}
    # Step 2: Collect data in parallel — RC payloads, Sippy regressions, and
    # Component Readiness are all independent once the version list is known.
    logger.info("Step 2: Collecting data (RC payloads, Sippy, Component Readiness)...")
    data_errors = []
    timing_report = None
    with ThreadPoolExecutor(max_workers=4) as pool:
        rc_future = pool.submit(collect_payloads, config, stream_names)

        if not skip_sippy:
            sippy_future = pool.submit(sippy.collect, config, active_versions)
            cr_future = pool.submit(component_readiness.collect, active_versions)

        if with_timing:
            cache_path = Path(config.report_dir) / "timing_cache.json"
            timing_future = pool.submit(
                timing.collect, config, active_versions, cache_path, days=7,
            )

        try:
            stream_reports = rc_future.result()
        except Exception as e:
            logger.exception("Release Controller collection failed")
            data_errors.append(f"Release Controller: {e}")
            stream_reports = []

        empty_streams = [s.version for s in stream_reports if not s.payloads]
        if empty_streams:
            logger.warning(f"No payload data for versions: {', '.join(empty_streams)}")

        if not skip_sippy:
            try:
                sippy_regressions = sippy_future.result()
                for version in active_versions:
                    if version not in sippy_regressions:
                        data_errors.append(f"Sippy: job health unavailable for {version}")
                for stream in stream_reports:
                    stream.regressions = sippy_regressions.get(stream.version, [])
            except Exception as e:
                logger.exception("Sippy collection failed")
                data_errors.append(f"Sippy: {e}")

            try:
                comp_regs = cr_future.result()
            except Exception as e:
                logger.exception("Component Readiness collection failed")
                data_errors.append(f"Component Readiness: {e}")
                comp_regs = []
        else:
            logger.info("  Skipping Sippy/Component Readiness (--skip-sippy)")
            comp_regs = []

        if with_timing:
            try:
                timing_report = timing_future.result()
            except Exception as e:
                logger.exception("Timing collection failed")
                data_errors.append(f"Timing: {e}")
        else:
            logger.info("  Skipping timing collection (use --with-timing to enable)")

    # Step 3: Enrich failing jobs with Prow data
    if not skip_prow:
        logger.info("Step 3: Fetching Prow artifacts for failing jobs...")
        for stream in stream_reports:
            for payload in stream.payloads:
                prow.enrich_failing_jobs(payload.jobs)
    else:
        logger.info("Step 3: Skipping Prow enrichment (--skip-prow)")

    # Build report
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    report = MonitorReport(
        generated_at=now,
        streams=stream_reports,
        component_regressions=comp_regs,
        skip_prow=skip_prow,
        skip_sippy=skip_sippy,
        skip_timing=not with_timing,
        timing_report=timing_report,
        data_errors=data_errors,
        recurring_threshold=config.recurring_threshold,
        persistent_threshold=config.persistent_threshold,
        payloads_per_stream=config.payloads_per_stream,
    )

    report = redact_data(report, os.environ.get("CHAI_MCP_TOKEN", "").strip())

    # Failure patterns remain data-driven; only Chai can recommend a bug.
    logger.info("Step 4: Analyzing failure patterns...")
    report.failure_counts = _find_recurring_failures(report.streams)
    for label, attribute, operation in (
        ("Escalation risk analysis", "escalation_risks", _find_escalation_risks),
        ("Cross-topology correlation", "cross_topology", _correlate_cross_topology),
    ):
        try:
            setattr(report, attribute, operation(report.streams, config))
        except Exception as e:
            logger.exception("%s failed", label)
            data_errors.append(f"{label}: {e}")

    # Generate HTML report
    generate_html(report, html_path)
    _write_sippy_summary(html_path, active_versions, sippy_regressions)

    # Optionally export full JSON
    if export_json:
        json_path = html_path.with_suffix(".json")
        generate_json(report, json_path)
        logger.info(f"JSON:   {json_path.resolve()}")

    # Summary
    total_edge_failures = sum(s.total_edge_failures for s in report.streams)
    total_regressions = sum(len(s.regressions) for s in report.streams)
    logger.info(f"Done. {total_edge_failures} edge failures, {total_regressions} regressions")
    logger.info(f"Report: {html_path.resolve()}")

    # Print job summaries to stdout for skill consumption
    _emit_job_section("BLOCKING", _collect_jobs_by_type(report, JobType.BLOCKING))
    _emit_job_section("INFORMING", _collect_jobs_by_type(report, JobType.INFORMING))

    return MonitorOutput(report, html_path)

