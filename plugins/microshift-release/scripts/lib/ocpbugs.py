"""OCPBUGS discovery and enrichment for MicroShift bugs.

Discovers bugs from git commit messages and MicroShift component CVE
trackers via the Jira REST API. Enriches commit-discovered bugs with
real status/labels when JIRA_API_TOKEN is available, degrades to
unenriched output when not set.
"""

import logging
import re
import subprocess

from lib.git_ops import ensure_microshift_repo, build_revision_range
from lib import jira_client

logger = logging.getLogger(__name__)

_OCPBUGS_RE = re.compile(r"OCPBUGS-\d+")


def extract_bugs_from_commits(branch, since_version, since_commit=None):
    """Extract OCPBUGS references from commit messages since a version.

    Args:
        branch: Branch name, e.g., "release-4.21".
        since_version: Version string, e.g., "4.18.36", or None.
        since_commit: Git commit hash to use as range base when the
            version tag is unavailable.

    Returns:
        set[str]: Unique OCPBUGS keys found in commits, e.g., {"OCPBUGS-12345"}.
    """
    revision = build_revision_range(branch, since_version, since_commit)

    repo = ensure_microshift_repo()
    try:
        result = subprocess.run(
            ["git", "log", revision, "--format=%B"],
            cwd=repo, capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        logger.warning("git log for OCPBUGS extraction timed out")
        return set()
    if result.returncode != 0:
        logger.warning("git log for OCPBUGS extraction failed: %s", result.stderr.strip())
        return set()

    return set(_OCPBUGS_RE.findall(result.stdout))


def query_resolved_bugs(version, branch=None, since_version=None,
                        since_commit=None, last_release_date=None,
                        shipped_cve_ids=None):
    """Scan commits and Jira for OCPBUGS references.

    Enriches commit-discovered bugs via Jira REST API when credentials
    are available. Also discovers MicroShift component CVE trackers.

    Args:
        version: Full version, e.g., "4.21.8".
        branch: Branch name for commit scanning, e.g., "release-4.21".
        since_version: Last released version for commit range, or None.
        since_commit: Git commit hash to use as range base when the
            version tag is unavailable.

    Returns:
        dict: {"count": int, "bugs": list[dict], "skipped": bool, "error": str|None}
    """
    commit_bug_keys = set()
    if branch:
        commit_bug_keys = extract_bugs_from_commits(
            branch, since_version, since_commit=since_commit,
        )
        if commit_bug_keys:
            logger.info("Found %d OCPBUGS references in commits: %s",
                        len(commit_bug_keys), ", ".join(sorted(commit_bug_keys)))

    all_bugs = []
    enrichment = jira_client.enrich_ocpbugs(sorted(commit_bug_keys))
    for key in sorted(commit_bug_keys):
        if enrichment and key in enrichment:
            data = enrichment[key]
            all_bugs.append({
                "key": key,
                "summary": data["summary"],
                "status": data["status"],
                "source": "commit",
                "release_note": "",
                "release_note_type": "",
                "release_note_status": "",
                "labels": data["labels"],
                "release_action": data["release_action"],
            })
        else:
            all_bugs.append({
                "key": key,
                "summary": "Pending Jira lookup",
                "status": "unknown",
                "source": "commit",
                "release_note": "",
                "release_note_type": "",
                "release_note_status": "",
                "labels": [],
                "release_action": "needs_review",
            })

    # Search for MicroShift component CVEs via Jira REST API
    minor = ".".join(version.split(".")[:2])
    component_cves = jira_client.find_microshift_component_cves(
        minor, resolved_after=last_release_date
    )
    jira_unavailable = component_cves is None
    shipped = shipped_cve_ids or {}
    for bug in (component_cves or []):
        if bug["key"] in commit_bug_keys:
            continue
        if bug.get("cve_id") and bug["cve_id"] in shipped:
            logger.info("Skipping %s (%s) — CVE already shipped",
                        bug["key"], bug["cve_id"])
            continue
        all_bugs.append(bug)
        commit_bug_keys.add(bug["key"])

    release_required = sum(
        1 for b in all_bugs if b.get("release_action") == "release_required"
    )
    release_not_required = sum(
        1 for b in all_bugs if b.get("release_action") == "release_not_required"
    )
    needs_review = sum(
        1 for b in all_bugs if b.get("release_action") == "needs_review"
    )

    enrichment_failed = enrichment is None and len(commit_bug_keys) > 0

    return {
        "count": len(all_bugs),
        "bugs": all_bugs,
        "release_required": release_required,
        "release_not_required": release_not_required,
        "needs_review": needs_review,
        "skipped": jira_unavailable or enrichment_failed,
        "error": ("Jira enrichment unavailable" if enrichment_failed
                  else "Jira CVE search unavailable" if jira_unavailable
                  else None),
    }
