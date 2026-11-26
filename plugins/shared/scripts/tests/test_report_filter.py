"""Report search probes: counts are rendered groups/jobs, scoped to tab and day.

Run with: python3 plugins/shared/scripts/tests/test_report_filter.py
The Node harness runs the actual embedded renderer with a dependency-free DOM.
"""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("report", SCRIPTS / "create-report.py")
REPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)
MISSING = object()  # fixture sentinel: leave the field out of the Prow status entry


def job_summary(text, status_class, visible):
    """Expected All Jobs summary text, colour class and visible job names for one release."""
    return {"summary": "All Jobs \u2014 " + text,
            "className": "jobs-pass-rate" + status_class, "visible": sorted(visible)}


class ReportFilterTests(unittest.TestCase):
    def test_literal_placeholder_and_existing_causal_chain_model(self):
        chain = [{"cause": "cause", "evidence": "evidence", "quote": "quote"}]
        issue = {"number": 1, "title": "add", "causal_chain": chain}
        html = REPORT.generate_html(
            "Probe", {"4.20": {"issues": [issue]}}, [], None, None,
            datetime(2026, 10, 19, tzinfo=timezone.utc),
        )
        self.assertIn('placeholder="Filter… (regex: title, root cause, causal chain, job name)"', html)
        self.assertNotIn(r"\u2026", html)
        self.assertNotIn('id="filter-regex"', html)
        self.assertNotIn('Regex mode', html)
        self.assertNotIn('literal matching', html)
        self.assertIn('aria-describedby="report-filter-error" aria-invalid="false"', html)
        self.assertIn('id="report-filter-error" class="filter-error" role="alert"', html)
        payload = html.split("window.REPORT_DATA = ", 1)[1].split(";</script>", 1)[0]
        data = json.loads(payload)
        self.assertEqual(data["releases_data"]["4.20"]["issues"][0]["causal_chain"], chain)

    def test_embedded_renderer_filters_and_counters(self):
        with tempfile.TemporaryDirectory() as temporary:
            renderer = Path(temporary) / "renderer.js"
            renderer.write_text(REPORT.JS)
            html = Path(temporary) / "report.html"
            html.write_text(REPORT.generate_html(
                "Probe", {}, [], None, None, datetime(2026, 10, 19, tzinfo=timezone.utc),
            ))
            result = subprocess.run(
                ["node", str(Path(__file__).with_name("test_report_filter.js")), str(renderer), "", "--regex-tests", str(html)],
                capture_output=True, text=True, check=False,
            )
            finished = subprocess.run(
                ["node", str(Path(__file__).with_name("test_report_filter.js")), str(renderer), "", "--regex-tests", str(html), "--finished-tests"],
                capture_output=True, text=True, check=False,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)
        self.assertIn("PASS Finished regressions", finished.stdout)
        self.assertIn("PASS renderer filter regressions", result.stdout)
        self.assertIn("PASS isolated regex regressions", result.stdout)
        print(result.stdout.strip())
        print(finished.stdout.strip())

    def test_supplied_finished_timestamp_representations(self):
        values = [
            "2026-10-15T01:21:52Z", 1792027312, "1792027312", 0, "0",
            -1, "-1", 0.5, "0.5", " 0 ", "2026-10-15T03:21:52+02:00",
            "2026-10-14T23:21:52-02:00", "2024-02-29T00:00:00Z",
            "", " ", None, "not-a-date", "2026junk", "2026-02-30T00:00:00Z",
            "2026-02-29T00:00:00Z", "2026-10-15T01:21:52", "2026-10-15T24:00:00Z",
            "NaN", "Infinity", 1e30, 1e308, "9" * 400, True, {}, [],
        ]
        jobs = [
            {"job": f"supplied-{index}", "url": f"https://example.invalid/supplied-{index}",
             "status": "success", "finished": value}
            for index, value in enumerate(values)
        ]
        jobs.append({"job": "missing-finished", "status": "success"})
        with tempfile.TemporaryDirectory() as temporary:
            renderer = Path(temporary) / "renderer.js"
            renderer.write_text(REPORT.JS)
            supplied = Path(temporary) / "supplied.json"
            supplied.write_text(json.dumps({"status_data": {"mixed": jobs}}))
            html = Path(temporary) / "report.html"
            html.write_text(REPORT.generate_html(
                "Probe", {}, [], None, None, datetime(2026, 10, 19, tzinfo=timezone.utc),
                status_data={"mixed": jobs},
            ))
            result = subprocess.run(
                ["node", str(Path(__file__).with_name("test_report_filter.js")), str(renderer),
                 str(supplied), "--regex-tests", str(html), "--finished-tests"],
                capture_output=True, text=True, check=False,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"PASS supplied Finished timestamps: {len(jobs)} actual job rows", result.stdout)
        print(result.stdout.strip())

    def run_jobs_table_harness(self, fixtures, search, today):
        """Run create-report.py on synthetic Prow status files and drive the All Jobs tables.

        fixtures maps a release to (job, finished, status, Prow duration, Duration cell)
        tuples; MISSING leaves the finished or duration field out of the status file.
        today is the UTC date the fixtures used for Today only.
        """
        def prow_job(name, finished, status, duration):
            job = {"job": name, "type": "periodic", "status": status,
                   "url": f"https://example.invalid/{name}"}
            if finished is not MISSING:
                job["finished"] = finished
            if duration is not MISSING:
                job["duration"] = duration
            return job

        with tempfile.TemporaryDirectory() as temporary:
            workdir = Path(temporary)
            (workdir / "jobs").mkdir()
            for version, jobs in fixtures.items():
                (workdir / "jobs" / f"release-{version}-summary.json").write_text(
                    json.dumps({"total_failed": 0, "issues": []}))
                (workdir / "jobs" / f"release-{version}-status.json").write_text(json.dumps([
                    prow_job(name, finished, status, duration)
                    for name, finished, status, duration, _ in jobs]))
            generated = subprocess.run(
                [sys.executable, str(SCRIPTS / "create-report.py"), "--component", "microshift",
                 "--workdir", str(workdir), ",".join(fixtures)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(generated.returncode, 0, generated.stdout + generated.stderr)
            html = workdir / "report-microshift-ci-doctor.html"
            renderer_source = html.read_text().rsplit("<script>\n", 1)[1].split("\n</script>", 1)[0]
            self.assertEqual(renderer_source, REPORT.JS)
            renderer = workdir / "renderer.js"
            renderer.write_text(renderer_source)
            result = subprocess.run(
                ["node", str(Path(__file__).with_name("test_report_filter.js")), str(renderer),
                 "", "", str(html), "--jobs-table-tests", search],
                capture_output=True, text=True, check=False,
            )
        if datetime.now(timezone.utc).date().isoformat() != today:
            self.skipTest("UTC date changed during the run; Today only fixtures are stale")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout.split("JOBS TABLE ", 1)[1].splitlines()[0])
        self.assertEqual(report["durations"], {
            name: expected for jobs in fixtures.values() for name, *_, expected in jobs})
        return report, result.stdout

    def test_generated_report_job_durations_and_today_pass_counts(self):
        today = datetime.now(timezone.utc).date().isoformat()
        old = "2020-01-01"
        epoch = int(datetime.fromisoformat(f"{today}T03:00:00+00:00").timestamp())
        # (job, finished, status, Prow duration, expected Duration cell); only today-* finish today.
        fixtures = {
            "4.20": [
                ("today-go-duration", f"{today}T01:00:00Z", "success", "2h5m22s", "2h 5m"),
                ("today-numeric-seconds", f"{today}T02:00:00Z", "success", 7522, "2h 5m"),
                ("today-numeric-text", epoch, "success", "60", "1m 0s"),
                ("today-fractional", f"{today}T04:00:00Z", "success", "45m3.5s", "45m 3s"),
                ("today-failure", f"{today}T05:00:00Z", "failure", "59m59s", "59m 59s"),
                ("running", MISSING, "pending", MISSING, "\u2014"),
                ("old-hour", f"{old}T01:00:00Z", "success", "1h0m0s", "1h 0m"),
                ("old-zero", f"{old}T02:00:00Z", "failure", "0s", "0m 0s"),
                ("old-milliseconds", f"{old}T02:00:00Z", "failure", "90500ms", "1m 30s"),
                ("old-micro-sign", f"{old}T02:00:00Z", "failure", "90000000\u00b5s", "1m 30s"),
                ("old-greek-mu", f"{old}T02:00:00Z", "failure", "90000000\u03bcs", "1m 30s"),
                ("old-microseconds", f"{old}T02:00:00Z", "failure", "90000000us", "1m 30s"),
                ("old-nanoseconds", f"{old}T02:00:00Z", "failure", "90000000000ns", "1m 30s"),
                ("old-padded-text", f"{old}T02:00:00Z", "failure", " 90.9 ", "1m 30s"),
                ("old-null", f"{old}T03:00:00Z", "failure", None, "\u2014"),
                ("old-empty", f"{old}T03:00:00Z", "failure", "", "\u2014"),
                ("old-invalid", f"{old}T03:00:00Z", "failure", "soon", "\u2014"),
                ("old-negative", f"{old}T03:00:00Z", "failure", -5, "\u2014"),
                ("old-negative-go", f"{old}T03:00:00Z", "failure", "-1h", "\u2014"),
                ("old-partial", f"{old}T03:00:00Z", "failure", "1h2", "\u2014"),
                ("old-spaced", f"{old}T03:00:00Z", "failure", "1h 2m", "\u2014"),
                ("old-boolean", f"{old}T03:00:00Z", "failure", True, "\u2014"),
            ],
            "4.21": [
                ("old-only", f"{old}T06:00:00Z", "success", "1h2m3s", "1h 2m"),
            ],
        }
        report, stdout = self.run_jobs_table_harness(fixtures, "failure|go-duration|old-hour", today)

        names = [name for name, *_ in fixtures["4.20"]]
        today_names = [name for name in names if name.startswith("today-")]
        no_jobs = job_summary("0/0 passed", "", [])
        all_jobs = {"4.20": job_summary("5/22 passed (23%)", " status-fail", names),
                    "4.21": job_summary("1/1 passed (100%)", " status-pass", ["old-only"])}
        today_jobs = {"4.20": job_summary("4/5 passed (80%)", "", today_names), "4.21": no_jobs}
        search_jobs = {"4.20": job_summary("2/3 passed (67%)", " status-fail",
                                           ["old-hour", "today-failure", "today-go-duration"]),
                       "4.21": no_jobs}
        today_search_jobs = {"4.20": job_summary("1/2 passed (50%)", " status-fail",
                                                 ["today-failure", "today-go-duration"]),
                             "4.21": no_jobs}
        self.assertEqual(report["states"], {
            "initial": all_jobs, "today": today_jobs, "reset": all_jobs, "todayAgain": today_jobs,
            "todaySearch": today_search_jobs, "search": search_jobs, "cleared": all_jobs,
            "sortedToday": today_jobs, "sortedReset": all_jobs,
        })
        self.assertIn("PASS All Jobs table: 23 duration cells, 9 Today/search states, 5 headers sorted both ways",
                      stdout)
        print(stdout.strip().splitlines()[-1])

    def test_generated_report_duration_header_sorts_by_parsed_duration(self):
        today = datetime.now(timezone.utc).date().isoformat()
        old = "2020-01-01"
        # (job, finished, status, Prow duration, expected Duration cell). Each *-early/*-late
        # pair shares a rounded label, so only the parsed duration can order it.
        jobs = [
            ("eight-minutes", f"{old}T01:00:00Z", "success", "8m26s", "8m 26s"),
            ("hour-late", f"{today}T01:00:00Z", "failure", "1h5m50s", "1h 5m"),
            ("hour-early", f"{old}T02:00:00Z", "success", "1h5m10s", "1h 5m"),
            ("two-hours", f"{today}T02:00:00Z", "success", "2h0m1s", "2h 0m"),
            ("ten-hours", f"{old}T03:00:00Z", "failure", 36000, "10h 0m"),
            ("seconds-late", f"{today}T03:00:00Z", "success", "45.5s", "0m 45s"),
            ("seconds-early", f"{old}T04:00:00Z", "success", "45s", "0m 45s"),
            ("zero", f"{today}T04:00:00Z", "failure", "0s", "0m 0s"),
            ("under-hour", f"{old}T05:00:00Z", "success", "59m59.9s", "59m 59s"),
            ("hour", f"{today}T05:00:00Z", "success", 3600, "1h 0m"),
            ("running", MISSING, "pending", MISSING, "\u2014"),
            ("invalid", f"{old}T06:00:00Z", "failure", "soon", "\u2014"),
            ("null", f"{today}T06:00:00Z", "failure", None, "\u2014"),
        ]
        report, stdout = self.run_jobs_table_harness({"4.20": jobs}, "", today)
        sorts = report["sorts"]["4.20"]

        by_duration = ["zero", "seconds-early", "seconds-late", "eight-minutes", "under-hour",
                       "hour", "hour-early", "hour-late", "two-hours", "ten-hours"]
        unknown = ["invalid", "null", "running"]
        for direction, known in (("asc", by_duration), ("desc", by_duration[::-1])):
            order = sorts["Duration"][direction]
            self.assertEqual(order[:len(known)], known, direction)
            self.assertEqual(sorted(order[len(known):]), unknown, direction)

        # Job names are lowercase letters and hyphens, so Python order matches localeCompare.
        names = sorted(name for name, *_ in jobs)
        self.assertEqual(sorts["Job Name"], {"asc": names, "desc": names[::-1]})
        by_finished = [name for name, finished, *_ in sorted(
            jobs, key=lambda job: "" if job[1] is MISSING else job[1])]
        self.assertEqual(sorts["initial"], by_finished)
        self.assertEqual(sorts["Finished"], {"asc": by_finished, "desc": by_finished[::-1]})
        labels = {"success": "PASS", "failure": "FAIL", "pending": "RUNNING"}
        status_label = {name: labels[status] for name, _, status, *_ in jobs}
        statuses = sorted(status_label.values())
        self.assertEqual([status_label[name] for name in sorts["Status"]["asc"]], statuses)
        self.assertEqual([status_label[name] for name in sorts["Status"]["desc"]], statuses[::-1])

        today_names = [name for name, finished, *_ in jobs
                       if finished is not MISSING and finished.startswith(today)]
        self.assertEqual(report["states"]["sortedToday"],
                         {"4.20": job_summary("3/6 passed (50%)", " status-fail", today_names)})
        self.assertEqual(report["states"]["sortedReset"], {
            "4.20": job_summary("7/13 passed (54%)", " status-fail", [name for name, *_ in jobs])})
        print(stdout.strip().splitlines()[-1])


if __name__ == "__main__":
    unittest.main()
