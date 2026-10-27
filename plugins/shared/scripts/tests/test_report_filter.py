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
        payload = html.split('<script id="report-data" type="application/json">', 1)[1].split('</script>', 1)[0]
        data = json.loads(payload)
        self.assertEqual(data["releases_data"]["4.20"]["issues"][0]["causal_chain"], chain)

    def test_generated_payload_corruption_and_escaping(self):
        text = 'Synthetic </ScRiPt><script>throw "injected"</script><!-- < & > " \\ \n \u2028 \u2029'
        html = REPORT.generate_html(
            "Probe", {}, [], None, None, datetime(2026, 10, 19, tzinfo=timezone.utc),
            diagnostics_text=text, status_data={"synthetic_rate": 0.999999999999999},
        )
        with tempfile.TemporaryDirectory() as temporary:
            renderer = Path(temporary) / "renderer.js"
            renderer.write_text(REPORT.JS)
            report = Path(temporary) / "report.html"
            report.write_text(html)
            for script, args in [
                ("test_report_payload.js", [str(report)]),
                ("test_report_filter.js", [str(renderer), "", "--regex-tests", str(report), "--payload-tests"]),
            ]:
                result = subprocess.run(
                    ["node", str(Path(__file__).with_name(script)), *args],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                print(result.stdout.strip())

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


if __name__ == "__main__":
    unittest.main()
