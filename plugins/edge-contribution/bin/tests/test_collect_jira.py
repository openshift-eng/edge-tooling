"""Tests for collect_jira.py — Jira REST activity collection and attribution.

The HTTP layer is injected (a fake JiraClient, or a real JiraClient over a fake
transport), so no test touches the network. Coverage spans happy-path
attribution, failure inputs (missing credentials, auth/HTTP errors surfaced),
and boundary/anti-cheat cases: multi-component fan-out, alias de-duplication,
Planning/unmapped → unattributed (never dropped), query scope (no unfiltered
project scans, no comment collection), and the full attribution chain
(component → parent → project).
"""

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import _common  # noqa: E402
import collect_jira  # noqa: E402

WINDOW = _common.Window(date(2026, 4, 1), date(2026, 6, 30))


def _issue(key, components, updated="2026-05-01T10:00:00.000+0000", comments=None, qa=None, parent=None):
    fields = {
        "components": [{"name": name} for name in components],
        "updated": updated,
        "customfield_10470": qa,
    }
    if comments is not None:
        fields["comment"] = {"comments": comments}
    if parent is not None:
        fields["parent"] = {"key": parent}
    return {"key": key, "fields": fields}


def _comment(author_email, created):
    return {"author": {"emailAddress": author_email}, "created": created}


class FakeJiraClient:
    """Returns canned issues, routing by a substring match on the JQL."""

    def __init__(self, issues=None, issues_by_jql=None, base_url="https://jira.test"):
        self.base_url = base_url
        self._issues = issues or []
        self._issues_by_jql = issues_by_jql or {}
        self.searches = []

    def search(self, jql, fields):
        self.searches.append(jql)
        for needle, issues in self._issues_by_jql.items():
            if needle in jql:
                return issues
        return list(self._issues)


class _QueuedTransport:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, method, path, body):
        self.calls.append((method, path, body))
        return self._responses.pop(0)


def _real_client(responses):
    config = _common.JiraConfig("https://jira.test", "u", "t")
    return _common.JiraClient(config, _QueuedTransport(responses), max_retries=1)


class TestAssigneeCollectionHappyPath(unittest.TestCase):
    def test_single_component_is_tagged_with_workstream(self):
        client = FakeJiraClient(issues=[_issue("OCPEDGE-1", ["SNO"])])
        activities = collect_jira.collect_assignee_activity(client, "u@redhat.com", WINDOW)
        assert len(activities) == 1
        assert activities[0].workstream == "SNO"
        assert activities[0].attribution_source == "component"
        assert activities[0].kind == "assignee"
        assert activities[0].issue_key == "OCPEDGE-1"
        assert activities[0].url == "https://jira.test/browse/OCPEDGE-1"

    def test_empty_result_returns_empty_list(self):
        client = FakeJiraClient(issues=[])
        assert collect_jira.collect_assignee_activity(client, "u@redhat.com", WINDOW) == []


class TestAttributionEdgeCases(unittest.TestCase):
    def test_multiple_components_yield_one_row_per_mappable_workstream(self):
        client = FakeJiraClient(issues=[_issue("OCPEDGE-2", ["SNO", "Two Node Fencing"])])
        activities = collect_jira.collect_assignee_activity(client, "u@redhat.com", WINDOW)
        assert {a.workstream for a in activities} == {"SNO", "TNF"}
        assert all(a.attribution_source == "component" for a in activities)

    def test_alias_components_dedupe_to_one_workstream(self):
        client = FakeJiraClient(issues=[_issue("OCPEDGE-3", ["TNF", "Two Node Fencing"])])
        activities = collect_jira.collect_assignee_activity(client, "u@redhat.com", WINDOW)
        assert [a.workstream for a in activities] == ["TNF"]
        assert activities[0].attribution_source == "component"

    def test_planning_component_becomes_unattributed_not_dropped(self):
        client = FakeJiraClient(issues=[_issue("OCPEDGE-4", ["Planning"])])
        activities = collect_jira.collect_assignee_activity(client, "u@redhat.com", WINDOW)
        assert len(activities) == 1
        assert activities[0].workstream is None
        assert activities[0].attribution_source is None

    def test_issue_without_components_becomes_unattributed(self):
        client = FakeJiraClient(issues=[_issue("OCPEDGE-5", [])])
        activities = collect_jira.collect_assignee_activity(client, "u@redhat.com", WINDOW)
        assert [a.workstream for a in activities] == [None]
        assert activities[0].attribution_source is None

    def test_qa_collection_tolerates_null_qa_field(self):
        client = FakeJiraClient(issues=[_issue("OCPEDGE-6", ["SNO"], qa=None)])
        activities = collect_jira.collect_qa_activity(client, "u@redhat.com", WINDOW)
        assert [a.workstream for a in activities] == ["SNO"]
        assert activities[0].kind == "qa"
        assert activities[0].attribution_source == "component"


class TestCommentCollectionIsGone(unittest.TestCase):
    """Comment collection was removed; these pin that it stays removed.

    Jira Cloud strips ``emailAddress`` from comment authors, so the author match
    could never fire and the collector produced zero comment records while
    scanning every issue in the project for each member. Re-adding it by email
    would silently restore that cost for no data.
    """

    def test_collect_member_emits_no_comment_activity(self):
        issue = _issue(
            "OCPEDGE-7",
            ["SNO"],
            comments=[_comment("u@redhat.com", "2026-05-15T09:00:00.000+0000")],
        )
        client = FakeJiraClient(issues=[issue])
        activities = collect_jira.collect_member(client, "u@redhat.com", WINDOW)
        assert [a for a in activities if a.kind == "comment"] == []

    def test_no_query_scans_a_whole_project(self):
        # Every activity query must narrow by member. An unfiltered
        # ``project = X AND updated ...`` scan is what made comment collection
        # cost thousands of issue fetches per member.
        client = FakeJiraClient(issues=[_issue("OCPEDGE-11", ["SNO"], parent="OCPEDGE-99")])
        collect_jira.collect_member(client, "u@redhat.com", WINDOW)
        for jql in client.searches:
            narrowed = "u@redhat.com" in jql or jql.startswith("key in (")
            assert narrowed, f"unfiltered project scan: {jql}"


class TestJqlBuilders(unittest.TestCase):
    def test_assignee_jql_scopes_projects_assignee_and_window(self):
        jql = collect_jira.assignee_jql("u@redhat.com", WINDOW)
        assert 'assignee = "u@redhat.com"' in jql
        assert "2026-04-01" in jql
        assert "2026-06-30" in jql

    def test_assignee_jql_covers_every_activity_project(self):
        # OCPEDGE alone missed 176 USHIFT and 112 OCPBUGS tickets in 2026Q3
        # that no PR title referenced.
        jql = collect_jira.assignee_jql("u@redhat.com", WINDOW)
        for project in ("OCPEDGE", "USHIFT", "OCPBUGS"):
            assert project in jql

    def test_qa_jql_uses_qa_contact_custom_field(self):
        assert "cf[10470]" in collect_jira.qa_contact_jql("u@redhat.com", WINDOW)

    def test_qa_jql_covers_every_activity_project(self):
        jql = collect_jira.qa_contact_jql("u@redhat.com", WINDOW)
        for project in ("OCPEDGE", "USHIFT", "OCPBUGS"):
            assert project in jql

    def test_ocpstrat_jql_uses_sme_custom_field(self):
        assert "cf[10475]" in collect_jira.ocpstrat_role_jql("u@redhat.com", WINDOW)

    def test_ocpstrat_jql_stays_scoped_to_ocpstrat(self):
        # OCPSTRAT is a different question (strategic roles, cf 10475), so it
        # must not pick up the widened activity-project list.
        jql = collect_jira.ocpstrat_role_jql("u@redhat.com", WINDOW)
        assert "project = OCPSTRAT" in jql
        assert "OCPBUGS" not in jql


class TestFailureInputs(unittest.TestCase):
    def test_missing_credentials_raise_before_any_http(self):
        calls = []

        def transport(method, path, body):
            calls.append((method, path, body))
            return _common.HttpResponse(200, "{}")

        with self.assertRaises(_common.JiraAuthError):
            collect_jira.build_jira_client({}, transport=transport)
        assert calls == []

    def test_auth_error_propagates_through_collector(self):
        client = _real_client([_common.HttpResponse(401, "nope")])
        with self.assertRaises(_common.JiraAuthError):
            collect_jira.collect_assignee_activity(client, "u@redhat.com", WINDOW)

    def test_malformed_json_propagates_through_collector(self):
        client = _real_client([_common.HttpResponse(200, "{not json")])
        with self.assertRaises(_common.CollectorError):
            collect_jira.collect_assignee_activity(client, "u@redhat.com", WINDOW)


class TestPaginationThroughCollector(unittest.TestCase):
    def test_two_page_search_returns_all_activities(self):
        page_one = _common.HttpResponse(
            200,
            '{"issues": [{"key": "OCPEDGE-1", "fields": '
            '{"components": [{"name": "SNO"}]}}], "nextPageToken": "t2"}',
        )
        page_two = _common.HttpResponse(
            200,
            '{"issues": [{"key": "OCPEDGE-2", "fields": '
            '{"components": [{"name": "MicroShift"}]}}], "isLast": true}',
        )
        client = _real_client([page_one, page_two])
        activities = collect_jira.collect_assignee_activity(client, "u@redhat.com", WINDOW)
        assert {a.workstream for a in activities} == {"SNO", "USHIFT"}


class TestAttributionChain(unittest.TestCase):
    def test_own_component_takes_precedence_over_parent(self):
        # Issue has its own "TNF" component AND a parent with "SNO" component
        parent_issue = _issue("OCPEDGE-100", ["SNO"])
        child_issue = _issue("OCPEDGE-2", ["TNF"], parent="OCPEDGE-100")
        client = FakeJiraClient(
            issues_by_jql={
                "assignee": [child_issue],
                "key in (OCPEDGE-100)": [parent_issue],
            }
        )
        activities = collect_jira.collect_assignee_activity(client, "u@redhat.com", WINDOW)
        assert len(activities) == 1
        assert activities[0].workstream == "TNF"
        assert activities[0].attribution_source == "component"

    def test_parent_hop_when_issue_has_no_component(self):
        # Component-less issue whose parent has "Two Node Fencing"
        parent_issue = _issue("OCPEDGE-101", ["Two Node Fencing"])
        child_issue = _issue("OCPEDGE-3", [], parent="OCPEDGE-101")
        client = FakeJiraClient(
            issues_by_jql={
                "assignee": [child_issue],
                "key in (OCPEDGE-101)": [parent_issue],
            }
        )
        activities = collect_jira.collect_assignee_activity(client, "u@redhat.com", WINDOW)
        assert len(activities) == 1
        assert activities[0].workstream == "TNF"
        assert activities[0].attribution_source == "parent"

    def test_project_fallback_for_ushift(self):
        # USHIFT-6337 with no component and no parent resolves to USHIFT via project
        issue = _issue("USHIFT-6337", [])
        client = FakeJiraClient(issues=[issue])
        activities = collect_jira.collect_assignee_activity(client, "u@redhat.com", WINDOW)
        assert len(activities) == 1
        assert activities[0].workstream == "USHIFT"
        assert activities[0].attribution_source == "project"

    def test_parent_precedence_over_project(self):
        # Component-less USHIFT issue WITH a parent that has a mappable component
        parent_issue = _issue("OCPEDGE-102", ["Logical Volume Manager Storage"])
        child_issue = _issue("USHIFT-100", [], parent="OCPEDGE-102")
        client = FakeJiraClient(
            issues_by_jql={
                "assignee": [child_issue],
                "OCPEDGE-102": [parent_issue],
            }
        )
        activities = collect_jira.collect_assignee_activity(client, "u@redhat.com", WINDOW)
        assert len(activities) == 1
        assert activities[0].workstream == "LVMS"
        assert activities[0].attribution_source == "parent"

    def test_dead_end_no_component_no_parent_unmapped_project(self):
        # OCPEDGE-2543: no component, no parent, unmapped project -> None
        issue = _issue("OCPEDGE-2543", [])
        client = FakeJiraClient(issues=[issue])
        activities = collect_jira.collect_assignee_activity(client, "u@redhat.com", WINDOW)
        assert len(activities) == 1
        assert activities[0].workstream is None
        assert activities[0].attribution_source is None

    def test_parent_exists_but_also_componentless(self):
        # Parent exists but is ALSO component-less -> falls through to project
        parent_issue = _issue("USHIFT-200", [])
        child_issue = _issue("USHIFT-201", [], parent="USHIFT-200")
        client = FakeJiraClient(
            issues_by_jql={
                "assignee": [child_issue],
                "key in (USHIFT-200)": [parent_issue],
            }
        )
        activities = collect_jira.collect_assignee_activity(client, "u@redhat.com", WINDOW)
        assert len(activities) == 1
        assert activities[0].workstream == "USHIFT"
        assert activities[0].attribution_source == "project"

    def test_issue_with_two_mapped_components_yields_both_workstreams(self):
        # An issue with two mapped components still yields both workstreams
        issue = _issue("OCPEDGE-999", ["SNO", "Logical Volume Manager Storage"])
        client = FakeJiraClient(issues=[issue])
        activities = collect_jira.collect_assignee_activity(client, "u@redhat.com", WINDOW)
        assert len(activities) == 2
        assert {a.workstream for a in activities} == {"SNO", "LVMS"}
        assert all(a.attribution_source == "component" for a in activities)


class TestResolveParentComponents(unittest.TestCase):
    def test_batches_at_boundary(self):
        # 150 keys with batch_size 100 should issue exactly 2 queries
        parent_keys = [f"OCPEDGE-{i}" for i in range(1, 151)]
        parent_issues = [_issue(key, ["SNO"]) for key in parent_keys]
        client = FakeJiraClient(
            issues_by_jql={
                # First batch
                "key in (" + ",".join(parent_keys[:100]): parent_issues[:100],
                # Second batch
                "key in (" + ",".join(parent_keys[100:]): parent_issues[100:],
            }
        )
        mapping = collect_jira.resolve_parent_components(client, parent_keys, batch_size=100)
        assert len(mapping) == 150
        assert all(mapping[key] == "SNO" for key in parent_keys)
        # Check that exactly 2 searches were made
        assert len(client.searches) == 2

    def test_empty_input_makes_zero_queries(self):
        client = FakeJiraClient()
        mapping = collect_jira.resolve_parent_components(client, [])
        assert mapping == {}
        assert len(client.searches) == 0

    def test_jira_auth_error_propagates(self):
        client = _real_client([_common.HttpResponse(401, "nope")])
        with self.assertRaises(_common.JiraAuthError):
            collect_jira.resolve_parent_components(client, ["OCPEDGE-1"])

    def test_collector_error_skips_batch(self):
        # First batch fails (exhausts retries), second batch succeeds
        # max_retries=1 means 2 attempts: initial + 1 retry
        batch1_initial = _common.HttpResponse(500, "server error")
        batch1_retry = _common.HttpResponse(500, "server error")
        batch2_response = _common.HttpResponse(
            200,
            '{"issues": [{"key": "OCPEDGE-101", "fields": {"components": [{"name": "SNO"}]}}], "isLast": true}',
        )
        client = _real_client([batch1_initial, batch1_retry, batch2_response])
        mapping = collect_jira.resolve_parent_components(
            client, ["OCPEDGE-100", "OCPEDGE-101"], batch_size=1
        )
        # First key failed, so it maps to None; second key succeeded
        assert mapping["OCPEDGE-100"] is None
        assert mapping["OCPEDGE-101"] == "SNO"


class TestUnattributedReason(unittest.TestCase):
    """The data-quality report needs to tell these two failures apart."""

    def test_no_parent_field_means_no_component_no_parent(self):
        issue = {"key": "OCPEDGE-1", "fields": {"components": []}}
        assert collect_jira.unattributed_reason(issue) == "no_component_no_parent"

    def test_null_parent_means_no_component_no_parent(self):
        issue = {"key": "OCPEDGE-1", "fields": {"components": [], "parent": None}}
        assert collect_jira.unattributed_reason(issue) == "no_component_no_parent"

    def test_parent_present_means_parent_also_empty(self):
        issue = {
            "key": "OCPEDGE-1",
            "fields": {"components": [], "parent": {"key": "OCPEDGE-9"}},
        }
        assert collect_jira.unattributed_reason(issue) == "parent_also_empty"

    def test_parent_object_without_a_key_is_treated_as_no_parent(self):
        issue = {"key": "OCPEDGE-1", "fields": {"components": [], "parent": {}}}
        assert collect_jira.unattributed_reason(issue) == "no_component_no_parent"

    def test_unattributed_activity_carries_the_reason(self):
        issue = {
            "key": "OCPEDGE-1",
            "fields": {"components": [], "parent": {"key": "OCPEDGE-9"}, "updated": "t"},
        }
        # The parent resolved to nothing, so attribution falls through to unattributed.
        activities = collect_jira.issue_to_activities(
            issue, "u@redhat.com", "assignee", "https://jira.test", {"OCPEDGE-9": None}
        )
        assert len(activities) == 1
        assert activities[0].workstream is None
        assert activities[0].unattributed_reason == "parent_also_empty"

    def test_attributed_activity_has_no_reason(self):
        issue = {
            "key": "OCPEDGE-1",
            "fields": {"components": [{"name": "SNO"}], "updated": "t"},
        }
        activities = collect_jira.issue_to_activities(
            issue, "u@redhat.com", "assignee", "https://jira.test", {}
        )
        assert activities[0].workstream == "SNO"
        assert activities[0].unattributed_reason is None


class TestJSONSerialization(unittest.TestCase):
    def test_activity_includes_attribution_source_in_json(self):
        import json
        from dataclasses import asdict

        activity = collect_jira.Activity(
            member="u@redhat.com",
            workstream="SNO",
            kind="assignee",
            issue_key="OCPEDGE-1",
            url="https://jira.test/browse/OCPEDGE-1",
            ts="2026-05-01T10:00:00.000+0000",
            attribution_source="component",
        )
        activity_dict = asdict(activity)
        assert "attribution_source" in activity_dict
        assert activity_dict["attribution_source"] == "component"
        # Verify it serializes to JSON
        json_str = json.dumps(activity_dict)
        assert "attribution_source" in json_str


if __name__ == "__main__":
    unittest.main()
