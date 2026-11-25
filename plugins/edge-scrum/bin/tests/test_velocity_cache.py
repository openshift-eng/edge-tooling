"""Tests for velocity_cache: the optional closed-sprint aggregate store (synthetic data)."""

import datetime as dt
import importlib.util
import os
import sys
import unittest

BIN = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, BIN)
import velocity_cache as vc  # noqa: E402

_spec = importlib.util.spec_from_file_location("compute_velocity", os.path.join(BIN, "compute-velocity.py"))
cv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cv)

ROSTER = {"members": [{"username": "aexample@redhat.com", "display_name": "Alex Example"},
                      {"username": "qquality@redhat.com", "display_name": "Quinn Quality"}]}
DIGEST = vc.roster_digest(ROSTER)
BOARD = 1000


def sprint(n, state="closed"):
    start = dt.date(2030, 1, 7) + dt.timedelta(weeks=3 * (n - 10))
    return {"id": str(490 + n), "name": f"Test Sprint {n}", "number": n, "state": state,
            "start": start, "end": start + dt.timedelta(weeks=3)}


def sprint_doc(n, done_sp):
    """A transform-sprint-issues document: done team issues finishing in sprint ``n``."""
    s = sprint(n)
    return {"sprint_id": int(s["id"]), "sprint_name": s["name"], "sprint_start": s["start"].isoformat(),
            "sprint_end": s["end"].isoformat(),
            "issues": [{"key": f"TEST-{n}{i}", "type": "Story", "status": "Closed", "sp": sp,
                        "assignee_display": "Alex Example", "assignee": "aexample@redhat.com",
                        "sprints": [int(s["id"])],
                        "sprint_details": [{"id": int(s["id"]), "end": s["end"].isoformat()}]}
                       for i, sp in enumerate(done_sp)]}


CLOSED = [sprint(11), sprint(12), sprint(13)]
DOCS = [sprint_doc(11, [3, 5]), sprint_doc(12, [8]), sprint_doc(13, [2, 2, 1])]


def entries_for(docs, closed):
    """The entries a cold run would create for these sprints."""
    fresh = cv.compute_velocity(docs, ROSTER)
    return vc.creates(closed, fresh, [s["id"] for s in closed], {}, BOARD, DIGEST)


class VelocityCacheTest(unittest.TestCase):
    def test_combined_figures_equal_compute_velocity(self):
        cold = cv.compute_velocity(DOCS, ROSTER)
        store = entries_for(DOCS, CLOSED)
        for cached in (0, 1, 2, 3):  # any mix of stored and fetched sprints
            with self.subTest(cached=cached):
                hits, to_fetch = vc.plan(CLOSED, {k: v for n, (k, v) in enumerate(store.items()) if n < cached},
                                         BOARD, DIGEST)
                self.assertEqual([s["id"] for s in to_fetch], [s["id"] for s in CLOSED[cached:]])
                fresh = cv.compute_velocity(DOCS[cached:], ROSTER)
                combined = vc.combine(CLOSED, hits, fresh)
                for field in ("team_velocity", "sprint_count", "skipped_no_sprint_data", "unmatched_done_sp"):
                    self.assertEqual(combined[field], cold[field], field)
                self.assertEqual([r["done_team_sp"] for r in combined["sprints"]],
                                 [r["done_team_sp"] for r in cold["sprints"]])

    def test_entries_hold_aggregates_only(self):
        for key, entry in entries_for(DOCS, CLOSED).items():
            self.assertTrue(key.startswith(f"{vc.KEY_PREFIX}/{BOARD}/"))
            self.assertEqual(set(entry), {"schema", "board", "sprint_id", "sprint_end", "state", "roster",
                                          "complete", *vc.FIGURES})
            self.assertNotIn("TEST-", repr(entry))  # no issue keys
            self.assertNotIn("Example", repr(entry))  # no people

    def test_only_complete_closed_sprints_are_stored(self):
        figures = {"done_team_sp": 8, "team_done_issues": 2, "skipped_no_sprint_data": 0, "unmatched_done_sp": 0}
        self.assertIsNotNone(vc.make_entry(BOARD, sprint(12), figures, DIGEST, True))
        self.assertIsNone(vc.make_entry(BOARD, sprint(12), figures, DIGEST, False))  # incomplete fetch
        self.assertIsNone(vc.make_entry(BOARD, sprint(14, "active"), figures, DIGEST, True))  # not closed
        for bad in ({"done_team_sp": -1}, {"done_team_sp": True}, {"team_done_issues": 1.5}, {"done_team_sp": None},
                    {"skipped_no_sprint_data": 1}):  # velocity not usable: read again once the data is fixed
            with self.subTest(bad=bad):
                self.assertIsNone(vc.make_entry(BOARD, sprint(12), dict(figures, **bad), DIGEST, True))
        fresh = cv.compute_velocity(DOCS, ROSTER)
        complete_only = vc.creates(CLOSED, fresh, [CLOSED[0]["id"]], {}, BOARD, DIGEST)
        self.assertEqual(len(complete_only), 1)  # only the complete fetches are stored

    def test_an_entry_is_used_only_for_its_board_sprint_schema_and_roster(self):
        store = entries_for(DOCS, CLOSED)
        key = vc.entry_key(BOARD, CLOSED[1]["id"], DIGEST)
        good = store[key]
        for change in ({"schema": 2}, {"board": "1001"}, {"sprint_id": "999"}, {"sprint_end": "2000-01-01"},
                       {"state": "active"}, {"roster": "other"}, {"complete": False}, {"done_team_sp": "8"}):
            with self.subTest(change=change):
                _, to_fetch = vc.plan(CLOSED, dict(store, **{key: dict(good, **change)}), BOARD, DIGEST)
                self.assertEqual([s["id"] for s in to_fetch], [CLOSED[1]["id"]])  # only that sprint is read again
        other_roster = vc.roster_digest({"members": ROSTER["members"][:1]})
        self.assertNotEqual(other_roster, DIGEST)
        self.assertEqual(len(vc.plan(CLOSED, store, BOARD, other_roster)[1]), 3)  # a new roster reads every sprint

    def test_non_finite_figures_are_a_miss(self):
        store = entries_for(DOCS, CLOSED)
        key = vc.entry_key(BOARD, CLOSED[1]["id"], DIGEST)
        figures = {"done_team_sp": 8, "team_done_issues": 2, "skipped_no_sprint_data": 0, "unmatched_done_sp": 0}
        for field in ("done_team_sp", "unmatched_done_sp", "team_done_issues", "skipped_no_sprint_data"):
            for bad in (float("nan"), float("inf"), float("-inf"), 10 ** 400):  # 10 ** 400: no float holds it
                with self.subTest(field=field, value=bad):
                    hits, to_fetch = vc.plan(CLOSED, dict(store, **{key: dict(store[key], **{field: bad})}),
                                             BOARD, DIGEST)  # never raises
                    self.assertEqual([s["id"] for s in to_fetch], [CLOSED[1]["id"]])  # read again
                    self.assertNotIn(CLOSED[1]["id"], hits)
                    self.assertIsNone(vc.make_entry(BOARD, sprint(12), dict(figures, **{field: bad}), DIGEST, True))

    def test_no_store_or_unreadable_store_is_cold(self):
        hits, to_fetch = vc.plan(CLOSED, None, BOARD, DIGEST)
        self.assertEqual((hits, len(to_fetch)), ({}, 3))
        self.assertEqual(vc.creates(CLOSED, cv.compute_velocity(DOCS, ROSTER), [s["id"] for s in CLOSED], None,
                                    BOARD, DIGEST), {})  # nothing is written without a store

    def test_a_newly_closed_sprint_is_the_only_read(self):
        last_week = entries_for(DOCS[:2], CLOSED[:2])  # sprints 11 and 12 measured before 13 closed
        hits, to_fetch = vc.plan(CLOSED, last_week, BOARD, DIGEST)
        self.assertEqual([s["id"] for s in to_fetch], [CLOSED[2]["id"]])
        self.assertEqual(sorted(hits), [CLOSED[0]["id"], CLOSED[1]["id"]])

    def test_creates_never_overwrite(self):
        store = entries_for(DOCS, CLOSED)
        key = vc.entry_key(BOARD, CLOSED[0]["id"], DIGEST)
        changed = dict(store, **{key: dict(store[key], done_team_sp=1)})
        again = vc.creates(CLOSED, cv.compute_velocity(DOCS, ROSTER), [s["id"] for s in CLOSED], changed, BOARD, DIGEST)
        self.assertEqual(again, {})  # every key is present: nothing to create
        absent = {key: None for key in store}  # what a store read gives for keys it does not hold
        self.assertEqual(len(vc.creates(CLOSED, cv.compute_velocity(DOCS, ROSTER), [s["id"] for s in CLOSED],
                                        absent, BOARD, DIGEST)), 3)
        concurrent = [entries_for(DOCS, CLOSED), entries_for(DOCS, CLOSED)]
        self.assertEqual(concurrent[0], concurrent[1])  # two runs create the same entries


if __name__ == "__main__":
    unittest.main()
