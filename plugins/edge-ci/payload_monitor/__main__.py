"""CLI entry point for Edge Enablement Payload Monitor."""

import logging
import re
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import click

from .analyzer import analyze
from .collectors import component_readiness, prow, sippy, timing
from .collectors.release_controller import collect as collect_payloads, discover_streams
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


def _collect_blocking_jobs(report: MonitorReport) -> list[dict]:
    """Collect blocking edge job failures from the report.

    Returns a list of dicts with keys: name, prow_url, topology, version, payload_tag.
    """
    blocking = []
    for stream in report.streams:
        for payload in stream.payloads:
            for j in payload.edge_jobs:
                if j.result == JobResult.FAILURE and j.job_type == JobType.BLOCKING:
                    blocking.append({
                        "name": j.name,
                        "prow_url": j.prow_url,
                        "topology": j.topology or "",
                        "version": stream.version,
                        "payload_tag": payload.tag,
                    })
    return blocking


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
@click.option("--merge-analysis", "merge_analysis_path", type=click.Path(exists=True), default=None,
              help="Merge analysis JSON into an existing HTML report (or into --from-json data)")
def main(
    versions, output_path, from_json, export_json,
    open_browser, verbose, skip_prow, skip_sippy, with_timing,
    merge_analysis_path,
):
    """Edge Enablement Payload Monitor — monitor OpenShift nightly payloads for edge topology failures."""
    _setup_logging(verbose)
    logger = logging.getLogger("payload_monitor")

    # Build config
    config = Config()

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

    logger.info("Starting Edge Enablement Payload Monitor")

    # Step 1: Discover versions and resolve stream names
    logger.info("Step 1: Discovering active versions...")
    stream_names = discover_streams(config)
    active_versions = [s.split(".0-0.nightly")[0] for s in stream_names]
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
    )

    # Step 4: Analyze and find JIRA matches
    # TODO: JIRA integration is temporarily disabled (work in progress).
    # Uncomment the lines below to re-enable:
    # logger.info("Step 4: Analyzing failures and searching JIRA...")
    # analyze(report, config)
    report.skip_jira = True
    logger.info("Step 4: Skipping JIRA analysis (temporarily disabled — work in progress)")

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

    # Print blocking job summary to stdout for skill consumption
    blocking = _collect_blocking_jobs(report)
    if blocking:
        print("BLOCKING_JOBS_START")
        for b in blocking:
            print(f"BLOCKING|{b['name']}|{b['prow_url']}|{b['topology']}|{b['version']}|{b['payload_tag']}")
        print("BLOCKING_JOBS_END")

    if open_browser:
        webbrowser.open(f"file://{html_path.resolve()}")


if __name__ == "__main__":
    main()
