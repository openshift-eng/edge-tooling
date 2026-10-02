#!/usr/bin/env python3
"""Standalone Jira REST collector for cross-workstream contribution.

For a reporting window and a set of roster members, this gathers each member's
Jira activity and attributes every item to a workstream via ``workstream_map``:

* **assignee** — issues assigned to the member, updated in the window.
* **QA contact** — issues where the member is the QA contact (cf 10470).
* OCPSTRAT **roles** — items where the member is the SME (cf 10475) or assignee.

Assignee and QA queries span ``_ACTIVITY_PROJECTS``; OCPSTRAT is asked
separately because it answers a different question (strategic roles, cf 10475).

There is deliberately no comment collection. Jira Cloud omits ``emailAddress``
from comment authors, so matching a member by email could never fire — the query
returned zero records while scanning every issue in the project once per member.
See ``TestCommentCollectionIsGone``.

Items whose component maps to no workstream (unknown, or the ``Planning``
cutline component) are kept with ``workstream=None`` so the unattributed bucket
can be reported rather than silently dropped.

Attribution chain (first hit wins):
1. The issue's own components (can yield multiple workstreams)
2. The parent epic's component (via batched lookup)
3. The Jira project key (e.g., USHIFT -> MicroShift)

An issue with several mappable components fans out to one row per distinct
workstream. Every ``Activity`` record carries an ``attribution_source`` showing
which step resolved it, and unattributed records carry an
``unattributed_reason`` separating "no component and no parent epic" from
"parent epic is component-less too" — the two need fixing in different places.

Output is the ``{"activities": [...], "collector_meta": {...}}`` envelope shared
with ``collect_github.py``; ``report.py`` also accepts a bare list for snapshots
collected before the envelope existed.

The HTTP layer (a ``JiraClient``) is injected, keeping this collector's logic
pure and unit-testable without live network access.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Tuple

from _common import (
    CollectorError,
    JiraAuthError,
    JiraClient,
    Transport,
    Window,
    activity_payload,
    build_default_transport,
    jira_config_from_env,
    resolve_window,
)
from workstream_map import component_to_workstream, project_to_workstream

_QA_CONTACT_FIELD = "customfield_10470"
_SME_FIELD = "customfield_10475"

_ATTRIBUTION_FIELDS = ["key", "components", "updated", "summary", "parent"]

# Projects searched for assignee and QA-contact activity. OCPEDGE is the team's
# own project. USHIFT is MicroShift — its tickets carry no components but the
# project key alone attributes them. OCPBUGS holds the bug work, which maps by
# component (TNF, USHIFT, LVMS, TNA). Both were previously visible only when a
# PR title happened to name the ticket.
_ACTIVITY_PROJECTS: Tuple[str, ...] = ("OCPEDGE", "USHIFT", "OCPBUGS")


# Why an issue ended up unattributed. These are different problems with different
# fixes, so the data-quality report separates them: the first needs a component on
# the ticket itself, the second needs one on the epic above it.
UNATTRIBUTED_NO_PARENT = "no_component_no_parent"
UNATTRIBUTED_PARENT_EMPTY = "parent_also_empty"


@dataclass(frozen=True)
class Activity:
    """One attributed contribution by one member.

    ``workstream`` is ``None`` for items that could not be mapped (unattributed).
    ``attribution_source`` is ``None`` exactly when ``workstream`` is ``None``.
    Valid sources: "component", "parent", "project".

    ``unattributed_reason`` is set exactly when ``workstream`` is ``None``, and is
    one of ``UNATTRIBUTED_NO_PARENT`` / ``UNATTRIBUTED_PARENT_EMPTY``.
    """

    member: str
    workstream: Optional[str]
    kind: str
    issue_key: str
    url: str
    ts: Optional[str]
    attribution_source: Optional[str] = None
    unattributed_reason: Optional[str] = None


# --- JQL builders -----------------------------------------------------------


def _window_clause(window: Window) -> str:
    return f'updated >= "{window.start.isoformat()}" AND updated <= "{window.end.isoformat()}"'


def _activity_project_clause() -> str:
    return f"project in ({', '.join(_ACTIVITY_PROJECTS)})"


def assignee_jql(member: str, window: Window) -> str:
    return (
        f'{_activity_project_clause()} AND assignee = "{member}" '
        f"AND {_window_clause(window)}"
    )


def qa_contact_jql(member: str, window: Window) -> str:
    return (
        f'{_activity_project_clause()} AND cf[10470] = "{member}" '
        f"AND {_window_clause(window)}"
    )


def ocpstrat_role_jql(member: str, window: Window) -> str:
    return (
        f'project = OCPSTRAT AND (cf[10475] = "{member}" OR assignee = "{member}") '
        f"AND {_window_clause(window)}"
    )


# --- Parent resolution (batched) --------------------------------------------


def resolve_parent_components(
    client: JiraClient, parent_keys: List[str], batch_size: int = 100
) -> Dict[str, Optional[str]]:
    """Map each parent key to its workstream via batched ``key in (...)`` queries.

    Keys absent from Jira resolve to ``None``. Returns an empty dict for empty
    input without making any request. ``JiraAuthError`` propagates; other
    ``CollectorError`` instances skip just that batch and leave those keys
    mapping to ``None``.
    """
    if not parent_keys:
        return {}
    mapping: Dict[str, Optional[str]] = {key: None for key in parent_keys}
    for i in range(0, len(parent_keys), batch_size):
        batch = parent_keys[i : i + batch_size]
        jql = f"key in ({','.join(batch)})"
        try:
            issues = client.search(jql, ["key", "components"])
        except JiraAuthError:
            raise
        except CollectorError:
            continue
        for issue in issues:
            key = issue.get("key")
            if key:
                components = _issue_components(issue)
                for component_name in components:
                    workstream = component_to_workstream(component_name)
                    if workstream:
                        mapping[key] = workstream
                        break
    return mapping


# --- Attribution (pure) -----------------------------------------------------


def _issue_components(issue: dict) -> List[str]:
    components = issue.get("fields", {}).get("components") or []
    return [component["name"] for component in components if component.get("name")]


def attribute_issue(
    issue: dict, parent_workstreams: Dict[str, Optional[str]]
) -> tuple[List[str], Optional[str]]:
    """Return (workstream acronyms, attribution_source) for one issue.

    Attribution chain (first hit wins):
    1. Issue's own components (can yield multiple workstreams) -> "component"
    2. Parent epic's component (via resolved map) -> "parent"
    3. Jira project key (e.g., USHIFT -> MicroShift) -> "project"

    Returns ``([], None)`` when nothing matches.
    """
    # Step 1: issue's own components
    mapped: List[str] = []
    for component_name in _issue_components(issue):
        workstream = component_to_workstream(component_name)
        if workstream and workstream not in mapped:
            mapped.append(workstream)
    if mapped:
        return (mapped, "component")

    # Step 2: parent epic's component
    parent = issue.get("fields", {}).get("parent")
    if parent:
        parent_key = parent.get("key")
        if parent_key:
            parent_workstream = parent_workstreams.get(parent_key)
            if parent_workstream:
                return ([parent_workstream], "parent")

    # Step 3: Jira project key
    issue_key = issue.get("key")
    if issue_key:
        project_workstream = project_to_workstream(issue_key)
        if project_workstream:
            return ([project_workstream], "project")

    # Dead end
    return ([], None)


def unattributed_reason(issue: dict) -> str:
    """Return why ``issue`` could not be attributed, for the data-quality report.

    Only meaningful for issues that ``attribute_issue`` returned no workstream for.
    An issue that names a parent epic but still ended up unattributed means the
    epic was itself component-less — a fixable gap one level up. Note that a parent
    Jira refused to return (deleted, or a failed lookup batch) is indistinguishable
    from a component-less one here and is counted the same way.
    """
    parent = issue.get("fields", {}).get("parent") or {}
    if parent.get("key"):
        return UNATTRIBUTED_PARENT_EMPTY
    return UNATTRIBUTED_NO_PARENT


def _extract_parent_keys(issues: List[dict]) -> List[str]:
    """Extract unique parent keys from a list of issues."""
    parent_keys: List[str] = []
    seen = set()
    for issue in issues:
        parent = issue.get("fields", {}).get("parent")
        if parent:
            parent_key = parent.get("key")
            if parent_key and parent_key not in seen:
                parent_keys.append(parent_key)
                seen.add(parent_key)
    return parent_keys


def issue_to_activities(
    issue: dict,
    member: str,
    kind: str,
    base_url: str,
    parent_workstreams: Dict[str, Optional[str]],
    timestamp: Optional[str] = None,
) -> List[Activity]:
    """Turn one issue into per-workstream activities (unattributed if unmappable)."""
    issue_key = issue.get("key", "")
    url = f"{base_url}/browse/{issue_key}"
    when = timestamp if timestamp is not None else issue.get("fields", {}).get("updated")
    workstreams, attribution_source = attribute_issue(issue, parent_workstreams)
    if not workstreams:
        return [
            Activity(member, None, kind, issue_key, url, when, None, unattributed_reason(issue))
        ]
    return [
        Activity(member, workstream, kind, issue_key, url, when, attribution_source)
        for workstream in workstreams
    ]


# --- Orchestration (injected client) ----------------------------------------


def collect_assignee_activity(client: JiraClient, member: str, window: Window) -> List[Activity]:
    issues = client.search(assignee_jql(member, window), _ATTRIBUTION_FIELDS)
    parent_keys = _extract_parent_keys(issues)
    parent_workstreams = resolve_parent_components(client, parent_keys)
    return _flatten(
        issue_to_activities(issue, member, "assignee", client.base_url, parent_workstreams)
        for issue in issues
    )


def collect_qa_activity(client: JiraClient, member: str, window: Window) -> List[Activity]:
    issues = client.search(qa_contact_jql(member, window), _ATTRIBUTION_FIELDS)
    parent_keys = _extract_parent_keys(issues)
    parent_workstreams = resolve_parent_components(client, parent_keys)
    return _flatten(
        issue_to_activities(issue, member, "qa", client.base_url, parent_workstreams)
        for issue in issues
    )


def collect_ocpstrat_role_activity(
    client: JiraClient, member: str, window: Window
) -> List[Activity]:
    issues = client.search(ocpstrat_role_jql(member, window), _ATTRIBUTION_FIELDS)
    parent_keys = _extract_parent_keys(issues)
    parent_workstreams = resolve_parent_components(client, parent_keys)
    return _flatten(
        issue_to_activities(issue, member, "ocpstrat_role", client.base_url, parent_workstreams)
        for issue in issues
    )


def collect_member(client: JiraClient, member: str, window: Window) -> List[Activity]:
    """Collect all Jira activity for one member within the window."""
    return [
        *collect_assignee_activity(client, member, window),
        *collect_qa_activity(client, member, window),
        *collect_ocpstrat_role_activity(client, member, window),
    ]


def collect_roster(client: JiraClient, members: List[str], window: Window) -> List[Activity]:
    """Collect Jira activity for every member in the roster."""
    activities: List[Activity] = []
    for member in members:
        activities.extend(collect_member(client, member, window))
    return activities


def _flatten(activity_lists) -> List[Activity]:
    return [activity for activities in activity_lists for activity in activities]


def build_jira_client(
    env: Dict[str, str], transport: Optional[Transport] = None
) -> JiraClient:
    """Build a ``JiraClient`` from the environment.

    Credentials are validated first, so a missing token raises before any
    transport is constructed or any request is sent.
    """
    config = jira_config_from_env(env)
    transport = transport or build_default_transport(config)
    return JiraClient(config, transport)


# --- CLI --------------------------------------------------------------------


def _member_list_from_args(members_arg: Optional[str], members_file: Optional[str]) -> List[str]:
    if members_arg:
        return [member.strip() for member in members_arg.split(",") if member.strip()]
    if members_file:
        with open(members_file, encoding="utf-8") as handle:
            roster = json.load(handle)
        return [entry["jira_username"] for entry in roster]
    raise SystemExit("provide --members or --members-file")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Collect Jira contribution activity.")
    parser.add_argument("--members", help="comma-separated Jira usernames")
    parser.add_argument("--members-file", help="roster.json produced by load_context.py")
    parser.add_argument("--quarter", help="reporting quarter, e.g. 2026Q2")
    parser.add_argument("--from", dest="from_date", help="window start, YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", help="window end, YYYY-MM-DD")
    parser.add_argument("--output", default="jira_activity.json")
    args = parser.parse_args(argv)

    window = resolve_window(args.quarter, args.from_date, args.to_date)
    members = _member_list_from_args(args.members, args.members_file)
    client = build_jira_client(os.environ)

    activities = collect_roster(client, members, window)
    # No collector-level counters on the Jira side: every data-quality signal here
    # is recoverable from the records themselves.
    payload = activity_payload([asdict(activity) for activity in activities])
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
