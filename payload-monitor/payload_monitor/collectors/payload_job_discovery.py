"""Discover which payload jobs are appropriate for a PR based on changed files.

Cross-references the PR's changed files against ci-operator configs in the
openshift/release repo to find matching payload jobs via pipeline_run_if_changed
regexes, then constructs the full /payload-job trigger commands.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class PayloadJobSuggestion:
    as_name: str
    periodic_name: str
    trigger_command: str
    matched_by: str
    matched_files: list[str] = field(default_factory=list)


@dataclass
class DiscoveryResult:
    pr_ref: str
    org: str
    repo: str
    branch: str
    version: str
    suggestions: list[PayloadJobSuggestion] = field(default_factory=list)
    error: Optional[str] = None


def find_release_repo() -> Optional[str]:
    """Locate the cloned openshift/release repo."""
    if env_dir := os.environ.get("RELEASE_REPO_DIR"):
        if Path(env_dir).is_dir():
            return str(Path(env_dir).resolve())
        logger.warning(f"RELEASE_REPO_DIR set but not found: {env_dir}")

    script_dir = Path(__file__).resolve().parent
    candidates = [
        script_dir / "../../../repos/release",
        Path.cwd() / "repos/release",
    ]

    home = Path.home()
    for base in ["Documents/Projects", "Projects", "src", "go/src"]:
        candidates.append(home / base / "release")
        candidates.append(home / base / "openshift" / "release")

    # Also check the dev-env repos layout
    dev_env_dirs = [
        home / "Documents/Projects/tnf-dev-env/repos/release",
    ]
    candidates.extend(dev_env_dirs)

    for c in candidates:
        ci_config = c / "ci-operator" / "config"
        if ci_config.is_dir():
            return str(c.resolve())

    return None


def _parse_pr_ref(pr_ref: str) -> Optional[tuple[str, str, str]]:
    """Parse a PR reference into (org, repo, number)."""
    m = re.match(
        r'(?:https?://github\.com/)?([^/]+)/([^/#]+)(?:/pull/|#)(\d+)', pr_ref
    )
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)


def _get_pr_branch(org: str, repo: str, number: str) -> str:
    """Get the base branch of a PR via gh CLI."""
    try:
        result = subprocess.run(
            ["gh", "pr", "view", number, "--repo", f"{org}/{repo}",
             "--json", "baseRefName", "--jq", ".baseRefName"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return "main"


def detect_version(release_repo: str, branch: str) -> Optional[str]:
    """Map a branch name to an OCP version.

    release-X.Y → X.Y
    main/master → latest nightly config version
    """
    m = re.match(r'release-(\d+\.\d+)', branch)
    if m:
        return m.group(1)

    nightly_dir = Path(release_repo) / "ci-operator/config/openshift/release"
    if not nightly_dir.is_dir():
        return None

    pattern = re.compile(r'openshift-release-main__nightly-(\d+\.\d+)\.yaml$')
    versions = []
    for f in nightly_dir.iterdir():
        m = pattern.match(f.name)
        if m:
            versions.append(m.group(1))

    if not versions:
        return None

    def version_key(v: str) -> tuple[int, ...]:
        return tuple(int(x) for x in v.split("."))

    versions.sort(key=version_key)
    return versions[-1]


def load_repo_ci_config(
    release_repo: str, org: str, repo: str, branch: str
) -> list[dict]:
    """Load test entries from a repo's ci-operator config in the release repo."""
    config_dir = Path(release_repo) / "ci-operator/config" / org / repo
    config_name = f"{org}-{repo}-{branch}.yaml"
    config_path = config_dir / config_name

    if not config_path.exists():
        logger.warning(f"CI config not found: {config_path}")
        return []

    with open(config_path) as f:
        data = yaml.safe_load(f)

    if not data or "tests" not in data:
        return []

    return data["tests"]


def match_changed_files(
    test_entries: list[dict], changed_files: list[str]
) -> list[dict]:
    """Find test entries whose pipeline_run_if_changed matches any changed file."""
    matched = []
    for entry in test_entries:
        regex_str = entry.get("pipeline_run_if_changed") or entry.get("run_if_changed")
        if not regex_str:
            continue

        try:
            pattern = re.compile(regex_str)
        except re.error as e:
            logger.debug(f"Bad regex in {entry.get('as', '?')}: {e}")
            continue

        hits = [f for f in changed_files if pattern.search(f)]
        if hits:
            matched.append({**entry, "_matched_files": hits, "_matched_by": regex_str})

    return matched


def load_nightly_job_names(release_repo: str, version: str) -> set[str]:
    """Load the set of test `as` names from the nightly config for a version."""
    config_path = (
        Path(release_repo)
        / "ci-operator/config/openshift/release"
        / f"openshift-release-main__nightly-{version}.yaml"
    )
    if not config_path.exists():
        logger.warning(f"Nightly config not found: {config_path}")
        return set()

    with open(config_path) as f:
        data = yaml.safe_load(f)

    if not data or "tests" not in data:
        return set()

    return {t["as"] for t in data["tests"] if "as" in t}


def validate_existing_command(
    trigger_command: str, suggestions: list[PayloadJobSuggestion]
) -> dict:
    """Check if an existing /payload-job command matches any discovered job."""
    job_name = trigger_command.replace("/payload-job", "").strip()

    for s in suggestions:
        if s.periodic_name == job_name or s.as_name in job_name:
            return {
                "existing_command": trigger_command,
                "is_valid": True,
                "note": f"Matches discovered job {s.as_name}",
            }

    suggestion_names = [s.as_name for s in suggestions]
    return {
        "existing_command": trigger_command,
        "is_valid": False,
        "note": f"Does not match any discovered job. Expected one of: {suggestion_names}",
    }


def discover_payload_jobs(
    pr_ref: str,
    changed_files: list[str],
    release_repo: str,
) -> DiscoveryResult:
    """Discover which payload jobs are appropriate for a PR's changed files."""
    parsed = _parse_pr_ref(pr_ref)
    if not parsed:
        return DiscoveryResult(
            pr_ref=pr_ref, org="", repo="", branch="", version="",
            error=f"Could not parse PR reference: {pr_ref}",
        )
    org, repo, number = parsed

    branch = _get_pr_branch(org, repo, number)
    logger.info(f"PR {org}/{repo}#{number} targets branch: {branch}")

    version = detect_version(release_repo, branch)
    if not version:
        return DiscoveryResult(
            pr_ref=pr_ref, org=org, repo=repo, branch=branch, version="",
            error="Could not determine OCP version from branch",
        )
    logger.info(f"Detected OCP version: {version}")

    test_entries = load_repo_ci_config(release_repo, org, repo, branch)
    if not test_entries:
        return DiscoveryResult(
            pr_ref=pr_ref, org=org, repo=repo, branch=branch, version=version,
            error=f"No CI config found for {org}/{repo} branch {branch}",
        )
    logger.info(f"Loaded {len(test_entries)} test entries from CI config")

    matched = match_changed_files(test_entries, changed_files)
    if not matched:
        return DiscoveryResult(
            pr_ref=pr_ref, org=org, repo=repo, branch=branch, version=version,
        )

    nightly_names = load_nightly_job_names(release_repo, version)
    logger.info(f"Nightly config has {len(nightly_names)} jobs for version {version}")

    suggestions = []
    for entry in matched:
        as_name = entry.get("as", "")
        if not as_name:
            continue

        if as_name not in nightly_names:
            logger.debug(f"Job {as_name} not in nightly config — skipping")
            continue

        periodic_name = (
            f"periodic-ci-openshift-release-main-nightly-{version}-{as_name}"
        )
        suggestions.append(PayloadJobSuggestion(
            as_name=as_name,
            periodic_name=periodic_name,
            trigger_command=f"/payload-job {periodic_name}",
            matched_by=entry["_matched_by"],
            matched_files=entry["_matched_files"],
        ))

    logger.info(f"Discovered {len(suggestions)} payload job suggestion(s)")
    return DiscoveryResult(
        pr_ref=pr_ref,
        org=org,
        repo=repo,
        branch=branch,
        version=version,
        suggestions=suggestions,
    )


def format_discovery_json(result: DiscoveryResult) -> str:
    """Format discovery result as JSON."""
    output = {
        "pr_ref": result.pr_ref,
        "org": result.org,
        "repo": result.repo,
        "branch": result.branch,
        "version": result.version,
        "suggestions": [
            {
                "as_name": s.as_name,
                "periodic_name": s.periodic_name,
                "trigger_command": s.trigger_command,
                "matched_by": s.matched_by,
                "matched_files": s.matched_files,
            }
            for s in result.suggestions
        ],
    }
    if result.error:
        output["error"] = result.error
    return json.dumps(output, indent=2)


def format_discovery_markdown(result: DiscoveryResult) -> str:
    """Format discovery result as readable markdown."""
    lines = []
    lines.append("## Payload Job Discovery")
    lines.append("")
    lines.append(f"**PR:** {result.pr_ref}")
    lines.append(f"**Branch:** {result.branch} (OCP {result.version})")
    lines.append("")

    if result.error:
        lines.append(f"**Error:** {result.error}")
        return "\n".join(lines)

    if not result.suggestions:
        lines.append("No matching payload jobs found for the changed files.")
        return "\n".join(lines)

    lines.append(f"Found **{len(result.suggestions)}** matching payload job(s):")
    lines.append("")

    for s in result.suggestions:
        lines.append(f"### {s.as_name}")
        lines.append(f"**Trigger:** `{s.trigger_command}`")
        lines.append(f"**Matched by:** `{s.matched_by}`")
        lines.append(f"**Files:** {', '.join(s.matched_files[:5])}")
        lines.append("")

    return "\n".join(lines)
