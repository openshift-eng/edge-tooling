# Contribution metrics

Every metric name below says what it counts. Nothing here is a coined term you
have to look up.

The counts derive from a single **binary contribution matrix** `M`, where
`M[member][workstream] = 1` iff the member has at least one activity item
attributed to that workstream during the reporting window, and `0` otherwise.

There are six canonical workstreams (see `bin/workstream_map.py`): **SNO, TNA,
TNF, LVMS, USHIFT, TOPO**. Workstreams touched is always reported "of 6".

An activity item is attributed to a workstream by mapping its Jira component(s)
through `workstream_map.component_to_workstream`. Items that map to nothing (an
unknown component, or the `Planning` cutline component) are **unattributed**:
they are counted and reported separately, but never placed in the matrix.

## Workstreams touched (per member)

The number of distinct workstreams a member touched:

```text
workstreams_touched(m) = sum over w of M[m][w]
```

Reported as "N of 6".

## Total touches (team)

The number of 1s in the matrix — equivalently, the sum of every member's
workstreams-touched count:

```text
total_touches = sum over m, w of M[m][w]
```

This is **not** the number of activity records. That is the sum of the weighted
`counts` matrix, reported as `total_by_member` in the allocation signals.

## Mean people per workstream (team scalar)

The average number of distinct contributors a canonical workstream had:

```text
mean_people_per_workstream = total_touches / 6
```

A high value means the team is broadly cross-trained; a low value flags
workstreams with thin coverage.

The per-workstream contributor count itself lives in the allocation signals as
`contributors_by_workstream`, which is computed from the weighted `counts`
matrix and also covers SHARED. It is exported as `contributors:<workstream>` in
`metrics.csv`. There is deliberately only one per-workstream contributor count;
an earlier second name for it was removed.

## Mean workstreams per person (team scalar)

The average workstreams-touched count over **active** members (those with at
least one contribution), reported alongside the active/total roster count:

```text
mean_workstreams_per_person = total_touches / active_count   (0.0 when active_count == 0)
```

Averaging over active members answers "when someone contributes, across how many
workstreams do they spread?" without inactive members deflating the number.

## Edge-case contract

- **Empty roster / no workstreams** → both team scalars are `0.0` (no division by
  zero).
- **All-zero matrix** → `active_count == 0` and both team scalars are `0.0`.
- **Single active member** → `mean_workstreams_per_person ==
  workstreams_touched(that member)`.
- **Zero-contributor workstream** → still present in
  `contributors_by_workstream` as `0`, in canonical order.

These are enforced by `bin/tests/test_metrics.py`.
