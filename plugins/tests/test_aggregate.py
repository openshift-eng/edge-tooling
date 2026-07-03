"""Tests for the deterministic overlay in plugins/shared/scripts/aggregate.py.

Aggregation must start from the detected-job list written by the prepare
phase (jobs/release-<v>-jobs.json, jobs/prs-jobs.json) so that a detected
failed job can never silently vanish from the summary when its analysis
report is missing or unparseable.

Run: python3 -m unittest discover -s plugins/tests -v
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "shared" / "scripts"


def _report(job_name, job_url, release="4.99", severity=3):
    entry = {
        "severity": severity,
        "stack_layer": "test",
        "step_name": "e2e-test",
        "error_signature": f"failure in {job_name}",
        "root_cause": "something broke",
        "raw_error": "boom",
        "infrastructure_failure": False,
        "remediation": "fix it",
        "confidence": "high",
        "job_name": job_name,
        "job_url": job_url,
        "release": release,
        "finished": "2026-07-01",
        "analysis_gaps": [],
        "scenarios": [],
        "causal_chain": [],
    }
    return (
        "Analysis prose.\n\n--- STRUCTURED SUMMARY ---\n"
        + json.dumps([entry])
        + "\n--- END STRUCTURED SUMMARY ---\n"
    )


def _detected(job, url, build_id, **extra):
    return {"job": job, "url": url, "build_id": build_id,
            "artifacts_dir": f"/x/{build_id}", **extra}


def _run(workdir, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "aggregate.py"),
         *args, "--workdir", str(workdir)],
        capture_output=True, text=True)


class AggregateOverlayTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workdir = Path(self._tmp.name)
        self.jobs_dir = self.workdir / "jobs"
        self.jobs_dir.mkdir()

    def _release_summary(self):
        return json.loads(
            (self.jobs_dir / "release-4.99-summary.json").read_text())

    def test_release_missing_analyses_become_placeholders(self):
        detected = [
            _detected("j-analyzed", "https://prow/111", "111"),
            _detected("j-lost", "https://prow/222", "222"),
            _detected("j-dl-failed", "https://prow/333", "333",
                      artifacts_dir="",
                      download_error="artifact download failed"),
        ]
        (self.jobs_dir / "release-4.99-jobs.json").write_text(
            json.dumps(detected))
        (self.jobs_dir / "release-4.99-job-1-111.txt").write_text(
            _report("j-analyzed", "https://prow/111"))

        result = _run(self.workdir, "--release", "4.99")
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = self._release_summary()

        self.assertEqual(summary["total_failed"], 3)
        self.assertEqual(len(summary["issues"]), 1)
        self.assertEqual(
            summary["issues"][0]["affected_jobs"][0]["build_id"], "111")

        reasons = {m["build_id"]: m["reason"]
                   for m in summary["missing_analysis"]}
        self.assertEqual(reasons, {
            "222": "analysis report missing or unparseable",
            "333": "artifact download failed",
        })

    def test_release_placeholders_have_no_fingerprint(self):
        # Downstream tooling keys on issue fingerprints; placeholders must
        # never grow one.
        (self.jobs_dir / "release-4.99-jobs.json").write_text(
            json.dumps([_detected("j-lost", "https://prow/222", "222")]))

        result = _run(self.workdir, "--release", "4.99")
        self.assertEqual(result.returncode, 0, result.stderr)
        for placeholder in self._release_summary()["missing_analysis"]:
            self.assertNotIn("fingerprint", placeholder)
            self.assertNotIn("severity", placeholder)

    def test_release_empty_detected_list_writes_zero_summary(self):
        (self.jobs_dir / "release-4.99-jobs.json").write_text("[]")

        result = _run(self.workdir, "--release", "4.99")
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = self._release_summary()
        self.assertEqual(summary["total_failed"], 0)
        self.assertEqual(summary["issues"], [])
        self.assertEqual(summary["missing_analysis"], [])

    def test_release_zero_parsed_reports_still_succeeds(self):
        (self.jobs_dir / "release-4.99-jobs.json").write_text(
            json.dumps([_detected("j-a", "https://prow/111", "111"),
                        _detected("j-b", "https://prow/222", "222")]))
        (self.jobs_dir / "release-4.99-job-1-111.txt").write_text(
            "no summary block")

        result = _run(self.workdir, "--release", "4.99")
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = self._release_summary()
        self.assertEqual(summary["total_failed"], 2)
        self.assertEqual(summary["issues"], [])
        self.assertEqual(len(summary["missing_analysis"]), 2)

    def test_release_collection_error_writes_no_summary(self):
        # create-report.py renders release-<v>-error.txt only when the
        # summary file is absent; writing one would mask the collection
        # error as "0 failures".
        (self.jobs_dir / "release-4.99-jobs.json").write_text("[]")
        (self.jobs_dir / "release-4.99-error.txt").write_text(
            "collection blew up")

        result = _run(self.workdir, "--release", "4.99")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(
            (self.jobs_dir / "release-4.99-summary.json").exists())

    def test_release_without_detected_list_keeps_legacy_behavior(self):
        (self.jobs_dir / "release-4.99-job-1-111.txt").write_text(
            _report("j-analyzed", "https://prow/111"))

        result = _run(self.workdir, "--release", "4.99")
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = self._release_summary()
        self.assertEqual(summary["total_failed"], 1)
        self.assertEqual(summary["missing_analysis"], [])

    def test_release_no_inputs_at_all_errors(self):
        result = _run(self.workdir, "--release", "4.99")
        self.assertEqual(result.returncode, 1)

    def test_pr_mode_overlay(self):
        detected = [
            _detected("j-pr42-failed", "https://prow/444", "444",
                      pr_number=42, status="FAILURE"),
            _detected("j-pr42-passed", "https://prow/555", "555",
                      pr_number=42, status="SUCCESS"),
            _detected("j-pr43-failed", "https://prow/666", "666",
                      pr_number=43, status="FAILURE"),
        ]
        (self.jobs_dir / "prs-jobs.json").write_text(json.dumps(detected))
        (self.jobs_dir / "prs-job-1-pr42-444.txt").write_text(
            _report("j-pr42-failed", "https://prow/444"))

        result = _run(self.workdir, "--prs")
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads((self.jobs_dir / "prs-summary.json").read_text())

        # Only FAILURE entries count as detected.
        self.assertEqual(summary["total_failed"], 2)
        prs = {pr["number"]: pr for pr in summary["prs"]}

        self.assertEqual(prs[42]["failed"], 1)
        self.assertEqual(prs[42]["missing_analysis"], [])
        self.assertEqual(
            prs[42]["issues"][0]["affected_jobs"][0]["build_id"], "444")

        # PR 43 had no parsed report at all but must still appear.
        self.assertEqual(prs[43]["failed"], 1)
        self.assertEqual(prs[43]["issues"], [])
        self.assertEqual(prs[43]["missing_analysis"][0]["build_id"], "666")
        self.assertEqual(prs[43]["missing_analysis"][0]["reason"],
                         "analysis report missing or unparseable")

    def test_pr_mode_without_detected_list_keeps_legacy_behavior(self):
        (self.jobs_dir / "prs-job-1-pr42-444.txt").write_text(
            _report("j-pr42-failed", "https://prow/444"))

        result = _run(self.workdir, "--prs")
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads((self.jobs_dir / "prs-summary.json").read_text())
        self.assertEqual(summary["total_failed"], 1)
        self.assertEqual(summary["prs"][0]["failed"], 1)


if __name__ == "__main__":
    unittest.main()
