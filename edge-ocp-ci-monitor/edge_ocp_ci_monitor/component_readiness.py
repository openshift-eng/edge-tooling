"""Edge-owned selection of saved Component Readiness views over the shared parser."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from payload_monitor.collectors.component_readiness import fetch_component_regressions
from payload_monitor.config import CR_VIEW_PATTERNS
from payload_monitor.models import ComponentRegression

logger = logging.getLogger(__name__)

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


def collect(versions: list[str] | None = None) -> list[ComponentRegression]:
    """Collect saved views for selected versions, or all views when omitted."""
    selected_versions = set(versions) if versions is not None else None
    tasks = [
        (view_def["version"], view_def["view"], view_def["topology"])
        for view_def in CR_VIEWS
        if selected_versions is None or view_def["version"] in selected_versions
    ]
    if not tasks:
        return []

    all_regressions = []
    with ThreadPoolExecutor(max_workers=min(len(tasks), 10)) as pool:
        futures = {
            pool.submit(fetch_component_regressions, v, view, topo): (v, topo)
            for v, view, topo in tasks
        }
        for future in as_completed(futures):
            v, topo = futures[future]
            try:
                all_regressions.extend(future.result())
            except Exception:
                logger.exception(f"Failed to fetch component readiness for {v} ({topo})")
                continue
    return all_regressions
