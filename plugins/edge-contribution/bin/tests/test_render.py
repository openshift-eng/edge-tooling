"""Tests for render.py — text and markdown renderers for all report types.

Covers happy-path structure, failure inputs (unknown format, empty roster), and
boundary cases: single-column matrix, all-zero matrix, gradient rendering, and
summary source-disclosure sections.
"""

import csv
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import metrics  # noqa: E402
import render  # noqa: E402

SIX = ["SNO", "TNA", "TNF", "LVMS", "USHIFT", "TOPO"]


def _manager_report(
    members, touched, workstreams=None, counts=None, unattributed=0, period="2026Q2"
):
    matrix = metrics.ContributionMatrix(
        members=members, workstreams=list(workstreams or SIX), touched=touched, counts=counts or {}
    )
    return render.ManagerReport(
        period_label=period,
        metrics=metrics.compute_team_metrics(matrix),
        matrix=matrix,
        unattributed_count=unattributed,
    )


class TestManagerHappyPath(unittest.TestCase):
    def setUp(self):
        self.report = _manager_report(
            members=["alice", "bob", "carol"],
            touched={"alice": {"SNO", "TNA"}, "bob": {"SNO"}, "carol": set()},
        )

    def test_text_grid_shows_gradient_glyphs(self):
        # With counts, we should see gradient glyphs instead of checkmarks.
        report = _manager_report(
            members=["alice", "bob"],
            touched={"alice": {"SNO", "TNA"}, "bob": {"SNO"}},
            counts={"alice": {"SNO": 10, "TNA": 2}, "bob": {"SNO": 1}},
        )
        text = render.render_manager(report, "text", color=False)
        # Should contain gradient glyphs, not checkmarks.
        assert "✓" not in text
        # Should contain scale legend.
        assert "scale" in text.lower()


class TestManagerFailureInputs(unittest.TestCase):
    def test_unknown_format_raises(self):
        report = _manager_report(members=["alice"], touched={"alice": {"SNO"}})
        with self.assertRaises(ValueError):
            render.render_manager(report, "pdf")

    def test_empty_roster_renders_without_crashing(self):
        report = _manager_report(members=[], touched={})
        for output_format in ("text",):
            rendered = render.render_manager(report, output_format)
            assert isinstance(rendered, str)
            assert rendered != ""


class TestManagerEdgeCases(unittest.TestCase):
    def test_single_workstream_column(self):
        # Replaced: no longer testing CSV column selection; just verify a one-workstream report renders.
        report = _manager_report(members=["alice"], touched={"alice": {"SNO"}}, workstreams=["SNO"])
        text = render.render_manager(report, "text", color=False)
        assert "alice" in text
        assert "SNO" in text

    def test_all_zero_matrix_has_no_filled_cells_in_text(self):
        report = _manager_report(members=["alice", "bob"], touched={"alice": set(), "bob": set()})
        text = render.render_manager(report, "text", color=False)
        # With gradient, should only see the "none" glyph (·).
        assert "·" in text or "." in text


class TestManagerTextIsGridOnly(unittest.TestCase):
    """Text output is the grid plus its two legends, and stops there.

    A manager reads the text view at a glance in a terminal. Everything after
    the flags legend — the team-level means, the unattributed note, the
    data-quality block — repeated what the grid's TOTAL/PEOPLE rows already
    show, or buried it under a wall of names. All of it is still computed, and
    available via --view summary --show-sources; only the manager text view is
    trimmed.
    """

    def _loaded_report(self):
        """A report carrying every optional block, so omission is a real choice."""
        matrix = metrics.ContributionMatrix(
            members=["alice"],
            workstreams=SIX,
            touched={"alice": {"SNO"}},
            counts={"alice": {"SNO": 10}},
        )
        dq = render.DataQuality(
            unattributed_by_repo={"openshift/foo": 5},
            tickets_no_component_no_parent=3,
            tickets_parent_also_empty=2,
            excluded_personal_repo_prs=9,
            excluded_personal_repos={"someone/side-project": 9},
            excluded_members=["foobar@redhat.com"],
        )
        return render.ManagerReport(
            period_label="2026Q2",
            metrics=metrics.compute_team_metrics(matrix),
            matrix=matrix,
            unattributed_count=8,
            data_quality=dq,
        )

    def test_text_omits_the_team_level_means(self):
        text = render.render_manager(self._loaded_report(), "text", color=False)
        assert "people per workstream" not in text
        assert "workstreams per person" not in text

    def test_text_omits_the_data_quality_block(self):
        text = render.render_manager(self._loaded_report(), "text", color=False)
        assert "Data quality" not in text
        assert "openshift/foo" not in text
        assert "someone/side-project" not in text

    def test_text_omits_the_unattributed_note(self):
        text = render.render_manager(self._loaded_report(), "text", color=False)
        assert "unattributed" not in text.lower()

    def test_text_last_line_is_the_flags_legend(self):
        # Pins the cut point. Anything appended after the legend fails here.
        text = render.render_manager(self._loaded_report(), "text", color=False)
        assert text.strip().splitlines()[-1].lstrip().startswith("flags")


class TestICReport(unittest.TestCase):
    def _ic_report(self, workstreams_touched=2, activity=None, period="2026Q2"):
        activity = (
            activity
            if activity is not None
            else [
                render.WorkstreamActivity("SNO", {"assignee": 3, "pr_authored": 2}),
                render.WorkstreamActivity("TNA", {"pr_reviewed": 1}),
            ]
        )
        return render.ICReport(
            period_label=period,
            member="foobar@redhat.com",
            workstreams_touched=workstreams_touched,
            total_workstreams=6,
            activity=activity,
        )

    def test_text_shows_workstreams_touched_of_total(self):
        text = render.render_ic(self._ic_report(), "text")
        assert "2 of 6" in text or "2/6" in text
        assert "SNO" in text
        assert "TNA" in text

    def test_unknown_format_raises(self):
        with self.assertRaises(ValueError):
            render.render_ic(self._ic_report(), "pdf")

    def test_zero_workstreams_touched_still_renders(self):
        text = render.render_ic(self._ic_report(workstreams_touched=0, activity=[]), "text")
        assert "0 of 6" in text or "0/6" in text


class TestShadeBand(unittest.TestCase):
    def test_count_zero_returns_zero(self):
        assert render.shade_band(0, 100) == 0

    def test_count_one_with_grid_max_one_returns_four(self):
        # When count equals grid_max, it should be maximum intensity (band 4).
        assert render.shade_band(1, 1) == 4

    def test_count_equals_grid_max_returns_four(self):
        assert render.shade_band(69, 69) == 4

    def test_grid_max_zero_returns_zero(self):
        assert render.shade_band(10, 0) == 0

    def test_negative_count_returns_zero(self):
        assert render.shade_band(-5, 100) == 0

    def test_count_above_grid_max_clamps_to_four(self):
        assert render.shade_band(100, 50) == 4

    def test_log_spacing_spreads_values(self):
        # With grid_max 69, different counts should land in different bands.
        grid_max = 69
        band_1 = render.shade_band(1, grid_max)
        band_3 = render.shade_band(3, grid_max)
        band_10 = render.shade_band(10, grid_max)
        band_60 = render.shade_band(60, grid_max)

        # These should not all be the same band (linear would collapse them).
        all_bands = {band_1, band_3, band_10, band_60}
        assert len(all_bands) > 1, "Log spacing should spread counts across multiple bands"

    def test_band_values_are_in_range(self):
        # All band values should be 0..4 inclusive.
        for count in range(0, 100):
            for grid_max in [1, 10, 50, 100]:
                band = render.shade_band(count, grid_max)
                assert (
                    0 <= band <= 4
                ), f"Band {band} out of range for count={count}, grid_max={grid_max}"


class TestGradientHeatmap(unittest.TestCase):
    def test_color_true_contains_ansi_codes(self):
        report = _manager_report(
            members=["alice"],
            touched={"alice": {"SNO"}},
            counts={"alice": {"SNO": 10}},
        )
        text = render.render_manager(report, "text", color=True)
        assert "\x1b[38;5;" in text, "Color output should contain ANSI color codes"

    def test_color_false_has_no_ansi_codes(self):
        report = _manager_report(
            members=["alice"],
            touched={"alice": {"SNO"}},
            counts={"alice": {"SNO": 10}},
        )
        text = render.render_manager(report, "text", color=False)
        assert "\x1b" not in text, "No-color output should not contain any escape sequences"

    def test_ascii_only_true_is_pure_ascii(self):
        report = _manager_report(
            members=["alice"],
            touched={"alice": {"SNO"}},
            counts={"alice": {"SNO": 10}},
        )
        text = render.render_manager(report, "text", ascii_only=True, color=False)
        # Should be encodable as ASCII without error.
        text.encode("ascii")

    def test_ascii_only_false_uses_unicode(self):
        report = _manager_report(
            members=["alice"],
            touched={"alice": {"SNO"}},
            counts={"alice": {"SNO": 10}},
        )
        text = render.render_manager(report, "text", ascii_only=False, color=False)
        # Should contain Unicode box drawing or other non-ASCII characters.
        # The em-dash or box drawing characters would fail ASCII encoding.
        with self.assertRaises(UnicodeEncodeError):
            text.encode("ascii")

    def test_high_count_differs_from_low_count_glyph(self):
        report = _manager_report(
            members=["alice", "bob"],
            touched={"alice": {"SNO"}, "bob": {"SNO"}},
            counts={"alice": {"SNO": 1}, "bob": {"SNO": 60}},
        )
        text = render.render_manager(report, "text", color=False)
        # With different counts, the glyphs should differ.
        # We can't assert exact glyphs without parsing, but we can check that
        # multiple gradient glyphs appear.
        glyph_set = set(["░", "▒", "▓", "█"])
        found_glyphs = sum(1 for g in glyph_set if g in text)
        assert found_glyphs >= 2, "Different counts should produce different glyphs"

    def test_empty_roster_renders_without_crash(self):
        report = _manager_report(members=[], touched={}, counts={})
        for output_format in ("text",):
            rendered = render.render_manager(report, output_format, color=False)
            assert isinstance(rendered, str)
            assert rendered != ""

    def test_single_member_renders(self):
        report = _manager_report(
            members=["alice"],
            touched={"alice": {"SNO"}},
            counts={"alice": {"SNO": 5}},
        )
        text = render.render_manager(report, "text", color=False)
        assert "alice" in text

    def test_all_zero_matrix_renders(self):
        report = _manager_report(
            members=["alice", "bob"],
            touched={"alice": set(), "bob": set()},
            counts={},
        )
        text = render.render_manager(report, "text", color=False)
        assert "alice" in text
        assert "bob" in text

    def test_matrix_with_empty_counts_dict_renders(self):
        report = _manager_report(
            members=["alice"],
            touched={"alice": {"SNO"}},
            counts={},
        )
        text = render.render_manager(report, "text", color=False)
        assert "alice" in text

    def test_signals_none_still_renders(self):
        # When signals is None, it should be computed internally.
        matrix = metrics.ContributionMatrix(
            members=["alice"],
            workstreams=SIX,
            touched={"alice": {"SNO"}},
            counts={"alice": {"SNO": 10}},
        )
        report = render.ManagerReport(
            period_label="2026Q2",
            metrics=metrics.compute_team_metrics(matrix),
            matrix=matrix,
            signals=None,  # Explicitly None.
        )
        text = render.render_manager(report, "text", color=False)
        assert "alice" in text

    def test_data_quality_none_omits_section(self):
        report = _manager_report(
            members=["alice"],
            touched={"alice": {"SNO"}},
            counts={"alice": {"SNO": 10}},
        )
        text = render.render_manager(report, "text", color=False)
        assert "Data quality" not in text

    def _report_with_excluded_repos(self, repos, total):
        matrix = metrics.ContributionMatrix(
            members=["alice"],
            workstreams=SIX,
            touched={"alice": {"SNO"}},
            counts={"alice": {"SNO": 10}},
        )
        dq = render.DataQuality(excluded_personal_repo_prs=total, excluded_personal_repos=repos)
        return render.ManagerReport(
            period_label="2026Q2",
            metrics=metrics.compute_team_metrics(matrix),
            matrix=matrix,
            unattributed_count=0,
            data_quality=dq,
        )

    def _summary_with_excluded_repos(self, repos, total):
        """Build an ExecutiveSummary with excluded repos for testing --show-sources."""
        matrix = metrics.ContributionMatrix(
            members=["alice"],
            workstreams=SIX,
            touched={"alice": {"SNO"}},
            counts={"alice": {"SNO": 10}},
        )
        team_metrics = metrics.compute_team_metrics(matrix)
        dq = render.DataQuality(excluded_personal_repo_prs=total, excluded_personal_repos=repos)
        return render.ExecutiveSummary(
            period_label="2026Q2",
            window=("2026-04-01", "2026-06-30"),
            metrics=team_metrics,
            signals=metrics.compute_allocation_signals(matrix),
            data_quality=dq,
            counts_by_source_kind={},
            counts_by_attribution={},
            per_member=[],
            per_workstream=[],
            total_records=0,
            jira_projects=("OCPEDGE",),
            ocpstrat_project="OCPSTRAT",
            allowed_org_count=10,
            out_of_window_records=0,
        )

    # The excluded-repo listing now lives in the summary --show-sources view.

    def test_excluded_repos_are_named_in_html(self):
        summary = self._summary_with_excluded_repos(
            {"jeff-roche/roundhouse": 38, "jaypoulz/edge-tooling": 5}, 43
        )
        text = render.render_summary(summary, "text", show_sources=True)
        assert "jeff-roche/roundhouse" in text
        assert "jaypoulz/edge-tooling" in text

    def test_excluded_repos_listed_biggest_first(self):
        summary = self._summary_with_excluded_repos(
            {"small/one": 1, "big/two": 50, "mid/three": 10}, 61
        )
        text = render.render_summary(summary, "text", show_sources=True)
        assert text.index("big/two") < text.index("mid/three") < text.index("small/one")

    def test_long_excluded_repo_list_is_truncated(self):
        repos = {f"person{i}/repo": 1 for i in range(12)}
        summary = self._summary_with_excluded_repos(repos, 12)
        text = render.render_summary(summary, "text", show_sources=True)
        assert "and 4 more repos" in text

    def test_no_excluded_repos_adds_no_listing(self):
        # The total alone must still render; an empty breakdown is what a
        # pre-breakdown activity file produces.
        summary = self._summary_with_excluded_repos({}, 101)
        text = render.render_summary(summary, "text", show_sources=True)
        assert "101 PRs" in text
        assert "personal-namespace repos" in text
        assert "more repos" not in text

    def test_shared_column_appears_but_separated(self):
        from workstream_map import SHARED_COLUMN

        # SHARED should appear in the output but be visually separated.
        report = _manager_report(
            members=["alice"],
            touched={"alice": {"SNO", SHARED_COLUMN}},
            counts={"alice": {"SNO": 5, SHARED_COLUMN: 3}},
        )
        text = render.render_manager(report, "text", color=False, ascii_only=True)
        assert SHARED_COLUMN in text
        assert "|" in text  # ASCII box vert separator.

    def test_rows_ordered_by_total_descending(self):
        report = _manager_report(
            members=["alice", "bob", "carol"],
            touched={"alice": {"SNO"}, "bob": {"SNO", "TNA"}, "carol": {"SNO"}},
            counts={"alice": {"SNO": 5}, "bob": {"SNO": 10, "TNA": 20}, "carol": {"SNO": 1}},
        )
        text = render.render_manager(report, "text", color=False)
        lines = text.splitlines()
        # Find the member rows (they should be after the header).
        member_rows = [
            line for line in lines if "alice" in line or "bob" in line or "carol" in line
        ]
        # Bob (30 total) should come first, then alice (5), then carol (1).
        assert member_rows[0].startswith("bob") or "bob" in member_rows[0]
        assert member_rows[1].startswith("alice") or "alice" in member_rows[1]
        assert member_rows[2].startswith("carol") or "carol" in member_rows[2]


class TestRenderSummary(unittest.TestCase):
    def test_render_summary_rejects_html_format(self):
        # Build a minimal ExecutiveSummary.
        summary = render.ExecutiveSummary(
            period_label="2026Q3",
            window=("2026-07-01", "2026-09-30"),
            metrics=metrics.TeamMetrics(
                workstreams=SIX,
                members=[],
                workstreams_touched_by_member={},
                mean_people_per_workstream=0.0,
                mean_workstreams_per_person=0.0,
                active_member_count=0,
                total_member_count=0,
            ),
            signals=metrics.AllocationSignals(
                total_by_member={},
                total_by_workstream={},
                contributors_by_workstream={},
                team_median_total=0.0,
                grid_max=0,
                over_allocated=set(),
                under_allocated=set(),
                narrow=set(),
            ),
            data_quality=render.DataQuality(),
            counts_by_source_kind={},
            counts_by_attribution={},
            per_member=[],
            per_workstream=[],
            total_records=0,
            jira_projects=("OCPEDGE", "USHIFT", "OCPBUGS"),
            ocpstrat_project="OCPSTRAT",
            allowed_org_count=10,
        )
        with self.assertRaises(ValueError):
            render.render_summary(summary, "html")

    def test_render_summary_rejects_csv_format(self):
        summary = render.ExecutiveSummary(
            period_label="2026Q3",
            window=("2026-07-01", "2026-09-30"),
            metrics=metrics.TeamMetrics(
                workstreams=SIX,
                members=[],
                workstreams_touched_by_member={},
                mean_people_per_workstream=0.0,
                mean_workstreams_per_person=0.0,
                active_member_count=0,
                total_member_count=0,
            ),
            signals=metrics.AllocationSignals(
                total_by_member={},
                total_by_workstream={},
                contributors_by_workstream={},
                team_median_total=0.0,
                grid_max=0,
                over_allocated=set(),
                under_allocated=set(),
                narrow=set(),
            ),
            data_quality=render.DataQuality(),
            counts_by_source_kind={},
            counts_by_attribution={},
            per_member=[],
            per_workstream=[],
            total_records=0,
            jira_projects=("OCPEDGE", "USHIFT", "OCPBUGS"),
            ocpstrat_project="OCPSTRAT",
            allowed_org_count=10,
        )
        with self.assertRaises(ValueError):
            render.render_summary(summary, "csv")

    def test_render_summary_accepts_text_format(self):
        summary = render.ExecutiveSummary(
            period_label="2026Q3",
            window=("2026-07-01", "2026-09-30"),
            metrics=metrics.TeamMetrics(
                workstreams=SIX,
                members=[],
                workstreams_touched_by_member={},
                mean_people_per_workstream=0.0,
                mean_workstreams_per_person=0.0,
                active_member_count=0,
                total_member_count=0,
            ),
            signals=metrics.AllocationSignals(
                total_by_member={},
                total_by_workstream={},
                contributors_by_workstream={},
                team_median_total=0.0,
                grid_max=0,
                over_allocated=set(),
                under_allocated=set(),
                narrow=set(),
            ),
            data_quality=render.DataQuality(),
            counts_by_source_kind={},
            counts_by_attribution={},
            per_member=[],
            per_workstream=[],
            total_records=0,
            jira_projects=("OCPEDGE", "USHIFT", "OCPBUGS"),
            ocpstrat_project="OCPSTRAT",
            allowed_org_count=10,
        )
        result = render.render_summary(summary, "text")
        assert isinstance(result, str)

    def test_render_summary_accepts_markdown_format(self):
        summary = render.ExecutiveSummary(
            period_label="2026Q3",
            window=("2026-07-01", "2026-09-30"),
            metrics=metrics.TeamMetrics(
                workstreams=SIX,
                members=[],
                workstreams_touched_by_member={},
                mean_people_per_workstream=0.0,
                mean_workstreams_per_person=0.0,
                active_member_count=0,
                total_member_count=0,
            ),
            signals=metrics.AllocationSignals(
                total_by_member={},
                total_by_workstream={},
                contributors_by_workstream={},
                team_median_total=0.0,
                grid_max=0,
                over_allocated=set(),
                under_allocated=set(),
                narrow=set(),
            ),
            data_quality=render.DataQuality(),
            counts_by_source_kind={},
            counts_by_attribution={},
            per_member=[],
            per_workstream=[],
            total_records=0,
            jira_projects=("OCPEDGE", "USHIFT", "OCPBUGS"),
            ocpstrat_project="OCPSTRAT",
            allowed_org_count=10,
        )
        result = render.render_summary(summary, "markdown")
        assert isinstance(result, str)

    def _make_loaded_summary(self):
        """A summary with all sections populated, matching the plan's example."""
        matrix = metrics.ContributionMatrix(
            members=["alice", "bob"],
            workstreams=SIX,
            touched={"alice": {"SNO", "TNA"}, "bob": {"SNO"}},
            counts={"alice": {"SNO": 100, "TNA": 50}, "bob": {"SNO": 30}},
        )
        team_metrics = metrics.compute_team_metrics(matrix)
        signals = metrics.AllocationSignals(
            total_by_member={"alice": 150, "bob": 30},
            total_by_workstream={
                "SNO": 130,
                "TNA": 50,
                "TNF": 0,
                "LVMS": 0,
                "USHIFT": 0,
                "TOPO": 0,
            },
            contributors_by_workstream={
                "SNO": 2,
                "TNA": 1,
                "TNF": 0,
                "LVMS": 0,
                "USHIFT": 0,
                "TOPO": 0,
            },
            team_median_total=65.0,
            grid_max=100,
            over_allocated={"alice"},
            under_allocated=set(),
            narrow=set(),
        )
        dq = render.DataQuality(
            unattributed_by_repo={
                "openshift-eng/two-node-toolbox": 23,
                "openshift/some-repo": 5,
            },
            tickets_no_component_no_parent=104,
            tickets_parent_also_empty=33,
            excluded_personal_repo_prs=99,
            excluded_personal_repos={"foobar/fuzzbuzz": 38, "user/repo": 10},
            excluded_members=["foobar@redhat.com", "fuzzbuzz@redhat.com"],
        )
        return render.ExecutiveSummary(
            period_label="2026Q3",
            window=("2026-07-01", "2026-09-30"),
            metrics=team_metrics,
            signals=signals,
            data_quality=dq,
            counts_by_source_kind={
                "jira": {"assignee": 737, "qa": 94, "ocpstrat_role": 45},
                "github": {"pr_authored": 555, "pr_reviewed": 729},
            },
            counts_by_attribution={
                "component": 847,
                "repo": 337,
                "project": 333,
                "shared": 277,
                "parent": 117,
                "": 249,
            },
            per_member=[
                render.MemberLine(
                    member="alice", total=150, workstreams_touched=2, top_workstream="SNO", top_share=67
                ),
                render.MemberLine(
                    member="bob", total=30, workstreams_touched=1, top_workstream="SNO", top_share=100
                ),
            ],
            per_workstream=[
                render.WorkstreamLine(
                    workstream="SNO",
                    volume=130,
                    contributors=2,
                    top_contributor="alice",
                    top_share=77,
                ),
                render.WorkstreamLine(
                    workstream="TNA",
                    volume=50,
                    contributors=1,
                    top_contributor="alice",
                    top_share=100,
                ),
                render.WorkstreamLine(
                    workstream="TNF", volume=0, contributors=0, top_contributor=None, top_share=0
                ),
                render.WorkstreamLine(
                    workstream="LVMS", volume=0, contributors=0, top_contributor=None, top_share=0
                ),
                render.WorkstreamLine(
                    workstream="USHIFT", volume=0, contributors=0, top_contributor=None, top_share=0
                ),
                render.WorkstreamLine(
                    workstream="TOPO", volume=0, contributors=0, top_contributor=None, top_share=0
                ),
            ],
            total_records=2160,
            jira_projects=("OCPEDGE", "USHIFT", "OCPBUGS"),
            ocpstrat_project="OCPSTRAT",
            allowed_org_count=10,
        )

    def test_text_output_contains_total_records(self):
        summary = self._make_loaded_summary()
        text = render.render_summary(summary, "text", show_sources=True)
        assert "2160" in text

    def test_text_output_contains_attribution_tally(self):
        summary = self._make_loaded_summary()
        text = render.render_summary(summary, "text", show_sources=True)
        assert "component" in text.lower()
        assert "847" in text
        assert "repo" in text.lower()
        assert "337" in text

    def test_text_output_contains_team_scores(self):
        summary = self._make_loaded_summary()
        text = render.render_summary(summary, "text")
        # TEAM section carries the count, under a label that says what it counts
        assert "contributions" in text.lower()
        assert "people per workstream" in text.lower()

    def test_text_output_contains_per_member_table(self):
        summary = self._make_loaded_summary()
        text = render.render_summary(summary, "text")
        assert "alice" in text
        assert "bob" in text
        assert "150" in text
        assert "30" in text

    def test_text_output_contains_all_exclusion_categories(self):
        summary = self._make_loaded_summary()
        text = render.render_summary(summary, "text", show_sources=True)
        # Check for excluded members
        assert "foobar@redhat.com" in text or "2 roster members" in text
        # Check for excluded PRs
        assert "99" in text  # excluded_personal_repo_prs
        # Check for unattributed items
        assert "249" in text  # unattributed count

    def test_text_output_preserves_two_node_toolbox_note(self):
        summary = self._make_loaded_summary()
        text = render.render_summary(summary, "text", show_sources=True)
        assert "two-node-toolbox" in text
        assert "not mapped by design" in text

    def test_markdown_output_contains_total_records(self):
        summary = self._make_loaded_summary()
        markdown = render.render_summary(summary, "markdown", show_sources=True)
        assert "2160" in markdown

    def test_markdown_output_contains_attribution_tally(self):
        summary = self._make_loaded_summary()
        markdown = render.render_summary(summary, "markdown", show_sources=True)
        assert "component" in markdown.lower()
        assert "847" in markdown

    def test_markdown_output_contains_team_scores(self):
        summary = self._make_loaded_summary()
        markdown = render.render_summary(summary, "markdown")
        # TEAM section contains contributions count
        assert "contributions" in markdown.lower()
        assert "people per workstream" in markdown.lower()

    def test_markdown_output_contains_per_member_table(self):
        summary = self._make_loaded_summary()
        markdown = render.render_summary(summary, "markdown")
        assert "alice" in markdown
        assert "bob" in markdown

    def test_markdown_output_has_pipe_tables(self):
        summary = self._make_loaded_summary()
        markdown = render.render_summary(summary, "markdown")
        # Markdown tables use pipes
        assert "|" in markdown

    def test_percentages_handle_empty_summary_without_error(self):
        # An empty summary should not cause ZeroDivisionError.
        summary = render.ExecutiveSummary(
            period_label="2026Q3",
            window=None,
            metrics=metrics.TeamMetrics(
                workstreams=SIX,
                members=[],
                workstreams_touched_by_member={},
                mean_people_per_workstream=0.0,
                mean_workstreams_per_person=0.0,
                active_member_count=0,
                total_member_count=0,
            ),
            signals=metrics.AllocationSignals(
                total_by_member={},
                total_by_workstream={},
                contributors_by_workstream={},
                team_median_total=0.0,
                grid_max=0,
                over_allocated=set(),
                under_allocated=set(),
                narrow=set(),
            ),
            data_quality=render.DataQuality(),
            counts_by_source_kind={},
            counts_by_attribution={},
            per_member=[],
            per_workstream=[],
            total_records=0,
            jira_projects=(),
            ocpstrat_project="",
            allowed_org_count=0,
        )
        # Should not raise
        text = render.render_summary(summary, "text")
        assert isinstance(text, str)
        markdown = render.render_summary(summary, "markdown")
        assert isinstance(markdown, str)

    def test_attribution_descriptions_are_grammatical(self):
        # Test exact descriptions to prevent regressions like "the inherited from".
        summary = self._make_loaded_summary()
        text = render.render_summary(summary, "text", show_sources=True)
        # Check that descriptions are grammatical (no "the X" where X starts with a vowel or article)
        assert "the issue's own components field" in text
        assert "the PR's repo maps to exactly one workstream" in text
        assert "the Jira project key" in text
        # These must NOT have "the" prefix
        assert "a cross-cutting CI" in text or "cross-cutting CI" in text
        assert "the cross-cutting CI" not in text, "shared description must not have 'the' prefix"
        assert "inherited from the parent epic" in text
        assert "the inherited from" not in text, "parent description must not have 'the' prefix"

    def test_workstream_order_is_canonical_not_alphabetical(self):
        # WORKSTREAMS table should be sorted by volume descending.
        # Since SNO has 130 and TNA has 50, they should appear in that order.
        summary = self._make_loaded_summary()
        text = render.render_summary(summary, "text")
        lines = text.splitlines()
        sno_found = False
        tna_found = False
        for i, line in enumerate(lines):
            if "SNO" in line and sno_found == False:
                sno_line = i
                sno_found = True
            if "TNA" in line and tna_found == False:
                tna_line = i
                tna_found = True
        if sno_found and tna_found:
            assert sno_line < tna_line, "SNO (130 volume) should appear before TNA (50 volume)"

    def test_activity_kind_order_is_canonical_not_alphabetical(self):
        # Jira and GitHub detail lines must use ACTIVITY_KINDS order, not alphabetical.
        summary = self._make_loaded_summary()
        text = render.render_summary(summary, "text", show_sources=True)
        # Find the Jira line with kinds
        for line in text.splitlines():
            if "assignee" in line and "qa" in line and "ocpstrat_role" in line:
                # Should be assignee · qa · ocpstrat_role (ACTIVITY_KINDS order)
                # NOT assignee · ocpstrat_role · qa (alphabetical)
                assignee_pos = line.index("assignee")
                qa_pos = line.index("qa")
                ocpstrat_pos = line.index("ocpstrat_role")
                assert (
                    assignee_pos < qa_pos < ocpstrat_pos
                ), "Jira kinds must follow ACTIVITY_KINDS order"
                break

    def test_no_trailing_whitespace_in_rendered_output(self):
        # No line in either format may end in whitespace.
        # Test with a member who has no flags to catch trailing spaces.
        matrix = metrics.ContributionMatrix(
            members=["alice", "bob"],
            workstreams=SIX,
            touched={"alice": {"SNO"}, "bob": {"SNO"}},
            counts={"alice": {"SNO": 100}, "bob": {"SNO": 50}},
        )
        team_metrics = metrics.compute_team_metrics(matrix)
        signals = metrics.compute_allocation_signals(matrix)

        summary = render.ExecutiveSummary(
            period_label="2026Q3",
            window=("2026-07-01", "2026-09-30"),
            metrics=team_metrics,
            signals=signals,
            data_quality=render.DataQuality(),
            counts_by_source_kind={},
            counts_by_attribution={},
            per_member=[
                render.MemberLine(
                    member="alice", total=100, workstreams_touched=1, top_workstream="SNO", top_share=100
                ),
                render.MemberLine(
                    member="bob", total=50, workstreams_touched=1, top_workstream="SNO", top_share=100
                ),
            ],
            per_workstream=[],
            total_records=0,
            jira_projects=(),
            ocpstrat_project="",
            allowed_org_count=0,
        )
        for output_format in ("text", "markdown"):
            result = render.render_summary(summary, output_format)
            for line in result.splitlines():
                assert not line.endswith(" ") and not line.endswith(
                    "\t"
                ), f"Line ends in whitespace ({output_format}): {line!r}"

    def test_roster_count_arithmetic_is_correct(self):
        # metrics.total_member_count is post-exclusion, not pre-exclusion.
        # With 16 active and 2 excluded, output must be "16 of 18 (2 excluded)".
        matrix = metrics.ContributionMatrix(
            members=["m" + str(i) for i in range(16)],
            workstreams=SIX,
            touched={},
            counts={},
        )
        team_metrics = metrics.compute_team_metrics(matrix)
        signals = metrics.compute_allocation_signals(matrix)

        summary = render.ExecutiveSummary(
            period_label="2026Q3",
            window=("2026-07-01", "2026-09-30"),
            metrics=team_metrics,
            signals=signals,
            data_quality=render.DataQuality(
                excluded_members=["foobar@redhat.com", "fuzzbuzz@redhat.com"],
            ),
            counts_by_source_kind={},
            counts_by_attribution={},
            per_member=[],
            per_workstream=[],
            total_records=0,
            jira_projects=(),
            ocpstrat_project="",
            allowed_org_count=0,
        )
        text = render.render_summary(summary, "text")
        assert (
            "Roster 16 of 18 (2 excluded)" in text
        ), "Roster count must show included of full, not double-subtract excluded"
        markdown = render.render_summary(summary, "markdown")
        assert "16 of 18 (2 excluded)" in markdown

    def test_roster_count_omits_excluded_clause_when_zero(self):
        # When there are no excluded members, the "(N excluded)" clause should be omitted.
        matrix = metrics.ContributionMatrix(
            members=["m" + str(i) for i in range(16)],
            workstreams=SIX,
            touched={},
            counts={},
        )
        team_metrics = metrics.compute_team_metrics(matrix)
        signals = metrics.compute_allocation_signals(matrix)

        summary = render.ExecutiveSummary(
            period_label="2026Q3",
            window=("2026-07-01", "2026-09-30"),
            metrics=team_metrics,
            signals=signals,
            data_quality=render.DataQuality(
                excluded_members=[],
            ),
            counts_by_source_kind={},
            counts_by_attribution={},
            per_member=[],
            per_workstream=[],
            total_records=0,
            jira_projects=(),
            ocpstrat_project="",
            allowed_org_count=0,
        )
        text = render.render_summary(summary, "text")
        assert "Roster 16 of 16" in text, "Roster count with no exclusions"
        assert "excluded)" not in text.lower(), "Should not show '(0 excluded)'"
        markdown = render.render_summary(summary, "markdown")
        assert "16 of 16" in markdown
        assert "excluded)" not in markdown.lower()

    def test_github_caveat_appears_in_text_format(self):
        # When out_of_window_records > 0, the GitHub caveat must appear in text output.
        summary = self._make_loaded_summary()
        # Replace with a summary that has out_of_window_records
        summary_with_out_of_window = render.ExecutiveSummary(
            period_label=summary.period_label,
            window=summary.window,
            metrics=summary.metrics,
            signals=summary.signals,
            data_quality=summary.data_quality,
            counts_by_source_kind=summary.counts_by_source_kind,
            counts_by_attribution=summary.counts_by_attribution,
            per_member=summary.per_member,
            per_workstream=summary.per_workstream,
            total_records=summary.total_records,
            jira_projects=summary.jira_projects,
            ocpstrat_project=summary.ocpstrat_project,
            allowed_org_count=summary.allowed_org_count,
            out_of_window_records=145,
        )
        text = render.render_summary(summary_with_out_of_window, "text", show_sources=True)
        # Check for key phrases in the caveat
        assert "authored" in text
        assert "created in the window" in text or "created" in text.lower()
        assert "reviewed" in text
        assert "updated" in text.lower()
        assert "GitHub cannot search by review date" in text or "review date" in text.lower()
        assert "creation date" in text.lower()
        assert "145" in text
        assert "still counted" in text or "counted" in text.lower()

    def test_github_caveat_appears_in_markdown_format(self):
        # When out_of_window_records > 0, the GitHub caveat must appear in markdown output.
        summary = self._make_loaded_summary()
        summary_with_out_of_window = render.ExecutiveSummary(
            period_label=summary.period_label,
            window=summary.window,
            metrics=summary.metrics,
            signals=summary.signals,
            data_quality=summary.data_quality,
            counts_by_source_kind=summary.counts_by_source_kind,
            counts_by_attribution=summary.counts_by_attribution,
            per_member=summary.per_member,
            per_workstream=summary.per_workstream,
            total_records=summary.total_records,
            jira_projects=summary.jira_projects,
            ocpstrat_project=summary.ocpstrat_project,
            allowed_org_count=summary.allowed_org_count,
            out_of_window_records=145,
        )
        markdown = render.render_summary(summary_with_out_of_window, "markdown", show_sources=True)
        # Check for key phrases in the caveat
        assert "authored" in markdown
        assert "created" in markdown.lower()
        assert "reviewed" in markdown
        assert "updated" in markdown.lower()
        assert "review date" in markdown.lower()
        assert "creation date" in markdown.lower()
        assert "145" in markdown

    def test_github_caveat_omits_count_when_zero(self):
        # When out_of_window_records is 0, the count sentence must be omitted,
        # but the method explanation must still appear.
        summary = self._make_loaded_summary()
        summary_with_zero = render.ExecutiveSummary(
            period_label=summary.period_label,
            window=summary.window,
            metrics=summary.metrics,
            signals=summary.signals,
            data_quality=summary.data_quality,
            counts_by_source_kind=summary.counts_by_source_kind,
            counts_by_attribution=summary.counts_by_attribution,
            per_member=summary.per_member,
            per_workstream=summary.per_workstream,
            total_records=summary.total_records,
            jira_projects=summary.jira_projects,
            ocpstrat_project=summary.ocpstrat_project,
            allowed_org_count=summary.allowed_org_count,
            out_of_window_records=0,
        )
        text = render.render_summary(summary_with_zero, "text", show_sources=True)
        # Method explanation should still appear
        assert "authored" in text
        assert "reviewed" in text
        # But the specific count/warning sentences should not appear
        assert "carry a date before the window" not in text
        assert "do not filter activities.csv" not in text

    def test_github_caveat_includes_filter_warning_when_nonzero(self):
        # When out_of_window_records > 0, must warn against filtering activities.csv by ts.
        summary = self._make_loaded_summary()
        summary_with_out_of_window = render.ExecutiveSummary(
            period_label=summary.period_label,
            window=summary.window,
            metrics=summary.metrics,
            signals=summary.signals,
            data_quality=summary.data_quality,
            counts_by_source_kind=summary.counts_by_source_kind,
            counts_by_attribution=summary.counts_by_attribution,
            per_member=summary.per_member,
            per_workstream=summary.per_workstream,
            total_records=summary.total_records,
            jira_projects=summary.jira_projects,
            ocpstrat_project=summary.ocpstrat_project,
            allowed_org_count=summary.allowed_org_count,
            out_of_window_records=145,
        )
        text = render.render_summary(summary_with_out_of_window, "text", show_sources=True)
        assert "ts" in text.lower() or "timestamp" in text.lower()
        assert "filter" in text.lower() or "filtering" in text.lower()


class TestSummaryRefactored(unittest.TestCase):
    """Tests for the refactored summary view with the --show-sources flag."""

    def test_default_summary_does_not_contain_source_sections(self):
        # Default output should NOT contain WHAT WAS COUNTED, HOW EACH ITEM WAS ATTRIBUTED, or WHAT WAS EXCLUDED
        matrix = metrics.ContributionMatrix(
            members=["alice"],
            workstreams=SIX,
            touched={"alice": {"SNO"}},
            counts={"alice": {"SNO": 10}},
        )
        summary = render.ExecutiveSummary(
            period_label="2026Q3",
            window=("2026-07-01", "2026-09-30"),
            metrics=metrics.compute_team_metrics(matrix),
            signals=metrics.compute_allocation_signals(matrix),
            data_quality=render.DataQuality(),
            counts_by_source_kind={"jira": {"assignee": 10}, "github": {}},
            counts_by_attribution={"component": 10},
            per_member=[
                render.MemberLine(
                    member="alice", total=10, workstreams_touched=1, top_workstream="SNO", top_share=100
                )
            ],
            per_workstream=[
                render.WorkstreamLine(
                    workstream="SNO",
                    volume=10,
                    contributors=1,
                    top_contributor="alice",
                    top_share=100,
                )
            ],
            total_records=10,
            jira_projects=("OCPEDGE",),
            ocpstrat_project="OCPSTRAT",
            allowed_org_count=10,
        )
        text = render.render_summary(summary, "text", show_sources=False)
        assert "WHAT WAS COUNTED" not in text
        assert "HOW EACH ITEM WAS ATTRIBUTED" not in text
        assert "WHAT WAS EXCLUDED" not in text

    def test_default_summary_does_not_contain_flag_strings(self):
        # Default output should NOT contain "over", "under", or "narrow" as flags
        matrix = metrics.ContributionMatrix(
            members=["alice", "bob"],
            workstreams=SIX,
            touched={"alice": {"SNO"}, "bob": {"TNA"}},
            counts={"alice": {"SNO": 200}, "bob": {"TNA": 10}},
        )
        signals = metrics.compute_allocation_signals(matrix)
        summary = render.ExecutiveSummary(
            period_label="2026Q3",
            window=("2026-07-01", "2026-09-30"),
            metrics=metrics.compute_team_metrics(matrix),
            signals=signals,
            data_quality=render.DataQuality(),
            counts_by_source_kind={},
            counts_by_attribution={},
            per_member=[
                render.MemberLine(
                    member="alice", total=200, workstreams_touched=1, top_workstream="SNO", top_share=100
                ),
                render.MemberLine(
                    member="bob", total=10, workstreams_touched=1, top_workstream="TNA", top_share=100
                ),
            ],
            per_workstream=[],
            total_records=0,
            jira_projects=(),
            ocpstrat_project="",
            allowed_org_count=0,
        )
        text = render.render_summary(summary, "text", show_sources=False)
        # Check that flag tokens don't appear in the member section
        # Look for lines that would have flags and verify they don't contain the tokens
        lines = text.splitlines()
        for line in lines:
            if "alice" in line.lower() or "bob" in line.lower():
                # These lines should not contain flag tokens as standalone words
                # Match whole words to avoid false positives like "cover" or "underline"
                import re

                assert not re.search(r"\bover\b", line), f"Found 'over' flag in: {line}"
                assert not re.search(r"\bunder\b", line), f"Found 'under' flag in: {line}"
                assert not re.search(r"\bnarrow\b", line), f"Found 'narrow' flag in: {line}"

    def test_show_sources_true_adds_all_sections(self):
        # With show_sources=True, output should contain all three source sections
        matrix = metrics.ContributionMatrix(
            members=["alice"],
            workstreams=SIX,
            touched={"alice": {"SNO"}},
            counts={"alice": {"SNO": 10}},
        )
        dq = render.DataQuality(
            unattributed_by_repo={"openshift-eng/two-node-toolbox": 5},
            tickets_no_component_no_parent=3,
            excluded_members=["foobar@redhat.com"],
        )
        summary = render.ExecutiveSummary(
            period_label="2026Q3",
            window=("2026-07-01", "2026-09-30"),
            metrics=metrics.compute_team_metrics(matrix),
            signals=metrics.compute_allocation_signals(matrix),
            data_quality=dq,
            counts_by_source_kind={"jira": {"assignee": 10}, "github": {}},
            counts_by_attribution={"component": 5, "": 5},  # 5 unattributed
            per_member=[
                render.MemberLine(
                    member="alice", total=10, workstreams_touched=1, top_workstream="SNO", top_share=100
                )
            ],
            per_workstream=[],
            total_records=10,
            jira_projects=("OCPEDGE",),
            ocpstrat_project="OCPSTRAT",
            allowed_org_count=10,
        )
        text = render.render_summary(summary, "text", show_sources=True)
        assert "WHAT WAS COUNTED" in text
        assert "HOW EACH ITEM WAS ATTRIBUTED" in text
        assert "WHAT WAS EXCLUDED" in text
        assert "foobar@redhat.com" in text  # excluded member
        assert "two-node-toolbox" in text
        assert "not mapped by design" in text

    def test_workstream_line_dataclass_exists(self):
        # Test that WorkstreamLine exists with correct fields
        line = render.WorkstreamLine(
            workstream="SNO", volume=100, contributors=5, top_contributor="alice", top_share=50
        )
        assert line.workstream == "SNO"
        assert line.volume == 100
        assert line.contributors == 5
        assert line.top_contributor == "alice"
        assert line.top_share == 50

    def test_workstream_line_with_zero_volume(self):
        # Zero-volume workstream should have top_contributor=None, top_share=0
        line = render.WorkstreamLine(
            workstream="TOPO", volume=0, contributors=0, top_contributor=None, top_share=0
        )
        assert line.volume == 0
        assert line.top_contributor is None
        assert line.top_share == 0

    def test_member_line_has_top_workstream_and_share(self):
        # New MemberLine should have top_workstream and top_share, no flags
        line = render.MemberLine(
            member="alice", total=150, workstreams_touched=3, top_workstream="SNO", top_share=60
        )
        assert line.member == "alice"
        assert line.total == 150
        assert line.workstreams_touched == 3
        assert line.top_workstream == "SNO"
        assert line.top_share == 60
        # Should not have flags attribute
        assert not hasattr(line, "flags")

    def test_default_summary_contains_team_section(self):
        # Default output should contain TEAM section
        matrix = metrics.ContributionMatrix(
            members=["alice", "bob"],
            workstreams=SIX,
            touched={"alice": {"SNO", "TNA"}, "bob": {"SNO"}},
            counts={"alice": {"SNO": 50, "TNA": 30}, "bob": {"SNO": 20}},
        )
        summary = render.ExecutiveSummary(
            period_label="2026Q3",
            window=("2026-07-01", "2026-09-30"),
            metrics=metrics.compute_team_metrics(matrix),
            signals=metrics.compute_allocation_signals(matrix),
            data_quality=render.DataQuality(),
            counts_by_source_kind={},
            counts_by_attribution={},
            per_member=[
                render.MemberLine(
                    member="alice", total=80, workstreams_touched=2, top_workstream="SNO", top_share=62
                ),
                render.MemberLine(
                    member="bob", total=20, workstreams_touched=1, top_workstream="SNO", top_share=100
                ),
            ],
            per_workstream=[
                render.WorkstreamLine(
                    workstream="SNO",
                    volume=70,
                    contributors=2,
                    top_contributor="alice",
                    top_share=71,
                ),
                render.WorkstreamLine(
                    workstream="TNA",
                    volume=30,
                    contributors=1,
                    top_contributor="alice",
                    top_share=100,
                ),
            ],
            total_records=100,
            jira_projects=(),
            ocpstrat_project="",
            allowed_org_count=0,
        )
        text = render.render_summary(summary, "text", show_sources=False)
        assert "TEAM" in text
        assert "contributions" in text.lower()
        assert "median" in text.lower()

    def test_default_summary_contains_workstreams_section(self):
        # Default output should contain WORKSTREAMS section
        matrix = metrics.ContributionMatrix(
            members=["alice"],
            workstreams=SIX,
            touched={"alice": {"SNO"}},
            counts={"alice": {"SNO": 10}},
        )
        summary = render.ExecutiveSummary(
            period_label="2026Q3",
            window=("2026-07-01", "2026-09-30"),
            metrics=metrics.compute_team_metrics(matrix),
            signals=metrics.compute_allocation_signals(matrix),
            data_quality=render.DataQuality(),
            counts_by_source_kind={},
            counts_by_attribution={},
            per_member=[
                render.MemberLine(
                    member="alice", total=10, workstreams_touched=1, top_workstream="SNO", top_share=100
                )
            ],
            per_workstream=[
                render.WorkstreamLine(
                    workstream="SNO",
                    volume=10,
                    contributors=1,
                    top_contributor="alice",
                    top_share=100,
                )
            ],
            total_records=10,
            jira_projects=(),
            ocpstrat_project="",
            allowed_org_count=0,
        )
        text = render.render_summary(summary, "text", show_sources=False)
        assert "WORKSTREAMS" in text
        assert "volume" in text.lower()
        assert "people" in text.lower() or "contributors" in text.lower()

    def test_default_summary_contains_people_section(self):
        # Default output should contain PEOPLE section
        matrix = metrics.ContributionMatrix(
            members=["alice"],
            workstreams=SIX,
            touched={"alice": {"SNO"}},
            counts={"alice": {"SNO": 10}},
        )
        summary = render.ExecutiveSummary(
            period_label="2026Q3",
            window=("2026-07-01", "2026-09-30"),
            metrics=metrics.compute_team_metrics(matrix),
            signals=metrics.compute_allocation_signals(matrix),
            data_quality=render.DataQuality(),
            counts_by_source_kind={},
            counts_by_attribution={},
            per_member=[
                render.MemberLine(
                    member="alice", total=10, workstreams_touched=1, top_workstream="SNO", top_share=100
                )
            ],
            per_workstream=[],
            total_records=10,
            jira_projects=(),
            ocpstrat_project="",
            allowed_org_count=0,
        )
        text = render.render_summary(summary, "text", show_sources=False)
        assert "PEOPLE" in text
        assert "alice" in text

    def test_default_summary_contains_data_health_section(self):
        # Default output should contain DATA HEALTH section
        matrix = metrics.ContributionMatrix(
            members=["alice"],
            workstreams=SIX,
            touched={"alice": {"SNO"}},
            counts={"alice": {"SNO": 10}},
        )
        summary = render.ExecutiveSummary(
            period_label="2026Q3",
            window=("2026-07-01", "2026-09-30"),
            metrics=metrics.compute_team_metrics(matrix),
            signals=metrics.compute_allocation_signals(matrix),
            data_quality=render.DataQuality(
                unattributed_by_repo={"org/repo": 5}, tickets_no_component_no_parent=3
            ),
            counts_by_source_kind={},
            counts_by_attribution={"": 8},
            per_member=[
                render.MemberLine(
                    member="alice", total=10, workstreams_touched=1, top_workstream="SNO", top_share=100
                )
            ],
            per_workstream=[],
            total_records=18,
            jira_projects=(),
            ocpstrat_project="",
            allowed_org_count=0,
        )
        text = render.render_summary(summary, "text", show_sources=False)
        assert "DATA HEALTH" in text
        assert "could not be placed" in text.lower() or "unattributed" in text.lower()


if __name__ == "__main__":
    unittest.main()
