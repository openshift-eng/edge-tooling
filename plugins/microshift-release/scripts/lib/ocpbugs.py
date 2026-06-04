"""OCPBUGS commit scanning for MicroShift bugs.

Extracts OCPBUGS references from git commit messages on release branches.
Jira enrichment (status, labels, release action) is handled at the skill
level via Atlassian MCP OAuth — this module only does local git operations.
"""

import logging
import re
import subprocess

from lib.git_ops import ensure_microshift_repo, build_revision_range

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
                        since_commit=None):
    """Scan commits for OCPBUGS references.

    Returns unenriched bug entries — Jira enrichment (summary, status,
    labels, release action) is done by the skill via Atlassian MCP.

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
    for key in sorted(commit_bug_keys):
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

    return {
        "count": len(all_bugs),
        "bugs": all_bugs,
        "release_required": 0,
        "release_not_required": 0,
        "needs_review": len(all_bugs),
        "skipped": False,
        "error": None,
    }
