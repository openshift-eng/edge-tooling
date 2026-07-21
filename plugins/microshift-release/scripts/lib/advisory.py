"""Advisory publication report — extract CVEs from OCP shipment data.

Fetches advisory information from ocp-build-data (GitHub) and
ocp-shipment-data (GitLab) to identify CVEs in OCP advisories.
Jira lookup is NOT performed here — CVEs are returned with a
``pending`` marker so the caller (or skill layer) can enrich them
via MCP.

Reuses GitLab helpers from lib.artifacts.
"""

import logging
import os

import requests
import urllib3
import yaml

from lib.artifacts import (
    _get_gitlab_project_id,
    _gitlab_get,
)

logger = logging.getLogger(__name__)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_OCP_BUILD_DATA = (
    "https://raw.githubusercontent.com/openshift-eng/ocp-build-data"
    "/refs/heads/openshift-{minor}/releases.yml"
)


def _get_shipment_mr_url(version):
    """Fetch releases.yml from GitHub and extract the shipment MR URL.

    Args:
        version: Full OCP version, e.g. "4.21.8".

    Returns:
        str or None: GitLab MR web URL, or None on failure.
    """
    minor = ".".join(version.split(".")[:2])
    url = _OCP_BUILD_DATA.format(minor=minor)
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = yaml.safe_load(resp.text)
        return (data.get("releases", {})
                .get(version, {})
                .get("assembly", {})
                .get("group", {})
                .get("shipment", {})
                .get("url"))
    except (requests.RequestException, yaml.YAMLError, AttributeError) as exc:
        logger.warning("Failed to fetch releases.yml for %s: %s", version, exc)
        return None


def _get_mr_yaml_files(project_id, mr_iid, mr_info):
    """Fetch and parse YAML files changed in a GitLab MR.

    Args:
        project_id: Numeric GitLab project ID.
        mr_iid: MR internal ID.
        mr_info: MR metadata dict from GitLab API.

    Returns:
        dict: {file_path: parsed_yaml_content}
    """
    changes_resp = _gitlab_get(
        f"projects/{project_id}/merge_requests/{mr_iid}/changes"
    )
    if changes_resp is None:
        logger.warning("GitLab API unreachable for MR !%s changes", mr_iid)
        return {}
    if changes_resp.status_code != 200:
        logger.warning("GitLab API returned %d for MR !%s changes",
                        changes_resp.status_code, mr_iid)
        return {}

    try:
        changes = changes_resp.json().get("changes", [])
    except ValueError as exc:
        logger.warning("Non-JSON response from GitLab MR !%s changes: %s",
                        mr_iid, exc)
        return {}

    yaml_content = {}
    for change in changes:
        file_path = change.get("new_path", change.get("old_path", ""))
        if not file_path.endswith((".yml", ".yaml")):
            continue

        encoded_path = file_path.replace("/", "%2F")
        for ref in (mr_info.get("source_branch"), mr_info.get("target_branch")):
            if not ref:
                continue
            file_resp = _gitlab_get(
                f"projects/{project_id}/repository/files/{encoded_path}/raw"
                f"?ref={ref}"
            )
            if file_resp and file_resp.status_code == 200:
                try:
                    yaml_content[file_path] = yaml.safe_load(file_resp.text)
                except yaml.YAMLError as exc:
                    logger.warning("Failed to parse YAML %s (ref=%s): %s",
                                   file_path, ref, exc)
                break

    return yaml_content


def _extract_cves(data):
    """Recursively extract CVE identifiers from parsed YAML data.

    Returns:
        list[str]: CVE IDs found (may contain duplicates; caller deduplicates).
    """
    found = []
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(key, str) and key.startswith("CVE-"):
                found.append(key)
            if isinstance(value, str) and value.startswith("CVE-"):
                found.append(value)
            found.extend(_extract_cves(value))
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, str) and item.startswith("CVE-"):
                found.append(item)
            if (isinstance(item, dict) and isinstance(item.get("key"), str)
                    and item["key"].startswith("CVE-")):
                found.append(item["key"])
            found.extend(_extract_cves(item))
    return found


def _get_advisories(version):
    """Extract advisory names, types, and CVE IDs from shipment MR data.

    Args:
        version: Full OCP version, e.g. "4.21.8".

    Returns:
        dict: {advisory_type: {"name": str, "cves": [str]}}
              or None on failure.
    """
    mr_url = _get_shipment_mr_url(version)
    if not mr_url:
        logger.debug("No shipment MR URL found for %s", version)
        return None

    mr_iid = mr_url.rstrip("/").split("/")[-1]
    project_id = _get_gitlab_project_id()
    if project_id is None:
        logger.warning("Could not resolve GitLab project ID — check GITLAB_API_TOKEN and VPN")
        return None

    mr_resp = _gitlab_get(
        f"projects/{project_id}/merge_requests/{mr_iid}"
    )
    if mr_resp is None:
        logger.warning("GitLab API unreachable for MR !%s", mr_iid)
        return None
    if mr_resp.status_code != 200:
        logger.warning("GitLab API returned %d for MR !%s", mr_resp.status_code, mr_iid)
        return None

    try:
        mr_info = mr_resp.json()
    except ValueError as exc:
        logger.warning("Non-JSON response from GitLab MR !%s: %s", mr_iid, exc)
        return None

    yaml_files = _get_mr_yaml_files(project_id, mr_iid, mr_info)
    if not yaml_files:
        logger.debug("No YAML files found in MR !%s", mr_iid)
        return None

    advisories = {}
    for file_path, content in yaml_files.items():
        if "fbc-openshift" in file_path or not content:
            continue

        try:
            public_url = (content.get("shipment", {})
                          .get("environments", {})
                          .get("stage", {})
                          .get("advisory", {})
                          .get("url", ""))
        except AttributeError:
            continue
        if not public_url:
            continue

        basename = os.path.basename(file_path)
        for advisory_type in ("image", "extras", "metadata", "rpm"):
            if advisory_type in basename:
                advisory_name = public_url.split("/")[-1] if "/" in public_url else public_url
                cves = list(set(_extract_cves(content)))
                advisories[advisory_type] = {"name": advisory_name, "cves": cves}
                break

    return advisories if advisories else None


def get_advisory_report(version):
    """Build an advisory report with CVEs marked as pending Jira lookup.

    Returns the same JSON structure that ``interpret_cves()`` in
    ``precheck_xyz.py`` expects, but with each CVE marked
    ``{"pending": True}`` instead of containing Jira ticket data.

    Args:
        version: Full OCP version, e.g. "4.21.8".

    Returns:
        dict: Advisory report keyed by advisory name, or
              ``{"error": "...", "skipped": True}`` on failure.
    """
    parts = version.split(".")
    if len(parts) >= 2 and parts[1].isdigit():
        minor_int = int(parts[1])
        if minor_int >= 20 and not os.environ.get("GITLAB_API_TOKEN", "").strip():
            return {"error": "Missing env var: GITLAB_API_TOKEN", "skipped": True}

    from lib import brew  # noqa: PLC0415
    if not brew.check_vpn():
        return {"error": "VPN not connected", "skipped": True}

    advisories = _get_advisories(version)
    if advisories is None:
        return {"error": f"No advisory data found for {version}", "skipped": True}

    report = {}
    for advisory_type, data in advisories.items():
        advisory_name = data["name"]
        cves = {}
        for cve_id in data["cves"]:
            cves[cve_id] = {"pending": True}
        report[advisory_name] = {"type": advisory_type, "cves": cves}

    return report
