"""Unit tests for lib.prow module."""

import unittest
from unittest import mock

from lib import prow


class TestParseVersion(unittest.TestCase):

    def test_valid_xyz(self):
        result = prow.parse_version("4.21.3")
        self.assertEqual(result["version"], "4.21.3")
        self.assertEqual(result["minor"], "4.21")
        self.assertEqual(result["branch"], "release-4.21")
        self.assertEqual(result["pr_title"], "[release-4.21] Release Testing 4.21.3")

    def test_valid_rc(self):
        result = prow.parse_version("4.22.0-rc.1")
        self.assertEqual(result["version"], "4.22.0-rc.1")
        self.assertEqual(result["minor"], "4.22")
        self.assertEqual(result["branch"], "release-4.22")

    def test_valid_ec(self):
        result = prow.parse_version("4.22.0-ec.5")
        self.assertEqual(result["version"], "4.22.0-ec.5")

    def test_tilde_normalization(self):
        result = prow.parse_version("4.22.0~ec.5")
        self.assertEqual(result["version"], "4.22.0-ec.5")

    def test_rejects_old_version(self):
        with self.assertRaises(ValueError) as ctx:
            prow.parse_version("4.18.3")
        self.assertIn("Jenkins", str(ctx.exception))

    def test_rejects_nightly(self):
        with self.assertRaises(ValueError) as ctx:
            prow.parse_version("4.21.0-nightly-2025-01-01")
        self.assertIn("nightly", str(ctx.exception))

    def test_rejects_invalid_format(self):
        with self.assertRaises(ValueError) as ctx:
            prow.parse_version("4.21")
        self.assertIn("Invalid version format", str(ctx.exception))

    def test_rejects_bare_minor(self):
        with self.assertRaises(ValueError):
            prow.parse_version("4.21")


class TestCiJobs(unittest.TestCase):

    def test_421_jobs(self):
        jobs = prow.ci_jobs("4.21")
        self.assertEqual(len(jobs), 4)
        self.assertIn("e2e-aws-tests-bootc-release", jobs)
        self.assertIn("e2e-aws-tests-bootc-release-arm", jobs)
        self.assertIn("e2e-aws-tests-release", jobs)
        self.assertIn("e2e-aws-tests-release-arm", jobs)

    def test_422_jobs(self):
        jobs = prow.ci_jobs("4.22")
        self.assertEqual(len(jobs), 6)
        self.assertIn("e2e-aws-tests-bootc-release-el9", jobs)
        self.assertIn("e2e-aws-tests-bootc-release-el10", jobs)
        self.assertIn("e2e-aws-tests-bootc-release-arm-el9", jobs)
        self.assertIn("e2e-aws-tests-bootc-release-arm-el10", jobs)

    def test_future_version_uses_422_jobs(self):
        jobs = prow.ci_jobs("4.25")
        self.assertEqual(len(jobs), 6)


class TestMatchCiJobs(unittest.TestCase):

    def test_all_matched_422(self):
        gcs_jobs = [
            "pull-ci-openshift-microshift-release-4.22-e2e-aws-tests-release",
            "pull-ci-openshift-microshift-release-4.22-e2e-aws-tests-release-arm",
            "pull-ci-openshift-microshift-release-4.22-e2e-aws-tests-bootc-release-el9",
            "pull-ci-openshift-microshift-release-4.22-e2e-aws-tests-bootc-release-el10",
            "pull-ci-openshift-microshift-release-4.22-e2e-aws-tests-bootc-release-arm-el9",
            "pull-ci-openshift-microshift-release-4.22-e2e-aws-tests-bootc-release-arm-el10",
        ]
        matched = prow.match_ci_jobs(gcs_jobs, "4.22")
        for short in prow.ci_jobs("4.22"):
            self.assertIsNotNone(matched[short], f"{short} should be matched")

    def test_all_matched_421(self):
        gcs_jobs = [
            "pull-ci-openshift-microshift-release-4.21-e2e-aws-tests-bootc-release",
            "pull-ci-openshift-microshift-release-4.21-e2e-aws-tests-bootc-release-arm",
            "pull-ci-openshift-microshift-release-4.21-e2e-aws-tests-release",
            "pull-ci-openshift-microshift-release-4.21-e2e-aws-tests-release-arm",
        ]
        matched = prow.match_ci_jobs(gcs_jobs, "4.21")
        for short in prow.ci_jobs("4.21"):
            self.assertIsNotNone(matched[short], f"{short} should be matched")

    def test_partial_match(self):
        gcs_jobs = [
            "pull-ci-openshift-microshift-release-4.22-e2e-aws-tests-release",
        ]
        matched = prow.match_ci_jobs(gcs_jobs, "4.22")
        self.assertIsNotNone(matched["e2e-aws-tests-release"])
        self.assertIsNone(matched["e2e-aws-tests-release-arm"])

    def test_empty_list(self):
        matched = prow.match_ci_jobs([], "4.22")
        for short in prow.ci_jobs("4.22"):
            self.assertIsNone(matched[short])

    def test_no_false_positives(self):
        gcs_jobs = [
            "pull-ci-openshift-microshift-release-4.22-e2e-aws-tests-release-arm",
        ]
        matched = prow.match_ci_jobs(gcs_jobs, "4.22")
        self.assertIsNone(matched["e2e-aws-tests-release"])
        self.assertIsNotNone(matched["e2e-aws-tests-release-arm"])


class TestGetPrCheckStatuses(unittest.TestCase):

    @mock.patch("lib.prow.subprocess.run")
    def test_maps_check_names_to_short_names(self, mock_run):
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout='[{"name":"pull-ci-openshift-microshift-release-4.22-e2e-aws-tests-release","state":"IN_PROGRESS"},'
                   '{"name":"pull-ci-openshift-microshift-release-4.22-e2e-aws-tests-release-arm","state":"COMPLETED"}]',
        )
        result = prow.get_pr_check_statuses(1234, "4.22")
        self.assertEqual(result["e2e-aws-tests-release"], "IN_PROGRESS")
        self.assertEqual(result["e2e-aws-tests-release-arm"], "COMPLETED")

    @mock.patch("lib.prow.subprocess.run")
    def test_returns_empty_on_failure(self, mock_run):
        mock_run.return_value = mock.Mock(returncode=1, stderr="error")
        result = prow.get_pr_check_statuses(1234, "4.22")
        self.assertEqual(result, {})

    @mock.patch("lib.prow.subprocess.run")
    def test_ignores_unrelated_checks(self, mock_run):
        mock_run.return_value = mock.Mock(
            returncode=0,
            stdout='[{"name":"tide","state":"COMPLETED"},{"name":"some-other-check","state":"IN_PROGRESS"}]',
        )
        result = prow.get_pr_check_statuses(1234, "4.22")
        self.assertEqual(result, {})


class TestFetchAllJobStatusesOverride(unittest.TestCase):
    """Verify that GitHub check statuses override stale GCS results."""

    @mock.patch("lib.prow.get_pr_check_statuses")
    @mock.patch("lib.prow.get_latest_build")
    def test_in_progress_overrides_stale_success(self, mock_build, mock_checks):
        mock_build.return_value = {
            "job": "pull-ci-openshift-microshift-release-4.22-e2e-aws-tests-release",
            "status": "SUCCESS",
            "url": "https://old-url",
            "build_id": "100",
        }
        mock_checks.return_value = {
            "e2e-aws-tests-release": "IN_PROGRESS",
        }

        matched = {"e2e-aws-tests-release": "full-job-name"}
        for short in prow.ci_jobs("4.22"):
            if short not in matched:
                matched[short] = None

        statuses = prow.fetch_all_job_statuses(1234, matched, "4.22")
        release_status = next(s for s in statuses if s["short_name"] == "e2e-aws-tests-release")
        self.assertEqual(release_status["status"], "PENDING")

    @mock.patch("lib.prow.get_pr_check_statuses")
    @mock.patch("lib.prow.get_latest_build")
    def test_completed_does_not_override(self, mock_build, mock_checks):
        mock_build.return_value = {
            "job": "pull-ci-openshift-microshift-release-4.22-e2e-aws-tests-release",
            "status": "SUCCESS",
            "url": "https://url",
            "build_id": "100",
        }
        mock_checks.return_value = {
            "e2e-aws-tests-release": "COMPLETED",
        }

        matched = {"e2e-aws-tests-release": "full-job-name"}
        for short in prow.ci_jobs("4.22"):
            if short not in matched:
                matched[short] = None

        statuses = prow.fetch_all_job_statuses(1234, matched, "4.22")
        release_status = next(s for s in statuses if s["short_name"] == "e2e-aws-tests-release")
        self.assertEqual(release_status["status"], "SUCCESS")


if __name__ == "__main__":
    unittest.main()
