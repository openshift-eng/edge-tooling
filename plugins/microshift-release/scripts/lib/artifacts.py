"""Artifact validation helpers for MicroShift Phase 1 release checks."""

import json
import logging
import os
import re
import subprocess

import requests
import urllib3

logger = logging.getLogger(__name__)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# NVR format regex patterns per release type
_NVR_GA = re.compile(
    r"^microshift-\d+\.\d+\.\d+-\d{12}\.p0\.g[0-9a-f]+"
    r"\.assembly\.\d+\.\d+\.\d+\.el\d+$"
)
_NVR_NIGHTLY = re.compile(
    r"^microshift-\d+\.\d+\.\d+~0\.nightly_\d{4}_\d{2}_\d{2}_\d{6}"
    r"-\d{12}\.p0\.g[0-9a-f]+\.assembly\.microshift\.el\d+$"
)
_NVR_PATTERNS = {
    "Z": _NVR_GA,
    "X": _NVR_GA,
    "Y": _NVR_GA,
    "RC": re.compile(
        r"^microshift-\d+\.\d+\.\d+~rc\.\d+-\d{12}\.p0\.g[0-9a-f]+"
        r"\.assembly\.rc\.\d+\.el\d+$"
    ),
    "EC": re.compile(
        r"^microshift-\d+\.\d+\.\d+~ec\.\d+-\d{12}\.p0\.g[0-9a-f]+"
        r"\.assembly\.ec\.\d+\.el\d+$"
    ),
    "nightly": _NVR_NIGHTLY,
}

_MIRROR_BASE = "https://mirror.openshift.com/pub/openshift-v4/{arch}/microshift"
_MIRROR_RPM = {
    "EC": _MIRROR_BASE + "/ocp-dev-preview/{version}/el{rhel}/os/Packages/",
    "RC": _MIRROR_BASE + "/ocp/{version}/el{rhel}/os/Packages/",
}
_MIRROR_BOOTC = {
    "EC": _MIRROR_BASE + "/ocp-dev-preview/{version}/el{rhel}/bootc-pullspec.txt",
    "RC": _MIRROR_BASE + "/ocp/{version}/el{rhel}/bootc-pullspec.txt",
}
_ARCHES = ["x86_64", "aarch64"]
_RHEL_VERSIONS = [9, 10]

# GitLab internal instance for ocp-shipment-data
_GITLAB_BASE = "https://gitlab.cee.redhat.com"
_SHIPMENT_PROJECT_PATH = "hybrid-platforms/art/ocp-shipment-data"


def _http_get(url, timeout=15, verify=True, headers=None):
    """Perform an HTTP GET, returning the response or raising RequestException."""
    return requests.get(url, timeout=timeout, verify=verify, headers=headers,
                        allow_redirects=True)


def validate_nvr_format(nvr, release_type):
    """Check that an NVR string matches the expected pattern for release_type.

    Args:
        nvr: Full NVR string from Brew, e.g.
             "microshift-4.21.8-202604201054.p0.g7f7539e.assembly.4.21.8.el9".
        release_type: One of "Z", "X", "Y", "RC", "EC", "nightly".

    Returns:
        dict: {valid: bool, reason: str}
    """
    pattern = _NVR_PATTERNS.get(release_type)
    if pattern is None:
        return {"valid": False, "reason": f"Unknown release type: {release_type}"}
    if pattern.match(nvr):
        return {"valid": True, "reason": f"NVR matches {release_type} pattern"}
    return {"valid": False, "reason": f"NVR '{nvr}' does not match {release_type} pattern"}


def validate_commit_on_branch(commit_hash, minor):
    """Verify that commit_hash exists in the microshift repo on release-{minor}.

    Uses git_ops to clone/fetch the repo and check the commit.

    Args:
        commit_hash: Short or full git commit hash.
        minor: Minor version string, e.g., "4.21".

    Returns:
        dict: {valid: bool, reason: str}
    """
    try:
        from . import git_ops  # noqa: PLC0415

        repo_path = git_ops.ensure_microshift_repo()
        if not git_ops.verify_commit_exists(commit_hash):
            return {
                "valid": False,
                "reason": f"Commit {commit_hash} not found in microshift repo",
            }

        # Verify the commit is reachable from origin/release-{minor}
        branch = f"origin/release-{minor}"
        result = subprocess.run(
            ["git", "-C", repo_path, "merge-base", "--is-ancestor", commit_hash, branch],
            capture_output=True,
        )
        if result.returncode == 0:
            date_result = subprocess.run(
                ["git", "-C", repo_path, "log", "-1",
                 "--date=format:%Y-%m-%d", "--format=%ad", commit_hash],
                capture_output=True, text=True,
            )
            commit_date = date_result.stdout.strip() if date_result.returncode == 0 else ""
            return {"valid": True, "reason": f"Commit {commit_hash} is on {branch}",
                    "commit_date": commit_date}
        return {
            "valid": False,
            "reason": f"Commit {commit_hash} is NOT an ancestor of {branch}",
        }
    except Exception as exc:
        return {"valid": False, "reason": f"Git check failed: {exc}"}


def validate_rhel_builds(build_info, require_el10=True):
    """Check that required RHEL variant builds are present.

    Args:
        build_info: dict returned by brew.get_build_info().
        require_el10: Whether el10 is required (True for 4.23+).

    Returns:
        dict: {valid: bool, el9: bool, el10: bool, reason: str}
    """
    el9 = build_info.get("el9", False)
    el10 = build_info.get("el10", False)
    if not require_el10:
        if el9:
            return {"valid": True, "el9": True, "el10": el10,
                    "reason": "el9 build present (el10 N/A before 4.23)"}
        return {"valid": False, "el9": False, "el10": el10,
                "reason": "Missing RHEL variant: el9"}
    if el9 and el10:
        return {"valid": True, "el9": True, "el10": True,
                "reason": "el9 and el10 builds present"}
    missing = []
    if not el9:
        missing.append("el9")
    if not el10:
        missing.append("el10")
    return {
        "valid": False,
        "el9": el9,
        "el10": el10,
        "reason": f"Missing RHEL variants: {', '.join(missing)}",
    }


def validate_mirror_rpms(version, release_type, rhel_versions=None):
    """Check that RPMs are available in the mirror repos for RC/EC.

    Args:
        version: Full version string, e.g., "4.22.0-rc.2".
        release_type: "RC" or "EC".
        rhel_versions: List of RHEL versions to check (default: [9, 10]).

    Returns:
        dict: {valid: bool, reason: str, details: list[str]}
    """
    if release_type not in _MIRROR_RPM:
        return {"valid": True, "reason": f"N/A ({release_type} not mirrored)",
                "details": []}

    rhel_versions = rhel_versions or _RHEL_VERSIONS
    failures = []
    successes = []
    for arch in _ARCHES:
        for rhel in rhel_versions:
            url = _MIRROR_RPM[release_type].format(
                arch=arch, version=version, rhel=rhel
            )
            try:
                resp = _http_get(url, timeout=10)
                if resp.status_code == 200:
                    successes.append(f"{arch}/el{rhel}: OK")
                else:
                    failures.append(f"{arch}/el{rhel}: HTTP {resp.status_code}")
            except requests.RequestException as exc:
                failures.append(f"{arch}/el{rhel}: {exc}")

    if not failures:
        return {"valid": True, "reason": "RPMs present in all mirror locations",
                "details": successes}
    return {"valid": False, "reason": f"{len(failures)} mirror location(s) missing",
            "details": failures + successes}


def validate_bootc_mirror(version, release_type, rhel_versions=None):
    """Check that bootc-pullspec.txt is available in mirror repos for RC/EC.

    Args:
        version: Full version string, e.g., "4.22.0-rc.2".
        release_type: "RC" or "EC".
        rhel_versions: List of RHEL versions to check (default: [9, 10]).

    Returns:
        dict: {valid: bool, reason: str, pullspecs: dict, details: list[str]}
    """
    if release_type not in _MIRROR_BOOTC:
        return {"valid": True, "reason": f"N/A ({release_type} not mirrored)",
                "pullspecs": {}, "details": []}

    rhel_versions = rhel_versions or _RHEL_VERSIONS
    failures = []
    pullspecs = {}
    for arch in _ARCHES:
        for rhel in rhel_versions:
            url = _MIRROR_BOOTC[release_type].format(
                arch=arch, version=version, rhel=rhel
            )
            key = f"{arch}/el{rhel}"
            try:
                resp = _http_get(url, timeout=10)
                if resp.status_code == 200:
                    pullspecs[key] = resp.text.strip()
                else:
                    failures.append(f"{key}: HTTP {resp.status_code}")
            except requests.RequestException as exc:
                failures.append(f"{key}: {exc}")

    if not failures:
        return {"valid": True, "reason": "bootc-pullspec.txt present in all mirror locations",
                "pullspecs": pullspecs, "details": list(pullspecs.keys())}
    return {"valid": False, "reason": f"{len(failures)} mirror location(s) missing",
            "pullspecs": pullspecs, "details": failures}


_ARCH_MAP = {"x86_64": "amd64", "aarch64": "arm64"}


def _fetch_advisory_data(advisory_url):
    """Fetch advisory YAML and extract spec.type and per-arch image SHAs.

    Args:
        advisory_url: GitLab raw URL to the advisory YAML.

    Returns:
        dict: {"spec_type": str|None, "images": {arch: sha}} or None on failure.
    """
    import yaml as _yaml  # noqa: PLC0415
    try:
        resp = _http_get(advisory_url, verify=False, timeout=15)
        if resp.status_code != 200:
            logger.debug("Advisory YAML fetch returned HTTP %d: %s",
                         resp.status_code, advisory_url)
            return None
        content = _yaml.safe_load(resp.text)
    except (requests.RequestException, _yaml.YAMLError) as exc:
        logger.debug("Advisory YAML fetch/parse failed for %s: %s",
                     advisory_url, exc)
        return None

    spec = content.get("spec", {})
    images = spec.get("content", {}).get("images", [])
    image_shas = {}
    for img in images:
        comp = img.get("component", "")
        if "microshift-bootc" not in comp:
            continue
        arch = img.get("architecture")
        sha_match = re.search(r"@sha256:([0-9a-f]+)",
                              img.get("containerImage", ""))
        if arch and sha_match:
            rhel_match = re.search(r"rhel(\d+)", comp)
            if rhel_match:
                image_shas[f"{arch}/el{rhel_match.group(1)}"] = sha_match.group(1)
            else:
                image_shas[arch] = sha_match.group(1)

    return {
        "spec_type": spec.get("type"),
        "images": image_shas if image_shas else None,
    }


def validate_bootc_sha_match(version, release_type, shipment=None,
                             rhel_versions=None):
    """Verify mirror pullspec SHAs match the advisory YAML SHAs for both arches.

    Fetches bootc-pullspec.txt from the mirror for each architecture,
    then fetches the advisory YAML (from the shipment MR's stage advisory URL)
    and compares SHAs per architecture.

    Args:
        version: Full version string.
        release_type: "RC" or "EC".
        shipment: Pre-fetched shipment MR dict (avoids duplicate API calls).
        rhel_versions: List of RHEL versions to check (default: [9, 10]).

    Returns:
        dict: {valid: bool, reason: str, details: list[str]}
    """
    if release_type not in _MIRROR_BOOTC:
        return {"valid": True, "reason": f"N/A ({release_type} has no mirror bootc)"}

    rhel_versions = rhel_versions or _RHEL_VERSIONS

    # 1. Fetch mirror pullspecs for all arches and RHEL versions
    mirror_shas = {}
    for arch in _ARCHES:
        for rhel in rhel_versions:
            url = _MIRROR_BOOTC[release_type].format(
                arch=arch, version=version, rhel=rhel)
            try:
                resp = _http_get(url, timeout=10)
                if resp.status_code != 200:
                    continue
                m = re.search(r"@sha256:([0-9a-f]+)", resp.text)
                if m:
                    mirror_shas[f"{_ARCH_MAP[arch]}/el{rhel}"] = m.group(1)
            except requests.RequestException:
                continue

    if not mirror_shas:
        return {"valid": False,
                "reason": "Could not fetch bootc-pullspec.txt from mirror"}

    # 2. Get the advisory URL from the shipment MR
    if shipment is None:
        shipment = fetch_shipment_mr(version)
    if not shipment.get("found"):
        return {"valid": None,
                "reason": "Shipment MR not found — cannot compare SHAs"}

    advisory_url = shipment.get("stage_advisory_url")
    if not advisory_url:
        return {"valid": None,
                "reason": "No stage advisory URL in shipment YAML"}

    # 3. Fetch and parse the advisory YAML
    advisory_data = _fetch_advisory_data(advisory_url)
    if not advisory_data or not advisory_data.get("images"):
        return {"valid": None,
                "reason": "Could not fetch or parse advisory YAML"}

    advisory_shas = advisory_data["images"]

    # 4. Compare per arch/RHEL combination
    details = []
    mismatches = 0
    for key, mirror_sha in sorted(mirror_shas.items()):
        adv_sha = advisory_shas.get(key)
        if adv_sha is None:
            arch_only = key.split("/")[0]
            adv_sha = advisory_shas.get(arch_only)
        if adv_sha is None:
            details.append(f"{key}: not in advisory YAML")
            mismatches += 1
        elif mirror_sha == adv_sha:
            details.append(f"{key}: match ({mirror_sha[:12]})")
        else:
            details.append(
                f"{key}: MISMATCH mirror={mirror_sha[:12]} "
                f"advisory={adv_sha[:12]}"
            )
            mismatches += 1

    if mismatches == 0:
        return {"valid": True,
                "reason": "Both arches match advisory YAML",
                "details": details}
    return {"valid": False,
            "reason": f"{mismatches} arch(es) SHA mismatch",
            "details": details}


def _gitlab_headers():
    """Return GitLab API auth headers, or None if token not set."""
    token = os.environ.get("GITLAB_API_TOKEN")
    if not token:
        return None
    return {"PRIVATE-TOKEN": token}


def _gitlab_get(path):
    """Perform a GET against the internal GitLab API.

    Uses GITLAB_API_TOKEN if set, otherwise tries unauthenticated.

    Returns:
        requests.Response or None on failure.
    """
    headers = _gitlab_headers() or {}
    url = f"{_GITLAB_BASE}/api/v4/{path.lstrip('/')}"
    try:
        return _http_get(url, headers=headers, verify=False, timeout=15)
    except requests.RequestException as exc:
        logger.debug("GitLab API request failed for %s: %s", path, exc)
        return None


def _get_gitlab_project_id():
    """Look up the numeric ID for the ocp-shipment-data GitLab project.

    Returns:
        int or None
    """
    encoded = _SHIPMENT_PROJECT_PATH.replace("/", "%2F")
    resp = _gitlab_get(f"projects/{encoded}")
    if resp is None or resp.status_code != 200:
        return None
    try:
        return resp.json().get("id")
    except (json.JSONDecodeError, ValueError):
        logger.debug("GitLab project lookup returned non-JSON response")
        return None


def fetch_shipment_mr(version):
    """Search ocp-shipment-data GitLab repo for a MR matching this version.

    Requires GITLAB_API_TOKEN and VPN. Degrades gracefully if unavailable.

    Args:
        version: Full version string, e.g., "4.21.8", "4.22.0-rc.2".

    Returns:
        dict: {found, mr_iid, mr_title, yaml_file, yaml_content,
               image_sha, release_notes_solution, release_notes_type,
               stage_advisory_url, prod_advisory_url, reason}
    """
    project_id = _get_gitlab_project_id()
    if project_id is None:
        return {
            "found": False,
            "reason": f"No access to {_SHIPMENT_PROJECT_PATH} — check GITLAB_API_TOKEN permissions",
            "skipped": True,
        }

    # Search all MRs (open + merged) filtering by version in title
    search_version = version.replace("-", " ")
    mr_url = (f"projects/{project_id}/merge_requests"
              f"?state=all&search={search_version}"
              f"&order_by=updated_at&per_page=50")
    resp = _gitlab_get(mr_url)
    if resp is None or resp.status_code != 200:
        return {
            "found": False,
            "reason": f"GitLab MR search failed: "
                      f"{resp.status_code if resp else 'no response'}",
        }

    try:
        mrs = resp.json()
    except (json.JSONDecodeError, ValueError):
        return {
            "found": False,
            "reason": "GitLab MR search returned non-JSON response",
        }
    matching = [mr for mr in mrs
                if "Microshift-bootc shipment for" in mr.get("title", "")
                and mr.get("title", "").strip().endswith(version)]

    if not matching:
        return {
            "found": False,
            "reason": f"No shipment MR found for {version} in {_SHIPMENT_PROJECT_PATH}",
        }

    mr = matching[0]
    mr_iid = mr["iid"]
    result = {"found": True, "mr_iid": mr_iid, "mr_title": mr["title"],
              "state": mr.get("state"),
              "reason": f"MR !{mr_iid}: {mr['title']}"}

    # Fetch MR changes to find the YAML file
    changes_resp = _gitlab_get(f"projects/{project_id}/merge_requests/{mr_iid}/changes")
    if changes_resp is None or changes_resp.status_code != 200:
        result["yaml_file"] = None
        result["yaml_count"] = 0
        return result

    try:
        changes = changes_resp.json().get("changes", [])
    except (json.JSONDecodeError, ValueError):
        result["yaml_file"] = None
        result["yaml_count"] = 0
        return result
    yaml_files = [c["new_path"] for c in changes
                  if c["new_path"].endswith(".yaml") or c["new_path"].endswith(".yml")]
    result["yaml_count"] = len(yaml_files)
    result["yaml_file"] = yaml_files[0] if len(yaml_files) == 1 else None

    if result["yaml_file"]:
        source_branch = mr.get("source_branch", "main")
        if mr.get("state") == "merged":
            ref = mr.get("merge_commit_sha") or mr.get("sha") or source_branch
        else:
            ref = source_branch
        encoded_path = result["yaml_file"].replace("/", "%2F")
        file_resp = _gitlab_get(
            f"projects/{project_id}/repository/files/{encoded_path}/raw"
            f"?ref={ref}"
        )
        if file_resp and file_resp.status_code == 200:
            import yaml as _yaml  # noqa: PLC0415
            try:
                content = _yaml.safe_load(file_resp.text)
                result["yaml_content"] = content
                result.update(_parse_shipment_yaml(content))
            except Exception as exc:
                logger.warning("Failed to parse shipment YAML: %s", exc)
                result["yaml_content"] = None

    return result


def _parse_shipment_yaml(content):
    """Extract key fields from a shipment YAML dict.

    Returns:
        dict of field values (release_notes_solution, release_notes_type,
        stage_advisory_url, prod_advisory_url, image_sha)
    """
    if not isinstance(content, dict):
        return {}

    # The YAML nests everything under "shipment"
    ship = content.get("shipment", content)

    envs = ship.get("environments", {})
    stage_advisory = envs.get("stage", {}).get("advisory", {})
    prod_advisory = envs.get("prod", {}).get("advisory", {})

    data = ship.get("data", {})
    release_notes = data.get("releaseNotes", {})

    # Extract IMAGE_SHA from snapshot components
    image_sha = None
    snapshot = ship.get("snapshot", {}).get("spec", {})
    for component in snapshot.get("components", []):
        sha_match = re.search(r"@sha256:([0-9a-f]+)",
                              component.get("containerImage", ""))
        if sha_match:
            image_sha = sha_match.group(1)
            break

    metadata = ship.get("metadata", {})

    return {
        "release_notes_solution": release_notes.get("solution"),
        "release_notes_type": release_notes.get("type"),
        "stage_advisory_url": stage_advisory.get("internal_url"),
        "prod_advisory_url": prod_advisory.get("internal_url"),
        "image_sha": image_sha,
        "assembly": metadata.get("assembly"),
    }


def validate_shipment_yaml(shipment, release_type):
    """Validate key fields in a parsed shipment MR result.

    Args:
        shipment: dict from fetch_shipment_mr().
        release_type: "X", "Y", "Z", "RC", "EC".

    Returns:
        list[dict]: One result dict per sub-check with {check, valid, reason}.
    """
    results = []

    if not shipment.get("found"):
        return results

    def _check(check_id, valid, reason):
        results.append({"check": check_id, "valid": valid, "reason": reason})

    # Yaml count
    yaml_count = shipment.get("yaml_count", 0)
    _check("bootc_shipment_yaml_count",
           yaml_count == 1,
           f"{'1 YAML file' if yaml_count == 1 else f'{yaml_count} YAML files'} in MR")

    content = shipment.get("yaml_content")
    if content is None:
        return results

    # X/Y-only checks
    if release_type in ("X", "Y", "XY"):
        rn_type = shipment.get("release_notes_type")
        rn_ok = rn_type == "RHEA"
        _check("bootc_shipment_xy0_type", rn_ok,
               f"releaseNotes.type = {rn_type!r}"
               + ("" if rn_ok else " (expected RHEA)"))

        rn_solution = shipment.get("release_notes_solution")
        _check("bootc_shipment_xy0_release_notes", bool(rn_solution),
               "releaseNotes.solution present" if rn_solution else "releaseNotes.solution missing")

        advisory_url = (shipment.get("prod_advisory_url")
                        or shipment.get("stage_advisory_url"))
        if advisory_url:
            adv_data = _fetch_advisory_data(advisory_url)
            if adv_data:
                adv_type = adv_data.get("spec_type")
                adv_ok = adv_type == "RHEA"
                _check("bootc_prod_xy0_type", adv_ok,
                       f"advisory spec.type = {adv_type!r}"
                       + ("" if adv_ok else " (expected RHEA)"))
            else:
                _check("bootc_prod_xy0_type", None,
                       "Could not fetch advisory YAML")
        else:
            _check("bootc_prod_xy0_type", None,
                   "No advisory URL available")

        prod_url = shipment.get("prod_advisory_url")
        _check("bootc_prod_advisory_url",
               bool(prod_url),
               "prod advisory URL present" if prod_url else "prod advisory URL missing")

    # All types (X, Y, Z, RC, EC)
    stage_url = shipment.get("stage_advisory_url")
    _check("bootc_stage_advisory_url",
           bool(stage_url),
           "stage advisory URL present" if stage_url else "stage advisory URL missing")

    return results


def get_expected_packages(minor):
    """Get expected RPM package names from the microshift spec file.

    Uses ``git show`` to read the spec file from the correct release branch
    (``origin/release-{minor}``) so the package list matches the version
    being validated.

    Args:
        minor: Minor version string, e.g. "4.20".

    Returns:
        list of package name strings, or None on failure.
    """
    try:
        from . import git_ops  # noqa: PLC0415
        repo_path = git_ops.ensure_microshift_repo()
    except Exception as exc:
        logger.warning("Could not ensure microshift repo: %s", exc)
        return None

    branch = f"origin/release-{minor}"
    spec_ref = f"{branch}:packaging/rpm/microshift.spec"
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "show", spec_ref],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            logger.warning("git show %s failed: %s", spec_ref, result.stderr.strip())
            return None
        return _parse_spec_content(result.stdout)
    except Exception as exc:
        logger.warning("Failed to read spec file from %s: %s", branch, exc)
        return None


def _parse_spec_content(content):
    """Parse RPM package names from spec file content.

    Handles:
      - Base package: the top-level Name: field
      - Subpackage: %package subname  → {base}-{subname}
      - Renamed:    %package -n name  → name
    """
    lines = content.splitlines()

    base_name = None
    names = []
    for line in lines:
        stripped = line.strip()
        if base_name is None and re.match(r"^Name\s*:", stripped, re.IGNORECASE):
            base_name = stripped.split(":", 1)[1].strip()
            names.append(base_name)
            continue
        m = re.match(r"^%package\s+(.+)$", stripped)
        if m:
            args = m.group(1).split()
            if args[0] == "-n" and len(args) > 1:
                names.append(args[1])
            elif args[0] != "-n":
                if base_name is None:
                    continue
                names.append(f"{base_name}-{args[0]}")

    return list(dict.fromkeys(names)) if names else None
