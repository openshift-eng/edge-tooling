"""Tests for load_context.py — the edge-context team roster.

The roster markdown is fetched straight from GitHub via the ``gh`` CLI (no local
checkout); an injected command runner keeps these tests hermetic (no subprocess,
no network). Coverage spans happy-path parsing, failure inputs (malformed rows,
missing Rover link, no table, gh failure, malformed JSON, wrong encoding, invalid
UTF-8), and the boundary/anti-cheat cases around the Eng+QE role filter, whose
titles are a substring trap ("Manager, Engineering" and "Product Security
Engineer" contain "Engineer" but must be excluded).
"""

import base64
import dataclasses
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import _common  # noqa: E402
import load_context  # noqa: E402

WELL_FORMED_ROSTER = """# Team Roster

## OpenShift Edge

| Name | GitHub | Role | Location |
|------|--------|------|----------|
| [Foo Bar](https://rover.redhat.com/people/profile/foobar) | foobar | Senior Software Engineer | Texas |
| [Fuzz Buzz](https://rover.redhat.com/people/profile/fuzzbuzz) | fuzzbuzz | Senior Software Quality Engineer | North Carolina |
"""

INCLUDED_ROLES = [
    "Software Engineer",
    "Associate Software Engineer",
    "Senior Software Engineer",
    "Principal Software Engineer",
    "Senior Software Quality Engineer",
    "Principal Software Quality Engineer",
    "Associate Software Quality Engineer",
]
EXCLUDED_ROLES = [
    "Senior Manager, Engineering",
    "Manager, Engineering",
    "Associate Manager, Engineering",
    "Principal Product Security Engineer",
    "Principal Product Manager - Technical",
    "Project Manager - Technical",
]

EXPECTED_ROSTER_ENDPOINT = "repos/openshift-eng/edge-context/contents/people/team-roster.md"


class FakeRunner:
    """A command runner that records commands and returns a canned result."""

    def __init__(self, result):
        self.result = result
        self.commands = []

    def __call__(self, command):
        self.commands.append(command)
        return self.result


def _ok(stdout):
    return _common.CommandResult(returncode=0, stdout=stdout, stderr="")


def _contents_response(markdown, encoding="base64", encoder=base64.b64encode):
    content = encoder(markdown.encode("utf-8")).decode("ascii")
    return _ok(json.dumps({"content": content, "encoding": encoding}))
class TestMemberDataclass(unittest.TestCase):
    def test_member_has_only_required_fields(self):
        """Member dataclass should only have name, github, role, jira_username."""
        member = load_context.Member(
            name="Test User",
            github="testuser",
            role="Software Engineer",
            jira_username="testuser@redhat.com"
        )
        fields = {f.name for f in dataclasses.fields(member)}
        assert fields == {"name", "github", "role", "jira_username"}
        # Explicitly verify removed fields don't exist
        assert not hasattr(member, "location")
        assert not hasattr(member, "kerberos")


class TestParseRosterFailureInputs(unittest.TestCase):
    def test_row_with_wrong_column_count_raises(self):
        markdown = WELL_FORMED_ROSTER + "| [X](https://rover.redhat.com/people/profile/x) | gh |\n"
        with self.assertRaises(load_context.ContextParseError):
            load_context.parse_roster(markdown)

    def test_markdown_without_a_roster_table_raises(self):
        with self.assertRaises(load_context.ContextParseError):
            load_context.parse_roster("# Team Roster\n\nNo table here.\n")


class TestFetchRosterMarkdownHappyPath(unittest.TestCase):
    def test_decodes_base64_content_to_markdown(self):
        runner = FakeRunner(_contents_response(WELL_FORMED_ROSTER))
        markdown = load_context.fetch_roster_markdown(runner)
        assert markdown == WELL_FORMED_ROSTER

    def test_calls_gh_api_for_the_default_roster_path(self):
        runner = FakeRunner(_contents_response(WELL_FORMED_ROSTER))
        load_context.fetch_roster_markdown(runner)
        assert runner.commands[0] == ["gh", "api", EXPECTED_ROSTER_ENDPOINT]

    def test_newline_wrapped_base64_still_decodes(self):
        # GitHub's contents API wraps base64 content in newlines.
        runner = FakeRunner(_contents_response(WELL_FORMED_ROSTER, encoder=base64.encodebytes))
        markdown = load_context.fetch_roster_markdown(runner)
        assert markdown == WELL_FORMED_ROSTER


class TestFetchRosterMarkdownCommandPlumbing(unittest.TestCase):
    def test_ref_is_added_as_a_query_parameter(self):
        runner = FakeRunner(_contents_response(WELL_FORMED_ROSTER))
        load_context.fetch_roster_markdown(runner, ref="release-4.20")
        assert runner.commands[0][-1] == f"{EXPECTED_ROSTER_ENDPOINT}?ref=release-4.20"

    def test_custom_repo_is_used_in_the_endpoint(self):
        runner = FakeRunner(_contents_response(WELL_FORMED_ROSTER))
        load_context.fetch_roster_markdown(runner, repo="acme/config")
        assert runner.commands[0][-1] == "repos/acme/config/contents/people/team-roster.md"


class TestFetchRosterMarkdownFailureInputs(unittest.TestCase):
    def test_nonzero_exit_raises_context_parse_error(self):
        runner = FakeRunner(_common.CommandResult(1, "", "gh: not authenticated"))
        with self.assertRaises(load_context.ContextParseError):
            load_context.fetch_roster_markdown(runner)

    def test_malformed_json_raises_context_parse_error(self):
        runner = FakeRunner(_ok("this is not json"))
        with self.assertRaises(load_context.ContextParseError):
            load_context.fetch_roster_markdown(runner)

    def test_unexpected_encoding_raises_context_parse_error(self):
        runner = FakeRunner(_ok(json.dumps({"content": "abc", "encoding": "none"})))
        with self.assertRaises(load_context.ContextParseError):
            load_context.fetch_roster_markdown(runner)

    def test_content_that_is_not_utf8_raises_context_parse_error(self):
        invalid_utf8 = base64.b64encode(b"\xff\xfe\xfa").decode("ascii")
        runner = FakeRunner(_ok(json.dumps({"content": invalid_utf8, "encoding": "base64"})))
        with self.assertRaises(load_context.ContextParseError):
            load_context.fetch_roster_markdown(runner)

    def test_empty_content_raises_rather_than_returning_empty(self):
        runner = FakeRunner(_ok(json.dumps({"content": "", "encoding": "base64"})))
        with self.assertRaises(load_context.ContextParseError):
            load_context.fetch_roster_markdown(runner)


class TestLoadRosterFromGithub(unittest.TestCase):
    def test_propagates_a_fetch_failure(self):
        runner = FakeRunner(_common.CommandResult(1, "", "gh: not authenticated"))
        with self.assertRaises(load_context.ContextParseError):
            load_context.load_roster_from_github(runner)


class TestRoleFilterEdgeCases(unittest.TestCase):
    def test_engineering_and_qe_titles_are_included(self):
        for role in INCLUDED_ROLES:
            assert load_context.is_engineering_or_qe(role) is True, role

    def test_manager_pm_and_security_titles_are_excluded(self):
        for role in EXCLUDED_ROLES:
            assert load_context.is_engineering_or_qe(role) is False, role

    def test_filter_is_case_insensitive(self):
        assert load_context.is_engineering_or_qe("SENIOR SOFTWARE ENGINEER") is True

    def test_whitespace_padding_in_cells_is_tolerated(self):
        padded = WELL_FORMED_ROSTER.replace("| copejon |", "|   copejon   |")
        members = load_context.parse_roster(padded)
        assert members[0].github == "foobar"


if __name__ == "__main__":
    unittest.main()
