"""Tests for attribute_github_activities.py — pure PR attribution from MCP-fetched Jira data.

No network, no subprocess: raw PR data and raw Jira issue data are both files on
disk, exercised end to end through ``main()``.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import attribute_github_activities  # noqa: E402


def _write(tmp_path, name, data):
    path = os.path.join(tmp_path, name)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    return path


class TestMainEndToEnd(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = self._tmpdir.name

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_component_attribution_flows_through(self):
        raw_prs = _write(
            self.tmp_path,
            "raw_prs.json",
            {
                "pr_pairs": [
                    {
                        "member": "alice@redhat.com",
                        "kind": "pr_authored",
                        "pr": {
                            "title": "OCPEDGE-1: fix",
                            "body": "",
                            "repository": {"nameWithOwner": "openshift/example"},
                            "url": "https://gh/pr/1",
                            "createdAt": "2026-05-01T00:00:00Z",
                        },
                    }
                ],
                "jira_keys": ["OCPEDGE-1"],
                "excluded_personal_repo_prs": 2,
                "excluded_personal_repos": {"alice/scratch": 2},
            },
        )
        raw_jira = _write(
            self.tmp_path,
            "raw_jira.json",
            {
                "issues": [{"fields": {"key": "OCPEDGE-1", "components": [{"name": "SNO"}]}}],
                "parent_workstreams": {},
            },
        )
        output = os.path.join(self.tmp_path, "github_activity.json")

        attribute_github_activities.main(
            ["--raw-prs", raw_prs, "--raw-jira-issues", raw_jira, "--output", output]
        )

        with open(output, encoding="utf-8") as handle:
            payload = json.load(handle)

        assert len(payload["activities"]) == 1
        activity = payload["activities"][0]
        assert activity["member"] == "alice@redhat.com"
        assert activity["workstream"] == "SNO"
        assert activity["attribution_source"] == "component"
        assert payload["collector_meta"] == {
            "excluded_personal_repo_prs": 2,
            "excluded_personal_repos": {"alice/scratch": 2},
        }

    def test_key_absent_from_jira_data_is_unattributed(self):
        raw_prs = _write(
            self.tmp_path,
            "raw_prs.json",
            {
                "pr_pairs": [
                    {
                        "member": "bob@redhat.com",
                        "kind": "pr_authored",
                        "pr": {
                            "title": "OCPEDGE-2: planning",
                            "body": "",
                            "repository": {"nameWithOwner": "openshift/example"},
                            "url": "https://gh/pr/2",
                            "createdAt": "2026-05-01T00:00:00Z",
                        },
                    }
                ],
                "jira_keys": ["OCPEDGE-2"],
            },
        )
        raw_jira = _write(self.tmp_path, "raw_jira.json", {"issues": [], "parent_workstreams": {}})
        output = os.path.join(self.tmp_path, "github_activity.json")

        attribute_github_activities.main(
            ["--raw-prs", raw_prs, "--raw-jira-issues", raw_jira, "--output", output]
        )

        with open(output, encoding="utf-8") as handle:
            payload = json.load(handle)

        assert len(payload["activities"]) == 1
        assert payload["activities"][0]["workstream"] is None
        assert payload["activities"][0]["attribution_source"] is None


if __name__ == "__main__":
    unittest.main()
