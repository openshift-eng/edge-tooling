"""CLI entry point for Edge OCP Payload Monitor."""

from __future__ import annotations

import logging
import re
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import click

from .analyzer import analyze
from .collectors import component_readiness, prow, sippy, timing
from .collectors.release_controller import (
    collect as collect_payloads,
    discover_streams,
    version_from_stream,
)
from .config import Config
from .models import JobResult, JobType, MonitorReport
from .report.generator import (
    generate_html,
    generate_json,
    load_json,
    merge_analysis,
    patch_analysis_html,
)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-5s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )


def _emit_job_section(label: str, jobs: list[dict]) -> None:
    """Print a labeled section of pipe-delimited job entries to stdout."""
    if not jobs:
        return
    print(f"{label}_JOBS_START")
    for j in jobs:
        prev = ";".join(j.get("previous_attempt_urls", []))
        print(f"{label}|{j['name']}|{j['prow_url']}|{j['topology']}|{j['version']}|{j['payload_tag']}|{prev}")
    print(f"{label}_JOBS_END")


def _collect_jobs_by_type(report: MonitorReport, job_type: JobType) -> list[dict]:
    """Collect edge job failures of a given type from the report."""
    jobs = []
    for stream in report.streams:
        for payload in stream.payloads:
            for j in payload.jobs:
                if j.result == JobResult.FAILURE and j.job_type == job_type:
                    jobs.append({
                        "name": j.name,
                        "prow_url": j.prow_url,
                        "topology": j.topology or "",
                        "version": stream.version,
                        "payload_tag": payload.tag,
                        "previous_attempt_urls": [pa.prow_url for pa in j.previous_attempts],
                    })
    return jobs


@click.command()
@click.option("--versions", type=str, default=None,
              help="Override versions, comma-separated (e.g., '4.18,4.19')")
@click.option("--output", "output_path", type=click.Path(), default=None,
              help="Output HTML file path")
@click.option("--from-json", "from_json", type=click.Path(exists=True), default=None,
              help="Regenerate HTML from an enriched JSON file (skips data collection)")
@click.option("--json", "export_json", is_flag=True, default=False,
              help="Also export full report data as JSON")
@click.option("--open", "open_browser", is_flag=True, default=False,
              help="Open report in browser after generation")
@click.option("--verbose", is_flag=True, default=False,
              help="Enable verbose logging")
@click.option("--skip-prow", is_flag=True, default=False,
              help="Skip Prow artifact fetching (faster, less detail)")
@click.option("--skip-sippy", is_flag=True, default=False,
              help="Skip Sippy regression and Component Readiness checks")
@click.option("--with-timing", is_flag=True, default=False,
              help="Include install/upgrade timing insights (disabled by default)")
@click.option("--payloads", type=click.IntRange(min=1, max=10), default=None,
              help="Number of payloads to analyze per stream (1-10, default 5)")
@click.option("--merge-analysis", "merge_analysis_path", type=click.Path(exists=True), default=None,
              help="Merge analysis JSON into an existing HTML report (or into --from-json data)")
def main(
    versions, output_path, from_json, export_json,
    open_browser, verbose, skip_prow, skip_sippy, with_timing,
    payloads, merge_analysis_path,
):
    """Edge OCP Payload Monitor — monitor OpenShift nightly payloads for edge topology failures."""
    _setup_logging(verbose)
    logger = logging.getLogger("payload_monitor")

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

    # --merge-analysis without --from-json: patch existing HTML directly
    if merge_analysis_path and not from_json:
        if not html_path.exists():
            logger.error(f"HTML report not found: {html_path}")
            raise SystemExit(1)
        logger.info(f"Patching analysis from {merge_analysis_path} into {html_path}")
        patch_analysis_html(html_path, Path(merge_analysis_path))
        if open_browser:
            webbrowser.open(f"file://{html_path.resolve()}")
        return

    # --from-json mode: regenerate HTML from JSON, optionally merging analysis data
    if from_json:
        logger.info(f"Loading report from {from_json}")
        report = load_json(Path(from_json))
        if merge_analysis_path:
            logger.info(f"Merging analysis from {merge_analysis_path}")
            merge_analysis(report, Path(merge_analysis_path))
        generate_html(report, html_path)
        logger.info(f"Report regenerated: {html_path.resolve()}")
        if open_browser:
            webbrowser.open(f"file://{html_path.resolve()}")
        return

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

    logger.info("Starting Edge OCP Payload Monitor")

    # Step 1: Discover versions and resolve stream names
    logger.info("Step 1: Discovering active versions...")
    stream_names = discover_streams(config)
    active_versions = [version_from_stream(s) for s in stream_names]
    logger.info(f"  Versions: {active_versions}")

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
            logger.error(f"Release Controller collection failed: {e}")
            data_errors.append(f"Release Controller: {e}")
            stream_reports = []

        empty_streams = [s.version for s in stream_reports if not s.payloads]
        if empty_streams:
            logger.warning(f"No payload data for versions: {', '.join(empty_streams)}")

        if not skip_sippy:
            try:
                sippy_regressions = sippy_future.result()
                for stream in stream_reports:
                    stream.regressions = sippy_regressions.get(stream.version, [])
            except Exception as e:
                logger.error(f"Sippy collection failed: {e}")
                data_errors.append(f"Sippy: {e}")

            try:
                comp_regs = cr_future.result()
            except Exception as e:
                logger.error(f"Component Readiness collection failed: {e}")
                data_errors.append(f"Component Readiness: {e}")
                comp_regs = []
        else:
            logger.info("  Skipping Sippy/Component Readiness (--skip-sippy)")
            comp_regs = []

        if with_timing:
            try:
                timing_report = timing_future.result()
            except Exception as e:
                logger.error(f"Timing collection failed: {e}")
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
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
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

    # Step 4: Analyze and find JIRA matches
    logger.info("Step 4: Analyzing failures and searching JIRA...")
    try:
        analyze(report, config)
    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        data_errors.append(f"Analysis: {e}")

    # Generate HTML report
    generate_html(report, html_path)

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

    if open_browser:
        webbrowser.open(f"file://{html_path.resolve()}")


if __name__ == "__main__":
    main()
