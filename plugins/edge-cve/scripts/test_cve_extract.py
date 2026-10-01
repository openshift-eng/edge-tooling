#!/usr/bin/env python3
"""Unit tests for CVE extraction helpers."""

import unittest
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from lib.cve_extract import (  # noqa: E402
    extract_cve_ids,
    extract_ocp_versions,
    extract_repo_urls,
    resolve_component_repo,
    resolve_git_refs,
    ticket_versions,
)


class CveExtractTests(unittest.TestCase):
    def test_extract_cve_ids(self):
        text = "Fix CVE-2024-12345 and CVE-2023-99999 in golang"
        self.assertEqual(
            extract_cve_ids(text),
            ["CVE-2024-12345", "CVE-2023-99999"],
        )

    def test_extract_ocp_versions(self):
        text = "MicroShift 4.17 and 4.18.z affected"
        self.assertEqual(extract_ocp_versions(text), ["4.17", "4.18"])

    def test_extract_ocp_versions_ignores_semver_patch(self):
        # Upstream library versions like "4.1.4" must not become OCP "4.1".
        text = "Prior to 4.1.4 and 3.0.5, decrypting a JWE object will panic [openshift-4.23]"
        self.assertEqual(extract_ocp_versions(text), ["4.23"])

    def test_extract_repo_urls(self):
        text = "See https://github.com/openshift/microshift/pull/1"
        patterns = [r"github\.com/(?P<org>[^/\s]+)/(?P<repo>[^/\s#?]+)"]
        repos = extract_repo_urls(text, patterns)
        self.assertEqual(repos[0]["slug"], "openshift/microshift")

    def test_resolve_component_repo(self):
        config = {
            "defaults": {"org": "openshift"},
            "components": {
                "MicroShift": {
                    "repo": "openshift/microshift",
                    "language": "go",
                    "version_ref_template": "release-{version}",
                    "version_ref_fallbacks": [],
                }
            },
        }
        repo = resolve_component_repo("MicroShift", config)
        self.assertEqual(repo["slug"], "openshift/microshift")
        # Ticket versions map to release branches only - never invent main.
        self.assertEqual(resolve_git_refs(["4.17", "4.18"], repo), ["release-4.17", "release-4.18"])
        self.assertEqual(resolve_git_refs([], repo), [])

    def test_resolve_git_refs_ignores_fallback_when_versions_present(self):
        repo = {
            "version_ref_template": "release-{version}",
            "version_ref_fallbacks": ["main"],
        }
        # Fallbacks must NOT be appended on top of version-derived refs -
        # tip-of-tree captures far more than the ticket is asking about.
        self.assertEqual(resolve_git_refs(["4.17"], repo), ["release-4.17"])

    def test_resolve_git_refs_uses_fallback_only_when_no_versions(self):
        repo = {
            "version_ref_template": "release-{version}",
            "version_ref_fallbacks": ["main"],
        }
        self.assertEqual(resolve_git_refs([], repo), ["main"])

    def test_resolve_git_refs_fixed_branch_template(self):
        # Unversioned components (e.g. two-node-toolbox) use a fixed branch
        # as their template - that single branch is fine to scan as-is.
        repo = {
            "version_ref_template": "main",
            "version_ref_fallbacks": [],
        }
        self.assertEqual(resolve_git_refs([], repo), ["main"])
        self.assertEqual(resolve_git_refs(["4.17"], repo), ["main"])

    def test_ticket_versions(self):
        issue = {
            "summary": "CVE-2024-1 in 4.19",
            "description": "",
            "affected_versions": ["4.18.z"],
            "fix_versions": [],
        }
        self.assertEqual(ticket_versions(issue), ["4.18", "4.19"])

    def test_ticket_versions_ignores_description_library_versions(self):
        issue = {
            "summary": (
                "CVE-2026-34986 lvms4/lvms-must-gather-rhel9: "
                "Go JOSE DoS [openshift-4.23]"
            ),
            "description": "Prior to 4.1.4 and 3.0.5, decrypting a JWE object will panic.",
            "affected_versions": ["4.23"],
            "fix_versions": [],
        }
        self.assertEqual(ticket_versions(issue), ["4.23"])


if __name__ == "__main__":
    unittest.main()
