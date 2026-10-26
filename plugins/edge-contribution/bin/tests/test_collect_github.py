"""Tests for collect_github.py — GitHub PR collection via batched GraphQL queries.

The command runner and the Jira workstream resolver are both injected, so tests
are hermetic (no ``gh`` subprocess, no Jira network). Coverage spans:
- Batched GraphQL query construction with aliased searches
- Pagination following hasNextPage/endCursor
- GraphQL response validation (errors[], null/missing aliases, issueCount completeness)
- Chunk splitting on retriable failures (GraphQL errors, transport failures)
- Rate-limit detection and non-splitting behavior
- Happy-path attribution of authored/reviewed PRs
- Boundary/anti-cheat cases: key in body only, multiple keys deduped across
  workstreams, unmapped/absent keys → unattributed (never dropped)
"""

import json
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import _common  # noqa: E402
import collect_github  # noqa: E402

WINDOW = _common.Window(date(2026, 4, 1), date(2026, 6, 30))


def _pr(
    title, body="", repo="openshift/example", url="https://gh/pr/1", created="2026-05-01T00:00:00Z"
):
    return {
        "title": title,
        "body": body,
        "repository": {"nameWithOwner": repo},
        "url": url,
        "createdAt": created,
    }


def _graphql_response(alias, prs, has_next_page=False, end_cursor=None):
    """Wrap PRs in a GraphQL batch response structure."""
    return {
        "data": {
            alias: {
                "issueCount": len(prs),
                "pageInfo": {
                    "hasNextPage": has_next_page,
                    "endCursor": end_cursor,
                },
                "nodes": prs,
            }
        }
    }


class FakeRunner:
    def __init__(self, result):
        self._result = result
        self.commands = []

    def __call__(self, command):
        self.commands.append(command)
        return self._result


def _ok(stdout):
    return collect_github.CommandResult(returncode=0, stdout=stdout, stderr="")


def _resolver(mapping):
    """Build a resolver that returns (workstream, source) tuples."""
    return lambda key: mapping.get(key, (None, None))


class TestExtractJiraKeys(unittest.TestCase):
    def test_finds_known_project_keys(self):
        keys = collect_github.extract_jira_keys("Fixes OCPEDGE-123 and USHIFT-7")
        assert keys == ["OCPEDGE-123", "USHIFT-7"]

    def test_dedupes_and_uppercases(self):
        assert collect_github.extract_jira_keys("ocpbugs-1 OCPBUGS-1") == ["OCPBUGS-1"]

    def test_ignores_unknown_project_prefixes(self):
        assert collect_github.extract_jira_keys("FOO-1 BAR-22") == []

    def test_empty_text_yields_no_keys(self):
        assert collect_github.extract_jira_keys("") == []


class TestBatchQuery(unittest.TestCase):
    def test_builds_one_alias_per_slot(self):
        slots = [
            collect_github.SearchSlot("a0", "user1", "h1", "pr_authored"),
            collect_github.SearchSlot("r0", "user1", "h1", "pr_reviewed"),
        ]
        query = collect_github.build_batch_query(slots, WINDOW)
        assert "a0:" in query
        assert "r0:" in query

    def test_authored_uses_author_and_created(self):
        slot = collect_github.SearchSlot("a0", "user", "handle", "pr_authored")
        query = collect_github.build_batch_query([slot], WINDOW)
        assert "author:handle" in query
        assert "created:2026-04-01..2026-06-30" in query

    def test_reviewed_uses_reviewed_by_and_updated(self):
        slot = collect_github.SearchSlot("r0", "user", "handle", "pr_reviewed")
        query = collect_github.build_batch_query([slot], WINDOW)
        assert "reviewed-by:handle" in query
        assert "updated:2026-04-01..2026-06-30" in query

    def test_cursored_slot_includes_after(self):
        slot = collect_github.SearchSlot("a0", "user", "h", "pr_authored", cursor="abc123")
        query = collect_github.build_batch_query([slot], WINDOW)
        assert 'after: "abc123"' in query

    def test_non_cursored_slot_omits_after(self):
        slot = collect_github.SearchSlot("a0", "user", "h", "pr_authored")
        query = collect_github.build_batch_query([slot], WINDOW)
        assert "after:" not in query


class TestAttributionEdgeCases(unittest.TestCase):
    def test_key_in_body_only_is_found(self):
        pr = _pr("no key here", "relates to USHIFT-9")
        activities = collect_github.pr_to_activities(
            pr, "u@redhat.com", "pr_authored", _resolver({"USHIFT-9": ("USHIFT", "component")})
        )
        assert [a.workstream for a in activities] == ["USHIFT"]
        assert activities[0].attribution_source == "component"

    def test_multiple_keys_attributed_across_workstreams_deduped(self):
        pr = _pr("OCPEDGE-1 OCPEDGE-1", "also OCPBUGS-5")
        activities = collect_github.pr_to_activities(
            pr,
            "u@redhat.com",
            "pr_authored",
            _resolver({"OCPEDGE-1": ("SNO", "component"), "OCPBUGS-5": ("TNF", "component")}),
        )
        assert {a.workstream for a in activities} == {"SNO", "TNF"}

    def test_key_resolving_to_none_is_unattributed_not_dropped(self):
        pr = _pr("OCPEDGE-2 planning", "")
        activities = collect_github.pr_to_activities(
            pr, "u@redhat.com", "pr_authored", _resolver({"OCPEDGE-2": (None, None)})
        )
        assert len(activities) == 1
        assert activities[0].workstream is None
        assert activities[0].source_key == "OCPEDGE-2"
        assert activities[0].attribution_source is None

    def test_pr_with_no_key_is_unattributed(self):
        pr = _pr("cleanup", "no ticket")
        activities = collect_github.pr_to_activities(
            pr, "u@redhat.com", "pr_authored", _resolver({})
        )
        assert len(activities) == 1
        assert activities[0].workstream is None
        assert activities[0].source_key is None
        assert activities[0].attribution_source is None


class TestFailureInputs(unittest.TestCase):
    def test_gh_nonzero_exit_raises(self):
        runner = FakeRunner(collect_github.CommandResult(1, "", "gh: not authenticated"))
        with self.assertRaises(_common.CollectorError):
            collect_github._run_gh_json(runner, ["gh", "api", "graphql"])

    def test_gh_malformed_json_raises(self):
        runner = FakeRunner(_ok("{not json"))
        with self.assertRaises(_common.CollectorError):
            collect_github._run_gh_json(runner, ["gh", "api", "graphql"])

    def test_rate_limit_is_retried_with_exponential_backoff(self):
        rate_limited = collect_github.CommandResult(
            1, "", "HTTP 403: You have exceeded a secondary rate limit"
        )
        runner = _SequenceRunner([rate_limited, rate_limited, _ok("[]")])
        delays = []
        activities = collect_github._run_gh_json(runner, ["gh", "search", "prs"], delays.append)
        assert activities == []
        assert runner.commands == [["gh", "search", "prs"]] * 3
        assert delays == [5.0, 10.0]

    def test_rate_limit_retries_are_bounded(self):
        rate_limited = collect_github.CommandResult(1, "", "API rate limit exceeded")
        runner = _SequenceRunner([rate_limited] * 6)
        delays = []
        with self.assertRaises(_common.CollectorError):
            collect_github._run_gh_json(runner, ["gh", "search", "prs"], delays.append)
        assert len(runner.commands) == 5
        assert delays == [5.0, 10.0, 20.0, 40.0]


class _FakeSearchClient:
    def __init__(self, issues_by_jql, errors=None):
        self._issues = issues_by_jql
        self._errors = errors or {}
        self.search_calls = []

    def search(self, jql, fields):
        self.search_calls.append(jql)
        if jql in self._errors:
            raise self._errors[jql]
        return self._issues.get(jql, [])


def _issue_with_components(key, components):
    return {"key": key, "fields": {"components": [{"name": name} for name in components]}}


class TestJiraWorkstreamResolver(unittest.TestCase):
    def test_resolves_component_to_workstream(self):
        client = _FakeSearchClient(
            {"key in (OCPEDGE-1)": [_issue_with_components("OCPEDGE-1", ["SNO"])]}
        )
        mapping = collect_github.resolve_workstreams(client, ["OCPEDGE-1"])
        assert mapping["OCPEDGE-1"] == ("SNO", "component")

    def test_planning_component_resolves_to_none(self):
        client = _FakeSearchClient(
            {"key in (OCPEDGE-2)": [_issue_with_components("OCPEDGE-2", ["Planning"])]}
        )
        mapping = collect_github.resolve_workstreams(client, ["OCPEDGE-2"])
        assert mapping["OCPEDGE-2"] == (None, None)

    def test_missing_issue_resolves_to_none(self):
        client = _FakeSearchClient({"key in (OCPBUGS-9)": []})
        mapping = collect_github.resolve_workstreams(client, ["OCPBUGS-9"])
        assert mapping["OCPBUGS-9"] == (None, None)

    def test_auth_error_is_not_swallowed(self):
        client = _FakeSearchClient({}, errors={"key in (OCPEDGE-1)": _common.JiraAuthError("401")})
        with self.assertRaises(_common.JiraAuthError):
            collect_github.resolve_workstreams(client, ["OCPEDGE-1"])

    def test_batches_large_key_lists(self):
        keys = [f"KEY-{i}" for i in range(250)]
        issues = {f"KEY-{i}": _issue_with_components(f"KEY-{i}", ["SNO"]) for i in range(250)}
        client = _FakeSearchClient(
            {
                f"key in ({','.join(keys[:100])})": [issues[k] for k in keys[:100]],
                f"key in ({','.join(keys[100:200])})": [issues[k] for k in keys[100:200]],
                f"key in ({','.join(keys[200:])})": [issues[k] for k in keys[200:]],
            }
        )
        mapping = collect_github.resolve_workstreams(client, keys)
        assert len(client.search_calls) >= 3  # Initial batch + possible parent batches
        assert all(mapping[k] == ("SNO", "component") for k in keys)

    def test_prefetched_resolver_returns_from_mapping(self):
        mapping = {"OCPEDGE-1": ("SNO", "component"), "OCPEDGE-2": (None, None)}
        resolve = collect_github.make_prefetched_resolver(mapping)
        assert resolve("OCPEDGE-1") == ("SNO", "component")
        assert resolve("OCPEDGE-2") == (None, None)
        assert resolve("UNKNOWN") == (None, None)


def _pr_json(title, body):
    import json

    return json.dumps(_pr(title, body))


class _SequenceRunner:
    def __init__(self, results):
        self._results = list(results)
        self.commands = []

    def __call__(self, command):
        self.commands.append(command)
        return self._results.pop(0)


class TestBatching(unittest.TestCase):
    def test_roster_generates_authored_and_reviewed_slots(self):
        roster = [{"jira_username": "u1", "github": "h1"}, {"jira_username": "u2", "github": "h2"}]
        response = _graphql_response("m0_authored", [])
        response["data"]["m0_reviewed"] = {
            "issueCount": 0,
            "pageInfo": {"hasNextPage": False},
            "nodes": [],
        }
        response["data"]["m1_authored"] = {
            "issueCount": 0,
            "pageInfo": {"hasNextPage": False},
            "nodes": [],
        }
        response["data"]["m1_reviewed"] = {
            "issueCount": 0,
            "pageInfo": {"hasNextPage": False},
            "nodes": [],
        }
        runner = FakeRunner(_ok(json.dumps(response)))
        pairs = collect_github.collect_prs(runner, roster, WINDOW)
        # Should have made one request (4 slots fits in default chunk size)
        assert len(runner.commands) == 1
        # All aliases present
        query = runner.commands[0][-1]
        assert "m0_authored:" in query
        assert "m0_reviewed:" in query
        assert "m1_authored:" in query
        assert "m1_reviewed:" in query

    def test_has_next_page_produces_cursored_follow_up(self):
        pr1 = _pr("First", url="https://gh/pr/1")
        pr2 = _pr("Second", url="https://gh/pr/2")
        # issueCount should be the total (2) across both pages
        page1 = {
            "data": {
                "m0_authored": {
                    "issueCount": 2,  # Total count
                    "pageInfo": {"hasNextPage": True, "endCursor": "cursor1"},
                    "nodes": [pr1],
                },
                "m0_reviewed": {
                    "issueCount": 0,
                    "pageInfo": {"hasNextPage": False},
                    "nodes": [],
                },
            }
        }
        page2 = {
            "data": {
                "m0_authored": {
                    "issueCount": 2,  # Same total count
                    "pageInfo": {"hasNextPage": False},
                    "nodes": [pr2],
                }
            }
        }
        runner = _SequenceRunner([_ok(json.dumps(page1)), _ok(json.dumps(page2))])
        roster = [{"jira_username": "user", "github": "handle"}]
        pairs = collect_github.collect_prs(runner, roster, WINDOW)
        # Two requests: initial + pagination
        assert len(runner.commands) == 2
        # Second request has cursor
        assert 'after: "cursor1"' in runner.commands[1][-1]
        # Both PRs returned
        assert len(pairs) == 2
        urls = {pair[1]["url"] for pair in pairs}
        assert urls == {"https://gh/pr/1", "https://gh/pr/2"}

    def test_failing_chunk_splits_and_retries(self):
        # Simulate a large chunk that 502s, then succeeds when split
        roster = [{"jira_username": f"u{i}", "github": f"h{i}"} for i in range(6)]
        fail_result = collect_github.CommandResult(22, "", "HTTP 502")
        success = _graphql_response("m0_authored", [])
        for i in range(6):
            success["data"][f"m{i}_authored"] = {
                "issueCount": 0,
                "pageInfo": {"hasNextPage": False},
                "nodes": [],
            }
            success["data"][f"m{i}_reviewed"] = {
                "issueCount": 0,
                "pageInfo": {"hasNextPage": False},
                "nodes": [],
            }
        runner = _SequenceRunner([fail_result, _ok(json.dumps(success)), _ok(json.dumps(success))])
        collect_github.collect_prs(runner, roster, WINDOW, chunk_size=12)
        # First request (all 12 slots) fails, then splits into 2 chunks of 6
        assert len(runner.commands) == 3

    def test_single_slot_failure_re_raises(self):
        roster = [{"jira_username": "user", "github": "handle"}]
        runner = FakeRunner(collect_github.CommandResult(1, "", "persistent error"))
        with self.assertRaises(_common.CollectorError):
            collect_github.collect_prs(runner, roster, WINDOW)


class TestGraphqlRateLimit(unittest.TestCase):
    def test_rate_limited_error_in_body_is_retried(self):
        rate_limited_body = json.dumps({"errors": [{"type": "RATE_LIMITED", "message": "..."}]})
        rate_limited_result = _ok(rate_limited_body)
        success = _ok(json.dumps(_graphql_response("m0_authored", [])))
        runner = _SequenceRunner([rate_limited_result, success])
        delays = []
        adapted = collect_github.graphql_rate_limit_adapter(runner)
        collect_github._run_gh_json(
            adapted, ["gh", "api", "graphql", "-f", "query=..."], delays.append
        )
        # Should have retried
        assert len(runner.commands) == 2
        assert len(delays) == 1

    def test_pr_body_containing_rate_limit_text_is_not_treated_as_error(self):
        pr = _pr("Normal PR", "This PR fixes a rate limit bug")
        response = _graphql_response("m0_authored", [pr])
        runner = FakeRunner(_ok(json.dumps(response)))
        adapted = collect_github.graphql_rate_limit_adapter(runner)
        result = adapted(["gh", "api", "graphql", "-f", "query=..."])
        # Should not be rewritten as an error
        assert result.returncode == 0

    def test_rate_limit_exhaustion_does_not_split(self):
        # GraphQL rate limit should be retried but not split
        rate_limited_body = json.dumps(
            {"errors": [{"type": "RATE_LIMITED", "message": "exhausted"}]}
        )
        rate_limited_result = _ok(rate_limited_body)
        runner = FakeRunner(rate_limited_result)
        adapted = collect_github.graphql_rate_limit_adapter(runner)
        roster = [{"jira_username": "u1", "github": "h1"}, {"jira_username": "u2", "github": "h2"}]
        delays = []
        with self.assertRaises(collect_github.GraphqlRateLimitExhausted):
            collect_github.collect_prs(adapted, roster, WINDOW, chunk_size=4, sleep=delays.append)
        # Retries should happen (max 4), but no splitting
        # 1 initial + 4 retries = 5 total, all with the same 4 slots
        assert len(runner.commands) == 5
        assert delays == [5.0, 10.0, 20.0, 40.0]
        # All commands should be identical (no splitting into smaller chunks)
        first_query = runner.commands[0][-1]
        assert all(cmd[-1] == first_query for cmd in runner.commands)


class TestGraphqlResponseValidation(unittest.TestCase):
    def test_errors_array_raises_graphql_response_error(self):
        payload = {
            "errors": [{"message": "Something went wrong", "path": ["search", "m0_authored"]}],
            "data": None,
        }
        slot = collect_github.SearchSlot("m0_authored", "user", "handle", "pr_authored")
        with self.assertRaises(collect_github.GraphqlResponseError) as ctx:
            collect_github.parse_batch_response(payload, [slot])
        # Error message should include GitHub's message
        assert "Something went wrong" in str(ctx.exception)

    def test_null_alias_raises_graphql_response_error_not_attribute_error(self):
        # Bug 2: null alias used to crash with AttributeError
        payload = {"data": {"m0_authored": None}}
        slot = collect_github.SearchSlot("m0_authored", "user", "handle", "pr_authored")
        with self.assertRaises(collect_github.GraphqlResponseError) as ctx:
            collect_github.parse_batch_response(payload, [slot])
        assert "null" in str(ctx.exception).lower()
        assert "handle" in str(ctx.exception)

    def test_missing_alias_raises_graphql_response_error(self):
        payload = {"data": {}}
        slot = collect_github.SearchSlot("m0_authored", "user", "handle", "pr_authored")
        with self.assertRaises(collect_github.GraphqlResponseError) as ctx:
            collect_github.parse_batch_response(payload, [slot])
        assert "missing" in str(ctx.exception).lower()
        assert "m0_authored" in str(ctx.exception)

    def test_missing_data_field_raises(self):
        payload = {"errors": []}
        with self.assertRaises(collect_github.GraphqlResponseError) as ctx:
            slot = collect_github.SearchSlot("m0_authored", "user", "handle", "pr_authored")
            collect_github.parse_batch_response(payload, [slot])
        assert "missing 'data'" in str(ctx.exception)

    def test_incomplete_issue_count_raises(self):
        # When pagination completes but node count doesn't match issueCount
        payload = {
            "data": {
                "m0_authored": {
                    "issueCount": 10,
                    "pageInfo": {"hasNextPage": False},
                    "nodes": [_pr("PR1"), _pr("PR2")],  # Only 2 nodes, but issueCount=10
                }
            }
        }
        slot = collect_github.SearchSlot("m0_authored", "user", "handle", "pr_authored")
        # No prior pages, so total is just 2
        with self.assertRaises(collect_github.GraphqlResponseError) as ctx:
            collect_github.parse_batch_response(payload, [slot], slot_node_counts={})
        assert "Incomplete" in str(ctx.exception)
        assert "collected 2" in str(ctx.exception)
        assert "issueCount reports 10" in str(ctx.exception)

    def test_issue_count_above_1000_skips_check(self):
        # GitHub caps results at 1000, so we skip completeness check for > 1000
        payload = {
            "data": {
                "m0_authored": {
                    "issueCount": 1500,
                    "pageInfo": {"hasNextPage": False},
                    "nodes": [],
                }
            }
        }
        slot = collect_github.SearchSlot("m0_authored", "user", "handle", "pr_authored")
        # Should not raise despite mismatch
        pairs, next_slots = collect_github.parse_batch_response(
            payload, [slot], slot_node_counts={}
        )
        assert pairs == []
        assert next_slots == []

    def test_multi_slot_chunk_splits_on_graphql_error(self):
        # A multi-slot chunk with an error should split and succeed
        fail_payload = {
            "errors": [{"message": "timeout", "path": []}],
            "data": None,
        }
        success_1 = _graphql_response("m0_authored", [])
        success_1["data"]["m0_reviewed"] = {
            "issueCount": 0,
            "pageInfo": {"hasNextPage": False},
            "nodes": [],
        }
        success_2 = _graphql_response("m1_authored", [])
        success_2["data"]["m1_reviewed"] = {
            "issueCount": 0,
            "pageInfo": {"hasNextPage": False},
            "nodes": [],
        }
        runner = _SequenceRunner(
            [
                _ok(json.dumps(fail_payload)),
                _ok(json.dumps(success_1)),
                _ok(json.dumps(success_2)),
            ]
        )
        roster = [
            {"jira_username": "u0", "github": "h0"},
            {"jira_username": "u1", "github": "h1"},
        ]
        # Should split after failure and succeed
        pairs = collect_github.collect_prs(runner, roster, WINDOW, chunk_size=4)
        assert len(runner.commands) == 3  # Initial fail + 2 splits


class TestAuthoredCollectionHappyPath(unittest.TestCase):
    def test_authored_pr_flows_through_collect_prs_to_activity(self):
        # End-to-end: roster → collect_prs → activities with correct member/kind/workstream
        pr = _pr("OCPEDGE-123: Add feature", "description", repo="openshift/example")
        response = _graphql_response("m0_authored", [pr])
        response["data"]["m0_reviewed"] = {
            "issueCount": 0,
            "pageInfo": {"hasNextPage": False},
            "nodes": [],
        }
        runner = FakeRunner(_ok(json.dumps(response)))
        roster = [{"jira_username": "alice@redhat.com", "github": "alice-gh"}]

        pairs = collect_github.collect_prs(runner, roster, WINDOW)
        assert len(pairs) == 1
        slot, pr_node = pairs[0]
        assert slot.kind == "pr_authored"
        assert slot.member == "alice@redhat.com"  # jira_username, not github handle
        assert slot.handle == "alice-gh"
        assert pr_node["title"] == "OCPEDGE-123: Add feature"

        # Convert to activities
        resolve = _resolver({"OCPEDGE-123": ("SNO", "component")})
        activities = collect_github.pr_to_activities(pr_node, slot.member, slot.kind, resolve)
        assert len(activities) == 1
        activity = activities[0]
        assert activity.kind == "pr_authored"
        assert activity.member == "alice@redhat.com"
        assert activity.workstream == "SNO"
        assert activity.repo == "openshift/example"
        assert activity.source_key == "OCPEDGE-123"
        assert activity.attribution_source == "component"


class TestReviewedCollection(unittest.TestCase):
    def test_reviewed_pr_flows_through_collect_prs_to_activity(self):
        pr = _pr("USHIFT-456: Fix bug", "body text", repo="openshift-eng/tooling")
        response = _graphql_response("m0_reviewed", [pr])
        response["data"]["m0_authored"] = {
            "issueCount": 0,
            "pageInfo": {"hasNextPage": False},
            "nodes": [],
        }
        runner = FakeRunner(_ok(json.dumps(response)))
        roster = [{"jira_username": "bob@redhat.com", "github": "bob-gh"}]

        pairs = collect_github.collect_prs(runner, roster, WINDOW)
        assert len(pairs) == 1
        slot, pr_node = pairs[0]
        assert slot.kind == "pr_reviewed"
        assert slot.member == "bob@redhat.com"
        assert pr_node["title"] == "USHIFT-456: Fix bug"

        resolve = _resolver({"USHIFT-456": ("USHIFT", "component")})
        activities = collect_github.pr_to_activities(pr_node, slot.member, slot.kind, resolve)
        assert len(activities) == 1
        activity = activities[0]
        assert activity.kind == "pr_reviewed"
        assert activity.member == "bob@redhat.com"
        assert activity.workstream == "USHIFT"
        assert activity.attribution_source == "component"


class TestUnattributedPathEndToEnd(unittest.TestCase):
    def test_unmappable_key_flows_through_as_unattributed(self):
        # Verify that a PR with an unmappable key carries through collect_prs
        # and becomes an unattributed activity (not dropped)
        # Use OCPEDGE prefix (recognized) but map it to None (unmapped)
        pr = _pr("OCPEDGE-999: roadmap", "planning work")
        response = _graphql_response("m0_authored", [pr])
        response["data"]["m0_reviewed"] = {
            "issueCount": 0,
            "pageInfo": {"hasNextPage": False},
            "nodes": [],
        }
        runner = FakeRunner(_ok(json.dumps(response)))
        roster = [{"jira_username": "charlie@redhat.com", "github": "charlie-gh"}]

        pairs = collect_github.collect_prs(runner, roster, WINDOW)
        slot, pr_node = pairs[0]

        # Resolver maps OCPEDGE-999 to None (unmapped workstream)
        resolve = _resolver({"OCPEDGE-999": (None, None)})
        activities = collect_github.pr_to_activities(pr_node, slot.member, slot.kind, resolve)
        assert len(activities) == 1
        assert activities[0].workstream is None
        assert activities[0].source_key == "OCPEDGE-999"
        assert activities[0].member == "charlie@redhat.com"
        assert activities[0].attribution_source is None


class TestWave2Attribution(unittest.TestCase):
    """Wave 2 attribution features: repo fallback, parent hop, SHARED, exclusions."""

    def test_jira_attribution_wins_over_repo(self):
        # PR in openshift/lvm-operator citing a TNF-component ticket attributes to TNF
        pr = _pr("OCPEDGE-100: Add test", "TNF work", repo="openshift/lvm-operator")
        resolve = _resolver({"OCPEDGE-100": ("TNF", "component")})
        activities = collect_github.pr_to_activities(pr, "u@redhat.com", "pr_authored", resolve)
        assert len(activities) == 1
        assert activities[0].workstream == "TNF"
        assert activities[0].attribution_source == "component"

    def test_repo_fallback_when_no_jira_keys(self):
        # Keyless PR in openshift/lvm-operator → LVMS via repo fallback
        pr = _pr("cleanup", "no ticket", repo="openshift/lvm-operator")
        resolve = _resolver({})
        activities = collect_github.pr_to_activities(pr, "u@redhat.com", "pr_authored", resolve)
        assert len(activities) == 1
        assert activities[0].workstream == "LVMS"
        assert activities[0].attribution_source == "repo"
        assert activities[0].source_key is None

    def test_keyless_pr_in_microshift_repo_attributes_to_ushift(self):
        # Keyless PR in openshift/microshift → USHIFT via repo fallback
        pr = _pr("fix bug", "no key", repo="openshift/microshift")
        resolve = _resolver({})
        activities = collect_github.pr_to_activities(pr, "u@redhat.com", "pr_authored", resolve)
        assert len(activities) == 1
        assert activities[0].workstream == "USHIFT"
        assert activities[0].attribution_source == "repo"

    def test_ushift_project_key_with_no_component_uses_project_fallback(self):
        # USHIFT-nnn key with no component → USHIFT via project fallback
        # This happens in resolve_workstreams, so we need to test that
        client = _FakeSearchClient(
            {
                "key in (USHIFT-6337)": [
                    {"key": "USHIFT-6337", "fields": {"components": [], "parent": None}}
                ]
            }
        )
        mapping = collect_github.resolve_workstreams(client, ["USHIFT-6337"])
        assert mapping["USHIFT-6337"] == ("USHIFT", "project")

    def test_parent_hop_when_issue_has_no_component(self):
        # PR citing a component-less ticket whose parent has "Two Node Fencing" → TNF
        client = _FakeSearchClient(
            {
                "key in (OCPEDGE-200)": [
                    {
                        "key": "OCPEDGE-200",
                        "fields": {
                            "components": [],
                            "parent": {"key": "OCPEDGE-100"},
                        },
                    }
                ],
                "key in (OCPEDGE-100)": [
                    _issue_with_components("OCPEDGE-100", ["Two Node Fencing"])
                ],
            }
        )
        mapping = collect_github.resolve_workstreams(client, ["OCPEDGE-200"])
        assert mapping["OCPEDGE-200"] == ("TNF", "parent")

    def test_shared_repo_attributes_to_shared_column(self):
        # Keyless PR in openshift/release → SHARED
        pr = _pr("Update CI config", "no key", repo="openshift/release")
        resolve = _resolver({})
        activities = collect_github.pr_to_activities(pr, "u@redhat.com", "pr_authored", resolve)
        assert len(activities) == 1
        assert activities[0].workstream == "SHARED"
        assert activities[0].attribution_source == "shared"

    def test_excluded_repo_returns_empty(self):
        # PR in personal namespace → dropped
        pr = _pr("Personal project", "no key", repo="jeff-roche/roundhouse")
        resolve = _resolver({})
        activities = collect_github.pr_to_activities(pr, "u@redhat.com", "pr_authored", resolve)
        assert activities == []

    def test_jira_key_does_not_rescue_a_personal_repo_pr(self):
        # Product rule: work only counts when it lands in a Red Hat product or
        # engineering org. A Jira key in the title does not promote a PR opened
        # against someone's own repo. Pins the order of the attribution chain —
        # exclusion must run before the Jira-key lookup, not after.
        pr = _pr(
            "OCPBUGS-104449: Add ODF documentation for TNF two-node clusters",
            "OCPBUGS-104449",
            repo="pablofontanilla/tnf-preset",
        )
        resolve = _resolver({"OCPBUGS-104449": ("TNF", "component")})
        activities = collect_github.pr_to_activities(pr, "u@redhat.com", "pr_authored", resolve)
        assert activities == []

    def test_jira_key_still_attributes_inside_an_org(self):
        # The mirror of the rule above: the same PR in an org repo does count,
        # so the exclusion is not silently swallowing everything.
        pr = _pr(
            "OCPBUGS-104449: Add ODF documentation for TNF two-node clusters",
            "OCPBUGS-104449",
            repo="openshift/oc-tnf",
        )
        resolve = _resolver({"OCPBUGS-104449": ("TNF", "component")})
        activities = collect_github.pr_to_activities(pr, "u@redhat.com", "pr_authored", resolve)
        assert [activity.workstream for activity in activities] == ["TNF"]

    def test_two_node_toolbox_keyless_pr_is_unattributed(self):
        # openshift-eng/two-node-toolbox is intentionally NOT in the repo map
        # Keyless PR → workstream None (regression pin)
        pr = _pr("Update docs", "no key", repo="openshift-eng/two-node-toolbox")
        resolve = _resolver({})
        activities = collect_github.pr_to_activities(pr, "u@redhat.com", "pr_authored", resolve)
        assert len(activities) == 1
        assert activities[0].workstream is None
        assert activities[0].attribution_source is None

    def test_multiple_keys_to_different_workstreams_emits_multiple_activities(self):
        # Already covered, but explicitly verify it still works
        pr = _pr("OCPEDGE-1 and OCPBUGS-2", "multi-workstream")
        resolve = _resolver({"OCPEDGE-1": ("SNO", "component"), "OCPBUGS-2": ("TNF", "component")})
        activities = collect_github.pr_to_activities(pr, "u@redhat.com", "pr_authored", resolve)
        assert len(activities) == 2
        assert {a.workstream for a in activities} == {"SNO", "TNF"}

    def test_json_output_contains_attribution_source(self):
        # Verify the dataclass serialization includes attribution_source
        import json
        from dataclasses import asdict

        activity = collect_github.GithubActivity(
            member="u@redhat.com",
            workstream="SNO",
            kind="pr_authored",
            repo="openshift/example",
            pr_url="https://gh/pr/1",
            ts="2026-05-01T00:00:00Z",
            source_key="OCPEDGE-1",
            attribution_source="component",
        )
        serialized = json.loads(json.dumps(asdict(activity)))
        assert serialized["attribution_source"] == "component"


class TestResolveWorkstreamsEmptyInput(unittest.TestCase):
    def test_empty_input_makes_zero_queries(self):
        client = _FakeSearchClient({})
        mapping = collect_github.resolve_workstreams(client, [])
        assert mapping == {}
        assert len(client.search_calls) == 0


class TestTallyExcludedRepos(unittest.TestCase):
    """A bare total hides which org went missing; the tally names names."""

    def test_counts_dropped_prs_per_repo(self):
        prs = [
            _pr("a", repo="jeff-roche/roundhouse"),
            _pr("b", repo="jeff-roche/roundhouse"),
            _pr("c", repo="jaypoulz/edge-tooling"),
        ]
        assert collect_github.tally_excluded_repos(prs) == {
            "jeff-roche/roundhouse": 2,
            "jaypoulz/edge-tooling": 1,
        }

    def test_org_repos_are_not_tallied(self):
        prs = [_pr("a", repo="openshift/lvm-operator"), _pr("b", repo="kubevirt/hco")]
        assert collect_github.tally_excluded_repos(prs) == {}

    def test_no_prs_yields_empty_tally(self):
        assert collect_github.tally_excluded_repos([]) == {}

    def test_total_matches_the_sum_of_the_tally(self):
        # The existing excluded_personal_repo_prs counter must stay consistent
        # with the per-repo breakdown, or the two numbers will disagree in the
        # data-quality block.
        prs = [
            _pr("a", repo="jeff-roche/cankan"),
            _pr("b", repo="sshnaidm/spenda"),
            _pr("c", repo="openshift/origin"),
        ]
        assert sum(collect_github.tally_excluded_repos(prs).values()) == 2


if __name__ == "__main__":
    unittest.main()
