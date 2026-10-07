#!/usr/bin/env python3
"""Canonical OpenShift Edge workstream -> Jira component map.

This is the single in-repo source of truth for the six workstreams and the Jira
OCPEDGE components that belong to each. It is intentionally a code constant so the
plugin works without waiting on an edge-context documentation PR.

The map is list-valued because live OCPEDGE components are not 1:1 with
workstreams: TNF owns both ``TNF`` and ``Two Node Fencing``; TOPO owns both
``Topology Transitions`` and ``Mutable Topology``. The ``Planning`` component is
not a workstream and is intentionally absent, so it resolves to ``None``.

This module also handles attribution recovery for work that lacks Jira components:
- Jira project key -> workstream (e.g., the USHIFT project is MicroShift work)
- GitHub repo -> workstream (e.g., openshift/lvm-operator is LVMS work)
- Shared repos (cross-cutting tooling/docs) that belong to no single workstream
- Personal-namespace repo exclusion (side projects not representing team work)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Tuple


@dataclass(frozen=True)
class Workstream:
    """A long-lived stream of Edge engineering work and its Jira components."""

    acronym: str
    name: str
    components: Tuple[str, ...]


# Order here defines the canonical column order used by metrics and renderers.
_WORKSTREAMS: Tuple[Workstream, ...] = (
    Workstream("SNO", "Single Node", ("SNO",)),
    Workstream("TNA", "Two Node with Arbiter", ("Two Node with Arbiter",)),
    Workstream("TNF", "Two Node Fencing", ("TNF", "Two Node Fencing")),
    Workstream("LVMS", "Logical Volume Manager Storage", ("Logical Volume Manager Storage",)),
    Workstream("USHIFT", "MicroShift", ("MicroShift",)),
    Workstream("TOPO", "Topology Transitions", ("Topology Transitions", "Mutable Topology")),
)

# Jira components that exist in OCPEDGE but are deliberately not workstreams.
# Kept for documentation; they resolve to None like any other unmapped component.
NON_WORKSTREAM_COMPONENTS: Tuple[str, ...] = ("Planning",)

# The SHARED pseudo-column for cross-cutting work that belongs to no single workstream.
SHARED_COLUMN: str = "SHARED"

# Jira project key -> workstream acronym. Seed with projects whose tickets are
# unambiguously attributable even without component metadata.
# The USHIFT Jira project's display name is literally "MicroShift" (verified
# against redhat.atlassian.net; e.g., USHIFT-6337, USHIFT-6685), so a ticket in
# that project is MicroShift work by definition even with no component.
_PROJECT_INDEX: Dict[str, str] = {
    "USHIFT": "USHIFT",
}

# GitHub repo (owner/name) -> workstream acronym. Only repos that unambiguously
# belong to a single workstream are mapped here.
# IMPORTANT: openshift-eng/two-node-toolbox is DELIBERATELY ABSENT. That repo
# covers BOTH arbiter (TNA) and fencing (TNF) topologies, so mapping it either
# way would mis-credit work. This was an explicit product decision, not an
# oversight — do not add it.
_REPO_INDEX: Dict[str, str] = {
    "openshift/lvm-operator": "LVMS",      # LVMS operator core
    "openshift/topolvm": "LVMS",           # LVMS storage provisioner
    "openshift/microshift": "USHIFT",      # MicroShift upstream
    "microshift-io/microshift": "USHIFT",  # MicroShift community fork
    "openshift/oc-tnf": "TNF",             # Two-Node Fencing test suite
}

# Repos that are cross-cutting (CI/tooling/docs) and belong to no single workstream.
_SHARED_REPOS: FrozenSet[str] = frozenset([
    "openshift/release",
    "openshift-eng/edge-tooling",
    "openshift-eng/edge-context",
    "openshift/openshift-docs",
    "openshift/enhancements",
])

# GitHub org allowlist for identifying team repos. Repos whose owner is NOT in
# this set are considered personal namespaces (side projects) and are excluded
# from attribution. This is an allowlist rather than a denylist of people because
# new personal forks appear constantly, while new orgs are rare.
_ALLOWED_ORGS: FrozenSet[str] = frozenset([
    "openshift",
    "openshift-eng",
    "microshift-io",
    "openshift-metal3",
    "metal3-io",
    "containers",
    "clusterlabs",
    "ovn-kubernetes",
    "opendatahub-io",
    "kubevirt",
])


def _normalize(component_name: str) -> str:
    return component_name.strip().lower()


_COMPONENT_INDEX: Dict[str, str] = {
    _normalize(component): stream.acronym
    for stream in _WORKSTREAMS
    for component in stream.components
}


def workstreams() -> Tuple[Workstream, ...]:
    """Return the six workstreams in canonical order."""
    return _WORKSTREAMS


def workstream_acronyms() -> List[str]:
    """Return the six workstream acronyms in canonical order."""
    return [stream.acronym for stream in _WORKSTREAMS]


def component_to_workstream(component_name: Optional[str]) -> Optional[str]:
    """Map a Jira component name to a workstream acronym.

    Returns ``None`` for a genuine miss: unknown components, non-workstream
    components such as ``Planning``, and empty/``None`` input. Lookup is
    case-insensitive and ignores surrounding whitespace.
    """
    if component_name is None:
        return None
    key = _normalize(component_name)
    if not key:
        return None
    return _COMPONENT_INDEX.get(key)


def display_columns() -> List[str]:
    """Return the six workstream acronyms plus SHARED, in canonical order.

    This is the full column list for the heatmap display. The SHARED column
    captures cross-cutting work (CI/tooling/docs) that belongs to no single
    workstream. Returns a NEW list each call so callers cannot mutate module state.
    """
    return workstream_acronyms() + [SHARED_COLUMN]


def project_to_workstream(issue_key: Optional[str]) -> Optional[str]:
    """Map a Jira issue key to a workstream acronym via its project prefix.

    Extracts the project key from a full issue identifier (e.g., "USHIFT-6337"
    -> "USHIFT") and looks it up in the project index. Returns ``None`` for
    ``None``, empty string, malformed keys (no "-"), or unknown projects.
    Lookup is case-insensitive and whitespace-tolerant.

    Args:
        issue_key: Full Jira issue key like "USHIFT-6337" or "OCPEDGE-123"

    Returns:
        Workstream acronym if the project is mapped, else ``None``
    """
    if issue_key is None:
        return None
    normalized = issue_key.strip().upper()
    if not normalized or "-" not in normalized:
        return None
    project_key = normalized.split("-", 1)[0]
    return _PROJECT_INDEX.get(project_key)


def repo_to_workstream(repo: Optional[str]) -> Optional[str]:
    """Map a GitHub repo (owner/name) to a workstream acronym.

    Only repos that unambiguously belong to a single workstream are mapped.
    Returns ``None`` for ``None``, empty string, or unknown repos. Lookup is
    case-insensitive and whitespace-tolerant.

    Args:
        repo: GitHub repository in "owner/name" format

    Returns:
        Workstream acronym if the repo is mapped, else ``None``
    """
    if repo is None:
        return None
    normalized = repo.strip().lower()
    if not normalized:
        return None
    return _REPO_INDEX.get(normalized)


def is_shared_repo(repo: Optional[str]) -> bool:
    """Check if a GitHub repo is cross-cutting (CI/tooling/docs).

    Shared repos belong to no single workstream and are credited to the SHARED
    column instead. Lookup is case-insensitive.

    Args:
        repo: GitHub repository in "owner/name" format

    Returns:
        ``True`` if the repo is shared, ``False`` otherwise
    """
    if repo is None:
        return False
    normalized = repo.strip().lower()
    if not normalized:
        return False
    return normalized in _SHARED_REPOS


def is_excluded_repo(repo: Optional[str]) -> bool:
    """Check if a GitHub repo is a personal-namespace side project.

    Repos whose owner is NOT in the org allowlist are considered personal
    namespaces and are excluded from attribution. Returns ``False`` for
    ``None``, empty string, or malformed input (no "/") to avoid excluding
    something we cannot parse. Org comparison is case-insensitive.

    Args:
        repo: GitHub repository in "owner/name" format

    Returns:
        ``True`` if the repo should be excluded, ``False`` otherwise
    """
    if repo is None:
        return False
    normalized = repo.strip().lower()
    if not normalized or "/" not in normalized:
        return False
    owner = normalized.split("/", 1)[0]
    return owner not in _ALLOWED_ORGS
