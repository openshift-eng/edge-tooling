"""Fetch Component Readiness data from Sippy (HA vs Single Node)."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from ..models import ComponentRegression
from .http import create_session

logger = logging.getLogger(__name__)

BASE_URL = "https://sippy.dptools.openshift.org"
CR_API_URL = f"{BASE_URL}/api/component_readiness"

# Status codes from Sippy Component Readiness
STATUS_REGRESSION = -400

# Only these versions have ha-vs-single views in Sippy
VIEW_PATTERN = "{version}-ha-vs-single"

_session = create_session()


def _test_detail_url(version: str, test_id: str, links: dict) -> str:
    """Build the Sippy UI URL for a component readiness test detail.

    The API returns an 'test_details' link pointing to the API endpoint.
    We convert it to the UI page URL instead.
    """
    api_url = links.get("test_details", "")
    if api_url:
        # The API URL contains query params we need — extract them
        # and redirect to the UI page
        from urllib.parse import urlparse
        parsed = urlparse(api_url)
        return f"{BASE_URL}/sippy-ng/component_readiness/test_details?{parsed.query}"
    if test_id:
        view = VIEW_PATTERN.format(version=version)
        return f"{BASE_URL}/sippy-ng/component_readiness/test_details?view={view}&testId={test_id}"
    return ""


def fetch_component_regressions(version: str) -> list[ComponentRegression]:
    """Fetch component readiness regressions for HA vs Single Node.

    Uses the pre-defined Sippy view '{version}-ha-vs-single' which compares
    HA topology (base) against Single Node topology (sample) to find
    components that regress on SNO compared to standard HA clusters.
    """
    view = VIEW_PATTERN.format(version=version)
    logger.info(f"Fetching component readiness for {view}")

    try:
        resp = _session.get(CR_API_URL, params={"view": view}, timeout=60)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.warning(f"Component readiness fetch failed for {version}: {e}")
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
                    sample_success=sample_success,
                    sample_failure=sample_failure,
                    sample_pass_rate=sample_rate,
                    base_success=base_success,
                    base_failure=base_failure,
                    base_pass_rate=base_rate,
                    fisher_exact=test.get("fisher_exact", 0.0),
                    last_failure=test.get("last_failure", ""),
                    detail_url=_test_detail_url(version, test.get("test_id", ""), links),
                    explanation=explanations[0] if explanations else "",
                ))

    logger.info(f"  {version}: {len(regressions)} component regression(s) on SNO vs HA")
    return regressions


def collect(versions: list[str]) -> list[ComponentRegression]:
    """Collect component readiness regressions for all versions.

    Only versions with a Sippy ha-vs-single view will return data.
    """
    all_regressions = []
    with ThreadPoolExecutor(max_workers=min(len(versions) or 1, 10)) as pool:
        futures = {
            pool.submit(fetch_component_regressions, version): version
            for version in versions
        }
        for future in as_completed(futures):
            version = futures[future]
            try:
                all_regressions.extend(future.result())
            except Exception as e:
                logger.error(f"Failed to fetch component readiness for {version}: {e}")
                continue
    return all_regressions
