"""Tests for the sprint day counts shared by transform-sprints.py and chai_report.py.

Synthetic dates only.
"""

import os
import sys
import unittest
from importlib import import_module

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from _jira_transforms import sprint_day_counts  # noqa: E402

transform_sprints = import_module("transform-sprints")
SPRINT = {"id": 501, "name": "Test Sprint 11", "goal": None, "start": "2030-01-28", "end": "2030-02-18",
          "state": "active"}


class SprintDayCountsTest(unittest.TestCase):
    def test_remaining_is_total_minus_elapsed(self):
        self.assertEqual(sprint_day_counts("2030-01-28", "2030-02-18", "2030-02-07"), (11, 11, 22))
        self.assertEqual(sprint_day_counts("2030-01-28", "2030-02-18", "2030-01-28"), (1, 21, 22))
        self.assertEqual(sprint_day_counts("2030-01-28", "2030-02-18", "2030-02-18"), (22, 0, 22))

    def test_clamped_outside_the_sprint(self):
        self.assertEqual(sprint_day_counts("2030-01-28", "2030-02-18", "2030-01-01"), (0, 22, 22))
        self.assertEqual(sprint_day_counts("2030-01-28", "2030-02-18", "2030-03-30"), (22, 0, 22))

    def test_build_target_sprint_uses_the_shared_definition(self):
        target = transform_sprints.build_target_sprint(SPRINT, "2030-02-07")
        self.assertEqual((target["days_elapsed"], target["days_remaining"], target["total_days"]), (11, 11, 22))
        self.assertEqual(target["days_elapsed"] + target["days_remaining"], target["total_days"])


if __name__ == "__main__":
    unittest.main()
