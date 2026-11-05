"""Tests for export_csv.py — CSV export builders.

Covers header correctness, field normalization between Jira and GitHub records,
source derivation from kind, flag columns in matrix.csv, empty-data handling,
and the EXPORT_FILES contract pinning what the skill document relies on.
"""

import os
import sys
import unittest
from io import StringIO

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import export_csv  # noqa: E402
from metrics import AllocationSignals, ContributionMatrix, TeamMetrics  # noqa: E402
from render import DataQuality, ManagerReport  # noqa: E402


class TestBuildActivitiesCSV(unittest.TestCase):
    def test_emits_documented_header(self):
        csv_output = export_csv.build_activities_csv([])
        lines = csv_output.strip().split("\n")
        assert len(lines) == 1
        assert (
            lines[0]
            == "member,source,kind,workstream,attribution_source,repo,issue_key,url,ts,unattributed_reason"
        )

    def test_jira_record_fills_issue_key_and_url_leaves_repo_empty(self):
        activities = [
            {
                "member": "alice",
                "kind": "assignee",
                "workstream": "SNO",
                "attribution_source": "component",
                "issue_key": "OCPEDGE-1234",
                "url": "https://issues.redhat.com/browse/OCPEDGE-1234",
                "ts": "2026-07-01T10:00:00Z",
            }
        ]
        csv_output = export_csv.build_activities_csv(activities)
        lines = csv_output.strip().split("\n")
        assert len(lines) == 2
        # Repo field should be empty for Jira records
        assert "alice,jira,assignee,SNO,component,,OCPEDGE-1234," in lines[1]

    def test_github_record_fills_repo_and_uses_pr_url_for_url(self):
        activities = [
            {
                "member": "bob",
                "kind": "pr_authored",
                "workstream": "LVMS",
                "attribution_source": "repo",
                "repo": "openshift/lvm-operator",
                "pr_url": "https://github.com/openshift/lvm-operator/pull/123",
                "source_key": "OCPEDGE-5678",
                "ts": "2026-07-02T11:00:00Z",
            }
        ]
        csv_output = export_csv.build_activities_csv(activities)
        lines = csv_output.strip().split("\n")
        assert len(lines) == 2
        # Should use pr_url for url field, and include repo
        assert "bob,github,pr_authored,LVMS,repo,openshift/lvm-operator,OCPEDGE-5678," in lines[1]
        assert "https://github.com/openshift/lvm-operator/pull/123" in lines[1]

    def test_github_record_without_source_key_leaves_issue_key_empty(self):
        activities = [
            {
                "member": "charlie",
                "kind": "pr_reviewed",
                "workstream": "USHIFT",
                "attribution_source": "repo",
                "repo": "openshift/microshift",
                "pr_url": "https://github.com/openshift/microshift/pull/456",
                "ts": "2026-07-03T12:00:00Z",
            }
        ]
        csv_output = export_csv.build_activities_csv(activities)
        lines = csv_output.strip().split("\n")
        assert len(lines) == 2
        # issue_key field should be empty when source_key is absent
        # Check that there are no literal "None" strings
        assert "None" not in lines[1]
        # The issue_key field (7th column) should be empty
        assert "openshift/microshift,," in lines[1]

    def test_source_column_derived_from_kind(self):
        activities = [
            {"member": "alice", "kind": "assignee", "workstream": "SNO"},
            {"member": "bob", "kind": "pr_authored", "workstream": "LVMS"},
            {"member": "charlie", "kind": "qa", "workstream": "TNA"},
            {"member": "dave", "kind": "pr_reviewed", "workstream": "TNF"},
        ]
        csv_output = export_csv.build_activities_csv(activities)
        lines = csv_output.strip().split("\n")
        assert len(lines) == 5  # header + 4 records
        # Check source column (2nd column after member)
        assert ",jira," in lines[1]  # assignee
        assert ",github," in lines[2]  # pr_authored
        assert ",jira," in lines[3]  # qa
        assert ",github," in lines[4]  # pr_reviewed

    def test_missing_fields_write_empty_not_none(self):
        activities = [
            {
                "member": "alice",
                "kind": "assignee",
                # Most fields missing
            }
        ]
        csv_output = export_csv.build_activities_csv(activities)
        lines = csv_output.strip().split("\n")
        # No literal "None" should appear
        assert "None" not in csv_output

    def test_sorted_by_member_then_ts(self):
        activities = [
            {"member": "bob", "kind": "assignee", "ts": "2026-07-02"},
            {"member": "alice", "kind": "assignee", "ts": "2026-07-03"},
            {"member": "alice", "kind": "assignee", "ts": "2026-07-01"},
        ]
        csv_output = export_csv.build_activities_csv(activities)
        lines = csv_output.strip().split("\n")
        assert len(lines) == 4
        # alice (sorted first alphabetically), with earlier ts first
        assert lines[1].startswith("alice,")
        assert "2026-07-01" in lines[1]
        assert lines[2].startswith("alice,")
        assert "2026-07-03" in lines[2]
        # bob comes after alice
        assert lines[3].startswith("bob,")


class TestBuildMatrixCSV(unittest.TestCase):
    def test_emits_documented_header_with_workstreams(self):
        matrix = ContributionMatrix(
            members=["alice"],
            workstreams=["SNO", "TNA", "TNF", "LVMS", "USHIFT", "TOPO", "SHARED"],
            touched={"alice": set()},
            counts={"alice": {}},
        )
        report = ManagerReport(
            period_label="2026Q3",
            metrics=TeamMetrics(
                workstreams=["SNO", "TNA", "TNF", "LVMS", "USHIFT", "TOPO", "SHARED"],
                members=["alice"],
                workstreams_touched_by_member={"alice": 0},
                mean_people_per_workstream=0.0,
                mean_workstreams_per_person=0.0,
                active_member_count=0,
                total_member_count=1,
            ),
            matrix=matrix,
            signals=AllocationSignals(
                total_by_member={"alice": 0},
                total_by_workstream={},
                contributors_by_workstream={},
                team_median_total=0.0,
                grid_max=0,
                over_allocated=set(),
                under_allocated=set(),
                narrow=set(),
            ),
        )
        csv_output = export_csv.build_matrix_csv(report)
        lines = csv_output.strip().split("\n")
        assert len(lines) == 2  # header + 1 member
        # Header should have workstreams + TOTAL, workstreams_touched, flags
        assert lines[0] == (
            "member,SNO,TNA,TNF,LVMS,USHIFT,TOPO,SHARED,TOTAL,"
            "workstreams_touched,over,under,narrow"
        )

    def test_includes_flag_and_workstreams_touched_columns(self):
        matrix = ContributionMatrix(
            members=["alice", "bob"],
            workstreams=["SNO", "TNA", "TNF", "LVMS", "USHIFT", "TOPO", "SHARED"],
            touched={"alice": {"SNO", "TNA"}, "bob": {"SNO"}},
            counts={"alice": {"SNO": 100, "TNA": 100}, "bob": {"SNO": 10}},
        )
        report = ManagerReport(
            period_label="2026Q3",
            metrics=TeamMetrics(
                workstreams=["SNO", "TNA", "TNF", "LVMS", "USHIFT", "TOPO", "SHARED"],
                members=["alice", "bob"],
                workstreams_touched_by_member={"alice": 2, "bob": 1},
                mean_people_per_workstream=0.0,
                mean_workstreams_per_person=0.0,
                active_member_count=2,
                total_member_count=2,
            ),
            matrix=matrix,
            signals=AllocationSignals(
                total_by_member={"alice": 200, "bob": 10},
                total_by_workstream={},
                contributors_by_workstream={},
                team_median_total=50.0,
                grid_max=100,
                over_allocated={"alice"},
                under_allocated={"bob"},
                narrow=set(),
            ),
        )
        csv_output = export_csv.build_matrix_csv(report)
        lines = csv_output.strip().split("\n")
        assert len(lines) == 3  # header + 2 members
        # alice row: over=true, under=false, narrow=false, workstreams_touched=2, total=200
        alice_row = [line for line in lines if line.startswith("alice,")][0]
        assert ",200,2,true,false,false" in alice_row
        # bob row: over=false, under=true, narrow=false, workstreams_touched=1, total=10
        bob_row = [line for line in lines if line.startswith("bob,")][0]
        assert ",10,1,false,true,false" in bob_row

    def test_member_name_with_comma_is_csv_quoted(self):
        """Ported from test_render.py — verifies CSV quoting for names with commas."""
        matrix = ContributionMatrix(
            members=["Cope, Jon"],
            workstreams=["SNO", "TNA", "TNF", "LVMS", "USHIFT", "TOPO", "SHARED"],
            touched={"Cope, Jon": {"SNO"}},
            counts={"Cope, Jon": {"SNO": 1}},
        )
        report = ManagerReport(
            period_label="2026Q3",
            metrics=TeamMetrics(
                workstreams=["SNO", "TNA", "TNF", "LVMS", "USHIFT", "TOPO", "SHARED"],
                members=["Cope, Jon"],
                workstreams_touched_by_member={"Cope, Jon": 1},
                mean_people_per_workstream=0.0,
                mean_workstreams_per_person=0.0,
                active_member_count=1,
                total_member_count=1,
            ),
            matrix=matrix,
            signals=AllocationSignals(
                total_by_member={"Cope, Jon": 1},
                total_by_workstream={},
                contributors_by_workstream={},
                team_median_total=0.0,
                grid_max=1,
                over_allocated=set(),
                under_allocated=set(),
                narrow=set(),
            ),
        )
        csv_output = export_csv.build_matrix_csv(report)
        # Parse the CSV to verify the name is correctly quoted and round-trips
        import csv
        import io

        lines = csv_output.strip().split("\n")
        rows = list(csv.reader(io.StringIO(csv_output)))
        # First data row should have the member name
        assert rows[1][0] == "Cope, Jon"
        # The SNO column (index 1) should have the count
        assert rows[1][1] == "1"

    def test_csv_contains_raw_counts_not_binary(self):
        """Ported from test_render.py — verifies raw counts appear in CSV, not 0/1."""
        matrix = ContributionMatrix(
            members=["alice"],
            workstreams=["SNO", "TNA", "TNF", "LVMS", "USHIFT", "TOPO", "SHARED"],
            touched={"alice": {"SNO"}},
            counts={"alice": {"SNO": 42}},
        )
        report = ManagerReport(
            period_label="2026Q3",
            metrics=TeamMetrics(
                workstreams=["SNO", "TNA", "TNF", "LVMS", "USHIFT", "TOPO", "SHARED"],
                members=["alice"],
                workstreams_touched_by_member={"alice": 1},
                mean_people_per_workstream=0.0,
                mean_workstreams_per_person=0.0,
                active_member_count=1,
                total_member_count=1,
            ),
            matrix=matrix,
            signals=AllocationSignals(
                total_by_member={"alice": 42},
                total_by_workstream={},
                contributors_by_workstream={},
                team_median_total=0.0,
                grid_max=42,
                over_allocated=set(),
                under_allocated=set(),
                narrow=set(),
            ),
        )
        csv_output = export_csv.build_matrix_csv(report)
        # Parse and verify the SNO column (index 1) contains the raw count 42
        import csv
        import io

        rows = list(csv.reader(io.StringIO(csv_output)))
        assert rows[1][1] == "42", "SNO column should contain raw count 42, not binary 0/1"


class TestBuildMetricsCSV(unittest.TestCase):
    def test_emits_documented_header(self):
        matrix = ContributionMatrix(members=[], workstreams=[], touched={}, counts={})
        report = ManagerReport(
            period_label="2026Q3",
            metrics=TeamMetrics(
                workstreams=[],
                members=[],
                workstreams_touched_by_member={},
                mean_people_per_workstream=0.0,
                mean_workstreams_per_person=0.0,
                active_member_count=0,
                total_member_count=0,
            ),
            matrix=matrix,
            signals=AllocationSignals(
                total_by_member={},
                total_by_workstream={},
                contributors_by_workstream={},
                team_median_total=0.0,
                grid_max=0,
                over_allocated=set(),
                under_allocated=set(),
                narrow=set(),
            ),
        )
        csv_output = export_csv.build_metrics_csv(report)
        lines = csv_output.strip().split("\n")
        assert lines[0] == "metric,value"

    @staticmethod
    def _two_workstream_report():
        matrix = ContributionMatrix(
            members=["alice", "bob"],
            workstreams=["SNO", "TNA"],
            touched={"alice": {"SNO"}, "bob": {"SNO", "TNA"}},
            counts={"alice": {"SNO": 50}, "bob": {"SNO": 50, "TNA": 50}},
        )
        return ManagerReport(
            period_label="2026Q3",
            metrics=TeamMetrics(
                workstreams=["SNO", "TNA"],
                members=["alice", "bob"],
                workstreams_touched_by_member={"alice": 1, "bob": 2},
                mean_people_per_workstream=1.5,
                mean_workstreams_per_person=1.5,
                active_member_count=2,
                total_member_count=2,
            ),
            matrix=matrix,
            unattributed_count=5,
            signals=AllocationSignals(
                total_by_member={"alice": 50, "bob": 100},
                total_by_workstream={"SNO": 100, "TNA": 50},
                contributors_by_workstream={"SNO": 2, "TNA": 1},
                team_median_total=75.0,
                grid_max=100,
                over_allocated=set(),
                under_allocated=set(),
                narrow=set(),
            ),
        )

    def test_includes_team_scalars_and_per_workstream_metrics(self):
        report = self._two_workstream_report()
        csv_output = export_csv.build_metrics_csv(report)
        lines = csv_output.strip().split("\n")

        # Check team scalars are present, each under a label that says what it counts
        assert "mean_people_per_workstream,1.5" in csv_output
        assert "mean_workstreams_per_person,1.5" in csv_output
        assert "active_members,2" in csv_output
        assert "total_members,2" in csv_output
        assert "team_median_total,75.0" in csv_output
        assert "grid_max,100" in csv_output
        assert "unattributed,5" in csv_output

        # Check per-workstream metrics
        assert "total:SNO,100" in csv_output
        assert "total:TNA,50" in csv_output
        assert "contributors:SNO,2" in csv_output
        assert "contributors:TNA,1" in csv_output

    def test_no_opaque_metric_labels_remain(self):
        """The words 'flexibility' and 'opportunity' must never reach a user.

        metrics.csv was the only place they surfaced. `flexibility:<ws>` also
        duplicated `contributors:<ws>` row for row, so it was removed outright
        rather than renamed.
        """
        csv_output = export_csv.build_metrics_csv(self._two_workstream_report())

        assert "flexibility" not in csv_output
        assert "opportunity" not in csv_output

        labels = [line.split(",")[0] for line in csv_output.strip().split("\n")[1:]]
        assert len(labels) == len(set(labels)), f"duplicate metric labels: {labels}"


class TestBuildDataQualityCSV(unittest.TestCase):
    def test_emits_documented_header(self):
        report = ManagerReport(
            period_label="2026Q3",
            metrics=TeamMetrics(
                workstreams=[],
                members=[],
                workstreams_touched_by_member={},
                mean_people_per_workstream=0.0,
                mean_workstreams_per_person=0.0,
                active_member_count=0,
                total_member_count=0,
            ),
            matrix=ContributionMatrix(members=[], workstreams=[], touched={}, counts={}),
            data_quality=DataQuality(),
        )
        csv_output = export_csv.build_data_quality_csv(report)
        lines = csv_output.strip().split("\n")
        assert lines[0] == "category,label,count"

    def test_does_not_truncate_repo_lists(self):
        # Create more than 8 repos (the _TOP_REPO_LIMIT in render.py)
        many_repos = {f"org/repo{i}": i + 1 for i in range(15)}
        report = ManagerReport(
            period_label="2026Q3",
            metrics=TeamMetrics(
                workstreams=[],
                members=[],
                workstreams_touched_by_member={},
                mean_people_per_workstream=0.0,
                mean_workstreams_per_person=0.0,
                active_member_count=0,
                total_member_count=0,
            ),
            matrix=ContributionMatrix(members=[], workstreams=[], touched={}, counts={}),
            data_quality=DataQuality(excluded_personal_repos=many_repos),
        )
        csv_output = export_csv.build_data_quality_csv(report)
        lines = [
            line
            for line in csv_output.strip().split("\n")
            if line.startswith("excluded_personal_repo,")
        ]
        # All 15 repos should be present, not truncated to 8
        assert len(lines) == 15


class TestExportFiles(unittest.TestCase):
    def test_export_files_constant_matches_actual_output(self):
        # The EXPORT_FILES constant pins the contract that the skill doc relies on
        assert export_csv.EXPORT_FILES == (
            "activities.csv",
            "matrix.csv",
            "metrics.csv",
            "data-quality.csv",
        )


class TestEmptyActivities(unittest.TestCase):
    def test_empty_activities_produces_header_only_files(self):
        # All builders should handle empty input gracefully
        csv_output = export_csv.build_activities_csv([])
        assert len(csv_output.strip().split("\n")) == 1  # header only

        matrix = ContributionMatrix(members=[], workstreams=[], touched={}, counts={})
        report = ManagerReport(
            period_label="2026Q3",
            metrics=TeamMetrics(
                workstreams=[],
                members=[],
                workstreams_touched_by_member={},
                mean_people_per_workstream=0.0,
                mean_workstreams_per_person=0.0,
                active_member_count=0,
                total_member_count=0,
            ),
            matrix=matrix,
            signals=AllocationSignals(
                total_by_member={},
                total_by_workstream={},
                contributors_by_workstream={},
                team_median_total=0.0,
                grid_max=0,
                over_allocated=set(),
                under_allocated=set(),
                narrow=set(),
            ),
            data_quality=DataQuality(),
        )

        csv_output = export_csv.build_matrix_csv(report)
        assert len(csv_output.strip().split("\n")) == 1  # header only

        csv_output = export_csv.build_metrics_csv(report)
        # Metrics will have header + 7 scalar rows even with empty data
        assert "metric,value" in csv_output

        csv_output = export_csv.build_data_quality_csv(report)
        # Data quality might have header + jira category rows even with no repos
        assert "category,label,count" in csv_output


if __name__ == "__main__":
    unittest.main()
