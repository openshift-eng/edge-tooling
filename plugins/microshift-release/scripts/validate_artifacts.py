#!/usr/bin/env python3
"""Validate MicroShift built artifacts (RPMs and bootc images) — Phase 1.

Checks that ART produced correct RPMs and bootc images for a given release.
Covers all release types: X/Y (GA), Z (z-stream), RC, EC, nightly.

Usage: validate_artifacts.py <version> [--verbose] [--json]
"""

import argparse
import json
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from lib import artifacts, brew, pyxis

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

# Minimum OCP version for bootc image checks
_BOOTC_MIN_MINOR = (4, 18)
# el10 builds required starting in 4.23 / 5.0
_EL10_MIN_MINOR = (4, 23)

# Check IDs in display order
_RPM_CHECKS = [
    "rpm_packages_list",
    "rpm_filename_format",
    "rpm_commit_id",
    "rpm_rhel_version",
    "rpm_mirror_ec",
    "rpm_mirror_rc",
    "rpm_xy0_commit_match",
]

_BOOTC_CHECKS = [
    "bootc_shipment_mr",
    "bootc_shipment_yaml_count",
    "bootc_shipment_xy0_type",
    "bootc_shipment_xy0_release_notes",
    "bootc_stage_advisory_url",
    "bootc_catalog",
    "bootc_prod_xy0_type",
    "bootc_prod_advisory_url",
    "bootc_image_sha_match",
    "bootc_mirror_ec",
    "bootc_mirror_rc",
]


def classify_version(version):
    """Classify a version string and extract its components.

    Args:
        version: e.g., "4.21.8", "4.22.0-ec.5", "4.22.0-rc.2", "4.22.0",
                 "4.21.0-0.nightly-2026-03-23-021947".

    Returns:
        dict: {
            type: "Z" | "XY" | "RC" | "EC" | "nightly",
            version: normalized version string,
            minor: "4.21",
            base: "4.21.8",
            z: int,
            ecrc_num: int (RC/EC only),
        }
    """
    # Nightly: 4.21.0-0.nightly-YYYY-MM-DD-HHMMSS
    nightly_match = re.match(
        r"(\d+)\.(\d+)\.(\d+)-0\.nightly-(\d{4}-\d{2}-\d{2}-\d{6})", version
    )
    if nightly_match:
        major, minor_num = nightly_match.group(1), nightly_match.group(2)
        return {
            "type": "nightly",
            "version": version,
            "minor": f"{major}.{minor_num}",
            "base": f"{major}.{minor_num}.0",
            "z": 0,
            "ecrc_num": None,
        }

    # EC: 4.22.0-ec.5
    ec_match = re.match(r"(\d+)\.(\d+)\.(\d+)-(ec)\.(\d+)$", version)
    if ec_match:
        major, minor_num, z_str, ecrc_num = (
            ec_match.group(1), ec_match.group(2),
            ec_match.group(3), int(ec_match.group(5))
        )
        return {
            "type": "EC",
            "version": version,
            "minor": f"{major}.{minor_num}",
            "base": f"{major}.{minor_num}.{z_str}",
            "z": int(z_str),
            "ecrc_num": ecrc_num,
        }

    # RC: 4.22.0-rc.2
    rc_match = re.match(r"(\d+)\.(\d+)\.(\d+)-(rc)\.(\d+)$", version)
    if rc_match:
        major, minor_num, z_str, ecrc_num = (
            rc_match.group(1), rc_match.group(2),
            rc_match.group(3), int(rc_match.group(5))
        )
        return {
            "type": "RC",
            "version": version,
            "minor": f"{major}.{minor_num}",
            "base": f"{major}.{minor_num}.{z_str}",
            "z": int(z_str),
            "ecrc_num": ecrc_num,
        }

    # GA / Z-stream: X.Y.Z
    ga_match = re.match(r"(\d+)\.(\d+)\.(\d+)$", version)
    if ga_match:
        major, minor_num, z_str = ga_match.group(1), ga_match.group(2), ga_match.group(3)
        z = int(z_str)
        release_type = "XY" if z == 0 else "Z"
        return {
            "type": release_type,
            "version": version,
            "minor": f"{major}.{minor_num}",
            "base": version,
            "z": z,
            "ecrc_num": None,
        }

    return None


def _minor_tuple(minor):
    """Convert "4.21" → (4, 21)."""
    parts = minor.split(".")
    return (int(parts[0]), int(parts[1]))


def _result(check, status, reason, details=None):
    return {"check": check, "status": status, "reason": reason,
            "details": details or []}


def _skip(check, reason="N/A for this release type"):
    return _result(check, "SKIP", reason)


def _pass(check, reason, details=None):
    return _result(check, "PASS", reason, details)


def _fail(check, reason, details=None):
    return _result(check, "FAIL", reason, details)


def _warn(check, reason, details=None):
    return _result(check, "WARN", reason, details)


def check_rpm_packages_list(version_info, build_info,
                            brew_packages=None, expected_packages=None):
    """rpm_packages_list: Verify NVR exists and all expected packages are built."""
    if not build_info.get("found"):
        return _fail("rpm_packages_list",
                     f"No Brew build found for {version_info['version']}")

    nvr = build_info["nvr"]

    if brew_packages is None:
        return _warn("rpm_packages_list",
                     f"NVR found ({nvr}) but could not retrieve package list from Brew")
    if expected_packages is None:
        return _warn("rpm_packages_list",
                     f"NVR found ({nvr}) but could not parse expected packages from spec file")

    missing = sorted(p for p in expected_packages if p not in brew_packages)
    if not missing:
        return _pass(
            "rpm_packages_list",
            f"All {len(expected_packages)} expected packages found in Brew",
            [f"NVR: {nvr}",
             f"Expected: {', '.join(sorted(expected_packages))}",
             f"Packages: {', '.join(sorted(brew_packages))}"],
        )
    return _fail(
        "rpm_packages_list",
        f"{len(missing)} package(s) missing from Brew build",
        [f"Missing: {', '.join(missing)}",
         f"Expected: {', '.join(sorted(expected_packages))}",
         f"Found: {', '.join(sorted(brew_packages))}"],
    )


def check_rpm_filename_format(version_info, build_info):
    """rpm_filename_format: Validate NVR against expected format."""
    if not build_info.get("found"):
        return _skip("rpm_filename_format", "No Brew build found")

    nvr = build_info["nvr"]
    release_type = version_info["type"]
    # Use the appropriate type key for validation
    type_key = release_type if release_type in ("RC", "EC", "nightly") else "Z"
    result = artifacts.validate_nvr_format(nvr, type_key)
    if result["valid"]:
        return _pass("rpm_filename_format", result["reason"])
    return _fail("rpm_filename_format", result["reason"])


def check_rpm_commit_id(version_info, build_info, vpn_ok):
    """rpm_commit_id: Verify commit is on the correct release branch."""
    if not build_info.get("found"):
        return _skip("rpm_commit_id", "No Brew build found")
    if not vpn_ok:
        return _warn("rpm_commit_id", "VPN required for git commit verification")

    commit = build_info.get("commit")
    if not commit:
        return _fail("rpm_commit_id", "Could not extract commit hash from NVR")

    minor = version_info["minor"]
    result = artifacts.validate_commit_on_branch(commit, minor)
    if result["valid"]:
        details = [f"Branch: origin/release-{minor}"]
        if result.get("commit_date"):
            details.append(f"Date: {result['commit_date']}")
        return _pass("rpm_commit_id",
                     f"Commit {commit} from {result.get('commit_date', '')} is on release-{minor}")
    return _fail("rpm_commit_id", result["reason"])


def check_rpm_rhel_version(version_info, build_info):
    """rpm_rhel_version: Check el9 and el10 builds are both present (el10 required 4.23+)."""
    if not build_info.get("found"):
        return _skip("rpm_rhel_version", "No Brew build found")

    require_el10 = _minor_tuple(version_info["minor"]) >= _EL10_MIN_MINOR
    result = artifacts.validate_rhel_builds(build_info, require_el10=require_el10)
    if result["valid"]:
        return _pass("rpm_rhel_version", result["reason"])
    return _fail("rpm_rhel_version", result["reason"],
                 [f"el9: {result['el9']}", f"el10: {result['el10']}"])


def _rhel_versions_for(minor):
    """Return the RHEL versions to check based on the minor version."""
    if _minor_tuple(minor) >= _EL10_MIN_MINOR:
        return [9, 10]
    return [9]


def check_rpm_mirror(version_info, release_type_key):
    """rpm_mirror_rc / rpm_mirror_ec: Check mirror.openshift.com availability."""
    check_id = f"rpm_mirror_{release_type_key.lower()}"
    vtype = version_info["type"]

    if vtype != release_type_key:
        return _skip(check_id, f"N/A ({vtype}, not {release_type_key})")

    rhel_versions = _rhel_versions_for(version_info["minor"])
    result = artifacts.validate_mirror_rpms(
        version_info["version"], release_type_key, rhel_versions
    )
    if result["valid"]:
        return _pass(check_id, result["reason"], result.get("details"))
    return _fail(check_id, result["reason"], result.get("details"))


def check_rpm_xy0_commit_match(version_info, build_info, vpn_ok):
    """rpm_xy0_commit_match: X.Y.0 commit must match the latest RC commit."""
    if version_info["type"] != "XY":
        return _skip("rpm_xy0_commit_match", f"N/A ({version_info['type']}, not X.Y.0)")

    if not vpn_ok:
        return _warn("rpm_xy0_commit_match", "VPN required for Brew RC lookup")

    if not build_info.get("found"):
        return _skip("rpm_xy0_commit_match", "No Brew build found for this version")

    xy0_commit = build_info.get("commit")
    if not xy0_commit:
        return _fail("rpm_xy0_commit_match", "Cannot extract commit from X.Y.0 NVR")

    minor = version_info["minor"]
    rc = brew.find_latest_rc(minor)

    if not rc.get("found"):
        return _warn("rpm_xy0_commit_match",
                     f"No RC found for {minor} to compare against")

    latest_rc_version = rc["version"]
    latest_rc_commit = rc["commit"]

    if xy0_commit == latest_rc_commit:
        return _pass("rpm_xy0_commit_match",
                     f"X.Y.0 commit {xy0_commit} matches {latest_rc_version}",
                     [f"RC: {latest_rc_version}", f"Commit: {xy0_commit}"])
    return _fail("rpm_xy0_commit_match",
                 f"MISMATCH: X.Y.0={xy0_commit}, {latest_rc_version}={latest_rc_commit}",
                 [f"X.Y.0 commit: {xy0_commit}",
                  f"{latest_rc_version} commit: {latest_rc_commit}"])


def run_rpm_checks(version_info):
    """Run all RPM artifact checks and return results."""
    vpn_ok = brew.check_vpn()
    if not vpn_ok:
        logger.warning("VPN not connected — Brew checks will be skipped or degraded")

    build_info = {"found": False}
    brew_packages = None
    expected_packages = None
    if vpn_ok:
        vtype = version_info["type"]
        logger.info("Fetching Brew build info for %s...", version_info["version"])
        if vtype in ("RC", "EC"):
            build_info = brew.get_build_info(version_info["version"], vtype)
        elif vtype == "nightly":
            # For nightly, look up via fetch_latest_nightly_builds
            nightly_builds = brew.fetch_latest_nightly_builds()
            minor = version_info["minor"]
            if minor in nightly_builds:
                nbuild = nightly_builds[minor]
                build_info = {
                    "found": True,
                    "nvr": nbuild["nvr"],
                    "build_date": nbuild["timestamp"][:10],
                    "commit": None,
                    "el9": True,  # assume present for nightly
                    "el10": False,
                }
                m = re.search(r"\.g([0-9a-f]+)\.", nbuild["nvr"])
                if m:
                    build_info["commit"] = m.group(1)
        else:
            build_info = brew.get_build_info(version_info["version"],
                                             "Z" if version_info["type"] == "Z" else "XY")

        if build_info.get("found") and vtype != "nightly":
            logger.info("Fetching build package list and spec file...")
            brew_packages = brew.get_build_packages(build_info["nvr"])
            expected_packages = artifacts.get_expected_packages(version_info["minor"])
    else:
        return [
            _warn("rpm_packages_list", "VPN required for Brew checks"),
            _warn("rpm_filename_format", "VPN required for Brew checks"),
            _warn("rpm_commit_id", "VPN required for Brew checks"),
            _warn("rpm_rhel_version", "VPN required for Brew checks"),
            check_rpm_mirror(version_info, "EC"),
            check_rpm_mirror(version_info, "RC"),
            _warn("rpm_xy0_commit_match", "VPN required for Brew checks"),
        ]

    def _run():
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = {
                ex.submit(
                    check_rpm_packages_list, version_info, build_info,
                    brew_packages, expected_packages,
                ): "rpm_packages_list",
                ex.submit(check_rpm_filename_format, version_info, build_info):
                    "rpm_filename_format",
                ex.submit(check_rpm_commit_id, version_info, build_info, vpn_ok):
                    "rpm_commit_id",
                ex.submit(check_rpm_rhel_version, version_info, build_info):
                    "rpm_rhel_version",
                ex.submit(check_rpm_mirror, version_info, "EC"): "rpm_mirror_ec",
                ex.submit(check_rpm_mirror, version_info, "RC"): "rpm_mirror_rc",
                ex.submit(check_rpm_xy0_commit_match, version_info, build_info, vpn_ok):
                    "rpm_xy0_commit_match",
            }
            results = {}
            for future in as_completed(futures):
                check_id = futures[future]
                try:
                    results[check_id] = future.result()
                except Exception as exc:
                    results[check_id] = _fail(check_id, f"Unexpected error: {exc}")

        # Return in canonical order
        return [results[c] for c in _RPM_CHECKS if c in results]

    return _run()


def run_bootc_checks(version_info):
    """Run all bootc artifact checks and return results."""
    minor = version_info["minor"]
    if _minor_tuple(minor) < _BOOTC_MIN_MINOR:
        return [_skip(c, f"N/A (bootc checks require 4.18+, version is {minor})")
                for c in _BOOTC_CHECKS]

    if version_info["type"] == "nightly":
        return [_skip(c, "N/A (nightly builds do not have bootc artifacts)")
                for c in _BOOTC_CHECKS]

    vtype = version_info["type"]
    version = version_info["version"]

    # Fetch shipment MR (shared across multiple checks)
    logger.info("Fetching shipment MR for %s...", version)
    shipment = artifacts.fetch_shipment_mr(version)

    _SHIPMENT_DEPENDENT = {"bootc_shipment_mr", "bootc_shipment_yaml_count",
                           "bootc_shipment_xy0_type", "bootc_shipment_xy0_release_notes",
                           "bootc_stage_advisory_url", "bootc_prod_xy0_type",
                           "bootc_prod_advisory_url"}

    if shipment.get("skipped"):
        shipment_results = [_warn(c, shipment["reason"])
                            for c in _BOOTC_CHECKS
                            if c in _SHIPMENT_DEPENDENT]
    elif not shipment.get("found"):
        shipment_results = [_fail("bootc_shipment_mr", shipment["reason"])]
        shipment_results += [_skip(c, "Shipment MR not found")
                             for c in _BOOTC_CHECKS
                             if c in _SHIPMENT_DEPENDENT and c != "bootc_shipment_mr"]
    else:
        shipment_results = [_pass("bootc_shipment_mr", shipment["reason"])]
        yaml_checks = artifacts.validate_shipment_yaml(shipment, vtype)
        for yc in yaml_checks:
            status = "PASS" if yc["valid"] else "FAIL"
            shipment_results.append(_result(yc["check"], status, yc["reason"]))

    # Add skips for X/Y-only checks if this is Z/RC/EC
    covered_checks = {r["check"] for r in shipment_results}
    xy_only = {"bootc_shipment_xy0_type", "bootc_shipment_xy0_release_notes",
               "bootc_prod_xy0_type", "bootc_prod_advisory_url"}
    for check_id in _BOOTC_CHECKS:
        if check_id in covered_checks:
            continue
        if check_id in xy_only and vtype not in ("X", "Y", "XY"):
            shipment_results.append(_skip(check_id, f"N/A ({vtype}, not X.Y.0)"))

    # Catalog check: stage for open shipment MRs, prod for merged/shipped, skip for EC/RC
    if vtype in ("EC", "RC"):
        catalog = None
    elif shipment.get("found") and not shipment.get("skipped"):
        catalog = "prod" if shipment.get("state") == "merged" else "stage"
    else:
        catalog = "prod"

    def _parallel_checks():
        with ThreadPoolExecutor(max_workers=4) as ex:
            futures = {}
            mirror_key = ex.submit(
                artifacts.validate_bootc_mirror, version, vtype,
                _rhel_versions_for(minor))
            futures[mirror_key] = "bootc_mirror_key"
            sha_key = ex.submit(
                artifacts.validate_bootc_sha_match, version, vtype,
                shipment, _rhel_versions_for(minor))
            futures[sha_key] = "bootc_image_sha_match"
            if catalog:
                cat_key = ex.submit(
                    pyxis.check_catalog_image, version, catalog)
                futures[cat_key] = "bootc_catalog"
            outcomes = {}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    outcomes[key] = future.result()
                except Exception as exc:
                    outcomes[key] = {"valid": False, "reason": f"Unexpected error: {exc}"}
            return outcomes

    outcomes = _parallel_checks()

    # Catalog check
    if catalog is None:
        catalog_result = _skip("bootc_catalog", f"N/A ({vtype}, not published to catalog)")
    else:
        sc = outcomes["bootc_catalog"]
        catalog_result = (_pass if sc["valid"] else _fail)(
            "bootc_catalog", sc["reason"])

    # Mirror checks
    mirror = outcomes["bootc_mirror_key"]
    ec_result = (_skip("bootc_mirror_ec", f"N/A ({vtype}, not EC)")
                 if vtype != "EC"
                 else (_pass if mirror["valid"] else _fail)(
                     "bootc_mirror_ec", mirror["reason"], mirror.get("details")))
    rc_result = (_skip("bootc_mirror_rc", f"N/A ({vtype}, not RC)")
                 if vtype != "RC"
                 else (_pass if mirror["valid"] else _fail)(
                     "bootc_mirror_rc", mirror["reason"], mirror.get("details")))

    # SHA match
    sha = outcomes["bootc_image_sha_match"]
    if vtype not in ("RC", "EC"):
        sha_result = _skip("bootc_image_sha_match", f"N/A ({vtype}, not RC/EC)")
    elif sha.get("valid") is None:
        sha_result = _warn("bootc_image_sha_match", sha["reason"])
    else:
        sha_result = (_pass if sha["valid"] else _fail)(
            "bootc_image_sha_match", sha["reason"],
            sha.get("details"))

    # Assemble in canonical order
    extra_by_id = {r["check"]: r for r in [
        catalog_result, ec_result, rc_result, sha_result
    ]}
    shipment_by_id = {r["check"]: r for r in shipment_results}
    all_by_id = {**shipment_by_id, **extra_by_id}

    return [all_by_id[c] for c in _BOOTC_CHECKS if c in all_by_id]


_STATUS_EMOJI = {
    "PASS": "✅",
    "FAIL": "❌",
    "SKIP": "⏩",
    "WARN": "⚠️ ",
}


def format_text_short(version, rpm_results, bootc_results):
    """Format one line per check: EMOJI  check_id  reason. SKIPs hidden."""
    rpm_visible = [r for r in rpm_results if r["status"] != "SKIP"]
    bootc_visible = [r for r in bootc_results if r["status"] != "SKIP"]
    all_visible = rpm_visible + bootc_visible
    max_id_len = max((len(r["check"]) for r in all_visible), default=20)

    def _fmt(results):
        lines = []
        for r in results:
            icon = _STATUS_EMOJI.get(r["status"], r["status"])
            check_id = r["check"].ljust(max_id_len)
            lines.append(f"{icon}  {check_id}  {r['reason']}")
            if r["status"] == "FAIL" and r.get("details"):
                pad = " " * (len(icon) + 2 + max_id_len + 2)
                for d in r["details"]:
                    lines.append(f"{pad}{d}")
        return lines

    skip_count = sum(1 for r in rpm_results + bootc_results if r["status"] == "SKIP")
    footer = [f"({skip_count} checks skipped — not applicable)"] if skip_count else []

    return "\n".join([
        f"Validating {version}",
        "",
        "── RPM ──────────────────────────────────────────────────────",
        *_fmt(rpm_visible),
        "",
        "── Bootc ────────────────────────────────────────────────────",
        *_fmt(bootc_visible),
        "",
        *footer,
    ])


def format_text_full(version, version_info, rpm_results, bootc_results):
    """Format a detailed markdown report."""
    lines = [
        f"# Artifact Validation: {version} ({version_info['type']})",
        "",
        "## RPM Checks",
        "",
        "| Status | Check | Details |",
        "|--------|-------|---------|",
    ]
    for r in rpm_results:
        detail = "; ".join(r.get("details", [])) or r["reason"]
        icon = _STATUS_EMOJI.get(r["status"], r["status"])
        lines.append(f"| {icon} | `{r['check']}` | {detail} |")

    lines += [
        "",
        "## Bootc Image Checks",
        "",
        "| Status | Check | Details |",
        "|--------|-------|---------|",
    ]
    for r in bootc_results:
        detail = "; ".join(r.get("details", [])) or r["reason"]
        icon = _STATUS_EMOJI.get(r["status"], r["status"])
        lines.append(f"| {icon} | `{r['check']}` | {detail} |")

    counts = {}
    for r in rpm_results + bootc_results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    summary_parts = [f"{v} {k}" for k, v in sorted(counts.items())]
    lines += ["", f"**Summary:** {', '.join(summary_parts)}"]

    return "\n".join(lines)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Validate MicroShift built artifacts (Phase 1)"
    )
    parser.add_argument("version", help="Version string, e.g., 4.21.8, 4.22.0-ec.5")
    parser.add_argument("--verbose", action="store_true",
                        help="Show detailed markdown report")
    parser.add_argument("--json", dest="json_output", action="store_true",
                        help="Output raw JSON")
    return parser.parse_args()


def main():
    args = parse_args()
    version_info = classify_version(args.version)
    if version_info is None:
        print(f"ERROR: Could not parse version string: {args.version!r}", file=sys.stderr)
        print("Expected formats: 4.21.8 | 4.22.0 | 4.22.0-ec.5 | 4.22.0-rc.2", file=sys.stderr)
        sys.exit(1)

    logger.info("Validating %s artifacts (%s)...", args.version, version_info["type"])

    rpm_results = run_rpm_checks(version_info)
    bootc_results = run_bootc_checks(version_info)

    if args.json_output:
        output = {
            "version": args.version,
            "type": version_info["type"],
            "minor": version_info["minor"],
            "rpm_checks": rpm_results,
            "bootc_checks": bootc_results,
        }
        print(json.dumps(output, indent=2))
        return

    if args.verbose:
        print(format_text_full(args.version, version_info, rpm_results, bootc_results))
    else:
        print(format_text_short(args.version, rpm_results, bootc_results))

    # Exit non-zero if any check failed
    all_results = rpm_results + bootc_results
    if any(r["status"] == "FAIL" for r in all_results):
        sys.exit(1)


if __name__ == "__main__":
    main()
