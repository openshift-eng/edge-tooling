#!/usr/bin/env python3
"""CVE and repository extraction helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

CVE_RE = re.compile(r"CVE-\d{4}-\d{4,}", re.IGNORECASE)
# OCP minor only (4.17, 5.2, also "4.18.z" Jira names). Reject longer numeric
# semver like "4.1.4" (upstream library versions) so a patch level is never
# mistaken for an OpenShift release - "(?!\.\d)" still allows the trailing
# ".z" used in Jira Affected Version fields.
OCP_VERSION_RE = re.compile(r"(?<![\d.])(4\.\d{1,2}|5\.\d)(?!\.\d)")
GO_MODULE_RE = re.compile(r"(?:module\s+)?([\w./-]+)\s+v[\d.]+(?:\+incompatible)?")


def extract_cve_ids(*texts: str) -> list[str]:
    """Return unique CVE IDs found across texts, preserving first-seen order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for text in texts:
        if not text:
            continue
        for match in CVE_RE.finditer(text):
            cve = match.group(0).upper()
            if cve not in seen:
                seen.add(cve)
                ordered.append(cve)
    return ordered


def extract_ocp_versions(*texts: str) -> list[str]:
    """Extract OCP minor versions like 4.17 from text fields."""
    seen: set[str] = set()
    ordered: list[str] = []
    for text in texts:
        if not text:
            continue
        for match in OCP_VERSION_RE.finditer(text):
            version = match.group(1)
            if version not in seen:
                seen.add(version)
                ordered.append(version)
    return ordered


def load_component_config(config_path: Path) -> dict[str, Any]:
    with open(config_path, encoding="utf-8") as fh:
        return json.load(fh)


def extract_repo_urls(text: str, patterns: list[str]) -> list[dict[str, str]]:
    """Find repository references in free text."""
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for pattern in patterns:
        regex = re.compile(pattern)
        for match in regex.finditer(text or ""):
            org = match.group("org")
            repo = match.group("repo").split("/")[0]
            if repo.endswith(".git"):
                repo = repo[:-4]
            slug = f"{org}/{repo}"
            if slug in seen:
                continue
            seen.add(slug)
            found.append(
                {
                    "org": org,
                    "repo": repo,
                    "slug": slug,
                    "url": f"https://github.com/{slug}.git",
                }
            )
    return found


def resolve_component_repo(
    component: str,
    config: dict[str, Any],
) -> dict[str, Any] | None:
    """Look up default repo metadata for a Jira component name."""
    components = config.get("components", {})
    entry = components.get(component)
    if not entry:
        return None
    repo = entry["repo"]
    if "/" in repo:
        org, name = repo.split("/", 1)
    else:
        org = config.get("defaults", {}).get("org", "openshift")
        name = repo
    return {
        "org": org,
        "repo": name,
        "slug": f"{org}/{name}",
        "url": f"https://github.com/{org}/{name}.git",
        "language": entry.get("language", "go"),
        "version_ref_template": entry.get("version_ref_template", "release-{version}"),
        "version_ref_fallbacks": entry.get("version_ref_fallbacks", []),
        "source": "component-map",
    }


def resolve_git_refs(
    versions: list[str],
    repo_meta: dict[str, Any],
) -> list[str]:
    """Build candidate git refs from OCP versions and repo metadata.

    Prefer the ticket's own release versions (e.g. release-4.18 from a
    version_ref_template of "release-{version}"). Do NOT invent a default
    like "main"/"master" here - those tip-of-tree branches capture far more
    than the ticket is asking about. version_ref_fallbacks is reserved for
    rare cases (e.g. a component whose only branch is unversioned) and is
    empty by default for versioned edge components.
    """
    refs: list[str] = []
    template = repo_meta.get("version_ref_template", "")
    fallbacks = repo_meta.get("version_ref_fallbacks", [])

    if versions:
        for version in versions:
            if template:
                refs.append(template.format(version=version))
    elif fallbacks:
        # Only fall back when the ticket itself has no version at all.
        # Never append fallbacks on top of version-derived refs.
        refs.extend(fallbacks)
    elif template and "{version}" not in template:
        # Unversioned component whose template is a fixed branch (e.g. "main"
        # for two-node-toolbox) - use that single branch as-is.
        refs.append(template)

    # Preserve order, drop duplicates.
    seen: set[str] = set()
    unique: list[str] = []
    for ref in refs:
        if ref and ref not in seen:
            seen.add(ref)
            unique.append(ref)
    return unique


def extract_go_modules(text: str) -> list[str]:
    """Extract Go module paths mentioned in ticket text."""
    modules: list[str] = []
    seen: set[str] = set()
    for match in GO_MODULE_RE.finditer(text or ""):
        module = match.group(1)
        if module not in seen:
            seen.add(module)
            modules.append(module)
    return modules


def primary_component(components: list[str]) -> str:
    return components[0] if components else "Unknown"


def is_private_ticket(issue: dict[str, Any]) -> bool:
    """Deterministically decide whether a ticket is labeled private.

    Checks the Jira "Security Level" field and the ticket's labels for
    anything containing "private" (case-insensitive) - covers Jira instances
    that mark restricted CVE tickets either way. Callers must not render CVE
    IDs, summaries, or scan findings for tickets this flags, only a link back
    to the Jira ticket.
    """
    security_level = str(issue.get("security_level", "")).strip().lower()
    if "private" in security_level:
        return True
    for label in issue.get("labels", []) or []:
        if "private" in str(label).strip().lower():
            return True
    return False


def ticket_versions(issue: dict[str, Any]) -> list[str]:
    """Resolve affected OCP versions from structured Jira fields and summary.

    Prefer Jira's Affected Version / Fix Version fields, then tokens in the
    summary (e.g. "[openshift-4.23]"). The description is intentionally NOT
    scanned - CVE writeups routinely mention upstream library versions like
    "Prior to 4.1.4" that are not OpenShift releases.
    """
    versions = list(issue.get("affected_versions", []))
    versions.extend(issue.get("fix_versions", []))
    extracted = extract_ocp_versions(issue.get("summary", ""))

    # Normalize Jira version names like "4.17.z" -> "4.17"
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in versions + extracted:
        m = OCP_VERSION_RE.search(str(raw))
        if not m:
            continue
        val = m.group(1)
        if val not in seen:
            seen.add(val)
            normalized.append(val)
    return normalized
