"""Unit tests for lib.prow module."""

import argparse
import io
import json
import os
import subprocess
import unittest
from unittest import mock

import prow_testing
from lib import prow

_AUTH_DIAGNOSTIC_SENTINELS = (
    "synthetic-token-do-not-use",
    "synthetic-api-key-do-not-use",
    "synthetic-user@example.invalid",
    "synthetic-host.example.invalid",
)
_AUTH_DIAGNOSTICS = "\n".join(_AUTH_DIAGNOSTIC_SENTINELS)
_AUTH_COMMANDS = {
    "gh": ["gh", "auth", "status"],
    "aws": ["aws", "sts", "get-caller-identity"],
    "gsutil": ["gsutil", "ls", "gs://test-platform-results-public/"],
}


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

    def test_rejects_major_below_4(self):
        with self.assertRaises(ValueError):
            prow.parse_version("3.22.0")

    def test_valid_5x_xyz(self):
        result = prow.parse_version("5.0.0")
        self.assertEqual(result["version"], "5.0.0")
        self.assertEqual(result["minor"], "5.0")
        self.assertEqual(result["branch"], "release-5.0")
        self.assertEqual(result["pr_title"], "[release-5.0] Release Testing 5.0.0")

    def test_valid_5x_rc(self):
        result = prow.parse_version("5.0.0-rc.0")
        self.assertEqual(result["version"], "5.0.0-rc.0")
        self.assertEqual(result["minor"], "5.0")
        self.assertEqual(result["release_type"], "RC")

    def test_valid_5x_ec(self):
        result = prow.parse_version("5.1.0-ec.3")
        self.assertEqual(result["version"], "5.1.0-ec.3")
        self.assertEqual(result["minor"], "5.1")
        self.assertEqual(result["release_type"], "EC")


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

    def test_5x_uses_422_jobs(self):
        jobs = prow.ci_jobs("5.0")
        self.assertEqual(len(jobs), 6)
        self.assertIn("e2e-aws-tests-bootc-release-el9", jobs)


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


class TestAuthChecks(unittest.TestCase):

    def _auth(self, binary, env=None, missing=False, returncode=0, timeout=False):
        credentials = {"AWS_PROFILE": "mock-profile"} if env is None else env
        with mock.patch.dict(os.environ, credentials, clear=True), \
             mock.patch.object(prow_testing.shutil, "which",
                               return_value=None if missing else "/mock/bin/tool") as lookup, \
             mock.patch.object(prow_testing.subprocess, "run", return_value=mock.Mock(
                 returncode=returncode, stdout=_AUTH_DIAGNOSTICS, stderr=_AUTH_DIAGNOSTICS,
             )) as run:
            if timeout:
                run.side_effect = subprocess.TimeoutExpired(
                    _AUTH_COMMANDS[binary], 30, output=_AUTH_DIAGNOSTICS,
                    stderr=_AUTH_DIAGNOSTICS,
                )
            result = getattr(prow_testing, f"_check_{binary}_auth")()
        self.assertEqual(result["check"], f"{binary}_auth")
        if run.called:
            run.assert_called_once_with(
                _AUTH_COMMANDS[binary], capture_output=True, text=True, timeout=30,
            )
        return result, lookup, run

    def _preflight(self, binary, timeout):
        def run(command, **kwargs):
            if command[0] == binary and timeout:
                raise subprocess.TimeoutExpired(
                    command, 30, output=_AUTH_DIAGNOSTICS, stderr=_AUTH_DIAGNOSTICS,
                )
            return mock.Mock(returncode=1 if command[0] == binary else 0,
                             stdout=_AUTH_DIAGNOSTICS, stderr=_AUTH_DIAGNOSTICS)

        output = io.StringIO()
        exit_code = 0
        with mock.patch.dict(os.environ, {"AWS_PROFILE": "mock-profile"}, clear=True), \
             mock.patch.object(prow_testing.shutil, "which", return_value="/mock/bin/tool"), \
             mock.patch.object(prow_testing.subprocess, "run", side_effect=run), \
             mock.patch.object(prow_testing, "_check_s3_rpms", return_value={
                 "check": "s3_rpms", "status": "PASS", "reason": "mock RPMs present",
             }) as cache_check, \
             mock.patch("sys.stdout", output):
            try:
                prow_testing.cmd_preflight(argparse.Namespace(version="4.22.1"))
            except SystemExit as error:
                exit_code = error.code
        result = json.loads(output.getvalue())
        self.assertEqual((result["action"], result["version"]), ("preflight", "4.22.1"))
        self.assertEqual(result["status"], "warn" if binary == "gsutil" else "fail")
        self.assertEqual(exit_code, 0 if binary == "gsutil" else 1)
        expected = {f"{name}_auth": "PASS" for name in _AUTH_COMMANDS}
        expected.update(s3_rpms="PASS")
        expected[f"{binary}_auth"] = "WARN" if binary == "gsutil" else "FAIL"
        if binary == "aws":
            expected["s3_rpms"] = "SKIP"
            cache_check.assert_not_called()
        else:
            cache_check.assert_called_once()
        self.assertEqual({check["check"]: check["status"] for check in result["checks"]}, expected)
        return output.getvalue()

    def test_auth_successes(self):
        cases = [(binary, None) for binary in _AUTH_COMMANDS]
        cases.append(("aws", {"AWS_ACCESS_KEY_ID": "mock-access-key",
                              "AWS_SECRET_ACCESS_KEY": "mock-secret-key"}))
        for binary, env in cases:
            with self.subTest(binary=binary, credentials=env):
                result, lookup, run = self._auth(binary, env=env)
                self.assertEqual(result["status"], "PASS")
                lookup.assert_called_once_with(binary)
                run.assert_called_once()

    def test_aws_uses_cli_provider_chain_without_environment_credentials(self):
        for env in ({}, {"AWS_SHARED_CREDENTIALS_FILE": "/example/credentials"},
                    {"AWS_CONFIG_FILE": "/example/config"}):
            with self.subTest(environment=env):
                result, lookup, run = self._auth("aws", env=env)
                self.assertEqual(result["status"], "PASS")
                lookup.assert_called_once_with("aws")
                run.assert_called_once()

    def test_aws_cli_reports_missing_or_invalid_credentials(self):
        result, _, run = self._auth("aws", env={}, returncode=255)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("AWS authentication check failed", result["reason"])
        run.assert_called_once()

    def test_missing_binaries(self):
        for binary in _AUTH_COMMANDS:
            with self.subTest(binary=binary):
                result, lookup, run = self._auth(binary, missing=True)
                self.assertEqual(result["status"], "WARN" if binary == "gsutil" else "FAIL")
                self.assertIn("not found", result["reason"])
                lookup.assert_called_once_with(binary)
                run.assert_not_called()

    def test_profile_with_access_key_without_secret_fails(self):
        result, _, run = self._auth("aws", env={
            "AWS_PROFILE": "mock-profile", "AWS_ACCESS_KEY_ID": "mock-access-key",
        }, returncode=255)
        self.assertEqual(result["status"], "FAIL")
        run.assert_called_once()

    def test_profile_with_secret_without_access_key_passes(self):
        result, _, run = self._auth("aws", env={
            "AWS_PROFILE": "mock-profile", "AWS_SECRET_ACCESS_KEY": "mock-secret-key",
        })
        self.assertEqual(result["status"], "PASS")
        run.assert_called_once()

    def test_auth_failures_do_not_expose_diagnostics(self):
        for binary in _AUTH_COMMANDS:
            for timeout in (False, True):
                with self.subTest(binary=binary, timeout=timeout):
                    result, _, run = self._auth(binary, returncode=1, timeout=timeout)
                    self.assertEqual(result["status"], "WARN" if binary == "gsutil" else "FAIL")
                    self.assertEqual(set(result), {"check", "status", "reason"})
                    self.assertIn("timed out after 30 seconds" if timeout else
                                  "authentication check failed", result["reason"])
                    run.assert_called_once()
                    emitted = json.dumps(result) + self._preflight(binary, timeout)
                    for sentinel in _AUTH_DIAGNOSTIC_SENTINELS:
                        self.assertNotIn(sentinel, emitted)


if __name__ == "__main__":
    unittest.main()
