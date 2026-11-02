"""Shared Sippy selection for dashboard findings and AI analysis targets."""

from .models import MonitorReport, Regression


def sippy_findings(report: MonitorReport) -> list[Regression]:
    """Return one observation per job/release, with its release populated."""
    findings = {}
    for stream in report.streams:
        for regression in stream.regressions:
            if not regression.version:
                regression.version = stream.version
            findings[regression.analysis_key] = regression
    return list(findings.values())


def priority_sippy_findings(findings: list[Regression]) -> list[Regression]:
    """Rank qualifying jobs; callers apply the same five-job limit."""
    return sorted(
        (r for r in findings if r.needs_attention),
        key=lambda r: (
            r.sample_pass_rate,
            r.relative_change if r.relative_change is not None else 0,
            r.version, r.test_name,
        ),
    )
