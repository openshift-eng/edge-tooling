#!/usr/bin/env python3
"""Verify all artifacts and docs are publicly available after shipping — Phase 4.

Post-release checks for GA and z-stream MicroShift releases. Confirms
bootc images, RPMs, errata, documentation, and lifecycle page are
accessible to customers.

Usage: post_release.py <version> [--verbose] [--json]
"""

import argparse
import json
import logging
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from lib import artifacts, brew, errata, lifecycle, pyxis
from validate_artifacts import (
    classify_version, _minor_tuple, _pass, _fail, _warn, _skip,
    _STATUS_EMOJI as _BASE_STATUS_EMOJI, _BOOTC_MIN_MINOR,
)

_STATUS_EMOJI = {**_BASE_STATUS_EMOJI, "SKIP": "⏭️"}

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

_ARCHES = ["amd64", "arm64"]

_CHECKS_ERRATA = [
    "pr_errata_rpms_found",
    "pr_errata_rpms_shipped",
    "pr_errata_bootc_found",
    "pr_errata_bootc_shipped",
]

_CHECKS_BOOTC = [
    "pr_bootc_catalog_el9",
    "pr_bootc_catalog_el10",
]

_CHECKS_RPMS = [
    "pr_rpms_customer_portal",
    "pr_rpms_cdn",
]

_CHECKS_DOCS = [
    "pr_docs_published",
]

_CHECKS_LIFECYCLE = [
    "pr_lifecycle_listed",
    "pr_lifecycle_active",
]

_CHECK_SECTIONS = {
    "pr_errata_rpms_found": "Errata (RPMs)",
    "pr_errata_rpms_shipped": "Errata (RPMs)",
    "pr_errata_bootc_found": "Errata (Bootc)",
    "pr_errata_bootc_shipped": "Errata (Bootc)",
    "pr_bootc_catalog_el9": "Bootc Images",
    "pr_bootc_catalog_el10": "Bootc Images",
    "pr_rpms_customer_portal": "RPMs",
    "pr_rpms_cdn": "RPMs",
    "pr_docs_published": "Documentation",
    "pr_lifecycle_listed": "Lifecycle",
    "pr_lifecycle_active": "Lifecycle",
}

_SECTION_ORDER = [
    "Errata (RPMs)", "Errata (Bootc)",
    "Bootc Images", "RPMs", "Documentation", "Lifecycle",
]

_DOCS_RELEASE_NOTES_URL = (
    "https://docs.redhat.com/en/documentation/"
    "red_hat_build_of_microshift/{minor}/"
    "html-single/red_hat_build_of_microshift_release_notes/"
)


def _rhel_versions(version_info):
    """Return RHEL versions to check based on the MicroShift version."""
    minor = _minor_tuple(version_info["minor"])
    z = version_info["z"]
    if minor > (4, 22) or (minor == (4, 22) and z >= 2):
        return [9, 10]
    return [9]


def _has_bootc(version_info):
    """Return True if this version has Konflux bootc images (4.18+)."""
    return _minor_tuple(version_info["minor"]) >= _BOOTC_MIN_MINOR


def _all_check_ids(version_info):
    """Return the ordered list of check IDs applicable to this version."""
    ids = ["pr_errata_rpms_found", "pr_errata_rpms_shipped"]
    if _has_bootc(version_info):
        ids += ["pr_errata_bootc_found", "pr_errata_bootc_shipped"]
    rhel_vers = _rhel_versions(version_info)
    for bc in _CHECKS_BOOTC:
        rhel_match = re.search(r"el(\d+)", bc)
        if rhel_match and int(rhel_match.group(1)) not in rhel_vers:
            continue
        ids.append(bc)
    ids.extend(_CHECKS_RPMS)
    ids.extend(_CHECKS_DOCS)
    if version_info["z"] == 0:
        ids.extend(_CHECKS_LIFECYCLE)
    return ids


# ── Hydra errata search ────────────────────────────────────────


def _hydra_search(query, rows=20):
    """Run a Hydra KCS search and return the response docs list."""
    url = "https://access.redhat.com/hydra/rest/search/kcs"
    params = {
        "q": query,
        "start": 0,
        "rows": rows,
        "sort": "portal_publication_date desc",
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json().get("response", {}).get("docs", [])


def _parse_hydra_doc(doc):
    """Extract advisory info from a Hydra search result document."""
    synopsis = doc.get("portal_synopsis", "")
    doc_uri = doc.get("uri", doc.get("id", ""))
    advisory_match = re.search(r"(RH[A-Z]A-\d{4}:\d+)", doc_uri)
    advisory_name = advisory_match.group(1) if advisory_match else ""
    if not advisory_name:
        advisory_match = re.search(r"(RH[A-Z]A-\d{4}:\d+)", synopsis)
        advisory_name = advisory_match.group(1) if advisory_match else ""
    portal_url = doc_uri
    if not portal_url.startswith("http"):
        portal_url = f"https://access.redhat.com/errata/{advisory_name}"
    date = doc.get("portal_publication_date", "")[:10]
    return {
        "advisory_name": advisory_name,
        "portal_url": portal_url,
        "publication_date": date,
        "synopsis": synopsis,
    }


def find_rpms_errata(version, minor):
    """Search Hydra for the RPM errata matching this exact version."""
    try:
        docs = _hydra_search(
            f'"Red Hat build of MicroShift {minor}" documentKind:Errata'
        )
    except (requests.RequestException, json.JSONDecodeError) as e:
        logger.warning("Hydra RPM errata search failed for %s: %s", version, e)
        return {"error": str(e)}

    version_pattern = re.compile(rf"\b{re.escape(version)}\b")
    for doc in docs:
        synopsis = doc.get("portal_synopsis", "")
        if version_pattern.search(synopsis):
            return _parse_hydra_doc(doc)
    return None


def find_bootc_errata(version, minor, rpms_errata_date=None):
    """Search Hydra for the Konflux bootc image errata for this version.

    Bootc erratas use "image mode" in their synopsis and don't include
    the z-stream version, so we match by minor version and pick the most
    recent one published on or near the RPM errata date.
    """
    try:
        docs = _hydra_search(
            f'"microshift" "{minor}" "image mode" documentKind:Errata'
        )
    except (requests.RequestException, json.JSONDecodeError) as e:
        logger.warning("Hydra bootc errata search failed for %s: %s", version, e)
        return {"error": str(e)}

    candidates = []
    minor_pattern = re.compile(rf"\b{re.escape(minor)}\b")
    for doc in docs:
        synopsis = doc.get("portal_synopsis", "")
        doc_uri = doc.get("uri", doc.get("id", ""))
        if not minor_pattern.search(synopsis):
            continue
        if "image mode" not in synopsis.lower():
            continue
        candidates.append(doc)

    if not candidates:
        return None

    if rpms_errata_date and len(candidates) > 1:
        for doc in candidates:
            doc_date = doc.get("portal_publication_date", "")[:10]
            if doc_date == rpms_errata_date:
                return _parse_hydra_doc(doc)

    return _parse_hydra_doc(candidates[0])


# ── Check functions ────────────────────────────────────────────


def _check_errata_found(check_id, label, errata_info):
    """Public errata advisory exists for this version."""
    if errata_info is None:
        return _fail(check_id,
                     f"No public {label} errata found via Hydra search")
    if "error" in errata_info:
        return _warn(check_id, f"Hydra search failed: {errata_info['error']}",
                     ["The advisory may exist but could not be verified"])
    name = errata_info.get("advisory_name", "?")
    date = errata_info.get("publication_date", "?")
    return _pass(check_id, f"{name} (published {date})",
                 [f"URL: {errata_info.get('portal_url', '?')}",
                  f"Synopsis: {errata_info.get('synopsis', '?')}"])


def _check_errata_shipped(check_id, advisory, errata_info, vpn_ok):
    """Advisory has reached SHIPPED_LIVE status."""
    if not vpn_ok:
        if errata_info and not errata_info.get("error"):
            return _warn(check_id,
                         "VPN required — Hydra confirms advisory is public",
                         [f"Advisory: {errata_info.get('advisory_name', '?')}"])
        return _warn(check_id, "VPN required for Errata Tool status check")

    if advisory is None:
        advisory_name = (errata_info or {}).get("advisory_name", "?")
        return _warn(check_id,
                     f"Could not fetch advisory {advisory_name} from Errata Tool",
                     ["Check VPN and Kerberos (kinit)"])

    advisory_name = advisory.get("fulladvisory", "?")
    status = (advisory.get("status") or "").upper()
    if status == "SHIPPED_LIVE":
        return _pass(check_id, f"Status: {status}",
                     [f"Advisory: {advisory_name}"])
    if status in ("IN_PUSH", "PUSH_READY"):
        return _warn(check_id, f"Status: {status} — not yet SHIPPED_LIVE",
                     [f"Advisory: {advisory_name}",
                      "Shipping is in progress"])
    return _fail(check_id, f"Status: {status} — expected SHIPPED_LIVE",
                 [f"Advisory: {advisory_name}",
                  f"Current: {status}"])


def check_bootc_catalog(rhel, version_info):
    """Bootc image published in prod catalog for the given RHEL version."""
    check_id = f"pr_bootc_catalog_el{rhel}"
    minor = _minor_tuple(version_info["minor"])
    if minor < _BOOTC_MIN_MINOR:
        return _skip(check_id, f"N/A (bootc requires 4.18+, got {version_info['minor']})")

    version = version_info["version"]
    missing = []
    found = []
    for arch in _ARCHES:
        try:
            result = pyxis.check_catalog_image_graphql(version, "prod", arch=arch, rhel=rhel)
        except Exception as exc:
            logger.warning("Pyxis query failed for %s/%s/rhel%d: %s",
                           version, arch, rhel, exc)
            missing.append(f"{arch}: catalog query error ({exc})")
            continue
        if result.get("valid"):
            found.append(arch)
        else:
            missing.append(f"{arch}: {result.get('reason', 'not found')}")

    if not missing:
        return _pass(check_id,
                     f"Found in prod catalog ({' and '.join(found)})")
    if found:
        return _fail(check_id,
                     f"Missing arch(es) in prod catalog (rhel{rhel})",
                     [f"Found: {', '.join(found)}"] + missing)
    return _fail(check_id, f"Not found in prod catalog (rhel{rhel})",
                 missing)


def check_rpms_customer_portal(advisory, errata_info, version_info, vpn_ok):
    """RPMs listed in the advisory on the Red Hat Customer Portal."""
    check_id = "pr_rpms_customer_portal"

    # Check advisory page is accessible
    portal_url = (errata_info or {}).get("portal_url")
    if portal_url:
        try:
            resp = requests.get(portal_url, timeout=15, allow_redirects=True)
            if resp.status_code != 200:
                return _fail(check_id,
                             f"Advisory page returned HTTP {resp.status_code}",
                             [f"URL: {portal_url}"])
        except requests.RequestException as e:
            return _warn(check_id, f"Could not reach customer portal: {e}",
                         [f"URL: {portal_url}"])

    # Verify RPMs are attached to the advisory
    if not vpn_ok or advisory is None:
        if portal_url:
            return _warn(check_id, "Advisory page accessible (RPM list requires VPN)",
                         [f"URL: {portal_url}"])
        return _warn(check_id, "No advisory data available for RPM check")

    advisory_id = advisory.get("id")
    builds_data = errata.fetch_builds(advisory_id)
    nvrs = errata.extract_microshift_nvrs(builds_data) if builds_data else []
    if not nvrs:
        return _fail(check_id, "No MicroShift RPMs found in advisory",
                     [f"Advisory: {advisory.get('fulladvisory', '?')}"])

    found_packages = sorted(errata.extract_package_names(nvrs))

    expected = artifacts.get_expected_packages(version_info["minor"])
    details = [f"URL: {portal_url}"] if portal_url else []

    if expected is None:
        details.append(f"Packages: {', '.join(found_packages)}")
        return _warn(check_id,
                     f"{len(found_packages)} packages found (could not determine expected list)",
                     details)

    expected_set = set(expected)
    missing = sorted(expected_set - set(found_packages))
    if missing:
        details.append(f"Missing: {', '.join(missing)}")
        details.append(f"Found: {', '.join(found_packages)}")
        return _fail(check_id,
                     f"{len(missing)} package(s) missing from advisory",
                     details)

    details.append(f"Packages: {', '.join(found_packages)}")
    return _pass(check_id,
                 f"All {len(expected_set)} expected packages in advisory",
                 details)


def check_rpms_cdn(advisory, vpn_ok):
    """RPMs pushed to customer-facing CDN repos."""
    check_id = "pr_rpms_cdn"
    if not vpn_ok:
        return _warn(check_id, "VPN required for CDN push check")
    if advisory is None:
        return _warn(check_id, "No advisory data available for CDN check")

    advisory_id = advisory.get("id")
    advisory_name = advisory.get("fulladvisory", "?")
    if not advisory_id:
        return _warn(check_id, "No advisory numeric ID for CDN check")

    pushes = errata.fetch_push_status(advisory_id)
    if pushes is None:
        return _warn(check_id,
                     f"Could not fetch push status for {advisory_name}",
                     ["Check VPN and Kerberos (kinit)"])

    cdn_pushes = [p for p in pushes
                  if p.get("target", {}).get("name") == "cdn"]
    if not cdn_pushes:
        return _fail(check_id, "No CDN push found",
                     [f"Advisory: {advisory_name}"])

    all_complete = all(
        (p.get("status") or "").upper() == "COMPLETE" for p in cdn_pushes
    )
    if all_complete:
        return _pass(check_id,
                     f"CDN push complete ({len(cdn_pushes)} job(s))",
                     [f"Advisory: {advisory_name}"])
    statuses = [p.get("status", "?") for p in cdn_pushes]
    return _fail(check_id,
                 f"CDN push not complete: {', '.join(statuses)}",
                 [f"Advisory: {advisory_name}"])


def check_docs_published(version_info):
    """Release notes published on docs.redhat.com with version mentioned."""
    check_id = "pr_docs_published"
    minor = version_info["minor"]
    version = version_info["version"]
    url = _DOCS_RELEASE_NOTES_URL.format(minor=minor)

    try:
        resp = requests.get(url, timeout=15, allow_redirects=True)
        if resp.status_code != 200:
            return _fail(check_id,
                         f"Release notes page returned HTTP {resp.status_code}",
                         [f"URL: {url}"])
        if version in resp.text:
            return _pass(check_id, f"Version {version} found in release notes",
                         [f"URL: {url}"])
        return _fail(check_id,
                     f"Release notes page exists but {version} not mentioned",
                     [f"URL: {url}",
                      "The z-stream release notes may not be published yet"])
    except requests.RequestException as e:
        return _warn(check_id, f"Could not reach docs.redhat.com: {e}",
                     [f"URL: {url}"])


def check_lifecycle_listed(version_info, lifecycle_data):
    """Version listed in the Product Life Cycle page."""
    check_id = "pr_lifecycle_listed"
    if version_info["z"] != 0:
        return _skip(check_id, "N/A (lifecycle check is X.Y.0 only)")

    minor = version_info["minor"]
    if lifecycle_data is None:
        return _warn(check_id, "Could not fetch lifecycle data")

    entry = lifecycle.get_lifecycle_status(minor, lifecycle_data)
    if entry:
        return _pass(check_id, f"{minor} listed (phase: {entry['phase']})",
                     [f"End date: {entry.get('end_date', '?')}"])
    return _fail(check_id, f"{minor} not found in Product Life Cycle page",
                 ["https://access.redhat.com/product-life-cycles/"
                  "?product=Red%20Hat%20build%20of%20MicroShift"])


def check_lifecycle_active(version_info, lifecycle_data):
    """Lifecycle status is Full Support for a new X.Y.0 release."""
    check_id = "pr_lifecycle_active"
    if version_info["z"] != 0:
        return _skip(check_id, "N/A (lifecycle check is X.Y.0 only)")

    minor = version_info["minor"]
    if lifecycle_data is None:
        return _warn(check_id, "Could not fetch lifecycle data")

    entry = lifecycle.get_lifecycle_status(minor, lifecycle_data)
    if entry is None:
        return _warn(check_id, f"{minor} not found in lifecycle data")
    if entry["phase"] == "Full Support":
        return _pass(check_id, "Full Support",
                     [f"End date: {entry.get('end_date', '?')}"])
    return _fail(check_id, f"Phase: {entry['phase']} (expected Full Support)",
                 [f"End date: {entry.get('end_date', '?')}"])


# ── Orchestrator ───────────────────────────────────────────────


def run_post_release_checks(version_info):
    """Run all post-release checks and return results in canonical order."""
    all_ids = _all_check_ids(version_info)
    version = version_info["version"]
    minor = version_info["minor"]

    vpn_ok = brew.check_vpn()
    if not vpn_ok:
        logger.info("VPN not available — some checks will be skipped")

    has_bootc = _has_bootc(version_info)

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {}

        # Errata searches (Hydra — public internet)
        rpms_errata_future = ex.submit(find_rpms_errata, version, minor)
        bootc_errata_future = None
        if has_bootc:
            bootc_errata_future = ex.submit(
                find_bootc_errata, version, minor
            )

        # Bootc catalog checks
        futures[ex.submit(check_bootc_catalog, 9, version_info)] = \
            "pr_bootc_catalog_el9"
        rhel_vers = _rhel_versions(version_info)
        if 10 in rhel_vers:
            futures[ex.submit(check_bootc_catalog, 10, version_info)] = \
                "pr_bootc_catalog_el10"

        # Docs check
        futures[ex.submit(check_docs_published, version_info)] = \
            "pr_docs_published"

        # Lifecycle checks (X.Y.0 only)
        lifecycle_data = None
        if version_info["z"] == 0:
            logger.info("Fetching lifecycle data...")
            try:
                lifecycle_data = lifecycle.fetch_lifecycle_data()
            except Exception as e:
                logger.warning("Lifecycle fetch failed: %s", e)

        # Wait for RPM errata search
        logger.info("Searching for errata advisories...")
        try:
            rpms_errata_info = rpms_errata_future.result()
        except Exception as exc:
            logger.exception("RPM errata search raised unexpected error")
            rpms_errata_info = None

        # Wait for bootc errata search (re-run with date hint if available)
        bootc_errata_info = None
        if bootc_errata_future is not None:
            try:
                bootc_errata_info = bootc_errata_future.result()
            except Exception as exc:
                logger.exception("Bootc errata search raised unexpected error")
            # Re-search with RPM errata date for better matching
            rpms_date = (rpms_errata_info or {}).get("publication_date")
            if rpms_date and bootc_errata_info and not bootc_errata_info.get("error"):
                bootc_errata_info = find_bootc_errata(
                    version, minor, rpms_errata_date=rpms_date
                )

        # Fetch ET advisories (VPN)
        rpms_advisory = None
        if vpn_ok and rpms_errata_info and rpms_errata_info.get("advisory_name"):
            logger.info("Fetching RPM advisory from Errata Tool...")
            try:
                rpms_advisory = errata.fetch_advisory(
                    rpms_errata_info["advisory_name"]
                )
            except Exception as exc:
                logger.warning("Failed to fetch RPM advisory: %s", exc)

        bootc_advisory = None
        if (vpn_ok and has_bootc
                and bootc_errata_info
                and bootc_errata_info.get("advisory_name")):
            logger.info("Fetching bootc advisory from Errata Tool...")
            try:
                bootc_advisory = errata.fetch_advisory(
                    bootc_errata_info["advisory_name"]
                )
            except Exception as exc:
                logger.warning("Failed to fetch bootc advisory: %s", exc)

        # RPM errata checks
        futures[ex.submit(
            _check_errata_found, "pr_errata_rpms_found",
            "RPMs", rpms_errata_info
        )] = "pr_errata_rpms_found"
        futures[ex.submit(
            _check_errata_shipped, "pr_errata_rpms_shipped",
            rpms_advisory, rpms_errata_info, vpn_ok
        )] = "pr_errata_rpms_shipped"

        # Bootc errata checks
        if has_bootc:
            futures[ex.submit(
                _check_errata_found, "pr_errata_bootc_found",
                "bootc images", bootc_errata_info
            )] = "pr_errata_bootc_found"
            futures[ex.submit(
                _check_errata_shipped, "pr_errata_bootc_shipped",
                bootc_advisory, bootc_errata_info, vpn_ok
            )] = "pr_errata_bootc_shipped"

        # RPM checks (depend on RPM errata info)
        futures[ex.submit(check_rpms_customer_portal,
                          rpms_advisory, rpms_errata_info,
                          version_info, vpn_ok)] = \
            "pr_rpms_customer_portal"
        futures[ex.submit(check_rpms_cdn, rpms_advisory, vpn_ok)] = \
            "pr_rpms_cdn"

        # Lifecycle checks
        if version_info["z"] == 0:
            futures[ex.submit(check_lifecycle_listed,
                              version_info, lifecycle_data)] = \
                "pr_lifecycle_listed"
            futures[ex.submit(check_lifecycle_active,
                              version_info, lifecycle_data)] = \
                "pr_lifecycle_active"

        results = {}
        for future in as_completed(futures):
            check_id = futures[future]
            try:
                results[check_id] = future.result()
            except Exception as exc:
                logger.exception("Check %s raised unexpected error", check_id)
                results[check_id] = _fail(check_id, f"Unexpected error: {exc}")

    return [results[c] for c in all_ids if c in results]


# ── Formatting ─────────────────────────────────────────────────


def _section_line(title):
    return f"── {title} " + "─" * max(1, 60 - len(title) - 4)


def format_text_short(version, results):
    """Format checks grouped by section."""
    max_id_len = max((len(r["check"]) for r in results), default=20)

    _ICON_DISPLAY_WIDTH = 2

    def _fmt_line(r):
        icon = _STATUS_EMOJI.get(r["status"], r["status"])
        cid = r["check"].ljust(max_id_len)
        gap = " " if "️" in icon else "  "
        lines = [f"{icon}{gap}{cid}  {r['reason']}"]
        if r["status"] == "FAIL" and r.get("details"):
            pad = " " * (_ICON_DISPLAY_WIDTH + 2 + max_id_len + 2)
            for d in r["details"]:
                lines.append(f"{pad}{d}")
        return lines

    by_section = {}
    for r in results:
        section = _CHECK_SECTIONS.get(r["check"], "Other")
        by_section.setdefault(section, []).append(r)

    output = [f"Post-Release Verification: {version}", ""]
    for section in _SECTION_ORDER:
        section_results = by_section.get(section, [])
        if not section_results:
            continue
        output.append(_section_line(section))
        for r in section_results:
            output.extend(_fmt_line(r))
        output.append("")

    return "\n".join(output)


def format_text_full(version, results, version_info):
    """Format a detailed markdown report."""
    lines = [f"# Post-Release Verification: {version} ({version_info['type']})", ""]

    by_section = {}
    for r in results:
        section = _CHECK_SECTIONS.get(r["check"], "Other")
        by_section.setdefault(section, []).append(r)

    for section in _SECTION_ORDER:
        section_results = by_section.get(section, [])
        if not section_results:
            continue
        lines += [
            f"## {section}", "",
            "| Status | Check | Details |",
            "|--------|-------|---------|",
        ]
        for r in section_results:
            detail = "; ".join(r.get("details", [])) or r["reason"]
            icon = _STATUS_EMOJI.get(r["status"], r["status"])
            lines.append(f"| {icon} | `{r['check']}` | {detail} |")
        lines.append("")

    counts = Counter(r["status"] for r in results)
    summary_parts = [f"{v} {k}" for k, v in sorted(counts.items())]
    lines.append(f"**Summary:** {', '.join(summary_parts)}")
    return "\n".join(lines)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Verify post-release artifact availability (Phase 4)"
    )
    parser.add_argument("version",
                        help="Version string, e.g., 4.21.7, 4.22.0")
    parser.add_argument("--verbose", action="store_true",
                        help="Show detailed markdown report")
    parser.add_argument("--json", dest="json_output", action="store_true",
                        help="Output raw JSON")
    return parser.parse_args()


def main():
    args = parse_args()
    version_info = classify_version(args.version)
    if version_info is None:
        print(f"ERROR: Could not parse version string: {args.version!r}",
              file=sys.stderr)
        print("Expected formats: 4.21.7 | 4.22.0", file=sys.stderr)
        sys.exit(1)

    if version_info["type"] in ("EC", "RC"):
        print(f"ERROR: Post-release checks are only for GA/z-stream releases, "
              f"not {version_info['type']}: {args.version}",
              file=sys.stderr)
        sys.exit(1)

    if version_info["type"] == "nightly":
        print(f"ERROR: Post-release checks are not applicable to nightly builds: "
              f"{args.version}", file=sys.stderr)
        sys.exit(1)

    logger.info("Checking post-release artifacts for %s (%s)...",
                args.version, version_info["type"])

    results = run_post_release_checks(version_info)

    if args.json_output:
        output = {
            "version": args.version,
            "type": version_info["type"],
            "minor": version_info["minor"],
            "post_release_checks": results,
        }
        print(json.dumps(output, indent=2))
    elif args.verbose:
        print(format_text_full(args.version, results, version_info))
    else:
        print(format_text_short(args.version, results))

    if any(r["status"] == "FAIL" for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
