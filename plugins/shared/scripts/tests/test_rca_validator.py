#!/usr/bin/env python3
"""Regression tests for validate-rca-output.py hook detection.

Zero-dependency (stdlib unittest + subprocess + tempfile). Run with:

    python3 plugins/shared/scripts/tests/test_rca_validator.py

The script is invoked as a subprocess with a synthetic JSON payload piped to
stdin — exactly how Claude Code calls the Stop / SubagentStop hooks. A blocked
turn is signalled by a ``{"decision": "block", ...}`` object on stdout; a no-op
is empty stdout. The script always exits 0 (a non-zero exit would fail the hook,
not block it), so every case asserts exit code 0 as well.

Guards against the shape-detection regression where a main-agent Stop payload
(which also carries ``last_assistant_message``) was validated as RCA output and
blocked every ordinary turn.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "validate-rca-output.py"


def run_hook(payload, extra_env=None):
    """Pipe *payload* (a dict) to the hook script; return (exit_code, stdout)."""
    env = dict(os.environ)
    # Ensure the RCA session gate is off unless a test opts in.
    env.pop("CI_DOCTOR_RCA_SESSION", None)
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.returncode, proc.stdout


def blocked(stdout):
    """True if the hook emitted a block decision."""
    stdout = stdout.strip()
    if not stdout:
        return False
    try:
        return json.loads(stdout).get("decision") == "block"
    except json.JSONDecodeError:
        return False


def valid_rca_entry(evidence_path):
    """A minimal RCA entry that passes validate_message.

    stack_layer is 'deploy phase' so the empty-scenarios/'test' rule does not
    apply; the single causal_chain link cites line 1 of *evidence_path*.
    """
    return {
        "severity": 3,
        "stack_layer": "deploy phase",
        "step_name": "ipi-install",
        "error_signature": "install timed out",
        "root_cause": "cluster operators never went available",
        "raw_error": "timed out waiting for the condition",
        "infrastructure_failure": False,
        "job_url": "https://prow.example/job/123",
        "job_name": "periodic-ci-example",
        "release": "4.20",
        "remediation": "retry the install",
        "finished": "2026-08-13T00:00:00Z",
        "confidence": "high",
        "analysis_gaps": [],
        "scenarios": [],
        "causal_chain": [
            {
                "cause": "install timed out",
                "evidence": f"{evidence_path}:1",
                "quote": "timed out waiting for the condition",
            }
        ],
    }


class HookDetectionTests(unittest.TestCase):
    def test_stop_without_env_is_noop(self):
        """Bug regression: main-agent Stop with no RCA session must not block."""
        code, out = run_hook(
            {"hook_event_name": "Stop", "last_assistant_message": "some prose"}
        )
        self.assertEqual(code, 0)
        self.assertFalse(blocked(out), f"unexpected block: {out!r}")
        self.assertEqual(out.strip(), "")

    def test_subagentstop_invalid_blocks(self):
        """SubagentStop with non-JSON output must still block."""
        code, out = run_hook(
            {"hook_event_name": "SubagentStop", "last_assistant_message": "not json"}
        )
        self.assertEqual(code, 0)
        self.assertTrue(blocked(out), f"expected block, got: {out!r}")

    def test_unknown_event_fails_safe(self):
        """Absent hook_event_name must skip (pre-fix this blocked via shape)."""
        code, out = run_hook({"last_assistant_message": "not json"})
        self.assertEqual(code, 0)
        self.assertFalse(blocked(out), f"unexpected block: {out!r}")

    def test_subagentstop_valid_passes(self):
        """A well-formed one-entry RCA array must not block."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False
        ) as f:
            f.write("timed out waiting for the condition\n")
            evidence_path = f.name
        try:
            payload = {
                "hook_event_name": "SubagentStop",
                "last_assistant_message": json.dumps([valid_rca_entry(evidence_path)]),
            }
            code, out = run_hook(payload)
            self.assertEqual(code, 0)
            self.assertFalse(blocked(out), f"unexpected block: {out!r}")
        finally:
            os.unlink(evidence_path)

    def test_stop_env_gated_transcript_blocks(self):
        """Stop with CI_DOCTOR_RCA_SESSION set validates the transcript message."""
        record = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "not json"}]},
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False
        ) as f:
            f.write(json.dumps(record) + "\n")
            transcript_path = f.name
        try:
            payload = {
                "hook_event_name": "Stop",
                "transcript_path": transcript_path,
            }
            code, out = run_hook(payload, extra_env={"CI_DOCTOR_RCA_SESSION": "1"})
            self.assertEqual(code, 0)
            self.assertTrue(blocked(out), f"expected block, got: {out!r}")
        finally:
            os.unlink(transcript_path)


class HtmlEntityNormalizationTests(unittest.TestCase):
    """Tests for HTML entity normalization in quote validation."""

    def test_html_entities_in_file_match_plain_quote(self):
        """A file line with &#34; entities should match a plain-text quote."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False
        ) as f:
            f.write('services &#34;prometheus-k8s&#34; not found\n')
            evidence_path = f.name
        try:
            entry = valid_rca_entry(evidence_path)
            entry["causal_chain"] = [
                {
                    "cause": "service not found",
                    "evidence": f"{evidence_path}:1",
                    "quote": 'services "prometheus-k8s" not found',
                }
            ]
            payload = {
                "hook_event_name": "SubagentStop",
                "last_assistant_message": json.dumps([entry]),
            }
            code, out = run_hook(payload)
            self.assertEqual(code, 0)
            self.assertFalse(blocked(out), f"unexpected block: {out!r}")
        finally:
            os.unlink(evidence_path)

    def test_amp_lt_gt_entities_match(self):
        """&amp; &lt; &gt; in file should match plain &, <, > in quote."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False
        ) as f:
            f.write('if x &lt; 0 &amp;&amp; y &gt; 1\n')
            evidence_path = f.name
        try:
            entry = valid_rca_entry(evidence_path)
            entry["causal_chain"] = [
                {
                    "cause": "conditional error",
                    "evidence": f"{evidence_path}:1",
                    "quote": "if x < 0 && y > 1",
                }
            ]
            payload = {
                "hook_event_name": "SubagentStop",
                "last_assistant_message": json.dumps([entry]),
            }
            code, out = run_hook(payload)
            self.assertEqual(code, 0)
            self.assertFalse(blocked(out), f"unexpected block: {out!r}")
        finally:
            os.unlink(evidence_path)


class StructuredGapTests(unittest.TestCase):
    """Tests for structured analysis_gaps format (object with gap/reason/detail)."""

    def _make_entry_with_gaps(self, gaps, evidence_path):
        entry = valid_rca_entry(evidence_path)
        entry["analysis_gaps"] = gaps
        return entry

    def test_structured_gaps_accepted(self):
        """Structured gap objects with valid reason enum should pass validation."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False
        ) as f:
            f.write("timed out waiting for the condition\n")
            evidence_path = f.name
        try:
            entry = self._make_entry_with_gaps(
                [
                    {"gap": "sosreport not extracted", "reason": "deprioritized", "detail": "turn budget exhausted"},
                    {"gap": "pod logs missing", "reason": "artifact_unavailable", "detail": ""},
                ],
                evidence_path,
            )
            payload = {
                "hook_event_name": "SubagentStop",
                "last_assistant_message": json.dumps([entry]),
            }
            code, out = run_hook(payload)
            self.assertEqual(code, 0)
            self.assertFalse(blocked(out), f"unexpected block: {out!r}")
        finally:
            os.unlink(evidence_path)

    def test_string_gaps_still_accepted(self):
        """Plain string gaps (old format) must still pass validation."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False
        ) as f:
            f.write("timed out waiting for the condition\n")
            evidence_path = f.name
        try:
            entry = self._make_entry_with_gaps(
                ["sosreport not extracted", "pod logs missing"],
                evidence_path,
            )
            payload = {
                "hook_event_name": "SubagentStop",
                "last_assistant_message": json.dumps([entry]),
            }
            code, out = run_hook(payload)
            self.assertEqual(code, 0)
            self.assertFalse(blocked(out), f"unexpected block: {out!r}")
        finally:
            os.unlink(evidence_path)

    def test_mixed_gaps_accepted(self):
        """Mix of string and object gaps must pass validation."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False
        ) as f:
            f.write("timed out waiting for the condition\n")
            evidence_path = f.name
        try:
            entry = self._make_entry_with_gaps(
                [
                    "plain string gap",
                    {"gap": "structured gap", "reason": "out_of_scope", "detail": "beyond agent tools"},
                ],
                evidence_path,
            )
            payload = {
                "hook_event_name": "SubagentStop",
                "last_assistant_message": json.dumps([entry]),
            }
            code, out = run_hook(payload)
            self.assertEqual(code, 0)
            self.assertFalse(blocked(out), f"unexpected block: {out!r}")
        finally:
            os.unlink(evidence_path)

    def test_invalid_reason_blocks(self):
        """Structured gap with invalid reason enum must block."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False
        ) as f:
            f.write("timed out waiting for the condition\n")
            evidence_path = f.name
        try:
            entry = self._make_entry_with_gaps(
                [{"gap": "something", "reason": "invalid_reason", "detail": ""}],
                evidence_path,
            )
            payload = {
                "hook_event_name": "SubagentStop",
                "last_assistant_message": json.dumps([entry]),
            }
            code, out = run_hook(payload)
            self.assertEqual(code, 0)
            self.assertTrue(blocked(out), f"expected block, got: {out!r}")
        finally:
            os.unlink(evidence_path)

    def test_empty_gap_text_blocks(self):
        """Structured gap with empty 'gap' field must block."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False
        ) as f:
            f.write("timed out waiting for the condition\n")
            evidence_path = f.name
        try:
            entry = self._make_entry_with_gaps(
                [{"gap": "", "reason": "deprioritized", "detail": ""}],
                evidence_path,
            )
            payload = {
                "hook_event_name": "SubagentStop",
                "last_assistant_message": json.dumps([entry]),
            }
            code, out = run_hook(payload)
            self.assertEqual(code, 0)
            self.assertTrue(blocked(out), f"expected block, got: {out!r}")
        finally:
            os.unlink(evidence_path)

    def test_all_reason_enums_accepted(self):
        """All valid reason enum values must be accepted."""
        valid_reasons = [
            "artifact_unavailable", "extraction_failed",
            "deprioritized", "not_realized", "out_of_scope",
        ]
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False
        ) as f:
            f.write("timed out waiting for the condition\n")
            evidence_path = f.name
        try:
            for reason in valid_reasons:
                entry = self._make_entry_with_gaps(
                    [{"gap": f"gap for {reason}", "reason": reason, "detail": ""}],
                    evidence_path,
                )
                payload = {
                    "hook_event_name": "SubagentStop",
                    "last_assistant_message": json.dumps([entry]),
                }
                code, out = run_hook(payload)
                self.assertEqual(code, 0)
                self.assertFalse(
                    blocked(out),
                    f"reason '{reason}' unexpectedly blocked: {out!r}",
                )
        finally:
            os.unlink(evidence_path)


if __name__ == "__main__":
    unittest.main()
