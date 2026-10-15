#!/usr/bin/env python3
"""Tests for deterministic bug-stage orchestration."""

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[3] / "shared" / "scripts" / "run-doctor.py"
SPEC = importlib.util.spec_from_file_location("run_doctor_under_test", SCRIPT)
run_doctor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_doctor)


class FakeTimer:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    def cancel(self):
        pass


class FakeProcess:
    pid = 999999
    returncode = 1
    stdout = []

    def wait(self):
        return self.returncode


class BugStageTests(unittest.TestCase):
    def test_nonzero_search_process_fails_stage(self):
        with tempfile.TemporaryDirectory() as tempdir:
            pipeline = run_doctor.DoctorPipeline.__new__(run_doctor.DoctorPipeline)
            pipeline.component = "microshift"
            pipeline.releases = ["4.22"]
            pipeline.plugin_dir = Path(__file__).resolve().parents[2]
            pipeline.workdir = Path(tempdir)
            pipeline.logs_dir = Path(tempdir) / "logs"
            pipeline.logs_dir.mkdir()
            messages = []
            pipeline.message = messages.append
            with mock.patch.object(run_doctor.subprocess, "Popen",
                                   return_value=FakeProcess()), \
                    mock.patch.object(run_doctor.threading, "Timer", FakeTimer):
                self.assertFalse(pipeline.bugs())
            self.assertTrue(any("failed with exit code 1" in msg
                                for msg in messages))

    def test_failed_bug_stage_aborts_before_finalize(self):
        pipeline = run_doctor.DoctorPipeline.__new__(run_doctor.DoctorPipeline)
        pipeline.stages = ["bugs", "finalize"]
        pipeline.bugs = lambda: False
        finalized = []
        pipeline.finalize = lambda: finalized.append(True) or True
        pipeline.prepare = pipeline.graphs = pipeline.analyze = lambda: True
        pipeline.message = lambda message: None
        pipeline.compute_costs = lambda: None
        self.assertEqual(pipeline.run(), 1)
        self.assertEqual(finalized, [])


if __name__ == "__main__":
    unittest.main()
