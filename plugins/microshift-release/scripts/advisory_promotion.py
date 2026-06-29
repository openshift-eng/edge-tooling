#!/usr/bin/env python3
"""Validate Konflux bootc advisory promotion readiness — Phase 3.

QE sign-off checks for bootc image advisories before shipping.
Every check is atomic and split per image variant (arch + RHEL version).

Usage: advisory_promotion.py <version> [--verbose] [--json]
"""

import argparse
import json
import logging
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from lib import artifacts, brew, pyxis
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

_EXPECTED_REPO_TEMPLATE = "registry.stage.redhat.io/openshift{major}/microshift-bootc-rhel{rhel}"

_PER_VARIANT_CHECKS = [
    "advisory_image_present",
    "advisory_repository",
    "advisory_image_sha",
    "catalog_stage_present",
    "catalog_stage_tag_commit",
    "catalog_stage_tag_date",
    "catalog_stage_no_xy0_tag",
    "catalog_stage_chi",
    "catalog_prod_present",
    "catalog_prod_tag_commit",
    "catalog_prod_tag_date",
    "catalog_prod_no_xy0_tag",
    "catalog_prod_chi",
]

_GLOBAL_CHECKS = [
    "advisory_type",
    "shipment_type",
    "shipment_filename",
    "shipment_nvr_commit",
    "advisory_sha_distinct_el9",
    "advisory_sha_distinct_el10",
    "shipment_mr_approved",
]


def _rhel_versions(version_info):
    """Return RHEL versions to check based on the MicroShift version."""
    minor = _minor_tuple(version_info["minor"])
    z = version_info["z"]
    if minor > (4, 22) or (minor == (4, 22) and z >= 2):
        return [9, 10]
    return [9]


def _variants(version_info):
    """Return list of (arch, rhel) tuples for this version."""
    return [(arch, rhel) for arch in _ARCHES for rhel in _rhel_versions(version_info)]


def _variant_key(arch, rhel):
    """Check ID prefix for an image variant, e.g. 'amd64_el9'."""
    return f"{arch}_el{rhel}"


def _all_check_ids(version_info):
    rhel_vers = _rhel_versions(version_info)
    ids = [f"{_variant_key(arch, rhel)}_{suffix}"
           for arch, rhel in _variants(version_info)
           for suffix in _PER_VARIANT_CHECKS]
    for gc in _GLOBAL_CHECKS:
        rhel_match = re.search(r"el(\d+)", gc)
        if rhel_match and int(rhel_match.group(1)) not in rhel_vers:
            continue
        ids.append(gc)
    return ids


def _expected_repo(rhel, major=4):
    return _EXPECTED_REPO_TEMPLATE.format(major=major, rhel=rhel)


def _get_advisory_image(advisory_details, arch, rhel):
    """Get the advisory image entry for a specific arch/rhel, or None."""
    if advisory_details is None or not advisory_details.get("images"):
        return None
    target_key = f"{arch}/el{rhel}"
    for img in advisory_details["images"]:
        if img.get("arch_key") == target_key:
            return img
    # Fallback: match on architecture alone (for advisory YAMLs without RHEL in component)
    if rhel == 9:
        for img in advisory_details["images"]:
            if img.get("architecture") == arch and "el" not in img.get("arch_key", ""):
                return img
    return None


def _get_assembly_tag(catalog_result):
    """Extract the assembly tag and image metadata from a catalog result."""
    image_meta = catalog_result.get("image") if catalog_result else None
    if image_meta is None:
        return None, None
    for t in image_meta.get("tags", []):
        if "assembly" in t.get("name", ""):
            return t["name"], image_meta
    return None, image_meta


# ── Per-variant checks ───────────────────────────────────────────


def check_in_advisory(vk, arch, rhel, advisory_details):
    """Image variant is present in advisory spec.content.images."""
    check_id = f"{vk}_advisory_image_present"
    img = _get_advisory_image(advisory_details, arch, rhel)
    if img is None:
        if advisory_details is None or not advisory_details.get("images"):
            return _fail(check_id, "Could not fetch or parse advisory YAML")
        return _fail(check_id, f"{arch}/el{rhel} not found in advisory")
    return _pass(check_id, f"{arch}/el{rhel} present",
                 [f"Component: {img.get('component', '?')}"])


def check_repository(vk, arch, rhel, major, advisory_details):
    """Image references the correct stage registry repository."""
    check_id = f"{vk}_advisory_repository"
    img = _get_advisory_image(advisory_details, arch, rhel)
    if img is None:
        return _warn(check_id, f"No {arch}/el{rhel} image in advisory")
    repo = img.get("repo", "")
    expected = _expected_repo(rhel, major)
    if repo == expected:
        return _pass(check_id, expected)
    return _fail(check_id, f"Wrong repository: {repo}",
                 [f"Expected: {expected}", f"Got: {repo}"])


def check_image_sha(vk, arch, rhel, advisory_details):
    """Advisory contains a non-empty image SHA for this variant."""
    check_id = f"{vk}_advisory_image_sha"
    img = _get_advisory_image(advisory_details, arch, rhel)
    if img is None:
        return _warn(check_id, f"No {arch}/el{rhel} image in advisory")
    sha = img.get("sha")
    if not sha:
        return _fail(check_id, f"No SHA found for {arch}/el{rhel}")
    return _pass(check_id, f"sha256:{sha[:12]}",
                 [f"Full: sha256:{sha}"])


def check_in_catalog(vk, catalog_env, catalog_result, version_info, phase="stage"):
    """Image is published in the specified catalog (uses prefetched result)."""
    check_id = f"{vk}_catalog_{catalog_env}_present"
    if catalog_env == "prod" and phase == "stage":
        return _skip(check_id, "N/A (stage mode)")
    if catalog_env == "prod" and version_info["type"] in ("EC", "RC"):
        return _skip(check_id, f"N/A ({version_info['type']} not shipped to prod)")
    if catalog_result.get("valid"):
        return _pass(check_id, f"Found in {catalog_env} catalog")
    return _fail(check_id, f"Not found in {catalog_env} catalog",
                 [catalog_result.get("reason", "")])


def _catalog_not_fetched(catalog_result):
    """True when catalog data was not fetched (empty dict from stage-only mode)."""
    return not catalog_result or (not catalog_result.get("valid") and not catalog_result.get("reason"))


def check_tag_commit_id(vk, catalog_env, catalog_result):
    """Assembly tag commit hash matches the catalog image's source commit."""
    check_id = f"{vk}_catalog_{catalog_env}_tag_commit"
    if _catalog_not_fetched(catalog_result):
        return _skip(check_id, f"N/A ({catalog_env} not queried)")
    assembly_tag, image_meta = _get_assembly_tag(catalog_result)
    if image_meta is None:
        return _warn(check_id, "Catalog image metadata unavailable")
    if assembly_tag is None:
        return _fail(check_id, "No assembly tag found on catalog image")

    commit_match = re.search(r"\.g([0-9a-f]+)\.", assembly_tag)
    if not commit_match:
        return _fail(check_id, "No commit hash in assembly tag",
                     [f"Tag: {assembly_tag}"])

    tag_commit = commit_match.group(1)
    catalog_commit = image_meta.get("commit_id") or image_meta.get("commit_short")
    if not catalog_commit:
        return _warn(check_id, f"Commit {tag_commit} in tag, no catalog metadata to compare",
                     [f"Tag: {assembly_tag}"])

    if catalog_commit.startswith(tag_commit) or tag_commit.startswith(catalog_commit):
        return _pass(check_id, f"Commit {tag_commit} matches catalog",
                     [f"Tag: {assembly_tag}", f"Catalog: {catalog_commit[:12]}"])
    return _fail(check_id, f"Commit mismatch: tag={tag_commit} catalog={catalog_commit[:12]}",
                 [f"Tag: {assembly_tag}"])


def check_tag_build_date(vk, catalog_env, catalog_result):
    """Assembly tag contains a valid build date timestamp."""
    check_id = f"{vk}_catalog_{catalog_env}_tag_date"
    if _catalog_not_fetched(catalog_result):
        return _skip(check_id, f"N/A ({catalog_env} not queried)")
    assembly_tag, image_meta = _get_assembly_tag(catalog_result)
    if image_meta is None:
        return _warn(check_id, "Catalog image metadata unavailable")
    if assembly_tag is None:
        return _fail(check_id, "No assembly tag found on catalog image")

    date_match = re.search(r"v[\d.]+-(\d{12})\.", assembly_tag)
    if not date_match:
        return _fail(check_id, "No build date in assembly tag",
                     [f"Tag: {assembly_tag}"])

    ts = date_match.group(1)
    year, month, day = int(ts[:4]), int(ts[4:6]), int(ts[6:8])
    hour, minute = int(ts[8:10]), int(ts[10:12])
    if not (2020 <= year <= 2099 and 1 <= month <= 12 and 1 <= day <= 31
            and 0 <= hour <= 23 and 0 <= minute <= 59):
        return _fail(check_id, f"Invalid timestamp {ts}",
                     [f"Tag: {assembly_tag}"])

    formatted = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[8:10]}:{ts[10:12]}"
    return _pass(check_id, formatted, [f"Tag: {assembly_tag}"])


def check_no_xy0_tag(vk, catalog_env, catalog_result, version_info):
    """For z-streams, verify no X.Y.0 assembly tag on this variant's image."""
    check_id = f"{vk}_catalog_{catalog_env}_no_xy0_tag"
    if _catalog_not_fetched(catalog_result):
        return _skip(check_id, f"N/A ({catalog_env} not queried)")
    if version_info["type"] != "Z":
        return _skip(check_id, f"N/A ({version_info['type']}, not z-stream)")
    if version_info["z"] == 0:
        return _skip(check_id, "N/A (X.Y.0, not z-stream)")

    _, image_meta = _get_assembly_tag(catalog_result)
    if image_meta is None:
        return _warn(check_id, "Catalog image metadata unavailable")

    tags = image_meta.get("tags", [])
    minor = version_info["minor"]
    xy0_pattern = re.compile(rf"assembly\.{re.escape(minor)}\.0\b")
    xy0_tags = [t.get("name", "") for t in tags if xy0_pattern.search(t.get("name", ""))]
    if xy0_tags:
        return _fail(check_id, f"Found {minor}.0 tag on image",
                     [f"Tags: {', '.join(xy0_tags)}"])
    return _pass(check_id, f"No {minor}.0 tags ({len(tags)} checked)")


def check_chi_freshness(vk, catalog_env, catalog_result):
    """Container Health Index grade is acceptable for promotion."""
    check_id = f"{vk}_catalog_{catalog_env}_chi"
    if _catalog_not_fetched(catalog_result):
        return _skip(check_id, f"N/A ({catalog_env} not queried)")
    if not catalog_result or not catalog_result.get("image"):
        return _warn(check_id, "Catalog image metadata unavailable")

    image_meta = catalog_result["image"]
    grade = image_meta.get("freshness_grade")
    if not grade:
        return _warn(check_id, "No CHI grade available")

    if grade == "A":
        return _pass(check_id, f"CHI grade {grade}")
    return _fail(check_id, f"CHI grade {grade} (expected A)",
                 ["Container health has degraded — review before promotion"])


# ── Global checks ────────────────────────────────────────────────


def check_advisory_type(advisory_details, version_info):
    """Advisory YAML spec.type matches expected type for this release."""
    check_id = "advisory_type"
    if advisory_details is None:
        return _warn(check_id, "Advisory data unavailable")

    spec_type = advisory_details.get("spec_type")
    if not spec_type:
        return _fail(check_id, "No spec.type found in advisory YAML")

    expected = _expected_types(version_info)
    if spec_type in expected:
        return _pass(check_id, f"spec.type = {spec_type}",
                     [f"Expected: {' or '.join(expected)} for {version_info['type']}"])
    return _fail(check_id,
                 f"spec.type = {spec_type}, expected {' or '.join(expected)}",
                 [f"Got: {spec_type}", f"Expected: {' or '.join(expected)}"])


def check_shipment_filename(shipment, version_info):
    """Shipment YAML filename matches expected path pattern."""
    check_id = "shipment_filename"
    if shipment.get("skipped") or not shipment.get("found"):
        return _warn(check_id, "Shipment MR unavailable")

    yaml_file = shipment.get("yaml_file")
    if not yaml_file:
        return _fail(check_id, "No YAML file found in shipment MR")

    version = version_info["version"]
    minor = version_info["minor"]
    minor_dash = minor.replace(".", "-")

    expected_prefix = f"shipment/ocp/openshift-{minor}/openshift-{minor_dash}/"
    if not yaml_file.startswith(expected_prefix):
        return _fail(check_id, f"Unexpected path: {yaml_file}",
                     [f"Expected prefix: {expected_prefix}"])

    if "microshift-bootc" not in yaml_file:
        return _fail(check_id, f"Filename missing 'microshift-bootc': {yaml_file}")

    base_version = re.sub(r"-(ec|rc)\.\d+$", "", version)
    if f"/{base_version}." not in yaml_file and f"/{version}." not in yaml_file:
        return _fail(check_id, f"Filename missing version {version}: {yaml_file}")

    return _pass(check_id, yaml_file)


def check_shipment_nvr_commit(shipment, version_info):
    """Shipment NVR commit matches the Brew RPM build commit."""
    check_id = "shipment_nvr_commit"
    if shipment.get("skipped") or not shipment.get("found"):
        return _warn(check_id, "Shipment MR unavailable")

    content = shipment.get("yaml_content")
    if not content:
        return _warn(check_id, "No shipment YAML content")

    ship = content.get("shipment", content)
    nvrs = ship.get("snapshot", {}).get("nvrs", [])
    if not nvrs:
        return _warn(check_id, "No NVRs in shipment snapshot")

    shipment_commit = None
    shipment_nvr = None
    for nvr in nvrs:
        m = re.search(r"\.g([0-9a-f]+)\.", nvr)
        if m:
            shipment_commit = m.group(1)
            shipment_nvr = nvr
            break

    if not shipment_commit:
        return _fail(check_id, "No commit hash found in shipment NVRs",
                     [f"NVRs: {', '.join(nvrs)}"])

    vpn_ok = brew.check_vpn()
    if not vpn_ok:
        return _warn(check_id,
                     f"Shipment NVR commit {shipment_commit}, VPN required for Brew comparison",
                     [f"Shipment NVR: {shipment_nvr}"])

    vtype = version_info["type"]
    brew_type = vtype if vtype in ("RC", "EC", "XY") else "Z"
    build_info = brew.get_build_info(version_info["version"], brew_type)
    if not build_info.get("found"):
        return _warn(check_id,
                     f"Shipment NVR commit {shipment_commit}, Brew build not found",
                     [f"Shipment NVR: {shipment_nvr}"])

    brew_commit = build_info.get("commit")
    if not brew_commit:
        return _warn(check_id, "No commit in Brew build NVR",
                     [f"Brew NVR: {build_info.get('nvr')}"])

    if shipment_commit == brew_commit:
        return _pass(check_id,
                     f"Commit {shipment_commit} matches Brew",
                     [f"Shipment NVR: {shipment_nvr}",
                      f"Brew NVR: {build_info['nvr']}"])

    return _fail(check_id,
                 f"Commit mismatch: shipment={shipment_commit} brew={brew_commit}",
                 [f"Shipment NVR: {shipment_nvr}",
                  f"Brew NVR: {build_info['nvr']}"])


def _expected_types(version_info):
    if version_info["type"] == "XY":
        return ["RHEA"]
    return ["RHBA", "RHSA"]


def check_shipment_type(shipment, version_info):
    """Shipment YAML releaseNotes.type matches expected advisory type."""
    check_id = "shipment_type"
    if shipment.get("skipped") or not shipment.get("found"):
        return _warn(check_id, "Shipment MR unavailable")

    rn_type = shipment.get("release_notes_type")
    if not rn_type:
        return _warn(check_id, "No releaseNotes.type in shipment YAML")

    expected = _expected_types(version_info)
    if rn_type in expected:
        return _pass(check_id, f"releaseNotes.type = {rn_type}",
                     [f"Expected: {' or '.join(expected)} for {version_info['type']}"])
    return _fail(check_id,
                 f"releaseNotes.type = {rn_type}, expected {' or '.join(expected)}",
                 [f"Got: {rn_type}", f"Expected: {' or '.join(expected)}"])


def check_image_sha_distinct(rhel, advisory_details):
    """amd64 and arm64 advisory SHAs are different for this RHEL version."""
    check_id = f"advisory_sha_distinct_el{rhel}"
    amd_img = _get_advisory_image(advisory_details, "amd64", rhel)
    arm_img = _get_advisory_image(advisory_details, "arm64", rhel)
    if amd_img is None or arm_img is None:
        return _warn(check_id, "Cannot compare — missing arch in advisory")

    amd_sha = amd_img.get("sha")
    arm_sha = arm_img.get("sha")
    if not amd_sha or not arm_sha:
        return _warn(check_id, "Cannot compare — missing SHA")

    if amd_sha == arm_sha:
        return _fail(check_id,
                     f"amd64 and arm64 el{rhel} have identical SHAs",
                     [f"amd64: {amd_sha[:12]}", f"arm64: {arm_sha[:12]}"])
    return _pass(check_id, "SHAs are distinct",
                 [f"amd64: {amd_sha[:12]}", f"arm64: {arm_sha[:12]}"])


def check_shipment_mr_approved(shipment):
    """Shipment MR in ocp-shipment-data is approved."""
    check_id = "shipment_mr_approved"
    if shipment.get("skipped"):
        return _warn(check_id, shipment.get("reason", "Shipment MR check skipped"))
    if not shipment.get("found"):
        return _fail(check_id, shipment.get("reason", "Shipment MR not found"))

    mr_iid = shipment.get("mr_iid")
    if mr_iid is None:
        return _warn(check_id, "Shipment MR found but missing merge request ID")
    project_id = artifacts._get_gitlab_project_id()
    if project_id is None:
        return _warn(check_id, "Cannot access GitLab project for approval check")

    approvals = artifacts.fetch_shipment_mr_approvals(project_id, mr_iid)
    if approvals is None:
        return _warn(check_id, f"Could not fetch approval status for MR !{mr_iid}")

    if approvals["approved"]:
        approvers = ", ".join(approvals["approvers"]) or "unknown"
        return _pass(check_id, f"MR !{mr_iid} approved by {approvers}",
                     [f"Approvals required: {approvals['approvals_required']}"])
    return _fail(check_id, f"MR !{mr_iid} not yet approved",
                 [f"Approvals required: {approvals['approvals_required']}",
                  f"Current approvers: {', '.join(approvals['approvers']) or 'none'}"])


# ── Orchestrator ─────────────────────────────────────────────────


def run_advisory_promotion_checks(version_info, phase="stage"):
    """Run all advisory promotion checks and return results in canonical order."""
    all_ids = _all_check_ids(version_info)
    minor = version_info["minor"]
    if _minor_tuple(minor) < _BOOTC_MIN_MINOR:
        return [_skip(c, f"N/A (requires 4.18+, version is {minor})") for c in all_ids]
    if version_info["type"] == "nightly":
        return [_skip(c, "N/A (nightly builds have no advisories)") for c in all_ids]

    version = version_info["version"]
    variants = _variants(version_info)
    rhel_versions = _rhel_versions(version_info)

    # Fetch shared data
    logger.info("Fetching shipment MR for %s...", version)
    shipment = artifacts.fetch_shipment_mr(version)

    advisory_details = None
    advisory_url = shipment.get("stage_advisory_url") if shipment.get("found") else None
    if advisory_url:
        logger.info("Fetching advisory YAML...")
        advisory_details = artifacts.fetch_advisory_details(advisory_url)

    # Fetch catalog data per variant
    catalog_envs = ("stage", "prod") if phase == "prod" else ("stage",)
    logger.info("Querying catalog (%s)...", "/".join(catalog_envs))
    catalog = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        cat_futures = {}
        for arch, rhel in variants:
            for env in catalog_envs:
                key = (arch, rhel, env)
                cat_futures[ex.submit(
                    pyxis.check_catalog_image_graphql, version, env, arch=arch, rhel=rhel
                )] = key
        for future in as_completed(cat_futures):
            key = cat_futures[future]
            try:
                catalog[key] = future.result()
            except Exception as exc:
                logger.exception("Catalog fetch failed for %s", key)
                catalog[key] = {"valid": False, "reason": str(exc),
                                "image": None, "catalog": key[2]}

    # Run all checks
    major = int(version_info["minor"].split(".")[0])
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {}
        for arch, rhel in variants:
            vk = _variant_key(arch, rhel)
            stage_cat = catalog.get((arch, rhel, "stage"), {})
            prod_cat = catalog.get((arch, rhel, "prod"), {})
            futures[ex.submit(check_in_advisory, vk, arch, rhel, advisory_details)] = \
                f"{vk}_advisory_image_present"
            futures[ex.submit(check_repository, vk, arch, rhel, major, advisory_details)] = \
                f"{vk}_advisory_repository"
            futures[ex.submit(check_image_sha, vk, arch, rhel, advisory_details)] = \
                f"{vk}_advisory_image_sha"
            for env, cat_data in (("stage", stage_cat), ("prod", prod_cat)):
                futures[ex.submit(check_in_catalog, vk, env, cat_data, version_info, phase)] = \
                    f"{vk}_catalog_{env}_present"
                futures[ex.submit(check_tag_commit_id, vk, env, cat_data)] = \
                    f"{vk}_catalog_{env}_tag_commit"
                futures[ex.submit(check_tag_build_date, vk, env, cat_data)] = \
                    f"{vk}_catalog_{env}_tag_date"
                futures[ex.submit(check_no_xy0_tag, vk, env, cat_data, version_info)] = \
                    f"{vk}_catalog_{env}_no_xy0_tag"
                futures[ex.submit(check_chi_freshness, vk, env, cat_data)] = \
                    f"{vk}_catalog_{env}_chi"


        futures[ex.submit(check_advisory_type, advisory_details, version_info)] = \
            "advisory_type"
        futures[ex.submit(check_shipment_type, shipment, version_info)] = \
            "shipment_type"
        futures[ex.submit(check_shipment_filename, shipment, version_info)] = \
            "shipment_filename"
        futures[ex.submit(check_shipment_nvr_commit, shipment, version_info)] = \
            "shipment_nvr_commit"
        for rhel in rhel_versions:
            futures[ex.submit(check_image_sha_distinct, rhel, advisory_details)] = \
                f"advisory_sha_distinct_el{rhel}"
        futures[ex.submit(check_shipment_mr_approved, shipment)] = \
            "shipment_mr_approved"

        results = {}
        for future in as_completed(futures):
            check_id = futures[future]
            try:
                results[check_id] = future.result()
            except Exception as exc:
                logger.exception("Check %s raised unexpected error", check_id)
                results[check_id] = _fail(check_id, f"Unexpected error: {exc}")

    return [results[c] for c in all_ids if c in results]


# ── Formatting ───────────────────────────────────────────────────


def _section_line(title):
    return f"── {title} " + "─" * max(1, 60 - len(title) - 4)


def _section_key(check_id, variant_keys):
    """Determine which section a check belongs to."""
    for vk in variant_keys:
        if check_id.startswith(vk + "_"):
            return vk
    return "Global"


def _group_by_section(results, version_info):
    """Group results by variant section, returning (variant_keys, by_section)."""
    variant_keys = [_variant_key(a, r) for a, r in _variants(version_info)]
    by_section = {}
    for r in results:
        section = _section_key(r["check"], variant_keys)
        by_section.setdefault(section, []).append(r)
    return variant_keys, by_section


def format_text_short(version, results, version_info):
    """Format checks grouped by variant section."""
    max_id_len = max((len(r["check"]) for r in results), default=20)

    # Status emojis render as 2 terminal columns (wide glyph) but len() returns 1
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

    variant_keys, by_section = _group_by_section(results, version_info)

    output = [f"Advisory Promotion: {version}", ""]
    for section in [*variant_keys, "Global"]:
        section_results = by_section.get(section, [])
        if not section_results:
            continue
        output.append(_section_line(section))
        for r in section_results:
            output.extend(_fmt_line(r))
        output.append("")

    return "\n".join(output)


def format_text_full(version, results, version_info):
    """Format a detailed markdown report grouped by variant."""
    lines = [f"# Advisory Promotion: {version} ({version_info['type']})", ""]

    variant_keys, by_section = _group_by_section(results, version_info)

    for section in [*variant_keys, "Global"]:
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
        description="Validate Konflux bootc advisory promotion (Phase 3)"
    )
    parser.add_argument("version",
                        help="Version string, e.g., 4.18.3, 4.19.0")
    parser.add_argument("--prod", action="store_true",
                        help="Check both stage and prod catalogs (default: stage only)")
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

    if _minor_tuple(version_info["minor"]) < _BOOTC_MIN_MINOR:
        print(f"ERROR: Advisory promotion checks require version 4.18+, "
              f"got {version_info['minor']}", file=sys.stderr)
        sys.exit(1)

    logger.info("Checking advisory promotion for %s (%s)...",
                args.version, version_info["type"])

    phase = "prod" if args.prod else "stage"
    results = run_advisory_promotion_checks(version_info, phase=phase)

    if args.json_output:
        output = {
            "version": args.version,
            "type": version_info["type"],
            "minor": version_info["minor"],
            "advisory_checks": results,
        }
        print(json.dumps(output, indent=2))
        return

    if args.verbose:
        print(format_text_full(args.version, results, version_info))
    else:
        print(format_text_short(args.version, results, version_info))

    if any(r["status"] == "FAIL" for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
