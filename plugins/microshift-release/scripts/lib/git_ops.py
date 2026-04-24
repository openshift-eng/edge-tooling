"""Git operations via subprocess.

All git commands run against the MicroShift repository. On first use,
the repo is cloned into _output/microshift (relative to the edge-tooling
repo root). Subsequent calls fetch updates instead of re-cloning.
"""

import logging
import os
import re
import subprocess

logger = logging.getLogger(__name__)

MICROSHIFT_REMOTE = "https://github.com/openshift/microshift.git"
_microshift_dir = None


def _get_edge_tooling_root():
    """Return the edge-tooling repository root directory."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def ensure_microshift_repo():
    """Ensure a local clone of the MicroShift repository exists.

    Clones into _output/microshift on first call, fetches on subsequent
    calls. The path is cached for the lifetime of the process.

    Returns:
        str: Absolute path to the MicroShift repo clone.
    """
    global _microshift_dir
    if _microshift_dir and os.path.isdir(_microshift_dir):
        return _microshift_dir

    output_dir = os.path.join(_get_edge_tooling_root(), "_output", "microshift")

    if os.path.isdir(os.path.join(output_dir, ".git")):
        logger.info("Updating MicroShift repo at %s...", output_dir)
        subprocess.run(
            ["git", "fetch", "--all", "--tags", "--prune"],
            cwd=output_dir, capture_output=True, text=True,
        )
    else:
        logger.info("Cloning MicroShift repo to %s...", output_dir)
        os.makedirs(os.path.dirname(output_dir), exist_ok=True)
        subprocess.run(
            ["git", "clone", "--filter=blob:none", "--sparse", MICROSHIFT_REMOTE, output_dir],
            capture_output=True, text=True, check=True,
        )
        subprocess.run(
            ["git", "sparse-checkout", "set", "scripts/advisory_publication"],
            cwd=output_dir, capture_output=True, text=True, check=True,
        )

    _microshift_dir = output_dir
    return _microshift_dir


def get_repo_root():
    """Return the MicroShift repository root directory.

    Returns:
        str: Absolute path to the MicroShift repo clone.
    """
    return ensure_microshift_repo()


def list_release_branches():
    """List active release-X.Y branches from the remote.

    Returns:
        list[str]: Minor versions sorted descending, e.g., ["5.1", "5.0", "4.22", ...].
    """
    repo = ensure_microshift_repo()
    result = subprocess.run(
        ["git", "branch", "-r", "--list", "origin/release-[45].*"],
        cwd=repo, capture_output=True, text=True,
    )
    if result.returncode != 0:
        logger.warning("git branch -r failed: %s", result.stderr.strip())
        return []

    # Match only X.Y branches (not feature branches like 4.21-common-versions-update).
    # Filter to 4.14+ — MicroShift GA'd at 4.14, older branches have no MicroShift builds.
    pattern = re.compile(r"origin/release-([45]\.\d+)$")
    versions = []
    for line in result.stdout.strip().split("\n"):
        match = pattern.search(line.strip())
        if match:
            v = match.group(1)
            parts = v.split(".")
            if parts[0] == "4" and int(parts[1]) < 14:
                continue
            versions.append(v)

    versions.sort(key=lambda v: [int(x) for x in v.split(".")], reverse=True)
    return versions


def get_release_date(version):
    """Get the release date for a version from git tags.

    Tags follow the format: X.Y.Z-YYYYMMDDHHMMSS.p0

    Args:
        version: Version string, e.g., "4.21.7".

    Returns:
        str or None: Date in YYYY-MM-DD format, or None if not found.
    """
    repo = ensure_microshift_repo()
    result = subprocess.run(
        ["git", "tag", "-l", f"{version}-*"],
        cwd=repo, capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None

    # Take the latest tag for this version
    tags = result.stdout.strip().split("\n")
    tags.sort(reverse=True)
    tag = tags[0]

    # Extract date from tag: 4.21.7-202603230928.p0 → 2026-03-23
    match = re.search(rf"{re.escape(version)}-(\d{{4}})(\d{{2}})(\d{{2}})", tag)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    return None


def fetch_branch(branch):
    """Run 'git fetch origin <branch>'.

    Args:
        branch: Branch name, e.g., "release-4.21".

    Returns:
        bool: True on success, False on failure.
    """
    repo = ensure_microshift_repo()
    result = subprocess.run(
        ["git", "fetch", "origin", branch],
        cwd=repo, capture_output=True, text=True,
    )
    if result.returncode != 0:
        logger.warning("git fetch origin %s failed: %s", branch, result.stderr.strip())
        return False
    return True


def find_version_tag(version):
    """Find the git tag for a given version.

    Tags follow the format: X.Y.Z-YYYYMMDDHHMMSS.p0

    Args:
        version: Version string, e.g., "4.18.36".

    Returns:
        str or None: The latest tag for the version, or None if not found.
    """
    repo = ensure_microshift_repo()
    result = subprocess.run(
        ["git", "tag", "-l", f"{version}-*"],
        cwd=repo, capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None

    tags = result.stdout.strip().split("\n")
    tags.sort(reverse=True)
    return tags[0]


def find_nearest_version_tag(minor_version, max_z):
    """Find the nearest available version tag, searching from max_z down.

    When the exact tag for a version doesn't exist (e.g., ART hasn't pushed
    it yet), this tries previous z-stream versions until it finds one.

    Args:
        minor_version: e.g., "4.21".
        max_z: Starting z-stream number to search from, e.g., 11.

    Returns:
        tuple[str, str] or tuple[None, None]: (version, tag) if found.
    """
    for z in range(max_z, -1, -1):
        version = f"{minor_version}.{z}"
        tag = find_version_tag(version)
        if tag:
            return version, tag
    return None, None


def build_revision_range(branch, since_version=None, since_commit=None):
    """Build a git revision range for log/diff commands.

    Resolves the best available range base: version tag first, then
    since_commit hash, then the full branch.

    Args:
        branch: Branch name, e.g., "release-4.21".
        since_version: Version string to look up a tag for, or None.
        since_commit: Git commit hash fallback when no tag exists.

    Returns:
        str: Revision range, e.g., "tag..origin/branch" or "origin/branch".
    """
    tag = find_version_tag(since_version) if since_version else None
    if tag:
        return f"{tag}..origin/{branch}"
    if since_commit:
        return f"{since_commit}..origin/{branch}"
    logger.warning("No tag or commit baseline for %s — using full branch history", branch)
    return f"origin/{branch}"


def verify_commit_exists(commit_hash):
    """Check if a commit hash exists in the local MicroShift clone.

    Args:
        commit_hash: Short or full git commit hash.

    Returns:
        bool: True if the commit exists.
    """
    repo = ensure_microshift_repo()
    result = subprocess.run(
        ["git", "cat-file", "-t", commit_hash],
        cwd=repo, capture_output=True, text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "commit"


def commits_since(branch, since_version, since_commit=None):
    """Get commits on origin/<branch> since a version tag or commit.

    Uses a tag-based range (tag..origin/branch) to count only commits
    after the specified version, rather than a time-based --since filter.
    Falls back to since_commit if provided and no tag is found.

    Args:
        branch: Branch name, e.g., "release-4.21".
        since_version: Version string, e.g., "4.18.36", or None to get
            all commits on the branch.
        since_commit: Git commit hash to use as range base when the
            version tag is unavailable.

    Returns:
        list[dict]: Each dict has keys: sha, subject, date.
    """
    revision = build_revision_range(branch, since_version, since_commit)

    repo = ensure_microshift_repo()
    result = subprocess.run(
        [
            "git", "log", revision,
            "--format=%H%x00%s%x00%ai",
        ],
        cwd=repo, capture_output=True, text=True,
    )
    if result.returncode != 0:
        logger.warning("git log origin/%s failed: %s", branch, result.stderr.strip())
        return []

    commits = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\x00", 2)
        if len(parts) == 3:
            commits.append({
                "sha": parts[0][:12],
                "subject": parts[1],
                "date": parts[2].split(" ")[0],
            })

    return commits
