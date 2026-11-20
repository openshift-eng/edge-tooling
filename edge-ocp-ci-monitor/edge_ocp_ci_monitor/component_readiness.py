"""Collect saved Component Readiness views and report per-view failures."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from payload_monitor.collectors.component_readiness import (
    CR_API_URL,
    STATUS_REGRESSION,
    _test_detail_url,
)
from payload_monitor.collectors.http import create_session
from payload_monitor.config import CR_VIEW_PATTERNS
from payload_monitor.models import ComponentRegression

logger = logging.getLogger(__name__)
_session = create_session()

# Component Readiness coverage from Sippy
# https://sippy.dptools.openshift.org/api/component_readiness/views
_RELEASE_GROUPS = (
    (("4.18", "4.19", "4.20", "4.21"), ("SNO",)),
    (("4.22", "4.23", "5.0", "5.1"), ("SNO", "TNA", "TNF")),
)

CR_VIEWS = [
    {
        "version": version,
        "view": CR_VIEW_PATTERNS[topology].format(version=version),
        "topology": topology,
    }
    for versions, topologies in _RELEASE_GROUPS
    for version in versions
    for topology in topologies
]


def fetch_component_regressions(version: str, view: str, comparison: str) -> list[ComponentRegression]:
    """Fetch one view, propagating errors so unavailable data stays distinguishable."""
    logger.info("Fetching component readiness for %s", view)
    response = _session.get(CR_API_URL, params={"view": view}, timeout=60)
    response.raise_for_status()
    regressions = []
    for row in response.json().get("rows", []):
        for column in row.get("columns", []):
            if column.get("status") != STATUS_REGRESSION:
                continue
            for test in column.get("regressed_tests", []):
                sample = test.get("sample_stats", {})
                base = test.get("base_stats", {})
                test_id = test.get("test_id", "")
                explanations = test.get("explanations", [])
                regressions.append(ComponentRegression(
                    component=test.get("component", row.get("component", "")),
                    test_name=test.get("test_name", ""),
                    test_suite=test.get("test_suite", ""),
                    test_id=test_id,
                    capability=test.get("capability", ""),
                    version=version,
                    variants=test.get("variants", column.get("variants", {})),
                    status=test.get("status", STATUS_REGRESSION),
                    comparison=comparison,
                    sample_success=sample.get("success_count", 0),
                    sample_failure=sample.get("failure_count", 0),
                    sample_pass_rate=sample.get("success_rate", 0) * 100,
                    base_success=base.get("success_count", 0),
                    base_failure=base.get("failure_count", 0),
                    base_pass_rate=base.get("success_rate", 0) * 100,
                    fisher_exact=test.get("fisher_exact", 0.0),
                    last_failure=test.get("last_failure", ""),
                    detail_url=_test_detail_url(view, test_id, test.get("links", {})),
                    explanation=explanations[0] if explanations else "",
                ))
    return regressions


def collect(versions: list[str] | None = None) -> tuple[list[ComponentRegression], list[str]]:
    """Collect saved views for selected versions, or all views when omitted."""
    selected_versions = set(versions) if versions is not None else None
    tasks = [
        (view_def["version"], view_def["view"], view_def["topology"])
        for view_def in CR_VIEWS
        if selected_versions is None or view_def["version"] in selected_versions
    ]
    errors = []
    if selected_versions is not None:
        missing = selected_versions - {version for version, _, _ in tasks}
        if missing:
            errors.append(f"No saved views configured for {', '.join(sorted(missing))}")
    if not tasks:
        return [], errors or ["No saved views configured"]

    all_regressions = []
    with ThreadPoolExecutor(max_workers=min(len(tasks), 10)) as pool:
        futures = {
            pool.submit(fetch_component_regressions, v, view, topo): view
            for v, view, topo in tasks
        }
        for future in as_completed(futures):
            view = futures[future]
            try:
                all_regressions.extend(future.result())
            except Exception as exc:
                logger.exception("Failed to fetch Component Readiness view %s", view)
                errors.append(f"View {view} unavailable: {exc}")
    return all_regressions, sorted(errors)
