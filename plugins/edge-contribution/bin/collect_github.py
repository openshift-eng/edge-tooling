#!/usr/bin/env python3
"""Standalone GitHub collector for cross-workstream contribution.

For a reporting window and a set of roster members, this gathers each member's
pull-request activity via batched GraphQL queries through ``gh api graphql``,
attributing every PR to a workstream:

* **pr_authored** — PRs the member opened in ``openshift``/``openshift-eng``.
* **pr_reviewed** — PRs the member reviewed in the same orgs.

Attribution parses referenced Jira keys from the PR title and body, then resolves
every unique key in a single batch prefetch, mapping each to a workstream via its
Jira components. A PR that references no mappable workstream is kept with
``workstream=None`` so it lands in the unattributed bucket rather than being dropped.

GraphQL queries are chunked to avoid request-size limits; if a chunk fails with an
HTTP error, it is split in half and retried until down to single-slot granularity.
Pagination follows ``pageInfo.hasNextPage`` cursors until all results are fetched.

PRs in personal-namespace repos are dropped before attribution. Because a dropped
PR leaves no record behind, the drop count is written alongside the records in a
``{"activities": [...], "collector_meta": {...}}`` envelope rather than only being
printed, so the data-quality block downstream can report it.

Both impure edges — the ``gh`` command runner and the Jira client — are injected,
so this collector's logic is fully unit-testable without a subprocess or network.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import asdict, dataclass
from typing import Callable, Dict, Iterable, List, Optional

from _common import (
    CollectorError,
    CommandResult,
    CommandRunner,
    JiraAuthError,
    JiraClient,
    Window,
    activity_payload,
    build_default_runner,
    jira_config_from_env,
    run_with_rate_limit_retry,
    resolve_window,
)
from workstream_map import (
    SHARED_COLUMN,
    component_to_workstream,
    is_excluded_repo,
    is_shared_repo,
    project_to_workstream,
    repo_to_workstream,
)


class GraphqlResponseError(CollectorError):
    """A GraphQL response carried errors or omitted a requested search."""


class GraphqlRateLimitExhausted(CollectorError):
    """GraphQL rate limit is exhausted; splitting will not help."""


# Re-exported from _common so existing references (and tests) keep resolving
# ``collect_github.CommandResult`` / ``CommandRunner`` / ``build_default_runner``.
__all__ = ["CommandResult", "CommandRunner", "build_default_runner"]

# A resolver maps a Jira key to (workstream, attribution_source), or (None, None) if unmappable.
WorkstreamResolver = Callable[[str], tuple[Optional[str], Optional[str]]]

_JIRA_KEY_PATTERN = re.compile(
    r"\b(?:OCPEDGE|USHIFT|OCPBUGS|OCPSTRAT|MGMT|ETCD|RHEL|CNV)-\d+\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SearchSlot:
    """One aliased search within a batched GraphQL request."""

    alias: str
    member: str
    handle: str
    kind: str  # "pr_authored" or "pr_reviewed"
    cursor: Optional[str] = None


@dataclass(frozen=True)
class GithubActivity:
    """One attributed pull-request contribution by one member."""

    member: str
    workstream: Optional[str]
    kind: str
    repo: str
    pr_url: str
    ts: Optional[str]
    source_key: Optional[str]
    attribution_source: Optional[str] = None


def extract_jira_keys(text: str) -> List[str]:
    """Return the distinct known-project Jira keys referenced in ``text``.

    Matching is case-insensitive; keys are normalized to upper case and returned
    in first-seen order.
    """
    keys: List[str] = []
    for match in _JIRA_KEY_PATTERN.finditer(text or ""):
        key = match.group(0).upper()
        if key not in keys:
            keys.append(key)
    return keys


# --- Attribution (pure) -----------------------------------------------------


def _pr_repo(pr: dict) -> str:
    repository = pr.get("repository") or {}
    return repository.get("nameWithOwner") or repository.get("name") or ""


def tally_excluded_repos(prs: Iterable[dict]) -> Dict[str, int]:
    """Count the dropped PRs per repo, so the report can name what it lost.

    A bare total says "101 PRs vanished" without saying where. That hides the
    failure mode this exclusion actually has: the org allowlist is a hand-kept
    list, so a legitimate new org reads as a personal namespace and disappears
    silently. Naming the repos makes an unexpected one obvious on sight.
    """
    tally: Dict[str, int] = {}
    for pr in prs:
        repo = _pr_repo(pr)
        if is_excluded_repo(repo):
            tally[repo] = tally.get(repo, 0) + 1
    return tally


def pr_to_activities(
    pr: dict, member: str, kind: str, resolve_workstream: WorkstreamResolver
) -> List[GithubActivity]:
    """Turn one PR into per-workstream activities (unattributed if unmappable).

    Attribution order (first hit wins):
      a. Excluded repo → return [] (drop the PR entirely)
      b. Jira key resolution → emit one activity per distinct workstream
      c. Repo fallback → single activity with source "repo"
      d. SHARED repo → single activity with SHARED_COLUMN, source "shared"
      e. Unattributed → single activity with workstream None, source None
    """
    repo = _pr_repo(pr)
    url = pr.get("url", "")
    timestamp = pr.get("createdAt")

    # a. Drop excluded repos (personal namespaces)
    if is_excluded_repo(repo):
        return []

    # b. Try Jira key resolution
    text = f"{pr.get('title', '')}\n{pr.get('body') or ''}"
    keys = extract_jira_keys(text)
    attributions = _attribute_keys(keys, resolve_workstream)
    if attributions:
        return [
            GithubActivity(member, workstream, kind, repo, url, timestamp, key, source)
            for workstream, key, source in attributions
        ]

    # c. Repo fallback
    repo_workstream = repo_to_workstream(repo)
    if repo_workstream:
        return [GithubActivity(member, repo_workstream, kind, repo, url, timestamp, None, "repo")]

    # d. SHARED repo
    if is_shared_repo(repo):
        return [GithubActivity(member, SHARED_COLUMN, kind, repo, url, timestamp, None, "shared")]

    # e. Unattributed (preserve existing behavior)
    source_key = keys[0] if keys else None
    return [GithubActivity(member, None, kind, repo, url, timestamp, source_key, None)]


def _attribute_keys(
    keys: List[str], resolve_workstream: WorkstreamResolver
) -> List[tuple[str, str, str]]:
    """Return (workstream, key, source) tuples for attributed keys."""
    attributions: List[tuple[str, str, str]] = []
    seen: set = set()
    for key in keys:
        workstream, source = resolve_workstream(key)
        if workstream and workstream not in seen:
            seen.add(workstream)
            attributions.append((workstream, key, source))
    return attributions


# --- GraphQL batching -------------------------------------------------------


def _window_range(window: Window) -> str:
    return f"{window.start.isoformat()}..{window.end.isoformat()}"


def build_batch_query(slots: List[SearchSlot], window: Window) -> str:
    """Build a GraphQL query with one aliased search per slot."""
    range_str = _window_range(window)
    searches = []
    for slot in slots:
        if slot.kind == "pr_authored":
            query_text = f"author:{slot.handle} type:pr created:{range_str}"
        else:  # pr_reviewed
            query_text = f"reviewed-by:{slot.handle} type:pr updated:{range_str}"
        after_clause = f', after: "{slot.cursor}"' if slot.cursor else ""
        search = f"""
    {slot.alias}: search(query: "{query_text}", type: ISSUE, first: 100{after_clause}) {{
      issueCount
      pageInfo {{ hasNextPage endCursor }}
      nodes {{
        ... on PullRequest {{
          url
          title
          body
          createdAt
          repository {{ nameWithOwner }}
        }}
      }}
    }}"""
        searches.append(search)
    return "query {" + "".join(searches) + "\n}"


def parse_batch_response(
    payload: dict, slots: List[SearchSlot], slot_node_counts: Optional[dict] = None
) -> tuple[List[tuple[SearchSlot, dict]], List[SearchSlot]]:
    """Parse a GraphQL batch response into PR nodes and continuation slots.

    Validates that:
    - The response contains no errors
    - Every requested slot appears in data (not missing, not null)
    - Node counts are consistent with issueCount when pagination completes

    Args:
        slot_node_counts: Optional dict mapping slot.alias to accumulated node count
                         across all pages, used for completeness checking.
    """
    # Check for GraphQL errors
    errors = payload.get("errors", [])
    if errors:
        first_error = errors[0]
        message = first_error.get("message", "unknown error")
        path = first_error.get("path", [])
        path_str = ".".join(str(p) for p in path) if path else "unknown"
        raise GraphqlResponseError(f"GraphQL error at {path_str}: {message}")

    # data may be absent or None on certain errors
    data = payload.get("data")
    if data is None:
        raise GraphqlResponseError("GraphQL response missing 'data' field")

    pairs: List[tuple[SearchSlot, dict]] = []
    next_slots: List[SearchSlot] = []

    for slot in slots:
        # Check that the alias is present
        if slot.alias not in data:
            raise GraphqlResponseError(
                f"GraphQL response missing expected alias '{slot.alias}' for {slot.handle}"
            )

        result = data[slot.alias]
        # Check for null result (GitHub returns null for failed searches)
        if result is None:
            raise GraphqlResponseError(
                f"GraphQL returned null for alias '{slot.alias}' (handle: {slot.handle})"
            )

        nodes = result.get("nodes", [])
        issue_count = result.get("issueCount", 0)

        for node in nodes:
            pairs.append((slot, node))

        page_info = result.get("pageInfo", {})
        has_next_page = page_info.get("hasNextPage", False)

        if has_next_page:
            next_slots.append(
                SearchSlot(
                    alias=slot.alias,
                    member=slot.member,
                    handle=slot.handle,
                    kind=slot.kind,
                    cursor=page_info.get("endCursor"),
                )
            )
        else:
            # Pagination complete: check completeness if we're tracking counts
            if slot_node_counts is not None and issue_count <= 1000:
                # GitHub caps search results at 1000, so only check if within that limit
                total_nodes = slot_node_counts.get(slot.alias, 0) + len(nodes)
                if total_nodes != issue_count:
                    raise GraphqlResponseError(
                        f"Incomplete results for {slot.handle}: collected {total_nodes} "
                        f"nodes but issueCount reports {issue_count}"
                    )

    return pairs, next_slots


def graphql_rate_limit_adapter(runner: CommandRunner) -> CommandRunner:
    """Wrap a runner to detect body-level GraphQL RATE_LIMITED errors.

    GraphQL rate limits arrive as HTTP 200 with errors[].type == "RATE_LIMITED",
    invisible to the existing ``is_rate_limit_failure`` check. This adapter
    rewrites such a response into a non-zero exit, allowing the retry logic
    to handle transient limits while marking quota exhaustion distinctly so
    chunk-splitting can avoid making it worse.
    """

    def adapted(command: List[str]) -> CommandResult:
        result = runner(command)
        if result.returncode == 0:
            try:
                body = json.loads(result.stdout)
                errors = body.get("errors", [])
                if any(err.get("type") == "RATE_LIMITED" for err in errors):
                    # Rewrite to trigger retries, with a marker for no-split behavior
                    return CommandResult(
                        returncode=1,
                        stdout="",
                        stderr="GraphQL rate_limit exhausted (quota)",
                    )
            except (ValueError, KeyError):
                pass
        return result

    return adapted


def collect_prs(
    runner: CommandRunner,
    roster: List[dict],
    window: Window,
    chunk_size: int = 6,
    sleep: Callable[[float], None] = time.sleep,
) -> List[tuple[SearchSlot, dict]]:
    """Collect all PRs for the roster via batched GraphQL, following pagination."""
    slots = []
    for idx, member in enumerate(roster):
        handle = member["github"]
        jira_user = member["jira_username"]
        slots.append(
            SearchSlot(
                alias=f"m{idx}_authored",
                member=jira_user,
                handle=handle,
                kind="pr_authored",
            )
        )
        slots.append(
            SearchSlot(
                alias=f"m{idx}_reviewed",
                member=jira_user,
                handle=handle,
                kind="pr_reviewed",
            )
        )

    adapted_runner = graphql_rate_limit_adapter(runner)
    pairs: List[tuple[SearchSlot, dict]] = []
    pending = slots[:]
    # Track accumulated node counts per slot alias for completeness checking
    slot_node_counts: Dict[str, int] = {}

    while pending:
        chunks = [pending[i : i + chunk_size] for i in range(0, len(pending), chunk_size)]
        pending = []
        for chunk in chunks:
            chunk_pairs, chunk_next = _fetch_chunk(
                adapted_runner, chunk, window, slot_node_counts, sleep
            )
            # Update counts for each slot in this chunk
            for slot, _node in chunk_pairs:
                slot_node_counts[slot.alias] = slot_node_counts.get(slot.alias, 0) + 1
            pairs.extend(chunk_pairs)
            pending.extend(chunk_next)

    return pairs


def _fetch_chunk(
    runner: CommandRunner,
    chunk: List[SearchSlot],
    window: Window,
    slot_node_counts: Optional[Dict[str, int]] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[List[tuple[SearchSlot, dict]], List[SearchSlot]]:
    """Fetch one chunk, splitting on failure when splitting can help.

    Splits on GraphqlResponseError or transport-level failures (e.g., HTTP 502),
    but re-raises rate-limit exhaustion immediately without splitting (since
    splitting only issues more requests and makes rate limiting worse).
    """
    try:
        query = build_batch_query(chunk, window)
        result = _run_gh_json(runner, ["gh", "api", "graphql", "-f", f"query={query}"], sleep)
        return parse_batch_response(result, chunk, slot_node_counts)
    except GraphqlRateLimitExhausted:
        # Rate limit exhaustion: splitting makes it worse, re-raise immediately
        raise
    except (GraphqlResponseError, CollectorError):
        # GraphQL errors or transport failures: try splitting
        if len(chunk) == 1:
            raise
        mid = len(chunk) // 2
        left_pairs, left_next = _fetch_chunk(runner, chunk[:mid], window, slot_node_counts, sleep)
        right_pairs, right_next = _fetch_chunk(runner, chunk[mid:], window, slot_node_counts, sleep)
        return left_pairs + right_pairs, left_next + right_next


# --- Orchestration (injected runner + resolver) -----------------------------


def _run_gh_json(
    runner: CommandRunner,
    command: List[str],
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    result = run_with_rate_limit_retry(runner, command, sleep=sleep)
    if result.returncode != 0:
        # Check for the GraphQL quota marker from the adapter
        if "(quota)" in result.stderr:
            raise GraphqlRateLimitExhausted("GraphQL rate limit exhausted")
        raise CollectorError(f"gh exited {result.returncode}: {result.stderr.strip()}")
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise CollectorError(f"malformed JSON from gh: {error}") from error


def _flatten(activity_lists) -> List[GithubActivity]:
    return [activity for activities in activity_lists for activity in activities]


# --- Jira workstream resolution (batched) ----------------------------------


def _components_to_workstream(components: List[dict]) -> Optional[str]:
    """Map an issue's components to its workstream, if any."""
    for component in components:
        workstream = component_to_workstream(component.get("name"))
        if workstream:
            return workstream
    return None


def resolve_workstreams(
    client: JiraClient, keys: List[str], batch_size: int = 100
) -> dict[str, tuple[Optional[str], Optional[str]]]:
    """Map each Jira key to (workstream, attribution_source).

    Attribution order (first hit wins):
      1. the issue's own components → source "component"
      2. the parent epic's component → source "parent"
      3. project_to_workstream(key) → source "project"

    Keys absent from Jira resolve to (None, None). Authentication errors propagate.
    """
    if not keys:
        return {}
    mapping: dict[str, tuple[Optional[str], Optional[str]]] = {key: (None, None) for key in keys}

    # First pass: fetch issues with their components and parent references
    issues_by_key: dict[str, dict] = {}
    parent_keys: List[str] = []

    for i in range(0, len(keys), batch_size):
        batch = keys[i : i + batch_size]
        jql = f"key in ({','.join(batch)})"
        try:
            issues = client.search(jql, ["key", "components", "parent"])
        except JiraAuthError:
            raise
        except CollectorError:
            continue
        for issue in issues:
            key = issue.get("key")
            if key:
                issues_by_key[key] = issue
                # Collect parent keys for batch resolution
                parent = issue.get("fields", {}).get("parent")
                if parent:
                    parent_key = parent.get("key")
                    if parent_key and parent_key not in parent_keys:
                        parent_keys.append(parent_key)

    # Second pass: batch-resolve parent keys to get their components
    parents_by_key: dict[str, dict] = {}
    for i in range(0, len(parent_keys), batch_size):
        batch = parent_keys[i : i + batch_size]
        jql = f"key in ({','.join(batch)})"
        try:
            parents = client.search(jql, ["key", "components"])
        except JiraAuthError:
            raise
        except CollectorError:
            continue
        for parent in parents:
            parent_key = parent.get("key")
            if parent_key:
                parents_by_key[parent_key] = parent

    # Third pass: apply attribution chain for each original key
    for key in keys:
        issue = issues_by_key.get(key)
        if not issue:
            # Issue not found in Jira, stays (None, None)
            continue

        # 1. Try own components
        components = issue.get("fields", {}).get("components") or []
        workstream = _components_to_workstream(components)
        if workstream:
            mapping[key] = (workstream, "component")
            continue

        # 2. Try parent's components
        parent = issue.get("fields", {}).get("parent")
        if parent:
            parent_key = parent.get("key")
            parent_issue = parents_by_key.get(parent_key)
            if parent_issue:
                parent_components = parent_issue.get("fields", {}).get("components") or []
                workstream = _components_to_workstream(parent_components)
                if workstream:
                    mapping[key] = (workstream, "parent")
                    continue

        # 3. Try project fallback
        workstream = project_to_workstream(key)
        if workstream:
            mapping[key] = (workstream, "project")
            continue

        # No attribution found, stays (None, None)

    return mapping


def make_prefetched_resolver(
    mapping: dict[str, tuple[Optional[str], Optional[str]]]
) -> WorkstreamResolver:
    """Return a resolver backed by a precomputed mapping."""
    return lambda key: mapping.get(key, (None, None))


# --- CLI --------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    import os

    from _common import JiraClient, build_default_transport

    parser = argparse.ArgumentParser(description="Collect GitHub PR contribution activity.")
    parser.add_argument(
        "--members-file",
        required=True,
        help="roster.json produced by load_context.py (needs github + jira_username)",
    )
    parser.add_argument("--quarter", help="reporting quarter, e.g. 2026Q2")
    parser.add_argument("--from", dest="from_date", help="window start, YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", help="window end, YYYY-MM-DD")
    parser.add_argument("--output", default="github_activity.json")
    args = parser.parse_args(argv)

    window = resolve_window(args.quarter, args.from_date, args.to_date)
    with open(args.members_file, encoding="utf-8") as handle:
        roster = json.load(handle)

    runner = build_default_runner()
    pr_pairs = collect_prs(runner, roster, window)

    # Extract all unique Jira keys from PRs
    all_keys: List[str] = []
    for _, pr in pr_pairs:
        text = f"{pr.get('title', '')}\n{pr.get('body') or ''}"
        keys = extract_jira_keys(text)
        for key in keys:
            if key not in all_keys:
                all_keys.append(key)

    # Resolve workstreams in batch
    config = jira_config_from_env(os.environ)
    client = JiraClient(config, build_default_transport(config))
    workstream_map = resolve_workstreams(client, all_keys)
    resolve_workstream = make_prefetched_resolver(workstream_map)

    # Attribute every PR
    activities: List[GithubActivity] = []
    for slot, pr in pr_pairs:
        activities.extend(pr_to_activities(pr, slot.member, slot.kind, resolve_workstream))

    excluded_repos = tally_excluded_repos(pr for _slot, pr in pr_pairs)
    excluded_count = sum(excluded_repos.values())

    # Excluded PRs produce no activity records at all, so the count would be lost
    # if it were only printed. Carry it in the envelope so report.py can show it
    # in the data-quality block instead of silently reporting zero. The per-repo
    # breakdown rides along so a legitimate org missing from the allowlist shows
    # up by name rather than disappearing into the total.
    payload = activity_payload(
        [asdict(activity) for activity in activities],
        excluded_personal_repo_prs=excluded_count,
        excluded_personal_repos=excluded_repos,
    )
    with open(args.output, "w", encoding="utf-8") as output:
        json.dump(payload, output, indent=2)

    # Report summary to stderr
    import sys
    total_prs = len(pr_pairs)
    attributed_prs = total_prs - excluded_count
    print(
        f"Collected {total_prs} PRs: {attributed_prs} processed, {excluded_count} excluded "
        f"(personal repos)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
