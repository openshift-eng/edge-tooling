"""OCPBUGS Jira queries for resolved MicroShift bugs (optional, graceful degradation)."""

import logging
import re
import subprocess

from lib.art_jira import create_jira_client, _sanitize_jql_value
from lib.git_ops import ensure_microshift_repo, build_revision_range

logger = logging.getLogger(__name__)

_OCPBUGS_RE = re.compile(r"OCPBUGS-\d+")
# Red Hat Atlassian Cloud custom fields for Release Notes
_RELEASE_NOTE_TEXT = "customfield_10783"     # Free-text release note content
_RELEASE_NOTE_TYPE = "customfield_10785"     # Type: "Rebase", "Release Note Not Required", etc.
_RELEASE_NOTE_STATUS = "customfield_10807"   # Status: "Done", "In Progress", "Not Required", etc.
_JIRA_FIELDS = (
    f"summary,status,labels,"
    f"{_RELEASE_NOTE_TEXT},{_RELEASE_NOTE_TYPE},{_RELEASE_NOTE_STATUS}"
)


def extract_bugs_from_commits(branch, since_version, since_commit=None):
    """Extract OCPBUGS references from commit messages since a version.

    Scans the full commit message (subject + body) for OCPBUGS-XXXXX patterns.

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


def _query_bugs_by_fixversion(client, version):
    """Query OCPBUGS with fixVersion targeting a specific z-stream.

    Args:
        client: Jira client instance.
        version: Full version, e.g., "4.21.8".

    Returns:
        list[dict]: Bug dicts with key, summary, status, source.
    """
    safe_version = _sanitize_jql_value(version)
    jql = (
        f"project = OCPBUGS AND component = MicroShift "
        f'AND fixVersion = "{safe_version}" '
        f"AND status in (MODIFIED, ON_QA, Verified, Closed) "
        f"ORDER BY status ASC"
    )

    issues = client.search_issues(jql, maxResults=50, fields=_JIRA_FIELDS)
    return [_issue_to_dict(issue, "fixVersion") for issue in issues]


def _query_bugs_by_keys(client, keys):
    """Look up specific OCPBUGS by key to get their status.

    Args:
        client: Jira client instance.
        keys: Set of bug keys, e.g., {"OCPBUGS-12345", "OCPBUGS-67890"}.

    Returns:
        list[dict]: Bug dicts with key, summary, status, source, release_note.
    """
    if not keys:
        return []

    keys_csv = ", ".join(sorted(keys))
    jql = (
        f"key in ({keys_csv}) "
        f"AND status in (MODIFIED, ON_QA, Verified, Closed) "
        f"ORDER BY status ASC"
    )

    issues = client.search_issues(jql, maxResults=50, fields=_JIRA_FIELDS)
    return [_issue_to_dict(issue, "commit") for issue in issues]


def _issue_to_dict(issue, source):
    """Convert a Jira issue to a bug dict.

    Args:
        issue: Jira issue object.
        source: How the bug was discovered ("fixVersion" or "commit").

    Returns:
        dict: Bug dict with key, summary, status, source, release_note,
              release_note_type, release_note_status.
    """
    rn_text = getattr(issue.fields, _RELEASE_NOTE_TEXT, None) or ""
    rn_type_obj = getattr(issue.fields, _RELEASE_NOTE_TYPE, None)
    rn_type = rn_type_obj.value if rn_type_obj and hasattr(rn_type_obj, "value") else ""
    rn_status_obj = getattr(issue.fields, _RELEASE_NOTE_STATUS, None)
    rn_status = rn_status_obj.value if rn_status_obj and hasattr(rn_status_obj, "value") else ""
    labels = getattr(issue.fields, "labels", []) or []
    has_required = "release-required" in labels
    has_not_required = "release-not-required" in labels
    if has_required and not has_not_required:
        release_action = "release_required"
    elif has_not_required and not has_required:
        release_action = "release_not_required"
    else:
        # Neither label or both labels — human must review
        release_action = "needs_review"
    return {
        "key": issue.key,
        "summary": issue.fields.summary,
        "status": str(issue.fields.status),
        "source": source,
        "release_note": rn_text.strip(),
        "release_note_type": rn_type,
        "release_note_status": rn_status,
        "labels": labels,
        "release_action": release_action,
    }


def query_resolved_bugs(version, branch=None, since_version=None,
                        since_commit=None):
    """Query OCPBUGS for resolved MicroShift bugs from two sources.

    1. Jira fixVersion query: bugs explicitly targeting this z-stream.
    2. Commit message scan: OCPBUGS references in commits since the last
       released version, looked up in Jira to confirm resolved status.

    Results are deduplicated by key. Bugs found via fixVersion take
    precedence; commit-only bugs are tagged with source="commit".

    Args:
        version: Full version, e.g., "4.21.8".
        branch: Branch name for commit scanning, e.g., "release-4.21".
        since_version: Last released version for commit range, or None.
        since_commit: Git commit hash to use as range base when the
            version tag is unavailable.

    Returns:
        dict: {"count": int, "bugs": list[dict], "skipped": bool, "error": str|None}
    """
    client = create_jira_client()
    if not client:
        return {"count": 0, "bugs": [], "skipped": True, "error": "Jira unavailable"}

    # Extract OCPBUGS from commits (only when Jira lookups can run)
    commit_bug_keys = set()
    if branch:
        commit_bug_keys = extract_bugs_from_commits(
            branch, since_version, since_commit=since_commit,
        )
        if commit_bug_keys:
            logger.info("Found %d OCPBUGS references in commits: %s",
                        len(commit_bug_keys), ", ".join(sorted(commit_bug_keys)))

    # Source 1: fixVersion query
    try:
        fixversion_bugs = _query_bugs_by_fixversion(client, version)
    except Exception as e:
        logger.warning("OCPBUGS fixVersion query failed for %s: %s", version, e)
        return {"count": 0, "bugs": [], "skipped": True,
                "release_required": 0, "release_not_required": 0,
                "needs_review": 0, "error": f"fixVersion query failed: {e}"}
    seen_keys = {b["key"] for b in fixversion_bugs}

    # Source 2: commit-referenced bugs not already found via fixVersion
    commit_only_keys = commit_bug_keys - seen_keys
    try:
        commit_bugs = _query_bugs_by_keys(client, commit_only_keys)
    except Exception as e:
        logger.warning("OCPBUGS key lookup failed for %s: %s", version, e)
        return {"count": 0, "bugs": [], "skipped": True,
                "release_required": 0, "release_not_required": 0,
                "needs_review": 0, "error": f"key lookup failed: {e}"}

    # Source 3: commit-referenced bugs that Jira didn't return
    # (security-restricted tickets, deleted, or inaccessible)
    returned_keys = {b["key"] for b in commit_bugs}
    missing_keys = commit_only_keys - returned_keys
    restricted_bugs = [
        {
            "key": key,
            "summary": "Restricted/inaccessible — review manually",
            "status": "unknown",
            "source": "commit",
            "release_note": "",
            "release_note_type": "",
            "release_note_status": "",
            "labels": [],
            "release_action": "needs_review",
        }
        for key in sorted(missing_keys)
    ]
    if restricted_bugs:
        logger.info("Found %d restricted OCPBUGS (not queryable): %s",
                    len(restricted_bugs),
                    ", ".join(b["key"] for b in restricted_bugs))

    # Merge: fixVersion bugs first, then commit-only bugs, then restricted
    all_bugs = fixversion_bugs + commit_bugs + restricted_bugs
    release_required = sum(1 for b in all_bugs if b["release_action"] == "release_required")
    needs_review = sum(1 for b in all_bugs if b["release_action"] == "needs_review")
    return {
        "count": len(all_bugs),
        "bugs": all_bugs,
        "release_required": release_required,
        "release_not_required": len(all_bugs) - release_required - needs_review,
        "needs_review": needs_review,
        "skipped": False,
        "error": None,
    }
