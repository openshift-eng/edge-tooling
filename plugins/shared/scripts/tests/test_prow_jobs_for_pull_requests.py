"""Regression tests for prow-jobs-for-pull-requests.sh PR job listing.

Run with: python3 plugins/shared/scripts/tests/test_prow_jobs_for_pull_requests.py

Fake ``curl`` and ``gh`` executables shadow the real ones on PATH, so no
network request, PR comment or CI job is made. The fake ``curl`` answers
exact URLs from a routes file and records every requested URL.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
COLLECTOR = SCRIPTS / "prow-jobs-for-pull-requests.sh"
# Same symlinked entry point run-doctor.py uses for MicroShift
DOCTOR_HELPER = SCRIPTS.parent.parent / "microshift-ci" / "scripts" / "doctor-helper.sh"

PR = 7691
PUBLIC_API = "https://storage.googleapis.com/storage/v1/b/test-platform-results-public/o"
PUBLIC_BASE = "https://storage.googleapis.com/test-platform-results-public"
PUBLIC_PROW_VIEW = "https://prow.ci.openshift.org/view/gs/test-platform-results-public"
PR_PREFIX = f"pr-logs/pull/openshift_microshift/{PR}"
LIST_URL = f"{PUBLIC_API}?prefix={PR_PREFIX}/&delimiter=/"
PROW_DATA_URL = "https://prow.ci.openshift.org/data.js"
OPEN_PRS = [{
    "number": PR,
    "title": "NO-ISSUE: rebase-release-4.19 synthetic",
    "url": f"https://github.com/openshift/microshift/pull/{PR}",
}]

FAKE_CURL = """
import json
import os
import sys

args = sys.argv[1:]
output = write_out = url = None
index = 0
while index < len(args):
    arg = args[index]
    if arg in ("-o", "-w", "--max-time", "--retry", "--retry-delay"):
        if arg == "-o":
            output = args[index + 1]
        elif arg == "-w":
            write_out = args[index + 1]
        index += 2
        continue
    if not arg.startswith("-"):
        url = arg
    index += 1

with open(os.environ["FAKE_CURL_LOG"], "a") as log:
    log.write(url + "\\n")
with open(os.environ["FAKE_CURL_ROUTES"]) as routes:
    status, body, *exit_code = json.load(routes).get(
        url, [404, "<Error><Code>NoSuchKey</Code></Error>"])
if exit_code:
    # Transport failure: curl reports code 000 and exits nonzero
    sys.stderr.write(f"curl: ({exit_code[0]}) {body}\\n")
    if write_out:
        sys.stdout.write(write_out.replace("%{http_code}", "000"))
    sys.exit(exit_code[0])
if output:
    with open(output, "w") as out:
        out.write(body)
else:
    sys.stdout.write(body)
if write_out:
    sys.stdout.write(write_out.replace("%{http_code}", str(status)))
"""

FAKE_GH = """
import os
import sys

sys.stdout.write(os.environ["FAKE_GH_PRS"])
"""


def job_routes(job, build_id, result):
    """Routes for one PR job with a latest build and finished.json."""
    job_base = f"{PUBLIC_BASE}/{PR_PREFIX}/{job}"
    return {
        f"{job_base}/latest-build.txt": [200, build_id],
        f"{job_base}/{build_id}/finished.json": [
            200, json.dumps({"timestamp": 1794199782, "result": result})],
    }


class PullRequestJobListingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        for name, source in (("curl", FAKE_CURL), ("gh", FAKE_GH)):
            path = bin_dir / name
            path.write_text(f"#!{sys.executable}\n{source}")
            path.chmod(0o755)
        self.routes = self.root / "routes.json"
        self.curl_log = self.root / "curl.log"
        self.curl_log.write_text("")
        self.env = dict(os.environ)
        self.env.update({
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.root),
            "TMPDIR": str(self.root),
            "FAKE_CURL_ROUTES": str(self.routes),
            "FAKE_CURL_LOG": str(self.curl_log),
            "FAKE_GH_PRS": json.dumps(OPEN_PRS),
        })

    def run_script(self, routes, *command):
        self.routes.write_text(json.dumps(routes))
        return subprocess.run(
            ["bash", *map(str, command)],
            capture_output=True, text=True, env=self.env, check=False,
        )

    def run_detail(self, routes):
        return self.run_script(
            routes, COLLECTOR, "--component", "microshift", "--mode", "detail",
            "--author", "microshift-rebase-script[bot]")

    def run_prepare(self, routes):
        workdir = Path(tempfile.mkdtemp(prefix="workdir-", dir=self.root))
        proc = self.run_script(
            {PROW_DATA_URL: [200, "[]"], **routes}, DOCTOR_HELPER, "prepare",
            "--component", "microshift", "--workdir", workdir, "4.19",
            "--pull-requests")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout), workdir / "jobs"

    def test_populated_listing_reads_public_bucket(self):
        verify = "pull-ci-openshift-microshift-release-4.19-verify"
        e2e = "pull-ci-openshift-microshift-release-4.19-e2e-aws-tests"
        routes = {LIST_URL: [200, json.dumps({
            "kind": "storage#objects",
            "prefixes": [f"{PR_PREFIX}/{verify}/", f"{PR_PREFIX}/{e2e}/"],
        })]}
        routes.update(job_routes(verify, "101", "SUCCESS"))
        routes.update(job_routes(e2e, "102", "FAILURE"))

        proc = self.run_detail(routes)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        [pr] = json.loads(proc.stdout)
        self.assertEqual(pr["pr_number"], PR)
        jobs = {job["job"]: job for job in pr["jobs"]}
        self.assertEqual(set(jobs), {verify, e2e})
        for job, build_id, status in ((verify, "101", "SUCCESS"), (e2e, "102", "FAILURE")):
            self.assertEqual(jobs[job]["status"], status)
            self.assertEqual(jobs[job]["build_id"], build_id)
            self.assertEqual(jobs[job]["url"], f"{PUBLIC_PROW_VIEW}/{PR_PREFIX}/{job}/{build_id}")
        requested = self.curl_log.read_text().split()
        self.assertTrue(requested)
        for url in requested:
            self.assertTrue(url.startswith((PUBLIC_API, f"{PUBLIC_BASE}/")), url)

    def test_empty_listing_is_valid_no_jobs_result(self):
        routes = {LIST_URL: [200, json.dumps({"kind": "storage#objects"})]}

        proc = self.run_detail(routes)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)[0]["jobs"], [])
        self.assertNotIn("Error", proc.stderr)

        summary, jobs_dir = self.run_prepare(routes)
        self.assertNotIn("error", summary["prs"])
        self.assertFalse((jobs_dir / "prs-error.txt").exists())
        [status] = json.loads((jobs_dir / "prs-status.json").read_text())
        self.assertEqual(status["total"], 0)

    def test_failed_listing_reaches_prepare_collection_error(self):
        cases = {
            "http-401": ([401, json.dumps({"error": {
                "code": 401, "message": "Anonymous caller does not have storage.objects.list access"}})],
                ["HTTP 401", "storage.objects.list access"]),
            "api-error-object": ([200, json.dumps({"error": {
                "code": 400, "message": "Invalid argument"}})],
                ["not a GCS object listing", "Invalid argument"]),
            "transport-failure": ([0, "Could not resolve host", 6],
                ["job listing request failed", "Could not resolve host"]),
        }
        for name, (response, expected) in cases.items():
            with self.subTest(name):
                routes = {LIST_URL: response}
                fragments = [f"PR #{PR}", *expected]

                proc = self.run_detail(routes)
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(proc.stdout, "")
                for fragment in fragments:
                    self.assertIn(fragment, proc.stderr)

                summary, jobs_dir = self.run_prepare(routes)
                error_file = jobs_dir / "prs-error.txt"
                self.assertTrue(error_file.exists())
                for fragment in fragments:
                    self.assertIn(fragment, summary["prs"]["error"])
                    self.assertIn(fragment, error_file.read_text())
                self.assertEqual(json.loads((jobs_dir / "prs-status.json").read_text()), [])


if __name__ == "__main__":
    unittest.main()
