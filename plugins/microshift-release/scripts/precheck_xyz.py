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
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from lib import art_jira, brew, git_ops, lifecycle, ocpbugs, pyxis, release_controller

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


def run_advisory_report(version, repo_root):
    """Call advisory_publication_report.sh as subprocess.

    Args:
        version: Full version, e.g., "4.21.8".
        repo_root: Path to the git repository root.

    Returns:
        dict: Parsed JSON report, or {"error": "...", "skipped": True} on failure.
    """
    # Check prerequisites
    parts = version.split(".")
    if len(parts) >= 2 and parts[1].isdigit():
        minor_int = int(parts[1])
        if minor_int >= 20 and not os.environ.get("GITLAB_API_TOKEN", "").strip():
            return {"error": "Missing env var: GITLAB_API_TOKEN", "skipped": True}

    # Check VPN
    if not brew.check_vpn():
        return {"error": "VPN not connected", "skipped": True}

    script = os.path.join(
        repo_root, "scripts", "advisory_publication", "advisory_publication_report.sh"
    )
    if not os.path.exists(script):
        return {"error": f"Script not found: {script}", "skipped": True}

    logger.info("Running advisory publication report for %s...", version)
    try:
        result = subprocess.run(
            ["bash", script, version],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            stdout = result.stdout.strip()
            # The advisory script may print warnings before the JSON.
            # Find the outermost JSON object by matching the last '}'
            # back to its opening '{'.
            json_end = stdout.rfind("}")
            if json_end >= 0:
                json_start = stdout.find("{")
                if json_start >= 0 and json_start < json_end:
                    return json.loads(stdout[json_start:json_end + 1])
            return {"error": "No JSON found in advisory report output", "skipped": True}
        return {"error": result.stderr.strip(), "skipped": True}
    except subprocess.TimeoutExpired:
        return {"error": "Advisory report timed out (120s)", "skipped": True}
    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON from advisory report: {e}", "skipped": True}


def interpret_cves(advisory_report):
    """Interpret CVE results from the advisory report.

    Rules from the MicroShift release process:
    - Empty cves dict -> no CVEs -> no action
    - CVE with empty dict (no Jira ticket) -> does NOT affect MicroShift -> no action
    - CVE with resolution "Done-Errata" or "Done" -> MUST release
    - CVE with resolution "Not a Bug" -> no action
    - CVE with any other status -> flag as NEEDS REVIEW

    Args:
        advisory_report: Parsed JSON from advisory_publication_report.sh.

    Returns:
        dict: {"impact": "none"|"must_release"|"needs_review", "details": [...]}
    """
    if not advisory_report or advisory_report.get("skipped"):
        return {"impact": "unknown", "details": ["Advisory report was skipped"]}

    must_release_cves = []
    needs_review_cves = []
    advisory_types_checked = []

    for advisory_name, advisory_data in advisory_report.items():
        advisory_type = advisory_data.get("type", "unknown")
        # Skip metadata advisories
        if advisory_type == "metadata":
            continue
        advisory_types_checked.append(advisory_type)

        cves = advisory_data.get("cves", {})
        for cve_id, cve_data in cves.items():
            jira_ticket = cve_data.get("jira_ticket")
            if not jira_ticket:
                # No MicroShift Jira ticket -> CVE does not affect MicroShift
                continue

            resolution = jira_ticket.get("resolution", "")
            status = jira_ticket.get("status", "")

            if resolution in ("Done-Errata", "Done"):
                must_release_cves.append({
                    "cve": cve_id,
                    "jira": jira_ticket.get("id", ""),
                    "reason": "Fix released via errata" if resolution == "Done-Errata" else "Fix completed",
                })
            elif resolution == "Not a Bug":
                continue
            else:
                needs_review_cves.append({
                    "cve": cve_id,
                    "jira": jira_ticket.get("id", ""),
                    "reason": f"Fix {status.lower()}" if status else "Unknown status",
                })

    if must_release_cves:
        return {
            "impact": "must_release",
            "details": must_release_cves,
            "advisory_types": advisory_types_checked,
        }
    if needs_review_cves:
        return {
            "impact": "needs_review",
            "details": needs_review_cves,
            "advisory_types": advisory_types_checked,
        }
    return {
        "impact": "none",
        "details": [],
        "advisory_types": advisory_types_checked,
    }


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

    # Must release: CVE with Done-Errata
    if cve_impact == "must_release":
        cve_details = evaluation.get("cve_impact", {}).get("details", [])
        cve_list = ", ".join(d["cve"] for d in cve_details)
        if not ocp_available:
            return "NEEDS REVIEW", f"CVE fix: {cve_list} (OCP payload not yet available)"
        return "ASK ART TO CREATE ARTIFACTS", f"CVE fix: {cve_list}"

    # 90-day rule (hard policy constraint — evaluated before OCPBUGS labels)
    if days_since is not None and days_since >= 90 and commits > 0:
        if not ocp_available:
            return ("NEEDS REVIEW",
                    f"90-day rule ({days_since}d, {commits} commits)"
                    " — OCP payload not yet available")
        return ("ASK ART TO CREATE ARTIFACTS",
                f"90-day rule ({days_since}d since last release,"
                f" {commits} commits)")

    # Resolved OCPBUGS targeting this version
    ocpbugs_data = evaluation.get("ocpbugs", {})
    ocpbugs_count = ocpbugs_data.get("count", 0)
    if ocpbugs_count > 0:
        release_required = ocpbugs_data.get("release_required", 0)
        needs_review_bugs = ocpbugs_data.get("needs_review", 0)

        if release_required > 0:
            bug_summary = f"{release_required} OCPBUGS labeled release-required"
            if not ocp_available:
                return "NEEDS REVIEW", f"{bug_summary} (OCP payload not yet available)"
            return "ASK ART TO CREATE ARTIFACTS", bug_summary
        if needs_review_bugs > 0:
            bug_summary = f"{ocpbugs_count} OCPBUGS ({needs_review_bugs} unlabeled, needs review)"
            return "NEEDS REVIEW", bug_summary
        # All bugs are release-not-required
        bug_summary = f"{ocpbugs_count} OCPBUGS (all labeled release-not-required)"
        return "SKIP", bug_summary

    # Needs review: CVE in progress
    if cve_impact == "needs_review":
        return "NEEDS REVIEW", "CVE fix in progress"

    # Needs review: advisory report skipped
    if cve_impact == "unknown":
        if commits > 0:
            return "NEEDS REVIEW", f"{commits} commits, advisory report unavailable"
        return "SKIP", "No commits, advisory report unavailable"

    # Skip: no changes
    if commits == 0:
        days_str = (f"{days_since}d since last release"
                    if days_since is not None
                    else "unknown last release")
        return "SKIP", f"No commits ({days_str})"

    # Has commits but no CVEs and within 90 days
    if days_since is not None:
        return "SKIP", f"{days_since}d since last release, {commits} commits, no CVEs"

    return "SKIP", f"{commits} commits, no CVEs"


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

    # 4b: OCPBUGS references from commit messages (enriched via MCP at skill level)
    logger.info("Checking resolved OCPBUGS for %s...", version)
    try:
        result["ocpbugs"] = ocpbugs.query_resolved_bugs(
            version, branch, since_version, since_commit=since_commit,
        )
    except Exception as e:
        logger.warning("OCPBUGS check failed for %s: %s", version, e)
        result["ocpbugs"] = {"count": 0, "bugs": [], "skipped": True}

    # 4c: Advisory publication report
    result["advisory_report"] = run_advisory_report(version, repo_root)

    # 4d: Interpret CVEs
    result["cve_impact"] = interpret_cves(result["advisory_report"])

    # 4e: 90-day rule — get date of last release from git tags
    if last_pub:
        release_date = git_ops.get_release_date(last_pub["version"])
        if not release_date and last_pub.get("date"):
            release_date = last_pub["date"]
            logger.info("Using errata date for %s: %s",
                        last_pub["version"], release_date)
        if not release_date:
            logger.info("Git tag/errata date not found for %s, "
                        "trying Pyxis...", last_pub["version"])
            release_date = pyxis.get_publish_date(last_pub["version"])
        if release_date:
            try:
                build_date = datetime.strptime(release_date, "%Y-%m-%d")
                result["days_since"] = (datetime.now() - build_date).days
                result["last_release_date"] = release_date
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
    if impact == "must_release":
        details = cve_impact.get("details", [])
        cve_list = ", ".join(d.get("cve", "") for d in details)
        parts.append(f"CVE fix: {cve_list}")
    elif impact == "needs_review":
        parts.append("CVE in progress")
    elif impact == "none":
        parts.append("no CVEs")
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
        return "No versions to evaluate."

    sections = []

    # Release Schedule table
    sections.append("## Release Schedule\n")
    sections.append("| Version | ART Ticket | Due Date | OCP Status | Lifecycle |")
    sections.append("|---------|-----------|----------|------------|-----------|")
    for e in evaluations:
        v = e.get("version", "?")
        art = e.get("art_ticket", "--")
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
        impact = e.get("cve_impact", {}).get("impact", "--")
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
        sections.append("\n## Advisory Report\n")
        sections.append("| Version | Advisory | Type | CVEs | MicroShift Impact |")
        sections.append("|---------|----------|------|------|-------------------|")
        for e in evaluations:
            report = e.get("advisory_report", {})
            if not report or report.get("skipped"):
                continue
            v = e.get("version", "?")
            for adv_name, adv_data in report.items():
                # Skip non-advisory keys (e.g., "error", "skipped")
                if not isinstance(adv_data, dict) or "type" not in adv_data:
                    continue
                adv_type = adv_data.get("type", "?")
                cves = adv_data.get("cves", {})
                if not cves:
                    sections.append(f"| {v} | {adv_name} | {adv_type} | none | -- |")
                else:
                    for cve_id, cve_data in cves.items():
                        jira_ticket = cve_data.get("jira_ticket")
                        if jira_ticket:
                            jid = jira_ticket.get('id', '?')
                            jres = jira_ticket.get('resolution', '?')
                            impact = f"{jid} ({jres})"
                        else:
                            impact = "not affected"
                        sections.append(f"| {v} | {adv_name} | {adv_type} | {cve_id} | {impact} |")

    # OCPBUGS Details table
    has_ocpbugs = any(
        e.get("ocpbugs", {}).get("count", 0) > 0
        for e in evaluations
    )
    if has_ocpbugs:
        sections.append("\n## Resolved OCPBUGS\n")
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

    # Recommendations table
    sections.append("\n## Recommendations\n")
    sections.append("| Version | Recommendation | Reason |")
    sections.append("|---------|---------------|--------|")
    for e in evaluations:
        v = e.get("version", "?")
        rec = e.get("recommendation", "UNKNOWN")
        reason = e.get("reason", "").replace("|", "\\|").replace("\n", " ")
        sections.append(f"| {v} | {rec} | {reason} |")

    return "\n".join(sections)


def main():
    parser = argparse.ArgumentParser(description="MicroShift X/Y/Z release evaluation")
    parser.add_argument("versions", nargs="+", help="X.Y or X.Y.Z versions")
    parser.add_argument("--verbose", action="store_true",
                        help="Show detailed tables instead of one-line summary")
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
        "verbose": args.verbose,
        "timestamp": datetime.now().isoformat(),
        "lifecycle": lifecycle_data,
        "evaluations": evaluations,
    }

    if args.json_output:
        print(json.dumps(output, indent=2))
    elif args.verbose:
        print(format_text_full(output))
    else:
        print(format_text_short(evaluations))


if __name__ == "__main__":
    main()
