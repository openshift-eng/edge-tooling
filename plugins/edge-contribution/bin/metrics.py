#!/usr/bin/env python3
"""Cross-workstream contribution metrics from a binary contribution matrix.

Given which workstreams each roster member touched at least once in a period,
this computes the counts defined in ``references/metrics.md``. Every name here
says what it counts, so a reader never has to look up a term:

* **Workstreams touched** (per member) — how many of the six canonical
  workstreams the member contributed to at least once. Reported as "N of 6".
* **Mean people per workstream** — the average number of distinct contributors
  a workstream had, over the six canonical workstreams.
* **Mean workstreams per person** — the average number of workstreams an
  *active* member touched (members with zero activity are left out, so they do
  not deflate the average).

This module also computes **allocation signals** from the contribution counts
matrix (``counts``), which are relative-to-team indicators of workload distribution:

* **Over-allocated** — members whose total contribution count is more than twice
  the team median among active members.
* **Under-allocated** — members whose total is less than half the team median, but
  greater than zero (zero-activity members are excluded from the comparison).
* **Narrow** — members where a single workstream accounts for more than 70% of
  their total contributions, indicating siloing.

The binary ``touched`` field drives the workstreams-touched counts, while
``counts`` drives allocation signals. This asymmetry is intentional: the SHARED
column (for cross-cutting CI/tooling work) contributes to a member's total
workload but is NOT a canonical workstream and does not count toward
workstreams touched.

All inputs are pure data, so this module has no I/O and is trivially testable.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Set

from workstream_map import SHARED_COLUMN


@dataclass(frozen=True)
class ContributionMatrix:
    """Who touched which workstreams during the reporting period.

    ``touched`` maps a member to the set of workstream acronyms they contributed
    to. Acronyms outside ``workstreams`` are ignored, and a member absent from
    ``touched`` is treated as having contributed to nothing.

    ``counts`` maps a member to a dict of workstream -> count. This is the weighted
    view used for allocation signals. Members or workstreams absent from ``counts``
    are treated as zero. Columns present in ``counts`` but absent from
    ``workstreams`` are ignored when computing allocation signals.
    """

    members: List[str]
    workstreams: List[str]
    touched: Dict[str, Set[str]] = field(default_factory=dict)
    counts: Dict[str, Dict[str, int]] = field(default_factory=dict)


@dataclass(frozen=True)
class TeamMetrics:
    """The full metric bundle for a team over one reporting period."""

    workstreams: List[str]
    members: List[str]
    workstreams_touched_by_member: Dict[str, int]
    mean_people_per_workstream: float
    mean_workstreams_per_person: float
    active_member_count: int
    total_member_count: int


@dataclass(frozen=True)
class AllocationSignals:
    """Relative workload distribution signals for a team over one reporting period.

    These are computed from the weighted ``counts`` matrix and are relative to the
    team median. The thresholds (2.0x over, 0.5x under, 70% narrow) are heuristic
    indicators of imbalance, not capacity targets. Zero-activity members are excluded
    from the median and under/over classifications, as they may be on leave, new, or
    misattributed rather than genuinely under-allocated in a comparable sense.
    """

    total_by_member: Dict[str, int]
    total_by_workstream: Dict[str, int]
    contributors_by_workstream: Dict[str, int]
    team_median_total: float
    grid_max: int
    over_allocated: Set[str]
    under_allocated: Set[str]
    narrow: Set[str]


# Allocation signal thresholds (relative to team median).
# Over-allocated: members with > 2.0x the team median total.
OVER_ALLOCATED_THRESHOLD = 2.0
# Under-allocated: members with < 0.5x the team median AND total > 0.
UNDER_ALLOCATED_THRESHOLD = 0.5
# Narrow: members where a single workstream is > 70% of their total.
NARROW_THRESHOLD = 0.70


def _canonical_workstreams(matrix: ContributionMatrix) -> List[str]:
    """Return the matrix columns that count as workstreams, i.e. everything but SHARED.

    Callers pass ``display_columns()`` (the six plus SHARED) so that allocation
    signals cover cross-cutting work, but the workstreams-touched count must stay
    "N of 6". Filtering here rather than trusting the caller keeps that invariant
    true no matter which column list is supplied.
    """
    return [ws for ws in matrix.workstreams if ws != SHARED_COLUMN]


def _touched_workstreams(matrix: ContributionMatrix, member: str) -> Set[str]:
    return matrix.touched.get(member, set()) & set(_canonical_workstreams(matrix))


def workstreams_touched(matrix: ContributionMatrix, member: str) -> int:
    """Return how many canonical workstreams ``member`` touched.

    Raises ``ValueError`` if ``member`` is not part of the matrix roster.
    """
    if member not in matrix.members:
        raise ValueError(f"member not in matrix roster: {member!r}")
    return len(_touched_workstreams(matrix, member))


def workstreams_touched_by_member(matrix: ContributionMatrix) -> Dict[str, int]:
    """Return the workstreams-touched count for every member, in roster order."""
    return {member: len(_touched_workstreams(matrix, member)) for member in matrix.members}


def total_touches(matrix: ContributionMatrix) -> int:
    """Return how many (member, workstream) pairs saw at least one contribution.

    This is the number of 1s in the binary matrix, i.e. the sum of every
    member's workstreams-touched count. It is NOT the number of activity
    records — that is the sum of the weighted ``counts`` matrix, which
    ``compute_allocation_signals`` reports as ``total_by_member``.
    """
    return sum(workstreams_touched_by_member(matrix).values())


def mean_people_per_workstream(matrix: ContributionMatrix) -> float:
    """Return the average number of distinct contributors per canonical workstream."""
    canonical = _canonical_workstreams(matrix)
    if not canonical:
        return 0.0
    return total_touches(matrix) / len(canonical)


def active_members(matrix: ContributionMatrix) -> List[str]:
    """Return members with at least one contribution, in roster order."""
    return [member for member, count in workstreams_touched_by_member(matrix).items() if count > 0]


def mean_workstreams_per_person(matrix: ContributionMatrix) -> float:
    """Return the average workstreams-touched count over active members.

    Returns 0.0 when nobody is active. Inactive members are excluded so they
    do not deflate the average.
    """
    active = active_members(matrix)
    if not active:
        return 0.0
    return total_touches(matrix) / len(active)


def compute_team_metrics(matrix: ContributionMatrix) -> TeamMetrics:
    """Compute the full metric bundle for a reporting period."""
    return TeamMetrics(
        workstreams=_canonical_workstreams(matrix),
        members=list(matrix.members),
        workstreams_touched_by_member=workstreams_touched_by_member(matrix),
        mean_people_per_workstream=mean_people_per_workstream(matrix),
        mean_workstreams_per_person=mean_workstreams_per_person(matrix),
        active_member_count=len(active_members(matrix)),
        total_member_count=len(matrix.members),
    )


def compute_allocation_signals(matrix: ContributionMatrix) -> AllocationSignals:
    """Compute relative workload distribution signals from the counts matrix.

    All counts are summed over the columns present in ``matrix.workstreams`` only.
    Columns in ``counts`` that are not in ``workstreams`` are ignored. The SHARED
    column (if present in ``workstreams``) contributes to member totals, even though
    it is not a canonical workstream and does not count toward workstreams touched.

    Zero-activity members (total == 0) are excluded from the team median calculation
    and from over/under-allocated sets. This treats them as on leave, new, or
    misattributed rather than under-allocated in a comparable sense.

    Returns:
        AllocationSignals with all fields populated, even for an empty matrix.
    """
    workstream_set = set(matrix.workstreams)

    # Compute total_by_member: sum each member's counts over valid workstreams.
    total_by_member: Dict[str, int] = {}
    for member in matrix.members:
        member_counts = matrix.counts.get(member, {})
        total = sum(count for ws, count in member_counts.items() if ws in workstream_set)
        total_by_member[member] = total

    # Compute total_by_workstream: sum down each column over all members.
    total_by_workstream: Dict[str, int] = {ws: 0 for ws in matrix.workstreams}
    for member in matrix.members:
        member_counts = matrix.counts.get(member, {})
        for ws in matrix.workstreams:
            total_by_workstream[ws] += member_counts.get(ws, 0)

    # Compute contributors_by_workstream: count members with count > 0 per workstream.
    contributors_by_workstream: Dict[str, int] = {ws: 0 for ws in matrix.workstreams}
    for member in matrix.members:
        member_counts = matrix.counts.get(member, {})
        for ws in matrix.workstreams:
            if member_counts.get(ws, 0) > 0:
                contributors_by_workstream[ws] += 1

    # Compute team_median_total over active members only (total > 0).
    active_totals = [total for total in total_by_member.values() if total > 0]
    team_median_total = statistics.median(active_totals) if active_totals else 0.0

    # Compute grid_max: largest single cell value in the whole matrix.
    grid_max = 0
    for member_counts in matrix.counts.values():
        for ws, count in member_counts.items():
            if ws in workstream_set:
                grid_max = max(grid_max, count)

    # Compute over_allocated: members with total > 2.0 * team_median_total.
    over_allocated: Set[str] = set()
    for member, total in total_by_member.items():
        if total > OVER_ALLOCATED_THRESHOLD * team_median_total:
            over_allocated.add(member)

    # Compute under_allocated: members with 0 < total < 0.5 * team_median_total.
    under_allocated: Set[str] = set()
    for member, total in total_by_member.items():
        if 0 < total < UNDER_ALLOCATED_THRESHOLD * team_median_total:
            under_allocated.add(member)

    # Compute narrow: members where a single workstream is > 70% of their total.
    narrow: Set[str] = set()
    for member, total in total_by_member.items():
        if total > 0:
            member_counts = matrix.counts.get(member, {})
            for ws in matrix.workstreams:
                if member_counts.get(ws, 0) > NARROW_THRESHOLD * total:
                    narrow.add(member)
                    break

    return AllocationSignals(
        total_by_member=total_by_member,
        total_by_workstream=total_by_workstream,
        contributors_by_workstream=contributors_by_workstream,
        team_median_total=team_median_total,
        grid_max=grid_max,
        over_allocated=over_allocated,
        under_allocated=under_allocated,
        narrow=narrow,
    )
