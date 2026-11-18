"""Edge-owned selection of saved Component Readiness views over the shared parser."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from payload_monitor.collectors.component_readiness import fetch_component_regressions
from payload_monitor.models import ComponentRegression

logger = logging.getLogger(__name__)

# Exact Component Readiness views from Sippy, checked 2026-11-17:
# https://sippy.dptools.openshift.org/api/component_readiness/views
# This lookup is independent of payload versions; refresh it when Sippy adds views.
CR_VIEWS = [
    {"version": "4.18", "view": "4.18-ha-vs-single", "topology": "SNO"},
    {"version": "4.19", "view": "4.19-ha-vs-single", "topology": "SNO"},
    {"version": "4.20", "view": "4.20-ha-vs-single", "topology": "SNO"},
    {"version": "4.21", "view": "4.21-ha-vs-single", "topology": "SNO"},
    {"version": "4.22", "view": "4.22-ha-vs-single", "topology": "SNO"},
    {"version": "4.22", "view": "4.22-ha-vs-two-node-arbiter", "topology": "TNA"},
    {"version": "4.22", "view": "4.22-ha-vs-two-node-fencing", "topology": "TNF"},
    {"version": "4.23", "view": "4.23-ha-vs-single", "topology": "SNO"},
    {"version": "4.23", "view": "4.23-ha-vs-two-node-arbiter", "topology": "TNA"},
    {"version": "4.23", "view": "4.23-ha-vs-two-node-fencing", "topology": "TNF"},
    {"version": "5.0", "view": "5.0-ha-vs-single", "topology": "SNO"},
    {"version": "5.0", "view": "5.0-ha-vs-two-node-arbiter", "topology": "TNA"},
    {"version": "5.0", "view": "5.0-ha-vs-two-node-fencing", "topology": "TNF"},
    {"version": "5.1", "view": "5.1-ha-vs-single", "topology": "SNO"},
    {"version": "5.1", "view": "5.1-ha-vs-two-node-arbiter", "topology": "TNA"},
    {"version": "5.1", "view": "5.1-ha-vs-two-node-fencing", "topology": "TNF"},
]


def collect() -> list[ComponentRegression]:
    """Collect every saved view in the static lookup, independent of payload scope."""
    tasks = [
        (view_def["version"], view_def["view"], view_def["topology"])
        for view_def in CR_VIEWS
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
