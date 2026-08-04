#!/usr/bin/env python3
"""Validate Errata Tool RPM advisory promotion readiness — Phase 3.

QE sign-off checks for MicroShift RPM advisories (Errata Tool) before
shipping.  Requires VPN and a valid Kerberos ticket (kinit).

Usage: errata_promotion.py <version> <advisory_id> [--verbose] [--json]
"""

import argparse
import json
import logging
import sys
from collections import Counter

from lib import artifacts, errata
from validate_artifacts import (
    classify_version, _pass, _fail, _warn, _skip,
    _STATUS_EMOJI as _BASE_STATUS_EMOJI,
)

_STATUS_EMOJI = {**_BASE_STATUS_EMOJI, "SKIP": "⏭️"}

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

_CHECKS = [
    "et_advisory_exists",
    "et_advisory_type",
    "et_qa_owner",
    "et_bugs_verified",
    "et_rpms_present",
    "et_rpms_product_listed",
    "et_cdn_staging",
    "et_cat_tests",
    "et_status_rel_prep",
]

_QA_DEFAULT_OWNERS = {"default", "default qa", ""}

_EXPECTED_STATUS = "REL_PREP"


def _expected_types(version_info):
    if version_info["type"] == "XY":
        return ["RHEA"]
    return ["RHBA", "RHSA"]


# ── Checks ──────────────────────────────────────────────────────


def check_advisory_exists(advisory):
    """Advisory is fetchable from the Errata Tool."""
    check_id = "et_advisory_exists"
    if advisory is None:
        return _fail(check_id, "Advisory not found in Errata Tool")
    name = advisory.get("fulladvisory") or advisory.get("advisory_name") or "?"
    return _pass(check_id, f"Advisory found: {name}",
                 [f"ID: {advisory.get('id', '?')}"])


def check_advisory_type(advisory, version_info):
    """Advisory type matches expectations for this release."""
    check_id = "et_advisory_type"
    if advisory is None:
        return _warn(check_id, "Advisory data unavailable")

    errata_type = (advisory.get("errata_type")
                   or advisory.get("type", "")).upper()
    if not errata_type:
        return _fail(check_id, "No errata_type in advisory response")

    expected = _expected_types(version_info)
    if errata_type in expected:
        return _pass(check_id, f"{errata_type} (expected for {version_info['type']})",
                     [f"Expected: {' or '.join(expected)}"])
    return _fail(check_id,
                 f"{errata_type}, expected {' or '.join(expected)} for {version_info['type']}",
                 [f"Got: {errata_type}", f"Expected: {' or '.join(expected)}"])


def check_qa_owner(advisory):
    """QA ownership has been changed from the default."""
    check_id = "et_qa_owner"
    if advisory is None:
        return _warn(check_id, "Advisory data unavailable")

    qa_name = (advisory.get("quality_responsibility_name")
               or advisory.get("qe_group") or "")
    if qa_name:
        if qa_name.lower().strip() in _QA_DEFAULT_OWNERS:
            return _fail(check_id, f"QA owner is still default: {qa_name!r}",
                         ["Change QA ownership before proceeding"])
        return _pass(check_id, f"QA: {qa_name}")

    qa_id = advisory.get("quality_responsibility_id")
    if qa_id is not None and qa_id > 0:
        return _pass(check_id, f"QA responsibility set (ID: {qa_id})")
    return _fail(check_id, "QA ownership not set",
                 ["Change QA ownership before proceeding"])


def check_bugs_verified(bugs):
    """All OCPBUGS linked to the advisory are in Verified state."""
    check_id = "et_bugs_verified"
    if bugs is None:
        return _warn(check_id, "Could not fetch Jira issues from advisory")

    if not bugs:
        return _pass(check_id, "No bugs linked to advisory")

    accepted = {"verified", "closed", "release pending"}
    not_verified = [b for b in bugs
                    if b.get("status", "").lower() not in accepted]
    total = len(bugs)
    verified = total - len(not_verified)

    if not not_verified:
        return _pass(check_id, f"{verified}/{total} bugs in Verified state",
                     [b["key"] for b in bugs])

    details = [f"{b['key']}: {b.get('status', '?')}" for b in not_verified]
    return _fail(check_id,
                 f"{verified}/{total} verified — {len(not_verified)} not yet verified",
                 details)


def check_rpms_present(nvrs, version_info):
    """MicroShift RPMs are attached to the advisory."""
    check_id = "et_rpms_present"
    if not nvrs:
        return _fail(check_id, "No MicroShift RPMs found in advisory builds")

    packages = errata.extract_package_names(nvrs)
    expected = artifacts.get_expected_packages(version_info["minor"])

    if expected is None:
        return _warn(check_id,
                     f"{len(nvrs)} MicroShift NVR(s) found, "
                     f"but could not determine expected package list",
                     [f"Packages: {', '.join(sorted(packages))}",
                      f"NVRs: {', '.join(nvrs[:5])}"])

    expected_set = set(expected)
    missing = sorted(expected_set - packages)
    if not missing:
        return _pass(check_id,
                     f"{len(expected_set)}/{len(expected_set)} MicroShift RPMs in advisory",
                     [f"Packages: {', '.join(sorted(packages))}"])
    return _fail(check_id,
                 f"{len(missing)} MicroShift RPM(s) missing from advisory",
                 [f"Missing: {', '.join(missing)}",
                  f"Found: {', '.join(sorted(packages))}",
                  "Notify #forum-ocp-release / rel-eng if packages are missing"])


def check_rpms_product_listed(builds_data, nvrs):
    """All MicroShift NVRs are mapped to product version listings."""
    check_id = "et_rpms_product_listed"
    if not builds_data:
        return _warn(check_id, "Builds data unavailable")
    if not nvrs:
        return _warn(check_id, "No MicroShift NVRs to check")

    product_versions = []
    for pv, pv_data in builds_data.items():
        entries = pv_data
        if isinstance(pv_data, dict):
            entries = pv_data.get("builds", [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict):
                for nvr_key in entry:
                    if "microshift" in nvr_key.lower():
                        product_versions.append(pv)
                        break

    if product_versions:
        pv_set = sorted(set(product_versions))
        return _pass(check_id,
                     f"RPMs listed in {len(pv_set)} product version(s)",
                     pv_set)
    return _fail(check_id,
                 "MicroShift RPMs not mapped to any product version listing",
                 ["Notify #forum-ocp-release / rel-eng"])


def check_cdn_staging(advisory):
    """CDN staging push has been completed."""
    check_id = "et_cdn_staging"
    if advisory is None:
        return _warn(check_id, "Advisory data unavailable")

    status = (advisory.get("status") or "").upper()
    text_only = advisory.get("text_only", False)

    if text_only:
        return _skip(check_id, "Text-only advisory — no CDN push needed")

    content_types = advisory.get("content_types", [])
    push_count = advisory.get("pushcount") or advisory.get("push_count")

    if status in ("REL_PREP", "PUSH_READY", "IN_PUSH", "SHIPPED_LIVE"):
        return _pass(check_id, f"CDN staging push completed (status: {status})",
                     [f"Content types: {', '.join(content_types)}"]
                     if content_types else [])

    if push_count is not None and push_count > 0:
        return _pass(check_id, f"CDN push recorded ({push_count} push(es))")

    if status == "QE":
        return _warn(check_id,
                     "Advisory in QE — verify CDN staging push in Errata Tool UI",
                     ["Check 'CDN Repos' tab in the advisory"])
    return _warn(check_id,
                 f"Cannot determine CDN staging status (status: {status})",
                 ["Check 'CDN Repos' tab in the advisory"])


def check_cat_tests(advisory):
    """RHN QA / CAT testing is complete."""
    check_id = "et_cat_tests"
    if advisory is None:
        return _warn(check_id, "Advisory data unavailable")

    rhnqa = advisory.get("rhnqa")
    qa_complete = advisory.get("qa_complete")
    status = (advisory.get("status") or "").upper()

    if rhnqa == 1:
        return _pass(check_id, "RHN QA testing passed (rhnqa=1)")
    if qa_complete == 1:
        return _pass(check_id, "QA marked complete (qa_complete=1)")
    if status in ("REL_PREP", "PUSH_READY", "IN_PUSH", "SHIPPED_LIVE"):
        return _pass(check_id, f"QA passed (advisory status: {status})")
    if rhnqa == 0 and status == "QE":
        return _fail(check_id, "RHN QA testing not yet passed (rhnqa=0)",
                     ["Complete testing and mark RHN QA in the advisory"])
    return _warn(check_id,
                 f"Cannot determine QA test status (rhnqa={rhnqa}, status={status})",
                 ["Check 'Testing' tab in the Errata Tool UI"])


def check_status_rel_prep(advisory):
    """Advisory has been moved to REL_PREP status."""
    check_id = "et_status_rel_prep"
    if advisory is None:
        return _warn(check_id, "Advisory data unavailable")

    status = (advisory.get("status") or "").upper()
    if not status:
        return _fail(check_id, "No status field in advisory response")

    past_rel_prep = ("REL_PREP", "PUSH_READY", "IN_PUSH", "SHIPPED_LIVE")
    if status in past_rel_prep:
        return _pass(check_id, f"Status: {status}")

    if status == "QE":
        return _fail(check_id, f"Status: {status} — not yet moved to REL_PREP",
                     ["Move advisory to REL_PREP after all checks pass"])
    return _fail(check_id, f"Status: {status} — expected REL_PREP or later",
                 [f"Current: {status}",
                  f"Expected: {' / '.join(past_rel_prep)}"])


# ── Orchestrator ─────────────────────────────────────────────────


def run_errata_promotion_checks(version_info, advisory_id):
    """Run all Errata Tool promotion checks and return results."""
    logger.info("Authenticating with Errata Tool...")
    if not errata.check_auth():
        return [_fail(c, "Kerberos auth failed — run 'kinit' first") for c in _CHECKS]

    logger.info("Fetching advisory %s...", advisory_id)
    advisory = errata.fetch_advisory(advisory_id)

    if advisory is None:
        return [check_advisory_exists(None)] + [
            _skip(c, "Advisory not found") for c in _CHECKS[1:]
        ]

    logger.info("Fetching builds...")
    builds_data = errata.fetch_builds(advisory_id)

    nvrs = errata.extract_microshift_nvrs(builds_data)

    embedded_issues = advisory.get("_jira_issues")
    bugs = errata.extract_bug_keys(embedded_issues)

    logger.info("Running checks...")
    results = [
        check_advisory_exists(advisory),
        check_advisory_type(advisory, version_info),
        check_qa_owner(advisory),
        check_bugs_verified(bugs),
        check_rpms_present(nvrs, version_info),
        check_rpms_product_listed(builds_data, nvrs),
        check_cdn_staging(advisory),
        check_cat_tests(advisory),
        check_status_rel_prep(advisory),
    ]

    return results


# ── Formatting ───────────────────────────────────────────────────


def _section_line(title):
    return f"── {title} " + "─" * max(1, 60 - len(title) - 4)


_CHECK_SECTIONS = {
    "et_advisory_exists": "Advisory",
    "et_advisory_type": "Advisory",
    "et_qa_owner": "Advisory",
    "et_status_rel_prep": "Advisory",
    "et_bugs_verified": "Bugs",
    "et_rpms_present": "Builds",
    "et_rpms_product_listed": "Builds",
    "et_cdn_staging": "Distribution",
    "et_cat_tests": "Distribution",
}

_SECTION_ORDER = ["Advisory", "Bugs", "Builds", "Distribution"]


def format_text_short(version, advisory_id, results):
    """Format checks grouped by section."""
    max_id_len = max((len(r["check"]) for r in results), default=20)

    _ICON_DISPLAY_WIDTH = 2

    def _fmt_line(r):
        icon = _STATUS_EMOJI.get(r["status"], r["status"])
        cid = r["check"].ljust(max_id_len)
        lines = [f"{icon}  {cid}  {r['reason']}"]
        if r["status"] == "FAIL" and r.get("details"):
            pad = " " * (_ICON_DISPLAY_WIDTH + 2 + max_id_len + 2)
            for d in r["details"]:
                lines.append(f"{pad}{d}")
        return lines

    by_section = {}
    for r in results:
        section = _CHECK_SECTIONS.get(r["check"], "Other")
        by_section.setdefault(section, []).append(r)

    output = [f"Errata Tool Promotion: {version} ({advisory_id})", ""]
    for section in _SECTION_ORDER:
        section_results = by_section.get(section, [])
        if not section_results:
            continue
        output.append(_section_line(section))
        for r in section_results:
            output.extend(_fmt_line(r))
        output.append("")

    return "\n".join(output)


def format_text_full(version, advisory_id, results, version_info):
    """Format a detailed markdown report."""
    lines = [f"# Errata Tool Promotion: {version} ({version_info['type']})", ""]
    lines.append(f"**Advisory:** {advisory_id}")
    lines.append("")

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
        description="Validate Errata Tool RPM advisory promotion (Phase 3)"
    )
    parser.add_argument("version",
                        help="Version string, e.g., 4.18.3, 4.19.0")
    parser.add_argument("advisory",
                        help="Errata Tool advisory ID or name "
                             "(e.g. 12345, RHBA-2026:12345)")
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
        print("Expected formats: 4.18.3 | 4.19.0 | 4.19.0-ec.5 | 4.19.0-rc.2",
              file=sys.stderr)
        sys.exit(1)

    logger.info("Checking Errata Tool advisory %s for %s (%s)...",
                args.advisory, args.version, version_info["type"])

    results = run_errata_promotion_checks(version_info, args.advisory)

    if args.json_output:
        output = {
            "version": args.version,
            "type": version_info["type"],
            "minor": version_info["minor"],
            "advisory": args.advisory,
            "errata_checks": results,
        }
        print(json.dumps(output, indent=2))
        return

    if args.verbose:
        print(format_text_full(args.version, args.advisory, results, version_info))
    else:
        print(format_text_short(args.version, args.advisory, results))

    if any(r["status"] == "FAIL" for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
