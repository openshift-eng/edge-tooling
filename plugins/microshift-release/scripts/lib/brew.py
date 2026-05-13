"""Brew (brewweb) scraping for MicroShift RPM builds."""

import logging
import re

import requests
import urllib3

BREW_PACKAGE_URL = "https://brewweb.engineering.redhat.com/brew/packageinfo?packageID=82827"
BREW_BUILDINFO_URL = "https://brewweb.engineering.redhat.com/brew/buildinfo?buildID={build_id}"
BREW_SEARCH_URL = (
    "https://brewweb.engineering.redhat.com/brew/search"
    "?match=glob&type=build&terms={terms}"
)
ERRATA_PROBE_URL = "https://errata.devel.redhat.com/"

logger = logging.getLogger(__name__)

# Internal Red Hat services use certs not in the system trust store;
# verify=False is required when connecting via VPN.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Module-level cache for the Brew HTML page
_cached_html = None


def check_vpn():
    """Check VPN connectivity by probing errata.devel.redhat.com.

    Returns:
        bool: True if VPN is connected.
    """
    try:
        response = requests.get(
            ERRATA_PROBE_URL, verify=False, timeout=5,
            allow_redirects=False,
        )
        return response.status_code < 500
    except requests.RequestException:
        return False


def _fetch_brew_page():
    """Fetch the Brew package page HTML, cached for the process lifetime.

    Returns:
        str: HTML content of the Brew page.

    Raises:
        requests.RequestException: On network failure.
    """
    global _cached_html
    if _cached_html is not None:
        return _cached_html

    response = requests.get(BREW_PACKAGE_URL, verify=False, timeout=30)
    response.raise_for_status()
    _cached_html = response.text
    return _cached_html


def fetch_latest_nightly_builds():
    """Parse Brew package page for latest MicroShift nightly builds per stream.

    Returns:
        dict: Keyed by stream (e.g., "4.21"), each value is a dict with:
            - nvr: Full NVR string
            - ocp_nightly_name: Mapped OCP nightly name
            - timestamp: ISO format timestamp string
    """
    html = _fetch_brew_page()

    # Pattern: microshift-X.Y.0~0.nightly_YYYY_MM_DD_HHMMSS
    pattern = re.compile(
        r"microshift-(\d+\.\d+)\.0~0\.nightly_(\d{4})_(\d{2})_(\d{2})_(\d{6})"
    )

    builds = {}
    for match in pattern.finditer(html):
        stream = match.group(1)
        year, month, day, time_part = match.group(2), match.group(3), match.group(4), match.group(5)
        hours = time_part[:2]
        minutes = time_part[2:4]
        seconds = time_part[4:6]

        timestamp = f"{year}-{month}-{day}T{hours}:{minutes}:{seconds}"
        ocp_name = f"{stream}.0-0.nightly-{year}-{month}-{day}-{time_part}"

        # Keep only the latest build per stream
        if stream not in builds or timestamp > builds[stream]["timestamp"]:
            builds[stream] = {
                "nvr": match.group(0),
                "ocp_nightly_name": ocp_name,
                "timestamp": timestamp,
            }

    return builds


def _find_rpms(brew_version):
    """Search Brew page for RPMs matching a Brew-format version string.

    Args:
        brew_version: Brew-format version, e.g., "4.22.0~ec.5" or "4.21.8".

    Returns:
        dict: {"found": True, "nvr": "...", "build_date": "..."} or {"found": False}.
    """
    html = _fetch_brew_page()
    escaped = re.escape(brew_version)
    pattern = re.compile(rf"(microshift-{escaped}-(\d{{12}})\.p0\.[^\s\"<]+)")

    match = pattern.search(html)
    if match:
        nvr = match.group(1)
        date_str = match.group(2)  # YYYYMMDDHHmm
        build_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        return {"found": True, "nvr": nvr, "build_date": build_date}

    return {"found": False}


def find_ecrc_rpms(version):
    """Search Brew package page for EC/RC RPMs matching a version.

    Args:
        version: e.g., "4.22.0-ec.5" or "4.22.0-rc.1".

    Returns:
        dict: {"found": True, "nvr": "...", "build_date": "..."} or {"found": False}.
    """
    # Normalize: 4.22.0-ec.5 -> 4.22.0~ec.5 (Brew uses tilde)
    brew_version = version.replace("-", "~")
    return _find_rpms(brew_version)


def find_zstream_rpms(version):
    """Search Brew package page for Z-stream RPMs matching a version.

    Args:
        version: e.g., "4.21.8".

    Returns:
        dict: {"found": True, "nvr": "...", "build_date": "..."} or {"found": False}.
    """
    return _find_rpms(version)


def _search_brew(brew_version):
    """Search Brew for NVRs matching a version when not on the package page.

    The package listing page only shows recent builds. This searches Brew
    directly for older builds.

    Returns:
        str: HTML content of the search results page, or empty string.
    """
    terms = f"microshift-{brew_version}-*"
    url = BREW_SEARCH_URL.format(terms=requests.utils.quote(terms, safe="*"))
    try:
        resp = requests.get(url, verify=False, timeout=30)
        if resp.status_code == 200:
            return resp.text
    except requests.RequestException as exc:
        logger.debug("Brew search failed for %s: %s", brew_version, exc)
    return ""


def _parse_build_matches(html, brew_version):
    """Extract NVR matches from HTML for a given brew version."""
    escaped = re.escape(brew_version)
    pattern = re.compile(
        rf"(microshift-{escaped}-(\d{{12}})\.p0\.g([0-9a-f]+)\.[^\s\"<]+)"
    )
    return pattern.findall(html)


def get_build_info(version, release_type):
    """Get detailed build info for a MicroShift version from Brew.

    Searches the cached package page first, then falls back to Brew search
    for older builds that are no longer on the first page.

    Args:
        version: e.g., "4.21.8", "4.22.0-ec.5", "4.22.0-rc.1".
        release_type: One of "Z", "X", "Y", "RC", "EC", "nightly".

    Returns:
        dict: {found, nvr, build_date, commit, el9, el10} or {found: False}.
    """
    brew_version = version.replace("-", "~") if release_type in ("RC", "EC") else version

    # Try cached package page first (fast, no extra request)
    try:
        html = _fetch_brew_page()
    except requests.RequestException:
        html = ""
    matches = _parse_build_matches(html, brew_version)

    # Fallback: search Brew directly for older builds
    if not matches:
        logger.info("NVR not on package page, searching Brew for %s...", brew_version)
        search_html = _search_brew(brew_version)
        matches = _parse_build_matches(search_html, brew_version)

    if not matches:
        return {"found": False}

    first_nvr, date_str, commit = matches[0]
    build_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

    all_nvrs = [m[0] for m in matches]
    el9 = any("el9" in nvr for nvr in all_nvrs)
    el10 = any("el10" in nvr for nvr in all_nvrs)

    return {
        "found": True,
        "nvr": first_nvr,
        "build_date": build_date,
        "commit": commit,
        "el9": el9,
        "el10": el10,
    }


def find_latest_rc(minor):
    """Find the latest RC build for a minor version.

    Searches Brew for all microshift-X.Y.0~rc.* builds in one query
    and returns the highest-numbered RC.

    Args:
        minor: Minor version string, e.g. "4.21".

    Returns:
        dict: {found, version, commit, rc_num} or {found: False}.
    """
    escaped = re.escape(minor)
    rc_pattern = re.compile(
        rf"microshift-{escaped}\.0~rc\.(\d+)-\d{{12}}\.p0\.g([0-9a-f]+)"
    )

    # Search cached page + Brew search in one pass
    combined = ""
    try:
        combined = _fetch_brew_page()
    except requests.RequestException as exc:
        logger.debug("Brew package page unavailable, will search directly: %s", exc)
    search_html = _search_brew(f"{minor}.0~rc*")
    combined += search_html

    matches = rc_pattern.findall(combined)
    if not matches:
        return {"found": False}

    best = max(matches, key=lambda m: int(m[0]))
    rc_num, commit = best
    return {
        "found": True,
        "version": f"{minor}.0-rc.{rc_num}",
        "commit": commit,
        "rc_num": int(rc_num),
    }


def _extract_build_id_for_nvr(html, nvr):
    """Find the Brew build ID nearest to the NVR text on the package page."""
    idx = html.find(nvr)
    if idx < 0:
        return None
    window = html[max(0, idx - 500):idx]
    m = re.search(r'buildID=(\d+)', window)
    return m.group(1) if m else None


def get_build_packages(nvr):
    """Return the set of RPM package names from a Brew build.

    Uses the cached package page to find the build ID for the NVR,
    then fetches the build detail page and parses RPM entries.

    Args:
        nvr: Full NVR string from get_build_info(), e.g.
             "microshift-4.20.20-202605040640.p0.g8c79976.assembly.4.20.20.el9".

    Returns:
        set of package name strings, or None on failure.
    """
    m = re.match(r'microshift-(\S+?)-\d{12}\.', nvr)
    if not m:
        logger.debug("Could not extract version from NVR: %s", nvr)
        return None
    brew_version = m.group(1)

    escaped = re.escape(brew_version)

    # Try cached package page first
    try:
        html = _fetch_brew_page()
    except requests.RequestException:
        html = ""
    build_id = _extract_build_id_for_nvr(html, nvr)

    if build_id:
        url = BREW_BUILDINFO_URL.format(build_id=build_id)
        try:
            resp = requests.get(url, verify=False, timeout=30)
            if resp.status_code == 200:
                matches = re.findall(
                    rf'(microshift[a-z-]*)-{escaped}-\S+\.rpm', resp.text
                )
                if matches:
                    return set(matches)
        except requests.RequestException as exc:
            logger.debug("Brew buildinfo fetch failed: %s", exc)

    # Fallback: search may return the build detail page or a list page
    search_html = _search_brew(brew_version)
    if search_html:
        matches = re.findall(
            rf'(microshift[a-z-]*)-{escaped}-\S+\.rpm', search_html
        )
        if matches:
            return set(matches)

        # List page — extract build ID and fetch detail page
        build_id = _extract_build_id_for_nvr(search_html, nvr)
        if build_id:
            try:
                url = BREW_BUILDINFO_URL.format(build_id=build_id)
                resp = requests.get(url, verify=False, timeout=30)
                if resp.status_code == 200:
                    matches = re.findall(
                        rf'(microshift[a-z-]*)-{escaped}-\S+\.rpm',
                        resp.text,
                    )
                    if matches:
                        return set(matches)
            except requests.RequestException as exc:
                logger.debug("Brew buildinfo fetch failed: %s", exc)

    logger.debug("Could not retrieve build packages for %s", nvr)
    return None


def extract_commit_from_nvr(version):
    """Extract the git commit hash from a Brew NVR for a z-stream version.

    Brew NVRs embed the source commit as a short hash prefixed with 'g',
    e.g., microshift-4.21.11-202604201054.p0.g7f7539e.assembly.4.21.11.el9
    contains commit 7f7539e.

    Args:
        version: e.g., "4.21.11".

    Returns:
        str or None: Short git commit hash, or None if not found.
    """
    rpms = find_zstream_rpms(version)
    if not rpms.get("found"):
        logger.debug("No Brew RPM found for %s", version)
        return None

    nvr = rpms["nvr"]
    match = re.search(r"\.g([0-9a-f]{7,})\.", nvr)
    if match:
        return match.group(1)
    logger.warning("Brew NVR for %s has no commit hash: %s",
                   version, nvr)
    return None
