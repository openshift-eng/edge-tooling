"""Tests for report.py — aggregate collected activity into report structures.

Covers happy-path matrix/report construction, failure inputs (activity for an
unknown member or an unknown workstream is ignored, never counted), and
boundary/anti-cheat cases: unattributed items counted but kept out of the
matrix, duplicate contributions counted once toward workstreams touched but summed
per kind for the IC breakdown, and a member with no activity touching 0.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import _common  # noqa: E402
import report  # noqa: E402

SIX = ["SNO", "TNA", "TNF", "LVMS", "USHIFT", "TOPO"]
SEVEN = SIX + ["SHARED"]


def _activity(member, workstream, kind="assignee"):
    return {"member": member, "workstream": workstream, "kind": kind}


class TestBuildContributionMatrix(unittest.TestCase):
    def test_touched_sets_reflect_activity(self):
        activities = [
            _activity("alice", "SNO"),
            _activity("alice", "TNA"),
            _activity("bob", "SNO"),
        ]
        matrix = report.build_contribution_matrix(activities, ["alice", "bob"], SIX)
        assert matrix.touched["alice"] == {"SNO", "TNA"}
        assert matrix.touched["bob"] == {"SNO"}

    def test_activity_for_unknown_member_is_ignored(self):
        activities = [_activity("stranger", "SNO")]
        matrix = report.build_contribution_matrix(activities, ["alice"], SIX)
        assert matrix.touched["alice"] == set()

    def test_activity_for_unknown_workstream_is_ignored(self):
        activities = [_activity("alice", "BOGUS")]
        matrix = report.build_contribution_matrix(activities, ["alice"], SIX)
        assert matrix.touched["alice"] == set()

    def test_unattributed_activity_is_not_in_matrix(self):
        activities = [_activity("alice", None)]
        matrix = report.build_contribution_matrix(activities, ["alice"], SIX)
        assert matrix.touched["alice"] == set()

    def test_counts_populated_correctly(self):
        activities = [
            _activity("alice", "SNO"),
            _activity("alice", "SNO"),
            _activity("alice", "TNA"),
            _activity("bob", "SNO"),
        ]
        matrix = report.build_contribution_matrix(activities, ["alice", "bob"], SIX)
        assert matrix.counts["alice"]["SNO"] == 2
        assert matrix.counts["alice"]["TNA"] == 1
        assert matrix.counts["bob"]["SNO"] == 1

    def test_seven_columns_with_shared_still_counts_n_of_6(self):
        # Matrix built with display_columns() (7 columns: 6 + SHARED).
        # Workstreams touched should still count only the 6 canonical ones.
        activities = [
            _activity("alice", "SNO"),
            _activity("alice", "SHARED"),
        ]
        matrix = report.build_contribution_matrix(activities, ["alice"], SEVEN)
        # Matrix has 7 workstreams including SHARED
        assert len(matrix.workstreams) == 7
        assert "SHARED" in matrix.workstreams
        # Alice touched both SNO and SHARED
        assert matrix.touched["alice"] == {"SNO", "SHARED"}
        # But workstreams touched should be 1 (SHARED doesn't count)
        from metrics import workstreams_touched

        assert workstreams_touched(matrix, "alice") == 1


class TestCountUnattributed(unittest.TestCase):
    def test_counts_only_none_workstream(self):
        activities = [_activity("alice", None), _activity("alice", "SNO"), _activity("bob", None)]
        assert report.count_unattributed(activities) == 2

    def test_scoped_to_member(self):
        activities = [_activity("alice", None), _activity("bob", None)]
        assert report.count_unattributed(activities, member="alice") == 1


class TestBuildManagerReport(unittest.TestCase):
    def test_report_bundles_metrics_and_unattributed(self):
        activities = [
            _activity("alice", "SNO"),
            _activity("alice", "TNA"),
            _activity("bob", "SNO"),
            _activity("bob", None),
        ]
        result = report.build_manager_report(activities, ["alice", "bob"], SIX, "2026Q2")
        assert result.metrics.workstreams_touched_by_member == {"alice": 2, "bob": 1}
        assert result.signals.contributors_by_workstream["SNO"] == 2
        assert result.unattributed_count == 1
        assert result.period_label == "2026Q2"


class TestTranslateActivitiesToDisplayNames(unittest.TestCase):
    def test_translates_member_field(self):
        activities = [
            _activity("alice@redhat.com", "SNO"),
            _activity("bob@redhat.com", "TNA"),
        ]
        mapping = {"alice@redhat.com": "Alice Doe", "bob@redhat.com": "Bob Smith"}
        translated = report.translate_activities_to_display_names(activities, mapping)
        assert translated[0]["member"] == "Alice Doe"
        assert translated[1]["member"] == "Bob Smith"
        assert translated[0]["workstream"] == "SNO"
        assert translated[1]["workstream"] == "TNA"

    def test_fallback_to_jira_username_when_missing(self):
        activities = [_activity("charlie@redhat.com", "SNO")]
        mapping = {"alice@redhat.com": "Alice Doe"}
        translated = report.translate_activities_to_display_names(activities, mapping)
        assert translated[0]["member"] == "charlie@redhat.com"

    def test_does_not_mutate_original_activities(self):
        activities = [_activity("alice@redhat.com", "SNO")]
        mapping = {"alice@redhat.com": "Alice Doe"}
        report.translate_activities_to_display_names(activities, mapping)
        # Original should be unchanged
        assert activities[0]["member"] == "alice@redhat.com"


class TestBuildDataQuality(unittest.TestCase):
    def test_tallies_unattributed_by_repo(self):
        activities = [
            {"member": "alice", "workstream": None, "repo": "org/repo-a"},
            {"member": "bob", "workstream": None, "repo": "org/repo-a"},
            {"member": "charlie", "workstream": None, "repo": "org/repo-b"},
        ]
        dq = report.build_data_quality(activities, [])
        assert dq.unattributed_by_repo == {"org/repo-a": 2, "org/repo-b": 1}

    def test_ignores_attributed_records(self):
        activities = [
            {"member": "alice", "workstream": "SNO", "repo": "org/repo-a"},
            {"member": "bob", "workstream": None, "repo": "org/repo-b"},
        ]
        dq = report.build_data_quality(activities, [])
        assert dq.unattributed_by_repo == {"org/repo-b": 1}

    def test_tallies_jira_tickets_no_component_no_parent(self):
        activities = [
            {"member": "alice", "workstream": None},  # Jira ticket, no repo key
            {"member": "bob", "workstream": None},
        ]
        dq = report.build_data_quality(activities, [])
        assert dq.tickets_no_component_no_parent == 2

    def test_mixed_jira_and_github(self):
        activities = [
            {"member": "alice", "workstream": None, "repo": "org/repo-a"},  # GitHub
            {"member": "bob", "workstream": None},  # Jira
            {"member": "charlie", "workstream": "SNO"},  # Attributed, ignored
        ]
        dq = report.build_data_quality(activities, [])
        assert dq.unattributed_by_repo == {"org/repo-a": 1}
        assert dq.tickets_no_component_no_parent == 1

    def test_excluded_members_captured(self):
        dq = report.build_data_quality([], ["alice", "bob"])
        assert dq.excluded_members == ["alice", "bob"]

    def test_undistinguishable_fields_are_zero(self):
        # tickets_parent_also_empty and excluded_personal_repo_prs cannot be derived
        dq = report.build_data_quality([], [])
        assert dq.tickets_parent_also_empty == 0
        assert dq.excluded_personal_repo_prs == 0


class TestShouldUseColor(unittest.TestCase):
    def test_all_conditions_true_enables_color(self):
        assert report.should_use_color("text", True, None, False) is True

    def test_no_color_flag_disables(self):
        assert report.should_use_color("text", True, None, True) is False

    def test_non_text_format_disables(self):
        assert report.should_use_color("markdown", True, None, False) is False

    def test_env_no_color_disables(self):
        assert report.should_use_color("text", True, "1", False) is False
        assert report.should_use_color("text", True, "true", False) is False

    def test_not_a_tty_disables(self):
        assert report.should_use_color("text", False, None, False) is False

    def test_no_color_flag_overrides_all(self):
        # Even if all other conditions are true, --no-color wins
        assert report.should_use_color("text", True, None, True) is False


class TestExcludedMembers(unittest.TestCase):
    def test_excluded_members_filtered_from_manager_report(self):
        # Simulate EXCLUDED_MEMBERS being filtered
        activities = [
            _activity("alice", "SNO"),
            _activity("fuzzbuzz@redhat.com", "TNA"),  # This is in EXCLUDED_MEMBERS
            _activity("bob", "LVMS"),
        ]
        # Build report with fuzzbuzz excluded
        members_without_excluded = ["alice", "bob"]
        excluded = ["fuzzbuzz@redhat.com"]
        report_obj = report.build_manager_report(
            activities, members_without_excluded, SIX, "2026Q2", excluded
        )
        # fuzzbuzz should not be in the matrix
        assert "fuzzbuzz@redhat.com" not in report_obj.matrix.members
        assert "alice" in report_obj.matrix.members
        assert "bob" in report_obj.matrix.members
        # But fuzzbuzz should be in data_quality.excluded_members
        assert report_obj.data_quality.excluded_members == ["fuzzbuzz@redhat.com"]


class TestParseActivityFile(unittest.TestCase):
    def test_envelope_yields_records_and_meta(self):
        payload = {
            "activities": [_activity("alice", "SNO")],
            "collector_meta": {"excluded_personal_repo_prs": 7},
        }
        records, meta = report.parse_activity_file(payload)
        assert records == [_activity("alice", "SNO")]
        assert meta == {"excluded_personal_repo_prs": 7}

    def test_bare_list_is_accepted_as_the_pre_envelope_format(self):
        records, meta = report.parse_activity_file([_activity("alice", "SNO")])
        assert records == [_activity("alice", "SNO")]
        assert meta == {}

    def test_envelope_without_meta_yields_empty_meta(self):
        records, meta = report.parse_activity_file({"activities": []})
        assert records == []
        assert meta == {}

    def test_null_meta_yields_empty_meta_not_none(self):
        _records, meta = report.parse_activity_file({"activities": [], "collector_meta": None})
        assert meta == {}


class TestLoadActivities(unittest.TestCase):
    def _write(self, tmpdir, name, payload):
        path = os.path.join(tmpdir, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        return path

    def test_counters_sum_across_files_and_records_concatenate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            jira = self._write(
                tmpdir,
                "jira.json",
                {"activities": [_activity("alice", "SNO")], "collector_meta": {}},
            )
            github = self._write(
                tmpdir,
                "github.json",
                {
                    "activities": [_activity("bob", "LVMS")],
                    "collector_meta": {"excluded_personal_repo_prs": 5},
                },
            )
            activities, meta = report._load_activities([jira, github])
        assert len(activities) == 2
        assert meta == {"excluded_personal_repo_prs": 5}

    def test_round_trips_the_envelope_the_collectors_actually_write(self):
        # Pins the two ends together: if _common.activity_payload changes shape,
        # this fails here rather than silently reporting zero excluded PRs.
        written = _common.activity_payload(
            [_activity("alice", "SNO")], excluded_personal_repo_prs=101
        )
        records, meta = report.parse_activity_file(json.loads(json.dumps(written)))
        assert records == [_activity("alice", "SNO")]
        assert meta["excluded_personal_repo_prs"] == 101

    def test_breakdown_counters_merge_key_by_key(self):
        # excluded_personal_repos is a {repo: count} map, not a total. Summing it
        # like an int would raise; ignoring it would lose the repo names.
        with tempfile.TemporaryDirectory() as tmpdir:
            first = self._write(
                tmpdir,
                "a.json",
                {
                    "activities": [],
                    "collector_meta": {"excluded_personal_repos": {"a/one": 2, "b/two": 1}},
                },
            )
            second = self._write(
                tmpdir,
                "b.json",
                {
                    "activities": [],
                    "collector_meta": {"excluded_personal_repos": {"a/one": 3, "c/three": 5}},
                },
            )
            _activities, meta = report._load_activities([first, second])
        assert meta["excluded_personal_repos"] == {"a/one": 5, "b/two": 1, "c/three": 5}

    def test_totals_and_breakdowns_merge_side_by_side(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write(
                tmpdir,
                "gh.json",
                {
                    "activities": [],
                    "collector_meta": {
                        "excluded_personal_repo_prs": 3,
                        "excluded_personal_repos": {"a/one": 3},
                    },
                },
            )
            _activities, meta = report._load_activities([path, path])
        assert meta["excluded_personal_repo_prs"] == 6
        assert meta["excluded_personal_repos"] == {"a/one": 6}

    def test_legacy_bare_list_file_loads_alongside_an_envelope(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            legacy = self._write(tmpdir, "legacy.json", [_activity("alice", "SNO")])
            modern = self._write(
                tmpdir,
                "modern.json",
                {
                    "activities": [_activity("bob", "LVMS")],
                    "collector_meta": {"excluded_personal_repo_prs": 3},
                },
            )
            activities, meta = report._load_activities([legacy, modern])
        assert len(activities) == 2
        assert meta == {"excluded_personal_repo_prs": 3}


class TestBuildDataQualityCounters(unittest.TestCase):
    """The two counters that used to be hardcoded to 0."""

    def test_parent_also_empty_is_separated_from_no_parent(self):
        activities = [
            {"member": "alice", "workstream": None, "kind": "assignee"},
            {
                "member": "alice",
                "workstream": None,
                "kind": "assignee",
                "unattributed_reason": "parent_also_empty",
            },
            {
                "member": "bob",
                "workstream": None,
                "kind": "qa",
                "unattributed_reason": "no_component_no_parent",
            },
        ]
        quality = report.build_data_quality(activities, [])
        assert quality.tickets_parent_also_empty == 1
        assert quality.tickets_no_component_no_parent == 2

    def test_attributed_tickets_are_not_counted(self):
        activities = [
            {
                "member": "alice",
                "workstream": "SNO",
                "kind": "assignee",
                "unattributed_reason": "parent_also_empty",
            }
        ]
        quality = report.build_data_quality(activities, [])
        assert quality.tickets_parent_also_empty == 0
        assert quality.tickets_no_component_no_parent == 0

    def test_github_prs_are_bucketed_by_repo_not_by_ticket_reason(self):
        activities = [
            {"member": "alice", "workstream": None, "kind": "pr_authored", "repo": "o/x"},
            {"member": "bob", "workstream": None, "kind": "pr_authored", "repo": "o/x"},
        ]
        quality = report.build_data_quality(activities, [])
        assert quality.unattributed_by_repo == {"o/x": 2}
        assert quality.tickets_no_component_no_parent == 0
        assert quality.tickets_parent_also_empty == 0

    def test_excluded_personal_repo_prs_comes_from_collector_meta(self):
        quality = report.build_data_quality([], [], {"excluded_personal_repo_prs": 101})
        assert quality.excluded_personal_repo_prs == 101

    def test_missing_collector_meta_leaves_the_counter_at_zero(self):
        assert report.build_data_quality([], []).excluded_personal_repo_prs == 0

    def test_excluded_repos_breakdown_reaches_data_quality(self):
        quality = report.build_data_quality(
            [],
            [],
            {
                "excluded_personal_repo_prs": 5,
                "excluded_personal_repos": {"jeff-roche/roundhouse": 5},
            },
        )
        assert quality.excluded_personal_repos == {"jeff-roche/roundhouse": 5}

    def test_missing_breakdown_leaves_an_empty_map_not_none(self):
        assert report.build_data_quality([], []).excluded_personal_repos == {}

    def test_manager_report_threads_collector_meta_through(self):
        report_obj = report.build_manager_report(
            [_activity("alice", "SNO")],
            ["alice"],
            SIX,
            "2026Q2",
            [],
            {"excluded_personal_repo_prs": 12},
        )
        assert report_obj.data_quality.excluded_personal_repo_prs == 12


class TestBuildICReport(unittest.TestCase):
    def test_workstreams_touched_and_per_kind_breakdown(self):
        activities = [
            _activity("alice", "SNO", "assignee"),
            _activity("alice", "SNO", "pr_authored"),
            _activity("alice", "TNA", "comment"),
            _activity("bob", "SNO", "assignee"),
        ]
        result = report.build_ic_report(activities, "alice", SIX, "2026Q2")
        assert result.workstreams_touched == 2
        assert result.total_workstreams == 6
        by_workstream = {activity.workstream: activity.counts for activity in result.activity}
        assert by_workstream["SNO"] == {"assignee": 1, "pr_authored": 1}
        assert by_workstream["TNA"] == {"comment": 1}

    def test_duplicate_contributions_count_once_but_sum_per_kind(self):
        activities = [
            _activity("alice", "SNO", "comment"),
            _activity("alice", "SNO", "comment"),
        ]
        result = report.build_ic_report(activities, "alice", SIX, "2026Q2")
        assert result.workstreams_touched == 1
        assert result.activity[0].counts == {"comment": 2}

    def test_member_with_no_activity_touches_zero_workstreams(self):
        result = report.build_ic_report([], "alice", SIX, "2026Q2")
        assert result.workstreams_touched == 0
        assert result.activity == []

    def test_workstreams_appear_in_canonical_order(self):
        activities = [_activity("alice", "TOPO"), _activity("alice", "SNO")]
        result = report.build_ic_report(activities, "alice", SIX, "2026Q2")
        assert [activity.workstream for activity in result.activity] == ["SNO", "TOPO"]


class TestClassifySource(unittest.TestCase):
    def test_jira_kinds_classified_as_jira(self):
        assert report.classify_source("assignee") == "jira"
        assert report.classify_source("qa") == "jira"
        assert report.classify_source("ocpstrat_role") == "jira"

    def test_github_kinds_classified_as_github(self):
        assert report.classify_source("pr_authored") == "github"
        assert report.classify_source("pr_reviewed") == "github"

    def test_unknown_kind_classified_as_other(self):
        assert report.classify_source("unknown_kind") == "other"
        assert report.classify_source("comment") == "other"


class TestResolvePeriodWindow(unittest.TestCase):
    def test_quarter_label_resolves_to_real_dates(self):
        window = report.resolve_period_window("2026Q3")
        assert window == ("2026-07-01", "2026-09-30")

    def test_explicit_date_range_splits_on_underscore(self):
        window = report.resolve_period_window("2026-01-15_2026-03-31")
        assert window == ("2026-01-15", "2026-03-31")

    def test_invalid_label_returns_none(self):
        assert report.resolve_period_window("invalid") is None
        assert report.resolve_period_window("2026") is None
        assert report.resolve_period_window("") is None

class TestCountOutOfWindowRecords(unittest.TestCase):
    def test_counts_records_before_window(self):
        activities = [
            {**_activity("alice", "SNO"), "ts": "2026-06-30T23:59:59Z"},
            {**_activity("bob", "TNA"), "ts": "2026-07-01T00:00:00Z"},
        ]
        window = ("2026-07-01", "2026-09-30")
        count = report.count_out_of_window_records(activities, window)
        assert count == 1

    def test_counts_records_after_window(self):
        activities = [
            {**_activity("alice", "SNO"), "ts": "2026-09-30T23:59:59Z"},
            {**_activity("bob", "TNA"), "ts": "2026-10-01T00:00:00Z"},
        ]
        window = ("2026-07-01", "2026-09-30")
        count = report.count_out_of_window_records(activities, window)
        assert count == 1

    def test_boundary_dates_are_inside(self):
        activities = [
            {**_activity("alice", "SNO"), "ts": "2026-07-01T00:00:00Z"},
            {**_activity("bob", "TNA"), "ts": "2026-09-30T23:59:59Z"},
        ]
        window = ("2026-07-01", "2026-09-30")
        count = report.count_out_of_window_records(activities, window)
        assert count == 0

    def test_missing_ts_does_not_raise(self):
        activities = [
            _activity("alice", "SNO"),  # No ts field
        ]
        window = ("2026-07-01", "2026-09-30")
        count = report.count_out_of_window_records(activities, window)
        assert count == 0

    def test_malformed_ts_does_not_raise(self):
        activities = [
            {**_activity("alice", "SNO"), "ts": "not-a-date"},
            {**_activity("bob", "TNA"), "ts": "2026-07-15T00:00:00Z"},
        ]
        window = ("2026-07-01", "2026-09-30")
        count = report.count_out_of_window_records(activities, window)
        # Malformed should not count as out-of-window
        assert count == 0

    def test_none_window_returns_zero(self):
        activities = [
            {**_activity("alice", "SNO"), "ts": "2026-06-15T00:00:00Z"},
        ]
        count = report.count_out_of_window_records(activities, None)
        assert count == 0

    def test_empty_activities_returns_zero(self):
        window = ("2026-07-01", "2026-09-30")
        count = report.count_out_of_window_records([], window)
        assert count == 0


class TestBuildWorkstreamLines(unittest.TestCase):
    """Tests for build_workstream_lines function."""

    def test_returns_one_line_per_workstream_sorted_by_volume(self):
        # Build a matrix with known counts
        matrix = report.build_contribution_matrix(
            [
                _activity("alice", "SNO"),
                _activity("alice", "SNO"),
                _activity("alice", "TNA"),
                _activity("bob", "SNO"),
                _activity("bob", "LVMS"),
                _activity("bob", "LVMS"),
                _activity("bob", "LVMS"),
            ],
            ["alice", "bob"],
            SEVEN,
        )
        workstreams = SEVEN
        lines = report.build_workstream_lines(matrix, workstreams)
        # Should be sorted by volume descending
        # SNO and LVMS both have volume 3, so either can be first (ties break deterministically)
        assert len(lines) == 7  # All 7 workstreams
        assert lines[0].volume == 3
        assert lines[1].volume == 3
        assert lines[0].workstream in ("SNO", "LVMS")
        assert lines[1].workstream in ("SNO", "LVMS")
        assert lines[0].workstream != lines[1].workstream  # Both appear but different
        # TNA should be third with volume 1
        assert lines[2].workstream == "TNA"
        assert lines[2].volume == 1

    def test_top_contributor_and_share_correct(self):
        # alice has 100 of 150 in SNO = 67%
        matrix = report.build_contribution_matrix(
            [_activity("alice", "SNO")] * 100 + [_activity("bob", "SNO")] * 50,
            ["alice", "bob"],
            SIX,
        )
        lines = report.build_workstream_lines(matrix, SIX)
        sno = next(line for line in lines if line.workstream == "SNO")
        assert sno.top_contributor == "alice"
        assert sno.top_share == 67  # rounded int percent

    def test_zero_volume_workstream_no_zero_division(self):
        # A workstream with zero activity should have top_contributor=None, top_share=0
        matrix = report.build_contribution_matrix(
            [_activity("alice", "SNO")],
            ["alice"],
            SIX,
        )
        lines = report.build_workstream_lines(matrix, SIX)
        # TNA should have volume 0
        tna = next((line for line in lines if line.workstream == "TNA"), None)
        if tna:
            assert tna.volume == 0
            assert tna.top_contributor is None
            assert tna.top_share == 0


class TestBuildExecutiveSummaryRefactored(unittest.TestCase):
    """Tests for updated build_executive_summary with per_workstream and member top_workstream."""

    def test_populates_per_workstream(self):
        activities = [
            _activity("alice", "SNO"),
            _activity("alice", "SNO"),
            _activity("bob", "SNO"),
            _activity("bob", "TNA"),
        ]
        summary = report.build_executive_summary(
            activities, ["alice", "bob"], SEVEN, "2026Q3", [], {}
        )
        assert hasattr(summary, "per_workstream")
        assert len(summary.per_workstream) > 0
        # SNO should be first (volume 3)
        assert summary.per_workstream[0].workstream == "SNO"
        assert summary.per_workstream[0].volume == 3

    def test_member_line_has_top_workstream_and_share(self):
        activities = [
            _activity("alice", "SNO"),
            _activity("alice", "SNO"),
            _activity("alice", "SNO"),
            _activity("alice", "TNA"),
        ]
        summary = report.build_executive_summary(activities, ["alice"], SEVEN, "2026Q3", [], {})
        alice = summary.per_member[0]
        assert alice.top_workstream == "SNO"
        assert alice.top_share == 75  # 3 of 4

    def test_empty_activities_produces_summary_without_error(self):
        # Should not raise ZeroDivisionError
        summary = report.build_executive_summary([], [], SEVEN, "2026Q3", [], {})
        # With no members, per_workstream should have all workstreams with volume=0
        assert len(summary.per_workstream) == 7  # SEVEN includes SHARED
        assert all(ws.volume == 0 for ws in summary.per_workstream)
        assert summary.per_member == []


class TestBuildExecutiveSummary(unittest.TestCase):
    def test_source_kind_split(self):
        activities = [
            _activity("alice", "SNO", "assignee"),
            _activity("alice", "SNO", "assignee"),
            _activity("alice", "TNA", "qa"),
            _activity("bob", "SNO", "pr_authored"),
            _activity("bob", "LVMS", "pr_reviewed"),
        ]
        summary = report.build_executive_summary(
            activities, ["alice", "bob"], SEVEN, "2026Q3", [], {}
        )

        assert summary.counts_by_source_kind["jira"]["assignee"] == 2
        assert summary.counts_by_source_kind["jira"]["qa"] == 1
        assert summary.counts_by_source_kind["github"]["pr_authored"] == 1
        assert summary.counts_by_source_kind["github"]["pr_reviewed"] == 1

    def test_attribution_tally_including_none(self):
        activities = [
            {**_activity("alice", "SNO"), "attribution_source": "component"},
            {**_activity("alice", "TNA"), "attribution_source": "repo"},
            {**_activity("bob", None), "attribution_source": None},
            {**_activity("bob", None), "attribution_source": None},
        ]
        summary = report.build_executive_summary(
            activities, ["alice", "bob"], SEVEN, "2026Q3", [], {}
        )

        assert summary.counts_by_attribution["component"] == 1
        assert summary.counts_by_attribution["repo"] == 1
        assert summary.counts_by_attribution[""] == 2  # None uses empty string as key

    def test_per_member_sorted_by_total_desc(self):
        activities = [
            _activity("alice", "SNO"),
            _activity("alice", "SNO"),
            _activity("alice", "TNA"),
            _activity("bob", "LVMS"),
            _activity("charlie", "SNO"),
            _activity("charlie", "TNA"),
            _activity("charlie", "TNF"),
            _activity("charlie", "LVMS"),
        ]
        summary = report.build_executive_summary(
            activities, ["alice", "bob", "charlie"], SEVEN, "2026Q3", [], {}
        )

        assert len(summary.per_member) == 3
        assert summary.per_member[0].member == "charlie"
        assert summary.per_member[0].total == 4
        assert summary.per_member[1].member == "alice"
        assert summary.per_member[1].total == 3
        assert summary.per_member[2].member == "bob"
        assert summary.per_member[2].total == 1

    def test_per_member_has_top_workstream_and_share(self):
        # Create activities with clear top workstreams
        activities = [
            _activity("alice", "SNO"),
            _activity("alice", "SNO"),
            _activity("alice", "SNO"),
            _activity("bob", "LVMS"),
        ]
        summary = report.build_executive_summary(
            activities, ["alice", "bob"], SEVEN, "2026Q3", [], {}
        )

        # Check that top_workstream and top_share are set correctly
        alice_line = next(ml for ml in summary.per_member if ml.member == "alice")
        bob_line = next(ml for ml in summary.per_member if ml.member == "bob")

        # Alice has 3 in SNO, bob has 1 in LVMS
        assert alice_line.top_workstream == "SNO"
        assert alice_line.top_share == 100  # 3 of 3 = 100%
        assert bob_line.top_workstream == "LVMS"
        assert bob_line.top_share == 100  # 1 of 1 = 100%

    def test_empty_activities_produces_zeros_not_error(self):
        # This should not raise ZeroDivisionError
        summary = report.build_executive_summary([], [], SEVEN, "2026Q3", [], {})

        assert summary.total_records == 0
        assert summary.per_member == []
        assert summary.counts_by_source_kind == {"jira": {}, "github": {}}
        assert summary.counts_by_attribution == {}

    def test_out_of_window_records_populated_when_window_resolved(self):
        activities = [
            {**_activity("alice", "SNO"), "ts": "2026-06-30T23:59:59Z"},
            {**_activity("bob", "TNA"), "ts": "2026-07-15T00:00:00Z"},
            {**_activity("charlie", "LVMS"), "ts": "2026-10-01T00:00:00Z"},
        ]
        summary = report.build_executive_summary(
            activities, ["alice", "bob", "charlie"], SEVEN, "2026Q3", [], {}
        )
        # 2026Q3 is 2026-07-01 to 2026-09-30, so alice and charlie are out of window
        assert summary.out_of_window_records == 2

    def test_out_of_window_records_zero_when_all_in_window(self):
        activities = [
            {**_activity("alice", "SNO"), "ts": "2026-07-01T00:00:00Z"},
            {**_activity("bob", "TNA"), "ts": "2026-09-30T23:59:59Z"},
        ]
        summary = report.build_executive_summary(
            activities, ["alice", "bob"], SEVEN, "2026Q3", [], {}
        )
        assert summary.out_of_window_records == 0

    def test_out_of_window_records_zero_when_window_not_resolved(self):
        activities = [
            {**_activity("alice", "SNO"), "ts": "2026-06-30T23:59:59Z"},
        ]
        # Use a period that doesn't resolve to a window (no underscore, not a quarter)
        summary = report.build_executive_summary(
            activities, ["alice"], SEVEN, "custom-period", [], {}
        )
        assert summary.out_of_window_records == 0


class TestFormatRestrictions(unittest.TestCase):
    """Test that invalid format choices are rejected by argparse."""

    def test_manager_view_rejects_html_format(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump([{"jira_username": "alice", "full_name": "Alice"}], f)
            roster = f.name
        try:
            with self.assertRaises(SystemExit) as cm:
                report.main(
                    [
                        "--view",
                        "manager",
                        "--members-file",
                        roster,
                        "--activity",
                        roster,  # dummy, won't be reached
                        "--period",
                        "2026Q3",
                        "--format",
                        "html",
                    ]
                )
            self.assertEqual(cm.exception.code, 2)
        finally:
            os.unlink(roster)

    def test_manager_view_rejects_csv_format(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump([{"jira_username": "alice", "full_name": "Alice"}], f)
            roster = f.name
        try:
            with self.assertRaises(SystemExit) as cm:
                report.main(
                    [
                        "--view",
                        "manager",
                        "--members-file",
                        roster,
                        "--activity",
                        roster,  # dummy
                        "--period",
                        "2026Q3",
                        "--format",
                        "csv",
                    ]
                )
            self.assertEqual(cm.exception.code, 2)
        finally:
            os.unlink(roster)

    def test_ic_view_rejects_html_format(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump([], f)
            activity = f.name
        try:
            with self.assertRaises(SystemExit) as cm:
                report.main(
                    [
                        "--view",
                        "ic",
                        "--member",
                        "alice",
                        "--activity",
                        activity,
                        "--period",
                        "2026Q3",
                        "--format",
                        "html",
                    ]
                )
            self.assertEqual(cm.exception.code, 2)
        finally:
            os.unlink(activity)

    def test_summary_view_rejects_csv_format(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump([{"jira_username": "alice", "full_name": "Alice"}], f)
            roster = f.name
        try:
            with self.assertRaises(SystemExit) as cm:
                report.main(
                    [
                        "--view",
                        "summary",
                        "--members-file",
                        roster,
                        "--activity",
                        roster,  # dummy
                        "--period",
                        "2026Q3",
                        "--format",
                        "csv",
                    ]
                )
            self.assertEqual(cm.exception.code, 2)
        finally:
            os.unlink(roster)

    def test_output_flag_is_rejected(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump([{"jira_username": "alice", "full_name": "Alice"}], f)
            roster = f.name
        try:
            with self.assertRaises(SystemExit) as cm:
                report.main(
                    [
                        "--view",
                        "manager",
                        "--members-file",
                        roster,
                        "--activity",
                        roster,
                        "--period",
                        "2026Q3",
                        "--output",
                        "/tmp/x",
                    ]
                )
            self.assertEqual(cm.exception.code, 2)
        finally:
            os.unlink(roster)


if __name__ == "__main__":
    unittest.main()
