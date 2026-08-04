"""Errata Tool REST API client for MicroShift RPM advisory validation.

Authenticates via Kerberos/GSSAPI (requires a valid kinit session).
All requests go through the internal Red Hat VPN.
"""

import logging
import re

logger = logging.getLogger(__name__)

ET_BASE_URL = "https://errata.devel.redhat.com"
ET_API_URL = f"{ET_BASE_URL}/api/v1"

_session = None


def _get_session():
    """Return a requests session with GSSAPI auth, creating it on first call."""
    global _session
    if _session is None:
        import requests  # noqa: PLC0415
        import urllib3  # noqa: PLC0415
        from requests_gssapi import HTTPSPNEGOAuth  # noqa: PLC0415
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        _session = requests.Session()
        _session.auth = HTTPSPNEGOAuth()
        _session.verify = False
        _session.headers.update({"Accept": "application/json"})
    return _session


def _et_get(path, **kwargs):
    """GET from the Errata Tool API.

    Returns:
        dict/list or None on failure.
    """
    import requests  # noqa: PLC0415
    session = _get_session()
    url = f"{ET_API_URL}/{path.lstrip('/')}"
    try:
        resp = session.get(url, timeout=30, **kwargs)
        if resp.status_code == 401:
            logger.error("Kerberos auth failed (HTTP 401) — run 'kinit' first")
            return None
        if resp.status_code == 404:
            logger.debug("ET API 404: %s", path)
            return None
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError as exc:
        logger.error("Cannot reach Errata Tool — check VPN: %s", exc)
        return None
    except ValueError as exc:
        logger.error("ET API returned non-JSON response for %s: %s", path, exc)
        return None
    except requests.RequestException as exc:
        logger.error("ET API error for %s: %s", path, exc)
        return None


def check_auth():
    """Verify Kerberos authentication against the Errata Tool.

    Returns:
        bool
    """
    import requests  # noqa: PLC0415
    session = _get_session()
    try:
        resp = session.get(ET_BASE_URL, timeout=10,
                           headers={"Accept": "text/html"})
        if resp.status_code >= 400:
            logger.error("Errata Tool auth check failed (HTTP %d)", resp.status_code)
        return resp.status_code < 400
    except requests.exceptions.ConnectionError as exc:
        logger.error("Cannot reach Errata Tool — check VPN: %s", exc)
        return False
    except requests.RequestException as exc:
        logger.error("Errata Tool auth check failed: %s", exc)
        return False


def _unwrap_advisory(data):
    """Extract advisory fields from the ET response envelope.

    The ET ``/erratum/{id}`` response has the structure::

        {"errata": {"rhba": {fields...}}, "original_type": "RHBA", ...}

    This extracts the inner advisory dict and merges in ``original_type``.
    """
    if data is None:
        return None

    errata = data.get("errata", {})
    advisory = None
    errata_type = None
    for key in ("rhba", "rhea", "rhsa"):
        if key in errata:
            advisory = errata[key]
            errata_type = key.upper()
            break

    if advisory is None:
        for key in ("rhba", "rhea", "rhsa"):
            if key in data:
                advisory = data[key]
                errata_type = key.upper()
                break

    if advisory is None:
        return None

    advisory["errata_type"] = (data.get("original_type")
                               or errata_type
                               or advisory.get("errata_type"))
    return advisory


def fetch_advisory(advisory_id):
    """Fetch advisory details.

    Args:
        advisory_id: Numeric ID or name (e.g. ``RHBA-2026:12345``).

    Returns:
        dict with advisory fields (including ``errata_type`` and
        ``_jira_issues``), or None.
    """
    data = _et_get(f"erratum/{advisory_id}")
    advisory = _unwrap_advisory(data)
    if advisory is not None and data is not None:
        ji = data.get("jira_issues", {})
        if isinstance(ji, dict):
            advisory["_jira_issues"] = ji.get("jira_issues", [])
    return advisory


def fetch_builds(advisory_id):
    """Fetch builds attached to an advisory.

    Returns:
        dict: product-version name -> {name, description, builds}, or None.
    """
    return _et_get(f"erratum/{advisory_id}/builds")


def fetch_jira_issues(advisory_id):
    """Fetch Jira issues linked to an advisory.

    Returns:
        list of issue dicts, or None.
    """
    return _et_get(f"erratum/{advisory_id}/jira_issues")


def fetch_external_tests(advisory_id):
    """Fetch external test results (CAT, rpmdiff, etc.).

    Returns:
        list or dict of test results, or None.
    """
    return _et_get(f"erratum/{advisory_id}/external_tests")


def fetch_cdn_repos(advisory_id):
    """Fetch CDN repo status for an advisory.

    Returns:
        dict of CDN repo info, or None.
    """
    return _et_get(f"erratum/{advisory_id}/cdn_repos")


def extract_microshift_nvrs(builds_data):
    """Extract MicroShift RPM filenames from the builds response.

    Handles the ET builds structure::

        {"ProductVersion": {"name": "...", "builds": [{"nvr-string": {"variant_arch": ...}}]}}

    Extracts individual RPM filenames from ``variant_arch`` to capture
    all subpackages (microshift-selinux, microshift-networking, etc.).

    Returns:
        list of RPM filename strings, or empty list.
    """
    if not builds_data:
        return []

    rpms = []
    for product_version, pv_data in builds_data.items():
        build_entries = pv_data
        if isinstance(pv_data, dict):
            build_entries = pv_data.get("builds", [])
        if not isinstance(build_entries, list):
            continue
        for entry in build_entries:
            if not isinstance(entry, dict):
                continue
            for nvr_key, build_info in entry.items():
                if "microshift" not in nvr_key.lower():
                    continue
                if not isinstance(build_info, dict):
                    rpms.append(nvr_key)
                    continue
                variant_arch = build_info.get("variant_arch", {})
                found_rpms = False
                for arches in variant_arch.values():
                    if not isinstance(arches, dict):
                        continue
                    for rpm_list in arches.values():
                        if not isinstance(rpm_list, list):
                            continue
                        for rpm_file in rpm_list:
                            if "microshift" in rpm_file.lower():
                                rpms.append(rpm_file)
                                found_rpms = True
                if not found_rpms:
                    rpms.append(nvr_key)
    return sorted(set(rpms))


def extract_package_names(nvrs):
    """Extract RPM package names from NVR strings or RPM filenames.

    Args:
        nvrs: List of NVR strings or RPM filenames, e.g.
              ``["microshift-selinux-4.20.26-...el9.x86_64.rpm"]``

    Returns:
        set of package name strings.
    """
    names = set()
    for nvr in nvrs:
        m = re.match(r"(microshift[a-z-]*)-\d+\.\d+\.\d+", nvr)
        if m:
            names.add(m.group(1))
    return names


def extract_bug_keys(jira_data):
    """Extract OCPBUGS keys from the Jira issues response.

    Handles the ET embedded format where each item wraps
    the issue fields under a ``jira_issue`` key::

        [{"jira_issue": {"key": "OCPBUGS-123", "status": "Verified", ...}}]

    Returns:
        list of dicts: ``[{"key": "OCPBUGS-123", "status": "Verified", ...}]``
    """
    if jira_data is None:
        return []

    bugs = []
    items = jira_data if isinstance(jira_data, list) else []

    for item in items:
        if not isinstance(item, dict):
            continue
        fields = item.get("jira_issue", item)
        bug = {}
        bug["key"] = fields.get("key") or fields.get("id_jira") or ""
        status = fields.get("status")
        if isinstance(status, dict):
            bug["status"] = status.get("name", "unknown")
        elif isinstance(status, str):
            bug["status"] = status
        else:
            bug["status"] = "unknown"
        bug["summary"] = fields.get("summary", "")
        bug["is_private"] = fields.get("is_private", False)
        if bug["key"] and bug["key"].startswith("OCPBUGS-"):
            bugs.append(bug)

    return bugs
