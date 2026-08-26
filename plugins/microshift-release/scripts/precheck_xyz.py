#!/usr/bin/env python3
"""X/Y/Z release evaluation for MicroShift.

Evaluates whether MicroShift should participate in upcoming OCP X, Y, or Z
releases by checking lifecycle status, OCP availability, advisory CVEs,
code changes, and the 90-day rule.

Usage: precheck_xyz.py <version...> [--verbose] [--json]
"""

import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from lib import (advisory, art_jira, brew, git_ops, jira_client,
                 lifecycle, ocpbugs, pyxis, release_controller)

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


def run_advisory_report(version, repo_root=None):
    """Get advisory report with CVE IDs.

    Extracts advisory and CVE data from OCP shipment MRs. CVEs are marked
    as ``pending`` — enriched by ``enrich_advisory_cves`` via Jira REST API.

    Args:
        version: Full version, e.g., "4.21.8".
        repo_root: Unused (kept for backward compatibility).

    Returns:
        dict: Advisory report keyed by advisory name, or
              {"error": "...", "skipped": True} on failure.
    """
    logger.info("Running advisory publication report for %s...", version)
    return advisory.get_advisory_report(version)


def _generate_version_range(last_released, version):
    """Generate list of z-stream versions from last_released+1 through version.

    Args:
        last_released: Last released version, e.g., "4.21.16".
        version: Current evaluation version, e.g., "4.21.28".

    Returns:
        list[str]: Version strings, e.g., ["4.21.17", ..., "4.21.28"].
    """
    parts_last = last_released.split(".")
    parts_curr = version.split(".")

    if len(parts_last) < 3 or len(parts_curr) < 3:
        logger.error("Malformed version: last_released=%r, version=%r",
                      last_released, version)
        return []

    minor_last = ".".join(parts_last[:2])
    minor_curr = ".".join(parts_curr[:2])
    if minor_last != minor_curr:
        logger.error("Cross-minor range not supported: %s -> %s",
                      last_released, version)
        return []

    z_start = int(parts_last[2]) + 1
    z_end = int(parts_curr[2])

    if z_start > z_end:
        logger.warning("Inverted version range: last_released=%s > version=%s "
                        "— evaluating from %s.%d through %s.%d",
                        last_released, version,
                        minor_curr, z_end, minor_curr, z_start - 1)
        z_start, z_end = z_end, z_start - 1

    return [f"{minor_curr}.{z}" for z in range(z_start, z_end + 1)]


def run_cumulative_advisory_report(version, last_released):
    """Fetch and merge advisory reports for all skipped z-stream versions.

    MicroShift often skips many OCP z-stream releases. When evaluating
    whether to participate in a release, we must check CVEs from ALL
    advisories between the last MicroShift release and the current
    evaluation version, not just the current version's advisory.

    Args:
        version: Current evaluation version, e.g., "4.21.28".
        last_released: Last released version, e.g., "4.21.16".

    Returns:
        tuple[dict, int]: (merged_report, versions_checked_count).
            merged_report: Merged advisory report dict, or
                           {"error": "...", "skipped": True} on total failure.
            versions_checked_count: Number of versions whose advisories
                                    were checked.
    """
    versions_to_check = _generate_version_range(last_released, version)

    if not versions_to_check:
        return run_advisory_report(version), 1

    # Cap at 20 versions to avoid excessive API calls
    if len(versions_to_check) > 20:
        logger.warning(
            "Capping advisory check at 20 versions (would be %d: %s through %s)",
            len(versions_to_check), versions_to_check[0], versions_to_check[-1],
        )
        versions_to_check = versions_to_check[-20:]

    logger.info(
        "Checking advisories for %d versions (%s through %s)...",
        len(versions_to_check), versions_to_check[0], versions_to_check[-1],
    )

    merged_report = {}
    failed_versions = []

    def _fetch_one(ver):
        try:
            return ver, advisory.get_advisory_report(ver)
        except Exception as e:
            logger.warning("Advisory fetch failed for %s: %s", ver, e)
            return ver, None

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(_fetch_one, v): v for v in versions_to_check}
        for future in as_completed(futures):
            ver, report = future.result()
            if report is None or report.get("skipped"):
                failed_versions.append(ver)
                continue

            for adv_name, adv_data in report.items():
                if not isinstance(adv_data, dict) or "type" not in adv_data:
                    continue

                entry = dict(adv_data)
                entry["source_version"] = ver
                if adv_name not in merged_report:
                    merged_report[adv_name] = entry
                else:
                    existing_cves = merged_report[adv_name].get("cves", {})
                    new_cves = adv_data.get("cves", {})
                    for cve_id, cve_data in new_cves.items():
                        if cve_id not in existing_cves:
                            existing_cves[cve_id] = cve_data
                    merged_report[adv_name]["cves"] = existing_cves

    if failed_versions:
        logger.warning(
            "Advisory fetch failed for %d version(s): %s",
            len(failed_versions), ", ".join(sorted(failed_versions)),
        )

    if not merged_report:
        return {"error": "All advisory fetches failed", "skipped": True}, len(versions_to_check)

    return merged_report, len(versions_to_check)


def enrich_advisory_cves(advisory_report, minor=None, shipped_cve_ids=None,
                         last_released=None):
    """Replace pending CVE entries with real Jira data.

    Searches Jira for each pending CVE ID and updates the advisory
    report in-place. After enrichment, ``interpret_cves`` can make
    definitive decisions instead of returning ``pending_enrichment``.

    Args:
        advisory_report: Advisory report dict (modified in-place).
        minor: Minor version string (e.g., "4.22") to filter Jira
            results to the correct OCP version.
        shipped_cve_ids: Dict mapping CVE ID → source version for CVEs
            already shipped in prior MicroShift releases.
        last_released: Last MicroShift release version (e.g., "4.22.7").
    """
    if not advisory_report or advisory_report.get("skipped"):
        return

    pending_ids = []
    for adv_data in advisory_report.values():
        if not isinstance(adv_data, dict):
            continue
        for cve_id, cve_data in adv_data.get("cves", {}).items():
            if cve_data.get("pending"):
                pending_ids.append(cve_id)

    unique_ids = sorted(set(pending_ids))
    if not unique_ids:
        return

    # Search ALL CVEs in Jira first, then apply shipped filter.
    # This ensures a MicroShift tracker with resolution "Done" is not
    # accidentally hidden by the shipped advisory filter.
    shipped = shipped_cve_ids or {}

    logger.info("Enriching %d advisory CVEs via Jira...", len(unique_ids))
    tickets = jira_client.search_cve_tickets(unique_ids, minor=minor)
    if tickets is None:
        logger.warning("Jira unavailable — CVEs remain pending")
        return

    for adv_data in advisory_report.values():
        if not isinstance(adv_data, dict):
            continue
        cves = adv_data.get("cves", {})
        for cve_id in list(cves.keys()):
            if not cves[cve_id].get("pending"):
                continue
            ticket = tickets.get(cve_id)

            # Jira search failed for this CVE — leave as pending
            if isinstance(ticket, dict) and ticket.get("error"):
                continue

            # CVE was in a shipped advisory — fix is in shipped OCP images.
            # This is ground truth regardless of Jira ticket status.
            if cve_id in shipped:
                src = shipped[cve_id]
                if src and last_released:
                    label = f"shipped in OCP {src}, MicroShift {last_released}"
                elif src:
                    label = f"shipped in {src}"
                else:
                    label = "already shipped"
                cves[cve_id] = {"reason": label}
                continue

            # Has a MicroShift tracker (not shipped) — use Jira data
            if ticket is not None:
                cves[cve_id] = {"jira_ticket": {
                    "id": ticket["key"],
                    "resolution": ticket["resolution"],
                    "status": ticket["status"],
                }}
            else:
                cves[cve_id] = {"reason": "no MicroShift tracker"}

    with_tracker = sum(1 for t in tickets.values()
                       if t is not None and not (isinstance(t, dict) and t.get("error")))
    shipped_count = sum(1 for cve in unique_ids if cve in shipped)
    logger.info("Enriched %d CVEs: %d with tracker, %d shipped, %d no tracker",
                len(unique_ids), with_tracker, shipped_count,
                len(unique_ids) - with_tracker - shipped_count)


def interpret_cves(advisory_report):
    """Interpret CVE results from the advisory report.

    Rules from the MicroShift release process:
    - Empty cves dict -> no CVEs -> no action
    - CVE with ``pending: True`` -> not yet looked up in Jira -> pending enrichment
    - CVE with empty dict (no Jira ticket) -> does NOT affect MicroShift -> no action
    - CVE with resolution "Done" -> MUST release (fix landed but not yet shipped)
    - CVE with resolution "Done-Errata" -> no action (fix already shipped in a prior errata)
    - CVE with resolution "Not a Bug" -> no action (CVE does not affect MicroShift)
    - CVE with any other status (in progress) -> no action (fix not yet landed)

    Args:
        advisory_report: Parsed advisory report dict.

    Returns:
        dict with keys:
            impact: "none"|"must_release"|"unknown"|"pending_enrichment"
            details: list of CVE dicts (for must_release), explanatory strings (for unknown), or empty list
            advisory_types: list of advisory types checked
            skipped_not_actionable: count of CVEs skipped (in-progress or unresolved fix)
            pending_cve_enrichment: list of CVE IDs needing Jira lookup
    """
    if not advisory_report or advisory_report.get("skipped"):
        return {
            "impact": "unknown",
            "details": ["Advisory report was skipped"],
            "advisory_types": [],
            "skipped_not_actionable": 0,
            "pending_cve_enrichment": [],
        }

    must_release_cves = []
    pending_cves = []
    skipped_not_actionable = 0
    advisory_types_checked = []
    seen_cves = set()

    for advisory_name, advisory_data in advisory_report.items():
        advisory_type = advisory_data.get("type", "unknown")
        # Skip metadata advisories
        if advisory_type == "metadata":
            continue
        advisory_types_checked.append(advisory_type)

        cves = advisory_data.get("cves", {})
        for cve_id, cve_data in cves.items():
            if cve_id in seen_cves:
                continue
            seen_cves.add(cve_id)

            if cve_data.get("pending"):
                pending_cves.append(cve_id)
                continue

            jira_ticket = cve_data.get("jira_ticket")
            if not jira_ticket:
                continue

            resolution = jira_ticket.get("resolution", "")
            status = jira_ticket.get("status", "")

            if resolution == "Done" or status == "Verified":
                must_release_cves.append({
                    "cve": cve_id,
                    "jira": jira_ticket.get("id", ""),
                    "reason": "Fix landed, must be released",
                })
            elif resolution in ("Done-Errata", "Not a Bug", "Won't Do",
                                "Duplicate"):
                continue
            else:
                skipped_not_actionable += 1
                logger.debug(
                    "Skipping CVE %s (%s): resolution=%r status=%r",
                    cve_id, jira_ticket.get("id", ""), resolution, status,
                )

    base = {
        "advisory_types": advisory_types_checked,
        "skipped_not_actionable": skipped_not_actionable,
        "pending_cve_enrichment": pending_cves,
    }

    if must_release_cves:
        return {**base, "impact": "must_release", "details": must_release_cves}
    if pending_cves:
        return {**base, "impact": "pending_enrichment", "details": []}
    return {**base, "impact": "none", "details": []}


def compute_recommendation(evaluation):
    """Compute the final recommendation for a version.

    Decision rules:
    - ASK ART TO CREATE ARTIFACTS: critical CVE fix or 90-day rule, OCP payload available
    - NEEDS REVIEW: ambiguous cases, or OCP payload not yet available when action would be needed
    - SKIP: no changes, no CVEs, within 90 days
    - SKIP: lifecycle inactive

    Args:
        evaluation: Dict with version evaluation data.

    Returns:
        tuple[str, str]: (recommendation, reason).
    """
    cve_impact = evaluation.get("cve_impact", {}).get("impact", "unknown")
    commits = evaluation.get("commits", 0)
    days_since = evaluation.get("days_since")
    ocp_status = evaluation.get("ocp_status", "")
    ocp_available = ocp_status == "available"

    # Shipped CVE dedup warning (appended to reason when collection failed)
    shipped_warn = ""
    if evaluation.get("shipped_cve_collection_failed"):
        shipped_warn = " (⚠ shipped CVE dedup unavailable — CVEs may be overcounted)"

    # Must release: CVE with resolution Done (fix landed, not yet shipped)
    if cve_impact == "must_release":
        cve_details = evaluation.get("cve_impact", {}).get("details", [])
        count = len(cve_details)
        label = f"{count} CVE fix{'es' if count != 1 else ''} (resolution Done)"
        pending = evaluation.get("cve_impact", {}).get("pending_cve_enrichment", [])
        if pending:
            label += f", {len(pending)} advisory CVEs pending enrichment"
        label += shipped_warn
        if not ocp_available:
            return "BLOCKED", f"{label} — waiting for OCP payload"
        return "ASK ART TO CREATE ARTIFACTS", label

    # 90-day rule — gap measured at planned shipping date, not today
    days_at_release = evaluation.get("days_at_release", days_since)
    if days_at_release is not None and days_at_release >= 90 and commits > 0:
        due = evaluation.get("due_date", "")
        gap_label = (f"90-day rule ({days_at_release}d gap at {due})"
                     if due else f"90-day rule ({days_at_release}d)")
        if not ocp_available:
            return ("BLOCKED",
                    f"{gap_label}, {commits} commits"
                    " — waiting for OCP payload")
        return ("ASK ART TO CREATE ARTIFACTS",
                f"{gap_label}, {commits} commits")

    # Build pending CVE suffix (appended to any recommendation reason)
    pending_cves = evaluation.get("cve_impact", {}).get("pending_cve_enrichment", [])
    cve_suffix = ""
    if cve_impact == "pending_enrichment" and pending_cves:
        cve_suffix = f", {len(pending_cves)} advisory CVEs pending enrichment"
    cve_suffix += shipped_warn

    # Resolved OCPBUGS targeting this version
    ocpbugs_data = evaluation.get("ocpbugs", {})
    ocpbugs_count = ocpbugs_data.get("count", 0)
    if ocpbugs_count > 0:
        release_required = ocpbugs_data.get("release_required", 0)
        needs_review_bugs = ocpbugs_data.get("needs_review", 0)

        if release_required > 0:
            bug_summary = f"{release_required} OCPBUGS labeled release-required"
            if not ocp_available:
                return "BLOCKED", f"{bug_summary}{cve_suffix} — waiting for OCP payload"
            return "ASK ART TO CREATE ARTIFACTS", f"{bug_summary}{cve_suffix}"
        if needs_review_bugs > 0:
            review_details = []
            for bug in ocpbugs_data.get("bugs", []):
                if bug.get("release_action") != "needs_review":
                    continue
                key = bug.get("key", "?")
                status = bug.get("status", "unknown")
                review_details.append(
                    f"{key} is {status} (no release label)"
                    f" — must be Verified before releasing"
                )
            reason = "; ".join(review_details) if review_details else (
                f"{needs_review_bugs} OCPBUGS need review"
            )
            return "NEEDS REVIEW", f"{reason}{cve_suffix}"
        # All bugs are release-not-required
        if cve_suffix:
            return "NEEDS REVIEW", f"{ocpbugs_count} OCPBUGS (release-not-required){cve_suffix}"
        bug_summary = f"{ocpbugs_count} OCPBUGS (all labeled release-not-required)"
        return "SKIP", bug_summary

    # Needs review: advisory report skipped or CVEs found without Jira lookup
    if cve_impact in ("unknown", "pending_enrichment"):
        if pending_cves:
            label = f"{len(pending_cves)} advisory CVEs pending enrichment"
        else:
            label = "advisory report unavailable"
        if commits > 0:
            return "NEEDS REVIEW", f"{commits} commits, {label}"
        return "NEEDS REVIEW", f"No commits, {label}"

    # Skip: no changes
    if commits == 0:
        days_str = (f"{days_since}d since last release"
                    if days_since is not None
                    else "unknown last release")
        return "SKIP", f"No commits ({days_str})"

    # Has commits but no CVEs and within 90 days
    skipped_cves = evaluation.get("cve_impact", {}).get("skipped_not_actionable", 0)
    cve_label = (f"no actionable CVEs ({skipped_cves} not actionable)"
                 if skipped_cves > 0 else "no CVEs")
    if days_since is not None:
        return "SKIP", f"{days_since}d since last release, {commits} commits, {cve_label}"

    return "SKIP", f"{commits} commits, {cve_label}"


def _resolve_range_base(version, minor, z):
    """Resolve the git range base for counting commits since a release.

    Tries four strategies in order:
    1. Exact git tag for the version.
    2. Brew NVR commit hash (embedded in the RPM build metadata).
    3. Pyxis image tag commit hash (embedded in published container tags).
    4. Nearest previous z-stream tag.

    Args:
        version: Published version string, e.g., "4.21.11".
        minor: Minor version, e.g., "4.21".
        z: Z-stream number of the published version.

    Returns:
        tuple[str|None, str|None]: (since_version, since_commit).
            Exactly one will be set, or both None if nothing found.
    """
    # Strategy 1: exact tag
    if git_ops.find_version_tag(version):
        return version, None

    # Strategy 2: Brew NVR commit hash
    logger.warning("Git tag not found for %s, trying Brew NVR...", version)
    commit = brew.extract_commit_from_nvr(version)
    if commit and git_ops.verify_commit_exists(commit):
        logger.info("Using Brew commit %s for %s", commit, version)
        return None, commit
    if commit:
        logger.warning("Brew commit %s for %s not found in local clone",
                       commit, version)

    # Strategy 3: Pyxis image tag commit hash
    commit = pyxis.extract_commit_from_image(version)
    if commit and git_ops.verify_commit_exists(commit):
        logger.info("Using Pyxis commit %s for %s", commit, version)
        return None, commit
    if commit:
        logger.warning("Pyxis commit %s for %s not found in local clone",
                       commit, version)

    # Strategy 4: nearest previous tag
    logger.warning("No commit found via tag/Brew/Pyxis, searching for nearest tag...")
    nearest_ver, _ = git_ops.find_nearest_version_tag(minor, z - 1)
    if nearest_ver:
        logger.info("Using nearest available tag: %s", nearest_ver)
        return nearest_ver, None

    return None, None


def evaluate_version(version, lifecycle_data, repo_root):
    """Run full evaluation pipeline for one version.

    Args:
        version: Full version, e.g., "4.21.8".
        lifecycle_data: Output from lifecycle.fetch_lifecycle_data().
        repo_root: Path to the git repository root.

    Returns:
        dict: Evaluation result with recommendation.
    """
    minor = ".".join(version.split(".")[:2])
    result = {"version": version, "minor": minor}

    # Lifecycle check
    lc = lifecycle.get_lifecycle_status(minor, lifecycle_data)
    if lc:
        result["lifecycle_status"] = lc["phase"]
        result["lifecycle_end_date"] = lc.get("end_date", "")
    else:
        result["lifecycle_status"] = "unknown"

    # Skip EOL versions immediately
    if result["lifecycle_status"] == "End of life":
        result["recommendation"] = "SKIP"
        result["reason"] = "End of life"
        return result

    # VPN check — required for Brew and advisory report access
    if not brew.check_vpn():
        result["recommendation"] = "NEEDS REVIEW"
        result["reason"] = "VPN not connected"
        return result

    # Already released check (Pyxis)
    logger.info("Checking if %s is already released...", version)
    try:
        if pyxis.is_version_published(version):
            result["already_released"] = True
            result["recommendation"] = "ALREADY RELEASED"
            result["reason"] = "MicroShift errata published"
            return result
        result["already_released"] = False
    except Exception as e:
        logger.warning("Pyxis check failed for %s: %s", version, e)
        result["already_released"] = None
        result["recommendation"] = "NEEDS REVIEW"
        result["reason"] = f"Pyxis check failed: {e}"
        return result

    # OCP payload status
    logger.info("Checking OCP payload for %s...", version)
    try:
        result["ocp_status"] = release_controller.check_ocp_payload_accepted(version)
    except Exception as e:
        logger.warning("OCP payload check failed for %s: %s", version, e)
        result["ocp_status"] = ""

    # ART ticket lookup
    try:
        art_tickets = art_jira.query_art_releases_due(specific_version=version)
        if art_tickets:
            result["art_ticket"] = art_tickets[0]["key"]
            result["due_date"] = art_tickets[0].get("due_date", "")
        else:
            result["art_ticket"] = None
            result["due_date"] = ""
    except Exception as e:
        logger.warning("ART ticket lookup failed for %s: %s", version, e)
        result["art_ticket"] = None
        result["due_date"] = ""

    # Z-stream evaluation
    # 4a: Code changes since last release
    branch = f"release-{minor}"
    logger.info("Fetching commits on %s...", branch)
    git_ops.fetch_branch(branch)

    last_pub = pyxis.find_latest_published_zstream_any(minor)
    if last_pub:
        result["last_released"] = last_pub["version"]
        since_version, since_commit = _resolve_range_base(
            last_pub["version"], minor, last_pub["z"])
    else:
        result["last_released"] = f"{minor}.0"
        since_version, since_commit = None, None

    commit_list = git_ops.commits_since(branch, since_version, since_commit=since_commit)
    result["commits"] = len(commit_list)
    result["commit_list"] = commit_list

    # 4b: Resolve last release date (needed by OCPBUGS component CVE filter)
    last_release_date = None
    if last_pub:
        last_release_date = git_ops.get_release_date(last_pub["version"])
        if not last_release_date and last_pub.get("date"):
            last_release_date = last_pub["date"]
        if not last_release_date:
            last_release_date = pyxis.get_publish_date(last_pub["version"])

    # 4c: Collect CVE IDs already shipped in prior releases
    # Maps CVE ID → source version where it was shipped
    shipped_cve_map = {}
    if last_pub and last_pub.get("version"):
        try:
            shipped_versions = _generate_version_range(
                f"{minor}.0", last_pub["version"]
            )
            if shipped_versions:
                logger.info("Collecting shipped CVEs from %d prior versions...",
                            len(shipped_versions))
                shipped_report, _ = run_cumulative_advisory_report(
                    last_pub["version"], f"{minor}.0"
                )
                if shipped_report and not shipped_report.get("skipped"):
                    for adv_data in shipped_report.values():
                        if isinstance(adv_data, dict):
                            src_ver = adv_data.get("source_version", "")
                            for cve_id in adv_data.get("cves", {}):
                                if cve_id not in shipped_cve_map:
                                    shipped_cve_map[cve_id] = src_ver
                    if shipped_cve_map:
                        logger.info("Found %d CVEs already shipped in ≤%s",
                                    len(shipped_cve_map), last_pub["version"])
        except Exception as e:
            logger.warning("Shipped CVE collection failed: %s", e)
            result["shipped_cve_collection_failed"] = True

    # 4d: OCPBUGS references from commit messages + component CVEs
    logger.info("Checking resolved OCPBUGS for %s...", version)
    try:
        result["ocpbugs"] = ocpbugs.query_resolved_bugs(
            version, branch, since_version, since_commit=since_commit,
            last_release_date=last_release_date,
            shipped_cve_ids=shipped_cve_map,
        )
    except Exception as e:
        logger.warning("OCPBUGS check failed for %s: %s", version, e)
        result["ocpbugs"] = {"count": 0, "bugs": [], "skipped": True}

    # 4d: Advisory publication report — scan all skipped z-stream advisories
    last_released_ver = result.get("last_released")
    if last_released_ver:
        report, versions_checked = run_cumulative_advisory_report(
            version, last_released_ver,
        )
        result["advisory_report"] = report
        result["advisory_versions_checked"] = versions_checked
    else:
        result["advisory_report"] = run_advisory_report(version, repo_root)
        result["advisory_versions_checked"] = 1

    # 4d: Interpret CVEs
    # 4e: Enrich advisory CVEs with Jira data, then interpret
    last_ver = last_pub["version"] if last_pub else None
    enrich_advisory_cves(result["advisory_report"], minor=minor,
                         shipped_cve_ids=shipped_cve_map,
                         last_released=last_ver)
    result["cve_impact"] = interpret_cves(result["advisory_report"])

    # 4f: 90-day rule — calculate gap at shipping time, not today
    if last_pub:
        release_date = last_release_date
        if release_date:
            try:
                build_date = datetime.strptime(release_date, "%Y-%m-%d")
                result["days_since"] = (datetime.now() - build_date).days
                result["last_release_date"] = release_date

                # Calculate gap at planned shipping date (ART due date)
                due = result.get("due_date", "")
                if due:
                    try:
                        ship_date = datetime.strptime(due, "%Y-%m-%d")
                        result["days_at_release"] = (ship_date - build_date).days
                    except (ValueError, TypeError):
                        result["days_at_release"] = result["days_since"]
                else:
                    result["days_at_release"] = result["days_since"]
            except (ValueError, TypeError) as e:
                logger.warning("Failed to parse release date '%s' "
                               "for %s: %s",
                               release_date, last_pub["version"], e)
                result["days_since"] = None
        else:
            result["days_since"] = None
    else:
        result["days_since"] = None

    # 4f: Recommendation
    result["recommendation"], result["reason"] = compute_recommendation(result)

    return result


def expand_versions(version_args, lifecycle_data):
    """Expand version arguments (X.Y -> query ART for specific z-stream).

    Args:
        version_args: List of version strings from CLI.
        lifecycle_data: Lifecycle data.

    Returns:
        list[str]: Expanded version strings.
    """
    versions = []
    for v in version_args:
        parts = v.split(".")
        if len(parts) == 2:
            # Minor version: query ART for specific releases
            art_tickets = art_jira.query_art_releases_due(minor_version=v)
            if art_tickets:
                for ticket in art_tickets:
                    versions.append(ticket["version"])
            else:
                # No ART tickets — can't determine specific z-stream
                logger.warning(
                    "No ART tickets found for %s, cannot determine specific z-stream", v
                )
        elif len(parts) == 3:
            versions.append(v)
        else:
            logger.warning("Invalid version format: %s", v)
    return versions


def _build_reason(e):
    """Build the reason string for a version evaluation."""
    parts = []

    # CVE / advisory impact
    cve_impact = e.get("cve_impact", {})
    impact = cve_impact.get("impact", "unknown")
    versions_checked = e.get("advisory_versions_checked", 1)
    adv_suffix = ""
    if versions_checked > 1:
        last = e.get("last_released", "")
        last_parts = last.split(".") if last else []
        ver_parts = e.get("version", "").split(".")
        if len(last_parts) == 3 and len(ver_parts) == 3:
            minor = ".".join(ver_parts[:2])
            first_checked = f"{minor}.{int(last_parts[2]) + 1}"
            adv_suffix = (f" (checked {versions_checked} versions:"
                          f" {first_checked}→{e.get('version', '?')})")
        else:
            adv_suffix = f" (checked {versions_checked} versions)"
    if impact == "must_release":
        details = cve_impact.get("details", [])
        count = len(details)
        parts.append(f"{count} CVE fix{'es' if count != 1 else ''}{adv_suffix}")
    elif impact == "none":
        skipped = cve_impact.get("skipped_not_actionable", 0)
        if skipped > 0:
            parts.append(f"no actionable CVEs ({skipped} not actionable){adv_suffix}")
        else:
            parts.append(f"no CVEs{adv_suffix}")
    elif impact == "pending_enrichment":
        pending = cve_impact.get("pending_cve_enrichment", [])
        parts.append(f"{len(pending)} advisory CVEs{adv_suffix}")
    elif impact == "unknown":
        advisory = e.get("advisory_report", {})
        if advisory and advisory.get("skipped"):
            parts.append("advisory report unavailable")
        else:
            parts.append("advisory unknown")

    # OCPBUGS
    ocpbugs_data = e.get("ocpbugs", {})
    ocpbugs_count = ocpbugs_data.get("count", 0)
    if ocpbugs_count > 0:
        release_req = ocpbugs_data.get("release_required", 0)
        needs_rev = ocpbugs_data.get("needs_review", 0)
        not_req = ocpbugs_data.get("release_not_required", 0)
        label_parts = []
        if release_req > 0:
            label_parts.append(f"{release_req} release-required")
        if not_req > 0:
            label_parts.append(f"{not_req} release-not-required")
        if needs_rev > 0:
            label_parts.append(f"{needs_rev} unlabeled")
        parts.append(f"{ocpbugs_count} OCPBUGS ({', '.join(label_parts)})")
    elif ocpbugs_data and not ocpbugs_data.get("skipped", False):
        parts.append("no OCPBUGS")

    # Last released
    days = e.get("days_since")
    last = e.get("last_released", "")
    if days is not None and last:
        parts.append(f"last: {last} ({days}d ago)")
    elif last:
        parts.append(f"last: {last}")

    return " | ".join(parts) if parts else "no data"


def format_text_short(evaluations):
    """Format evaluations as one-line-per-version text.

    Format: ACTION x.y.z [OCP: available/NOT available] [reason]

    Args:
        evaluations: List of evaluation result dicts.

    Returns:
        str: Pre-formatted text output.
    """
    if not evaluations:
        return "No versions to evaluate."

    REC_WIDTH = 28  # len("ASK ART TO CREATE ARTIFACTS")
    lines = []

    for e in evaluations:
        version = e.get("version", "?")
        rec = e.get("recommendation", "UNKNOWN")

        if rec == "ALREADY RELEASED":
            lines.append(f"{rec:<{REC_WIDTH}} {version}")
            continue

        if e.get("lifecycle_status") == "End of life":
            lines.append(f"{rec:<{REC_WIDTH}} {version} [End of life]")
            continue

        if e.get("reason") == "VPN not connected":
            lines.append(
                f"{rec:<{REC_WIDTH}} {version}"
                " [VPN not connected]")
            continue

        # OCP status
        ocp = e.get("ocp_status", "")
        if ocp == "available":
            ocp_str = "available"
        elif not ocp:
            ocp_str = "unknown"
        else:
            ocp_str = "NOT available"

        # Build reason using pipe-separated format
        reason = _build_reason(e)

        lines.append(f"{rec:<{REC_WIDTH}} {version} [OCP: {ocp_str}] [{reason}]")

    return "\n".join(lines)


def format_text_full(output):
    """Format evaluations as detailed markdown report.

    Args:
        output: Full output dict with lifecycle and evaluations.

    Returns:
        str: Markdown-formatted report.
    """
    evaluations = output.get("evaluations", [])
    if not evaluations:
        return ""

    sections = []

    # Release Schedule table
    sections.append("## Release Schedule\n")
    sections.append("| Version | ART Ticket | Due Date | OCP Status | Lifecycle |")
    sections.append("|---------|-----------|----------|------------|-----------|")
    for e in evaluations:
        v = e.get("version", "?")
        art = e.get("art_ticket", "None")
        due = e.get("due_date", "--") or "--"
        ocp = e.get("ocp_status", "--")
        lc = e.get("lifecycle_status", "--")
        sections.append(f"| {v} | {art} | {due} | {ocp} | {lc} |")

    # Z-Stream Evaluation table
    sections.append("\n## Z-Stream Evaluation\n")
    sections.append("| Version | Last Released | Days Since | Commits | CVE Impact | OCPBUGS |")
    sections.append("|---------|--------------|------------|---------|------------|---------|")
    for e in evaluations:
        if e.get("already_released") or e.get("recommendation") == "ALREADY RELEASED":
            continue
        v = e.get("version", "?")
        last = e.get("last_released", "--")
        days = str(e.get("days_since", "--")) if e.get("days_since") is not None else "--"
        commits = str(e.get("commits", 0))
        impact_raw = e.get("cve_impact", {}).get("impact", "--")
        impact = {"pending_enrichment": "pending", "must_release": "must release"}.get(
            impact_raw, impact_raw)
        ocpbugs_data = e.get("ocpbugs", {})
        ocpbugs_count = ("skipped" if ocpbugs_data.get("skipped")
                         else str(ocpbugs_data.get("count", 0)))
        sections.append(f"| {v} | {last} | {days} | {commits} | {impact} | {ocpbugs_count} |")

    # Advisory Report table
    has_advisories = any(
        e.get("advisory_report") and not e["advisory_report"].get("skipped")
        for e in evaluations
    )
    if has_advisories:
        # Note cumulative advisory scanning when applicable
        adv_notes = []
        for e in evaluations:
            checked = e.get("advisory_versions_checked", 1)
            if checked > 1:
                last = e.get("last_released", "")
                last_parts = last.split(".") if last else []
                ver_parts = e.get("version", "").split(".")
                if len(last_parts) == 3 and len(ver_parts) == 3:
                    minor = ".".join(ver_parts[:2])
                    first_checked = f"{minor}.{int(last_parts[2]) + 1}"
                    adv_notes.append(
                        f"Advisory CVEs checked across {checked} versions"
                        f" ({first_checked} → {e.get('version', '?')})"
                    )
        sections.append("\n## Advisory Report\n")
        for note in adv_notes:
            sections.append(f"> {note}\n")
        sections.append("| Version | Advisory | Type | CVEs | MicroShift Impact |")
        sections.append("|---------|----------|------|------|-------------------|")
        for e in evaluations:
            report = e.get("advisory_report", {})
            if not report or report.get("skipped"):
                continue
            eval_ver = e.get("version", "?")
            for adv_name, adv_data in report.items():
                if not isinstance(adv_data, dict) or "type" not in adv_data:
                    continue
                v = adv_data.get("source_version", eval_ver)
                adv_type = adv_data.get("type", "?")
                cves = adv_data.get("cves", {})
                if not cves:
                    sections.append(f"| {v} | {adv_name} | {adv_type} | none | -- |")
                else:
                    for cve_id, cve_data in cves.items():
                        if cve_data.get("pending"):
                            impact = "pending"
                        elif cve_data.get("jira_ticket"):
                            jt = cve_data["jira_ticket"]
                            jid = jt.get('id', '?')
                            jres = jt.get('resolution', '')
                            jstat = jt.get('status', '')
                            if jres == "Done" or jstat == "Verified":
                                impact = f"{jid} (**must release**)"
                            elif jres in ("Not a Bug", "Won't Do"):
                                impact = f"not affected ({jres})"
                            elif jres == "Done-Errata":
                                impact = "already shipped"
                            elif jres == "Duplicate":
                                impact = f"not affected (duplicate)"
                            else:
                                impact = f"{jid} ({jres or jstat})"
                        elif cve_data.get("reason"):
                            impact = cve_data["reason"]
                        else:
                            impact = "not affected"
                        sections.append(f"| {v} | {adv_name} | {adv_type} | {cve_id} | {impact} |")

    # OCPBUGS Details table
    has_ocpbugs = any(
        e.get("ocpbugs", {}).get("count", 0) > 0
        for e in evaluations
    )
    if has_ocpbugs:
        sections.append("\n## OCPBUGS in detail\n")
        header = ("| Version | Bug | Status | Source | Release Action "
                  "| Release Note Type | Release Note Status | Summary |")
        separator = ("|---------|-----|--------|--------|----------------"
                     "|-------------------|---------------------|---------|")
        sections.append(header)
        sections.append(separator)
        bugs_with_rn = 0
        for e in evaluations:
            v = e.get("version", "?")
            for bug in e.get("ocpbugs", {}).get("bugs", []):
                key = bug.get("key", "?")
                status = bug.get("status", "?")
                source = bug.get("source", "?")
                release_action = bug.get("release_action", "needs_review")
                rn_type = bug.get("release_note_type", "") or "--"
                rn_status = bug.get("release_note_status", "") or "--"
                summary = bug.get("summary", "").replace("|", "\\|").replace("\n", " ")
                row = (f"| {v} | {key} | {status} | {source} "
                       f"| {release_action} | {rn_type} | {rn_status} "
                       f"| {summary} |")
                sections.append(row)
                rn_text = bug.get("release_note", "")
                if rn_text and rn_type != "Release Note Not Required":
                    bugs_with_rn += 1

        # Release Note details (only if any bug has a release note)
        has_rn_text = any(
            bug.get("release_note", "")
            for e in evaluations
            for bug in e.get("ocpbugs", {}).get("bugs", [])
        )
        if has_rn_text:
            sections.append("\n### Release Notes\n")
            for e in evaluations:
                for bug in e.get("ocpbugs", {}).get("bugs", []):
                    rn_text = bug.get("release_note", "")
                    if rn_text:
                        rn_type = bug.get("release_note_type", "")
                        sections.append(f"**{bug['key']}** ({rn_type}):")
                        sections.append(f"> {rn_text}")
                        sections.append("")

        if bugs_with_rn > 0:
            sections.append(
                f"> **Note:** {bugs_with_rn} bug(s) have customer-facing Release Notes. "
                "Use the per-version recommendation table above for the action."
            )
        else:
            sections.append(
                "> **Note:** No customer-facing Release Notes found — bug fixes may be "
                "internal-only. Use the per-version recommendation table above for the action."
            )

    # Recommendations table (combined summary + recommendation)
    sections.append("\n## Recommendations\n")
    sections.append("| Recommendation | Version | OCP | CVEs | OCPBUGS | Last Release | Reason |")
    sections.append("|---------------|---------|-----|------|---------|--------------|--------|")
    _REC_ICON = {
        "ASK ART TO CREATE ARTIFACTS": "🔴 **ASK ART**",
        "BLOCKED": "⏳ **BLOCKED**",
        "NEEDS REVIEW": "🟡 **NEEDS REVIEW**",
        "SKIP": "🟢 **SKIP**",
        "ALREADY RELEASED": "✅ **ALREADY RELEASED**",
    }

    for e in evaluations:
        v = e.get("version", "?")
        rec = e.get("recommendation", "UNKNOWN")
        rec_label = _REC_ICON.get(rec, f"**{rec}**")
        reason = e.get("reason", "").replace("|", "\\|").replace("\n", " ")

        if rec == "ALREADY RELEASED":
            sections.append(f"| {rec_label} | {v} | — | — | — | — | — |")
            continue
        if e.get("lifecycle_status") == "End of life":
            sections.append(f"| {rec_label} | {v} | — | — | — | — | End of life |")
            continue

        ocp = e.get("ocp_status", "—")
        last = e.get("last_released", "—")
        days = e.get("days_since")
        last_col = f"{last} ({days}d ago)" if days is not None else last

        # CVE summary
        cve_impact = e.get("cve_impact", {})
        impact = cve_impact.get("impact", "unknown")
        versions_checked = e.get("advisory_versions_checked", 1)
        if impact == "must_release":
            count = len(cve_impact.get("details", []))
            cve_col = f"{count} CVE fix{'es' if count != 1 else ''}"
        elif impact == "none":
            skipped = cve_impact.get("skipped_not_actionable", 0)
            cve_col = f"no CVEs ({skipped} not actionable)" if skipped else "no CVEs"
        elif impact == "pending_enrichment":
            pending = cve_impact.get("pending_cve_enrichment", [])
            cve_col = f"{len(pending)} pending"
        else:
            cve_col = "—"
        if versions_checked > 1:
            cve_col += f" ({versions_checked} versions checked)"

        # OCPBUGS summary
        ocpbugs_data = e.get("ocpbugs", {})
        ocpbugs_count = ocpbugs_data.get("count", 0)
        if ocpbugs_data.get("skipped"):
            bugs_col = "skipped"
        elif ocpbugs_count == 0:
            bugs_col = "none"
        else:
            parts = []
            rr = ocpbugs_data.get("release_required", 0)
            nr = ocpbugs_data.get("needs_review", 0)
            nrq = ocpbugs_data.get("release_not_required", 0)
            if rr:
                parts.append(f"{rr} release-required")
            if nrq:
                parts.append(f"{nrq} release-not-required")
            if nr:
                parts.append(f"{nr} unlabeled")
            bugs_col = f"{ocpbugs_count} ({', '.join(parts)})"

        sections.append(
            f"| {rec_label} | {v} | {ocp} | {cve_col} | {bugs_col} | {last_col} | {reason} |"
        )

    # CVEs requiring release (detail table)
    cve_rows = []
    for e in evaluations:
        cve_impact = e.get("cve_impact", {})
        if cve_impact.get("impact") != "must_release":
            continue
        v = e.get("version", "?")
        for d in cve_impact.get("details", []):
            cve_rows.append((v, d.get("cve", "?"), d.get("jira", "—"),
                             d.get("reason", "")))
    if cve_rows:
        sections.append("\n## CVEs Requiring Release\n")
        sections.append("| Version | CVE | Jira Ticket | Detail |")
        sections.append("|---------|-----|-------------|--------|")
        for v, cve, jira, reason in cve_rows:
            sections.append(f"| {v} | {cve} | {jira} | {reason} |")

    return "\n".join(sections)


def main():
    parser = argparse.ArgumentParser(description="MicroShift X/Y/Z release evaluation")
    parser.add_argument("versions", nargs="+", help="X.Y or X.Y.Z versions")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="Output raw JSON instead of formatted text")
    args = parser.parse_args()

    # Step 1: Fetch lifecycle data
    logger.info("Fetching lifecycle data...")
    try:
        lifecycle_data = lifecycle.fetch_lifecycle_data()
    except Exception as e:
        logger.error("Failed to fetch lifecycle data: %s", e)
        if args.json_output:
            print(json.dumps({
                "command": "precheck_xyz",
                "error": f"Lifecycle API unavailable: {e}",
                "timestamp": datetime.now().isoformat(),
            }, indent=2))
        else:
            print(f"ERROR: Lifecycle API unavailable: {e}")
        sys.exit(1)

    try:
        repo_root = git_ops.get_repo_root()
    except Exception as e:
        logger.error("Failed to locate git repo root: %s", e)
        if args.json_output:
            print(json.dumps({
                "command": "precheck_xyz",
                "error": f"Git repo root not found: {e}",
                "timestamp": datetime.now().isoformat(),
            }, indent=2))
        else:
            print(f"ERROR: Git repo root not found: {e}")
        sys.exit(1)

    # Step 2: Determine versions to evaluate
    versions = expand_versions(args.versions, lifecycle_data)

    # Step 3: Evaluate each version (parallel when multiple)
    evaluations = []
    if len(versions) > 1:
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(evaluate_version, v, lifecycle_data, repo_root): v
                for v in versions
            }
            for future in as_completed(futures):
                try:
                    evaluations.append(future.result())
                except Exception as e:
                    v = futures[future]
                    logger.warning("Evaluation failed for %s: %s", v, e)
                    evaluations.append({
                        "version": v,
                        "recommendation": "NEEDS REVIEW",
                        "reason": f"evaluation error: {e}",
                    })
        # Restore original version ordering
        version_order = {v: i for i, v in enumerate(versions)}
        evaluations.sort(key=lambda e: version_order.get(e["version"], 0))
    else:
        for version in versions:
            logger.info("Evaluating %s...", version)
            try:
                result = evaluate_version(version, lifecycle_data, repo_root)
            except Exception as e:
                logger.warning("Evaluation failed for %s: %s", version, e)
                result = {
                    "version": version,
                    "recommendation": "NEEDS REVIEW",
                    "reason": f"evaluation error: {e}",
                }
            evaluations.append(result)

    # Step 4: Output
    output = {
        "command": "precheck_xyz",
        "timestamp": datetime.now().isoformat(),
        "lifecycle": lifecycle_data,
        "evaluations": evaluations,
    }

    if args.json_output:
        print(json.dumps(output, indent=2))
    else:
        # Always show one-liner summary followed by detail tables
        print(format_text_short(evaluations))
        details = format_text_full(output)
        if details.strip():
            print()
            print(details)


if __name__ == "__main__":
    main()
