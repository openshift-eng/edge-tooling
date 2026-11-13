"""Standalone Chai frontend over unchanged payload collector modules."""

from __future__ import annotations

import asyncio
import logging
import webbrowser
from copy import deepcopy

import click
from payload_monitor import __main__ as payload_cli
from payload_monitor.report.generator import generate_json

from .analysis import AnalysisResult, Settings, analyze_targets, build_targets
from .collection import collect_report
from .report import generate_html, merge_analysis_data

logger = logging.getLogger("edge_ocp_ci_monitor")


def _monitor_params():
    params = deepcopy([param for param in payload_cli.main.params if param.name != "from_json"])
    for param in params:
        if param.name == "merge_analysis_path":
            param.help = "Patch externally supplied analysis JSON into an existing HTML report."
    return params


@click.command(name="edge-ocp-ci-monitor", params=_monitor_params())
@click.option("--ai-timeout", type=click.FloatRange(min=1, max=2700), default=2700, show_default=True,
              help="Hard timeout in seconds per request, including setup; each category may make one final request.")
@click.option("--ai-concurrency", type=click.IntRange(min=1, max=2), default=2, show_default=True,
              help="Maximum active ask_persona calls: one blocking batch and one Sippy batch.")
@click.pass_context
def main(ctx, ai_timeout, ai_concurrency, **options):
    """Edge OCP CI Monitor: the payload dashboard enriched through Chai MCP.

    Reads CHAI_MCP_URL and CHAI_MCP_TOKEN from the environment. Missing
    configuration or unavailable AI produces a dashboard with a warning.
    """
    open_browser = options.pop("open_browser", False)
    output = ctx.invoke(collect_report, **options)
    if output.report is None:
        # Explicit --merge-analysis is a local operation, never a new AI run.
        if open_browser:
            webbrowser.open(output.html_path.resolve().as_uri())
        return

    # Do not expose SDK request details even when collector --verbose is used.
    for name in ("fastmcp", "httpx", "httpcore", "httpx2", "httpcore2", "mcp"):
        logging.getLogger(name).setLevel(logging.WARNING)

    settings, warnings = Settings.from_environment(timeout=ai_timeout, concurrency=ai_concurrency)
    for stream in output.report.streams:
        for payload in stream.payloads:
            for job in payload.jobs:
                job.deep_analysis = None
        for finding in stream.regressions:
            finding.deep_analysis = None
    targets = build_targets(output.report)

    def save_report(partial: AnalysisResult, *, in_progress: bool = False) -> None:
        merge_analysis_data(output.report, partial.data)
        current_warnings = list(partial.warnings)
        if in_progress and partial.completed < partial.requested:
            current_warnings.append(
                f"Chai analysis is in progress ({partial.completed}/{partial.requested} targets completed). "
                "Validated results have been saved; remaining requests may still be running."
            )
        generate_html(output.report, output.html_path, ai_warnings=current_warnings)
        if options.get("export_json"):
            generate_json(output.report, output.html_path.with_suffix(".json"))

    if settings is None:
        analysis = AnalysisResult(
            warnings=warnings, reasons=warnings.copy(),
            requested=len(targets["blocking_jobs"]) + len(targets["sippy_jobs"]),
        )
    else:
        analysis = asyncio.run(analyze_targets(
            targets, settings, on_update=lambda partial: save_report(partial, in_progress=True),
        ))

    for warning in analysis.warnings:
        logger.warning("Chai AI analysis: %s", warning)
    save_report(analysis)
    reasons = "; ".join(f"{reason} (x{count})" for reason, count in analysis.reason_counts.items())
    logger.info("Chai analysis: %s (%d/%d targets)%s", analysis.status, analysis.completed,
                analysis.requested, f". Reason(s): {reasons}" if reasons else "")
    logger.info("Report: %s", output.html_path.resolve())
    if open_browser:
        webbrowser.open(output.html_path.resolve().as_uri())


if __name__ == "__main__":
    main()
