"""Tests for metrics.py — workstreams touched, and the team means, from a binary matrix.

Covers a hand-computed happy-path matrix, failure inputs (empty roster, unknown
member), and boundary/anti-cheat cases: all-zero matrix, zero-contributor
workstreams still present in the per-workstream vector, a member touching all
six, and the single-active-member identity between the team mean and that
member's own count.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import metrics  # noqa: E402

SIX = ["SNO", "TNA", "TNF", "LVMS", "USHIFT", "TOPO"]


def _matrix(members, touched):
    return metrics.ContributionMatrix(members=members, workstreams=list(SIX), touched=touched)


class TestMetricsHappyPath(unittest.TestCase):
    def setUp(self):
        # alice touches 3 workstreams, bob 1, carol none.
        self.matrix = _matrix(
            members=["alice", "bob", "carol"],
            touched={"alice": {"SNO", "TNA", "TNF"}, "bob": {"SNO"}, "carol": set()},
        )

    def test_workstreams_touched_per_member(self):
        assert metrics.workstreams_touched(self.matrix, "alice") == 3
        assert metrics.workstreams_touched(self.matrix, "bob") == 1
        assert metrics.workstreams_touched(self.matrix, "carol") == 0

    def test_total_touches_is_sum_of_ones(self):
        assert metrics.total_touches(self.matrix) == 4

    def test_mean_people_per_workstream_is_over_all_six_workstreams(self):
        assert metrics.mean_people_per_workstream(self.matrix) == 4 / 6

    def test_mean_workstreams_per_person_is_over_active_members(self):
        # 4 ones across 2 active members (carol is inactive).
        assert metrics.mean_workstreams_per_person(self.matrix) == 2.0

    def test_active_members_excludes_zero_contribution_members(self):
        assert metrics.active_members(self.matrix) == ["alice", "bob"]

    def test_compute_team_metrics_bundles_everything(self):
        result = metrics.compute_team_metrics(self.matrix)
        assert result.active_member_count == 2
        assert result.total_member_count == 3
        assert result.mean_people_per_workstream == 4 / 6
        assert result.mean_workstreams_per_person == 2.0
        assert result.workstreams_touched_by_member == {"alice": 3, "bob": 1, "carol": 0}


class TestMetricsFailureInputs(unittest.TestCase):
    def test_empty_roster_does_not_divide_by_zero(self):
        empty = _matrix(members=[], touched={})
        result = metrics.compute_team_metrics(empty)
        assert result.mean_workstreams_per_person == 0.0
        assert result.mean_people_per_workstream == 0.0
        assert result.active_member_count == 0

    def test_workstreams_touched_for_unknown_member_raises(self):
        matrix = _matrix(members=["alice"], touched={"alice": {"SNO"}})
        with self.assertRaises(ValueError):
            metrics.workstreams_touched(matrix, "nobody")


class TestMetricsEdgeCases(unittest.TestCase):
    def test_all_zero_matrix_zeros_both_team_means(self):
        matrix = _matrix(members=["alice", "bob"], touched={"alice": set(), "bob": set()})
        result = metrics.compute_team_metrics(matrix)
        assert result.mean_workstreams_per_person == 0.0
        assert result.mean_people_per_workstream == 0.0
        assert set(result.workstreams_touched_by_member.values()) == {0}

    def test_zero_contributor_workstream_is_present_in_vector(self):
        # Ported from the deleted flexibility_vector test: the per-workstream
        # contributor counts must list every canonical workstream in order,
        # including those nobody touched, so a thin workstream shows as 0
        # rather than vanishing from the report. Counts are supplied because
        # contributors_by_workstream reads the weighted matrix, not `touched` —
        # without them every column would read 0 and the test would pass for
        # the wrong reason.
        matrix = metrics.ContributionMatrix(
            members=["alice"],
            workstreams=list(SIX),
            touched={"alice": {"SNO"}},
            counts={"alice": {"SNO": 1}},
        )
        vector = metrics.compute_allocation_signals(matrix).contributors_by_workstream
        assert vector["SNO"] == 1
        assert vector["TOPO"] == 0
        assert list(vector.keys()) == SIX

    def test_member_active_in_all_six_touches_six(self):
        matrix = _matrix(members=["alice"], touched={"alice": set(SIX)})
        assert metrics.workstreams_touched(matrix, "alice") == 6

    def test_single_active_member_team_mean_equals_their_own_count(self):
        matrix = _matrix(members=["alice"], touched={"alice": {"SNO", "TNA"}})
        assert metrics.mean_workstreams_per_person(matrix) == metrics.workstreams_touched(
            matrix, "alice"
        )

    def test_touched_entries_outside_canonical_workstreams_are_ignored(self):
        matrix = _matrix(members=["alice"], touched={"alice": {"SNO", "BOGUS"}})
        assert metrics.workstreams_touched(matrix, "alice") == 1


class TestAllocationSignalsBackwardCompatibility(unittest.TestCase):
    """Verify that ContributionMatrix built without counts field still works."""

    def test_matrix_without_counts_defaults_to_empty_dict(self):
        # Construct a matrix with only members, workstreams, and touched.
        matrix = metrics.ContributionMatrix(
            members=["alice", "bob"], workstreams=list(SIX), touched={"alice": {"SNO"}}
        )
        # The counts field should default to an empty dict.
        assert matrix.counts == {}

    def test_all_existing_functions_work_without_counts(self):
        matrix = metrics.ContributionMatrix(
            members=["alice"], workstreams=list(SIX), touched={"alice": {"SNO", "TNA"}}
        )
        # Every count derived from `touched` should still work.
        assert metrics.workstreams_touched(matrix, "alice") == 2
        assert metrics.mean_people_per_workstream(matrix) > 0
        assert metrics.mean_workstreams_per_person(matrix) == 2
        result = metrics.compute_team_metrics(matrix)
        assert result.active_member_count == 1

    def test_allocation_signals_with_empty_counts(self):
        matrix = metrics.ContributionMatrix(
            members=["alice", "bob"], workstreams=list(SIX), touched={"alice": {"SNO"}}
        )
        signals = metrics.compute_allocation_signals(matrix)
        # All totals should be zero when counts is empty.
        assert signals.total_by_member == {"alice": 0, "bob": 0}
        assert all(v == 0 for v in signals.total_by_workstream.values())
        assert signals.team_median_total == 0.0
        assert signals.grid_max == 0


class TestAllocationSignalsSums(unittest.TestCase):
    """Test total_by_member and total_by_workstream summing logic."""

    def test_total_by_member_sums_across_workstreams(self):
        matrix = metrics.ContributionMatrix(
            members=["alice", "bob"],
            workstreams=["SNO", "TNA", "TNF"],
            counts={"alice": {"SNO": 10, "TNA": 5, "TNF": 3}, "bob": {"SNO": 2}},
        )
        signals = metrics.compute_allocation_signals(matrix)
        assert signals.total_by_member == {"alice": 18, "bob": 2}

    def test_total_by_workstream_sums_down_column(self):
        matrix = metrics.ContributionMatrix(
            members=["alice", "bob", "carol"],
            workstreams=["SNO", "TNA"],
            counts={"alice": {"SNO": 10}, "bob": {"SNO": 5}, "carol": {"TNA": 3}},
        )
        signals = metrics.compute_allocation_signals(matrix)
        assert signals.total_by_workstream == {"SNO": 15, "TNA": 3}

    def test_column_absent_from_counts_appears_as_zero(self):
        matrix = metrics.ContributionMatrix(
            members=["alice"], workstreams=["SNO", "TNA", "TNF"], counts={"alice": {"SNO": 10}}
        )
        signals = metrics.compute_allocation_signals(matrix)
        assert signals.total_by_workstream == {"SNO": 10, "TNA": 0, "TNF": 0}

    def test_column_in_counts_but_not_in_workstreams_is_ignored(self):
        matrix = metrics.ContributionMatrix(
            members=["alice"],
            workstreams=["SNO", "TNA"],
            counts={"alice": {"SNO": 10, "BOGUS": 999}},
        )
        signals = metrics.compute_allocation_signals(matrix)
        # BOGUS should not contribute to alice's total.
        assert signals.total_by_member == {"alice": 10}
        assert "BOGUS" not in signals.total_by_workstream


class TestAllocationSignalsContributors(unittest.TestCase):
    """Test contributors_by_workstream counts members, not contributions."""

    def test_contributors_by_workstream_counts_members(self):
        matrix = metrics.ContributionMatrix(
            members=["alice", "bob", "carol"],
            workstreams=["SNO", "TNA"],
            counts={"alice": {"SNO": 100}, "bob": {"SNO": 1}, "carol": {"TNA": 50}},
        )
        signals = metrics.compute_allocation_signals(matrix)
        assert signals.contributors_by_workstream == {"SNO": 2, "TNA": 1}

    def test_zero_contributors_workstream_present(self):
        matrix = metrics.ContributionMatrix(
            members=["alice"], workstreams=["SNO", "TNA"], counts={"alice": {"SNO": 10}}
        )
        signals = metrics.compute_allocation_signals(matrix)
        assert signals.contributors_by_workstream == {"SNO": 1, "TNA": 0}


class TestAllocationSignalsMedian(unittest.TestCase):
    """Test team_median_total ignores zero-activity members."""

    def test_team_median_total_ignores_zero_activity_members(self):
        # alice: 10, bob: 20, carol: 0. Median of [10, 20] is 15.0.
        matrix = metrics.ContributionMatrix(
            members=["alice", "bob", "carol"],
            workstreams=["SNO"],
            counts={"alice": {"SNO": 10}, "bob": {"SNO": 20}},
        )
        signals = metrics.compute_allocation_signals(matrix)
        assert signals.team_median_total == 15.0

    def test_team_median_total_correct_for_even_active_count(self):
        # alice: 10, bob: 30. Median is 20.0.
        matrix = metrics.ContributionMatrix(
            members=["alice", "bob"], workstreams=["SNO"], counts={"alice": {"SNO": 10}, "bob": {"SNO": 30}}
        )
        signals = metrics.compute_allocation_signals(matrix)
        assert signals.team_median_total == 20.0

    def test_team_median_total_correct_for_odd_active_count(self):
        # alice: 10, bob: 20, carol: 30. Median is 20.
        matrix = metrics.ContributionMatrix(
            members=["alice", "bob", "carol"],
            workstreams=["SNO"],
            counts={"alice": {"SNO": 10}, "bob": {"SNO": 20}, "carol": {"SNO": 30}},
        )
        signals = metrics.compute_allocation_signals(matrix)
        assert signals.team_median_total == 20.0

    def test_team_median_total_zero_when_nobody_active(self):
        matrix = metrics.ContributionMatrix(members=["alice", "bob"], workstreams=["SNO"], counts={})
        signals = metrics.compute_allocation_signals(matrix)
        assert signals.team_median_total == 0.0


class TestAllocationSignalsGridMax(unittest.TestCase):
    """Test grid_max on normal, all-zero, and empty matrices."""

    def test_grid_max_on_normal_grid(self):
        matrix = metrics.ContributionMatrix(
            members=["alice", "bob"],
            workstreams=["SNO", "TNA"],
            counts={"alice": {"SNO": 10, "TNA": 25}, "bob": {"SNO": 15}},
        )
        signals = metrics.compute_allocation_signals(matrix)
        assert signals.grid_max == 25

    def test_grid_max_on_all_zero_grid(self):
        matrix = metrics.ContributionMatrix(
            members=["alice"], workstreams=["SNO"], counts={"alice": {"SNO": 0}}
        )
        signals = metrics.compute_allocation_signals(matrix)
        assert signals.grid_max == 0

    def test_grid_max_on_empty_matrix(self):
        matrix = metrics.ContributionMatrix(members=[], workstreams=[], counts={})
        signals = metrics.compute_allocation_signals(matrix)
        assert signals.grid_max == 0


class TestAllocationSignalsOverUnderAllocated(unittest.TestCase):
    """Test over_allocated and under_allocated boundary behavior."""

    def test_over_allocated_boundary(self):
        # Median is 10. 2.0 * 10 = 20. alice at exactly 20 should NOT be over.
        # bob at 21 should be over.
        matrix = metrics.ContributionMatrix(
            members=["alice", "bob", "carol"],
            workstreams=["SNO"],
            counts={"alice": {"SNO": 20}, "bob": {"SNO": 21}, "carol": {"SNO": 10}},
        )
        signals = metrics.compute_allocation_signals(matrix)
        # Median of [10, 20, 21] is 20. 2.0 * 20 = 40.
        # alice: 20 <= 40, bob: 21 <= 40, carol: 10 <= 40. None over.
        # Let me recalculate: median([10, 20, 21]) = 20.
        # Over threshold is > 40. So no one is over.
        # Actually, let me construct a clearer example.
        # Let's make median = 10 by having [5, 10, 15]. Median is 10.
        # Over threshold is > 20. alice at 20 should not be over, bob at 21 should be.
        matrix = metrics.ContributionMatrix(
            members=["alice", "bob", "carol", "dave"],
            workstreams=["SNO"],
            counts={"alice": {"SNO": 20}, "bob": {"SNO": 21}, "carol": {"SNO": 5}, "dave": {"SNO": 15}},
        )
        signals = metrics.compute_allocation_signals(matrix)
        # Median of [5, 15, 20, 21] is (15 + 20) / 2 = 17.5. 2.0 * 17.5 = 35.
        # alice: 20 <= 35, bob: 21 <= 35, carol: 5 <= 35, dave: 15 <= 35. None over.
        # Let me construct another example: [10, 10, 10, 30]. Median is (10+10)/2 = 10.
        # 2.0 * 10 = 20. dave at 30 should be over.
        matrix = metrics.ContributionMatrix(
            members=["alice", "bob", "carol", "dave"],
            workstreams=["SNO"],
            counts={"alice": {"SNO": 10}, "bob": {"SNO": 10}, "carol": {"SNO": 10}, "dave": {"SNO": 30}},
        )
        signals = metrics.compute_allocation_signals(matrix)
        # Median of [10, 10, 10, 30] is (10+10)/2 = 10. 2.0 * 10 = 20.
        # dave: 30 > 20, so dave is over.
        assert "dave" in signals.over_allocated
        # Now test exactly at the boundary: [10, 10, 10, 20].
        matrix = metrics.ContributionMatrix(
            members=["alice", "bob", "carol", "dave"],
            workstreams=["SNO"],
            counts={"alice": {"SNO": 10}, "bob": {"SNO": 10}, "carol": {"SNO": 10}, "dave": {"SNO": 20}},
        )
        signals = metrics.compute_allocation_signals(matrix)
        # Median is 10. 2.0 * 10 = 20. dave at exactly 20 is NOT over (> not >=).
        assert "dave" not in signals.over_allocated

    def test_under_allocated_boundary(self):
        # Median is 10. 0.5 * 10 = 5. alice at exactly 5 should NOT be under.
        # bob at 4 should be under.
        matrix = metrics.ContributionMatrix(
            members=["alice", "bob", "carol"],
            workstreams=["SNO"],
            counts={"alice": {"SNO": 5}, "bob": {"SNO": 4}, "carol": {"SNO": 10}},
        )
        signals = metrics.compute_allocation_signals(matrix)
        # Median of [4, 5, 10] is 5. 0.5 * 5 = 2.5.
        # alice: 5 >= 2.5, bob: 4 >= 2.5. None under.
        # Let me construct a clearer example: [10, 10, 10, 4].
        matrix = metrics.ContributionMatrix(
            members=["alice", "bob", "carol", "dave"],
            workstreams=["SNO"],
            counts={"alice": {"SNO": 10}, "bob": {"SNO": 10}, "carol": {"SNO": 10}, "dave": {"SNO": 4}},
        )
        signals = metrics.compute_allocation_signals(matrix)
        # Median of [4, 10, 10, 10] is (10+10)/2 = 10. 0.5 * 10 = 5.
        # dave: 4 < 5, so dave is under.
        assert "dave" in signals.under_allocated
        # Now test exactly at the boundary: [10, 10, 10, 5].
        matrix = metrics.ContributionMatrix(
            members=["alice", "bob", "carol", "dave"],
            workstreams=["SNO"],
            counts={"alice": {"SNO": 10}, "bob": {"SNO": 10}, "carol": {"SNO": 10}, "dave": {"SNO": 5}},
        )
        signals = metrics.compute_allocation_signals(matrix)
        # Median is 10. 0.5 * 10 = 5. dave at exactly 5 is NOT under (< not <=).
        assert "dave" not in signals.under_allocated

    def test_zero_activity_member_not_in_over_or_under_allocated(self):
        matrix = metrics.ContributionMatrix(
            members=["alice", "bob"], workstreams=["SNO"], counts={"alice": {"SNO": 10}}
        )
        signals = metrics.compute_allocation_signals(matrix)
        # bob has total 0, should not be in either set.
        assert "bob" not in signals.over_allocated
        assert "bob" not in signals.under_allocated


class TestAllocationSignalsNarrow(unittest.TestCase):
    """Test narrow: members where a single workstream is > 70% of their total."""

    def test_member_at_exactly_70_percent_is_not_narrow(self):
        # alice: SNO: 7, TNA: 3. SNO is exactly 70%.
        matrix = metrics.ContributionMatrix(
            members=["alice"], workstreams=["SNO", "TNA"], counts={"alice": {"SNO": 7, "TNA": 3}}
        )
        signals = metrics.compute_allocation_signals(matrix)
        assert "alice" not in signals.narrow

    def test_member_at_71_percent_is_narrow(self):
        # alice: SNO: 71, TNA: 29. SNO is 71%.
        matrix = metrics.ContributionMatrix(
            members=["alice"], workstreams=["SNO", "TNA"], counts={"alice": {"SNO": 71, "TNA": 29}}
        )
        signals = metrics.compute_allocation_signals(matrix)
        assert "alice" in signals.narrow

    def test_member_with_single_workstream_is_narrow(self):
        # alice only in SNO (100%).
        matrix = metrics.ContributionMatrix(
            members=["alice"], workstreams=["SNO", "TNA"], counts={"alice": {"SNO": 10}}
        )
        signals = metrics.compute_allocation_signals(matrix)
        assert "alice" in signals.narrow

    def test_zero_total_member_is_not_narrow(self):
        matrix = metrics.ContributionMatrix(members=["alice"], workstreams=["SNO"], counts={})
        signals = metrics.compute_allocation_signals(matrix)
        assert "alice" not in signals.narrow


class TestAllocationSignalsSharedAsymmetry(unittest.TestCase):
    """Test SHARED asymmetry: contributes to totals but not to workstreams touched."""

    def test_shared_contributes_to_total_by_member(self):
        # alice: SNO: 10, SHARED: 5. Total should be 15.
        matrix = metrics.ContributionMatrix(
            members=["alice"],
            workstreams=["SNO", "TNA", "SHARED"],
            counts={"alice": {"SNO": 10, "SHARED": 5}},
        )
        signals = metrics.compute_allocation_signals(matrix)
        assert signals.total_by_member == {"alice": 15}

    def test_shared_does_not_affect_workstreams_touched(self):
        # alice touches SNO and SHARED. Workstreams touched should be 1 (only SNO).
        # But wait, the test is about the asymmetry. SHARED is in workstreams here,
        # so it WILL be counted if touched is {"alice": {"SNO", "SHARED"}}.
        # The asymmetry is that SHARED is NOT in the canonical six workstreams,
        # but it can appear in the workstreams list for display purposes.
        # Let me construct this correctly.
        # The canonical six are SNO, TNA, TNF, LVMS, USHIFT, TOPO.
        # SHARED is NOT in that list. So if matrix.workstreams is the canonical six,
        # SHARED won't count as a workstream touched. If display_columns includes
        # SHARED, allocation signals include it but the touched count does not.
        # Let me set up the test to show that:
        # matrix.workstreams = canonical six only. alice touches SNO via touched.
        # alice has SNO: 10, SHARED: 5 in counts. But SHARED is not in workstreams,
        # so it should be ignored.
        matrix = metrics.ContributionMatrix(
            members=["alice"],
            workstreams=["SNO", "TNA", "TNF", "LVMS", "USHIFT", "TOPO"],
            touched={"alice": {"SNO"}},
            counts={"alice": {"SNO": 10, "SHARED": 5}},
        )
        # Workstreams touched should be 1 (only SNO).
        assert metrics.workstreams_touched(matrix, "alice") == 1
        # But total_by_member should only count SNO (10), not SHARED (5),
        # because SHARED is not in workstreams.
        signals = metrics.compute_allocation_signals(matrix)
        assert signals.total_by_member == {"alice": 10}

        # Now test the opposite: SHARED is in workstreams.
        matrix = metrics.ContributionMatrix(
            members=["alice"],
            workstreams=["SNO", "TNA", "TNF", "LVMS", "USHIFT", "TOPO", "SHARED"],
            touched={"alice": {"SNO"}},  # SHARED not in touched
            counts={"alice": {"SNO": 10, "SHARED": 5}},
        )
        # Workstreams touched should still be 1 (only SNO in touched).
        assert metrics.workstreams_touched(matrix, "alice") == 1
        # But total_by_member should now include SHARED (15).
        signals = metrics.compute_allocation_signals(matrix)
        assert signals.total_by_member == {"alice": 15}


class TestAllocationSignalsEmptyMatrix(unittest.TestCase):
    """Test empty matrix returns well-formed AllocationSignals."""

    def test_empty_matrix_no_exception(self):
        matrix = metrics.ContributionMatrix(members=[], workstreams=[], counts={})
        signals = metrics.compute_allocation_signals(matrix)
        assert signals.total_by_member == {}
        assert signals.total_by_workstream == {}
        assert signals.contributors_by_workstream == {}
        assert signals.team_median_total == 0.0
        assert signals.grid_max == 0
        assert signals.over_allocated == set()
        assert signals.under_allocated == set()
        assert signals.narrow == set()


if __name__ == "__main__":
    unittest.main()
