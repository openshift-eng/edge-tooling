"""Fetch Component Readiness data from Sippy (HA vs edge topologies)."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import requests

from ..config import CR_VIEWS
from ..models import ComponentRegression
from .http import create_session

logger = logging.getLogger(__name__)

BASE_URL = "https://sippy.dptools.openshift.org"
CR_API_URL = f"{BASE_URL}/api/component_readiness"

# Status codes from Sippy Component Readiness
STATUS_REGRESSION = -400

_session = create_session()


def _test_detail_url(view: str, test_id: str, links: dict) -> str:
    """Build the Sippy UI URL for a component readiness test detail.

    The API returns an 'test_details' link pointing to the API endpoint.
    We convert it to the UI page URL instead.
    """
    api_url = links.get("test_details", "")
    if api_url:
        parsed = urlparse(api_url)
        return f"{BASE_URL}/sippy-ng/component_readiness/test_details?{parsed.query}"
    if test_id:
        return f"{BASE_URL}/sippy-ng/component_readiness/test_details?view={view}&testId={test_id}"
    return ""


def fetch_component_regressions(
    version: str, view_pattern: str, comparison: str,
) -> list[ComponentRegression]:
    """Fetch component readiness regressions for HA vs a specific topology.

    Uses a Sippy view (e.g., '{version}-ha-vs-single') which compares
    HA topology (base) against the given topology (sample) to find
    components that regress compared to standard HA clusters.
    """
    view = view_pattern.format(version=version)
    logger.info(f"Fetching component readiness for {view}")

    try:
        resp = _session.get(CR_API_URL, params={"view": view}, timeout=60)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.warning(f"Component readiness fetch failed for {view}: {e}")
        return []

    regressions = []
    rows = data.get("rows", [])

    for row in rows:
        component = row.get("component", "")
        columns = row.get("columns", [])

        for col in columns:
            status = col.get("status", 0)
            if status != STATUS_REGRESSION:
                continue

            regressed_tests = col.get("regressed_tests", [])
            variants = col.get("variants", {})

            for test in regressed_tests:
                sample_stats = test.get("sample_stats", {})
                base_stats = test.get("base_stats", {})
                explanations = test.get("explanations", [])
                links = test.get("links", {})

                sample_success = sample_stats.get("success_count", 0)
                sample_failure = sample_stats.get("failure_count", 0)
                sample_rate = sample_stats.get("success_rate", 0) * 100

                base_success = base_stats.get("success_count", 0)
                base_failure = base_stats.get("failure_count", 0)
                base_rate = base_stats.get("success_rate", 0) * 100

                regressions.append(ComponentRegression(
                    component=test.get("component", component),
                    test_name=test.get("test_name", ""),
                    test_suite=test.get("test_suite", ""),
                    test_id=test.get("test_id", ""),
                    capability=test.get("capability", ""),
                    version=version,
                    variants=test.get("variants", variants),
                    status=test.get("status", status),
                    comparison=comparison,
                    sample_success=sample_success,
                    sample_failure=sample_failure,
                    sample_pass_rate=sample_rate,
                    base_success=base_success,
                    base_failure=base_failure,
                    base_pass_rate=base_rate,
                    fisher_exact=test.get("fisher_exact", 0.0),
                    last_failure=test.get("last_failure", ""),
                    detail_url=_test_detail_url(view, test.get("test_id", ""), links),
                    explanation=explanations[0] if explanations else "",
                ))

    logger.info(f"  {version}: {len(regressions)} component regression(s) on {comparison} vs HA")
    return regressions


def collect(versions: list[str]) -> list[ComponentRegression]:
    """Collect component readiness regressions for all versions and views.

    Fetches all configured CR_VIEWS (e.g., HA vs SNO, HA vs TNF) for
    each version. Views that don't exist in Sippy return empty results.
    """
    tasks = [
        (version, view_def["pattern"], view_def["topology"])
        for version in versions
        for view_def in CR_VIEWS
    ]
    if not tasks:
        return []

    all_regressions = []
    with ThreadPoolExecutor(max_workers=min(len(tasks), 10)) as pool:
        futures = {
            pool.submit(fetch_component_regressions, v, pattern, topo): (v, topo)
            for v, pattern, topo in tasks
        }
        for future in as_completed(futures):
            v, topo = futures[future]
            try:
                all_regressions.extend(future.result())
            except Exception:
                logger.exception(f"Failed to fetch component readiness for {v} ({topo})")
                continue
    return all_regressions
