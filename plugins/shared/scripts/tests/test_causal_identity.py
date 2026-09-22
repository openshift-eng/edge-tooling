#!/usr/bin/env python3
"""Regression coverage for canonical MicroShift CI causal identities.

Run with:

    python3 plugins/shared/scripts/tests/test_causal_identity.py
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
MICROSHIFT_SCRIPTS = SCRIPTS_DIR.parent.parent / "microshift-ci" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(MICROSHIFT_SCRIPTS))

def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


create_report = _load_module("create_report", SCRIPTS_DIR / "create-report.py")
search_bugs = _load_module("search_bugs", MICROSHIFT_SCRIPTS / "search-bugs.py")
_validator_module = _load_module("validate_rca_output", SCRIPTS_DIR / "validate-rca-output.py")
aggregate = _load_module("aggregate", SCRIPTS_DIR / "aggregate.py")
_parse_module = _load_module("parse", SCRIPTS_DIR / "parse.py")
group_by_signature = _parse_module.group_by_signature
parse_structured_summary = _parse_module.parse_structured_summary
validate_entry = _validator_module.validate_entry


def rca_entry(evidence_path, **overrides):
    """Build an evidence-backed MicroShift RCA entry for a dependency chain."""
    entry = {
        "severity": 4,
        "stack_layer": "deploy phase",
        "step_name": "openshift-microshift-e2e-metal-tests",
        "error_signature": "cert-manager webhook failed readiness during boot",
        "root_cause": "CNI readiness blocked cert-manager webhook startup",
        "raw_error": "pre_test_greenboot_check FAILED",
        "cause_identity": "CNI dependency unavailable before cert-manager webhook startup",
        "failure_signal": "pre_test_greenboot_check FAILED",
        "impacts": ["cert-manager webhook did not become Ready"],
        "trigger_context": ["greenboot pre-test check", "el96-lrel@cleanup-data"],
        "infrastructure_failure": False,
        "job_url": "https://prow.example/job/123",
        "job_name": "periodic-ci-openshift-microshift-release-4.22-e2e",
        "release": "4.22",
        "remediation": "restore the CNI dependency before starting the webhook",
        "finished": "2026-09-22",
        "causal_chain": [
            {
                "cause": "CNI was not Ready before webhook startup",
                "evidence": f"{evidence_path}:1",
                "quote": "CNI dependency is not Ready",
            }
        ],
        "confidence": "high",
        "analysis_gaps": [],
        "scenarios": ["el96-lrel@cleanup-data"],
    }
    entry.update(overrides)
    return entry


class CanonicalCausalIdentityTests(unittest.TestCase):
    def setUp(self):
        fd, self.evidence_path = tempfile.mkstemp(suffix=".log")
        with os.fdopen(fd, "w") as evidence_file:
            evidence_file.write("CNI dependency is not Ready\n")

    def tearDown(self):
        Path(self.evidence_path).unlink(missing_ok=True)

    def test_dependency_identity_groups_canaries_and_cleanup_as_context(self):
        cni_canary = rca_entry(self.evidence_path)
        cleanup_impact = rca_entry(
            self.evidence_path,
            step_name="openshift-microshift-e2e-cleanup",
            error_signature="cleanup-data failed after startup failure",
            failure_signal="cleanup-data did not run after pre-test startup failure",
            impacts=["cleanup-data was skipped after webhook startup failed"],
            trigger_context=["el96-lrel@cleanup-data", "post-failure cleanup"],
            scenarios=["el96-lrel@cleanup-data"],
        )
        webhook_after_cni = rca_entry(
            self.evidence_path,
            error_signature="cert-manager webhook TLS probe failed",
            root_cause="webhook TLS configuration failed after CNI became Ready",
            cause_identity="cert-manager webhook TLS configuration failed after CNI readiness",
            failure_signal="cert-manager webhook readiness probe failed after CNI Ready",
            impacts=[],
            trigger_context=["CNI Ready", "el96-lrel@standard1"],
            scenarios=["el96-lrel@standard1"],
        )

        groups = group_by_signature([cni_canary, cleanup_impact, webhook_after_cni])
        self.assertEqual(2, len(groups))
        cni_group = next(
            group for group in groups
            if group[0]["cause_identity"].startswith("CNI dependency")
        )
        self.assertEqual(2, len(cni_group))

        issues, _ = aggregate._build_issues_from_jobs(
            [cni_canary, cleanup_impact, webhook_after_cni]
        )
        cni_issue = next(
            issue for issue in issues
            if issue["title"].startswith("CNI dependency")
        )
        self.assertEqual(
            "CNI dependency unavailable before cert-manager webhook startup",
            cni_issue["title"],
        )
        self.assertNotIn("greenboot", cni_issue["title"].lower())
        self.assertNotIn("cleanup-data", cni_issue["title"])
        self.assertIn("pre_test_greenboot_check FAILED", cni_issue["failure_signals"])
        self.assertIn(
            "cleanup-data was skipped after webhook startup failed",
            cni_issue["impacts"],
        )

        candidate = next(
            candidate for candidate in search_bugs.build_candidates(groups)
            if candidate["cause_identity"].startswith("CNI dependency")
        )
        self.assertEqual(cni_issue["title"], search_bugs.candidate_key(candidate))
        self.assertIn("pre_test_greenboot_check FAILED", candidate["failure_signals"])

        create_url = create_report._create_bug_url(
            cni_issue, "Release 4.22", create_report.COMPONENT_JIRA_CREATE["microshift"]
        )
        summary = parse_qs(urlparse(create_url).query)["summary"][0]
        self.assertEqual(f"MicroShift CI: {cni_issue['title']}", summary)

    def test_validator_accepts_identity_fields_and_rejects_context_as_identity(self):
        valid = rca_entry(self.evidence_path)
        self.assertEqual([], validate_entry(valid, 0, {}))

        generic_canary = rca_entry(
            self.evidence_path,
            cause_identity="greenboot health check failed",
        )
        self.assertTrue(any(
            "generic greenboot health-check" in error
            for error in validate_entry(generic_canary, 0, {})
        ))

        cleanup_identity = rca_entry(
            self.evidence_path,
            cause_identity="cleanup-data failed after greenboot",
        )
        self.assertTrue(any(
            "cleanup-data scenario context" in error
            for error in validate_entry(cleanup_identity, 0, {})
        ))

    def test_parse_preserves_identity_fields_and_legacy_falls_back(self):
        new_entry = rca_entry(self.evidence_path)
        legacy_entry = rca_entry(self.evidence_path)
        for field in ("cause_identity", "failure_signal", "impacts", "trigger_context"):
            legacy_entry.pop(field)
        legacy_entry["error_signature"] = "legacy webhook readiness failure"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as report:
            json.dump([new_entry, legacy_entry], report)
            report_path = report.name
        try:
            parsed = parse_structured_summary(report_path)
        finally:
            Path(report_path).unlink(missing_ok=True)

        self.assertEqual(new_entry["cause_identity"], parsed[0]["cause_identity"])
        self.assertEqual([], parsed[1]["impacts"])
        legacy_issues, _ = aggregate._build_issues_from_jobs([parsed[1]])
        self.assertEqual("legacy webhook readiness failure", legacy_issues[0]["title"])

    def test_jira_cache_uses_canonical_identity_not_terminal_signal(self):
        with tempfile.TemporaryDirectory() as workdir:
            bugs_dir = Path(workdir) / "bugs"
            bugs_dir.mkdir()
            cached_candidate = {
                "cause_identity": "CNI dependency unavailable before cert-manager webhook startup",
                "error_signature": "old greenboot timeout wording",
                "duplicates": [{"key": "USHIFT-123", "summary": "existing"}],
                "regressions": [],
            }
            (bugs_dir / "bug-matches-4.22.json").write_text(json.dumps({
                "candidates": [cached_candidate],
            }))

            lookup = search_bugs._load_jira_lookup(workdir)
            new_candidate = {
                "cause_identity": cached_candidate["cause_identity"],
                "error_signature": "pre_test_greenboot_check FAILED",
            }
            self.assertIn(search_bugs.candidate_key(new_candidate), lookup)
            self.assertEqual("USHIFT-123", lookup[search_bugs.candidate_key(new_candidate)]["duplicates"][0]["key"])


if __name__ == "__main__":
    unittest.main()
