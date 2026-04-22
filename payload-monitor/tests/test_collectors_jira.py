"""Tests for payload_monitor.collectors.jira."""

import os
from unittest.mock import MagicMock, patch

import pytest
import requests

from payload_monitor.collectors import jira
from payload_monitor.config import Config
from payload_monitor.models import JobResult, JobRun, JobType, FailingTest


class TestHasAuth:
    def test_has_token(self):
        with patch.dict(os.environ, {"JIRA_TOKEN": "tok"}):
            assert jira.has_auth() is True

    def test_no_token(self):
        with patch.dict(os.environ, {}, clear=True):
            assert jira.has_auth() is False


class TestGetAuth:
    def test_basic_auth(self):
        with patch.dict(os.environ, {"JIRA_USERNAME": "user", "JIRA_TOKEN": "tok"}, clear=True):
            assert jira._get_auth() == ("user", "tok")

    def test_no_basic_auth(self):
        with patch.dict(os.environ, {"JIRA_TOKEN": "tok"}, clear=True):
            assert jira._get_auth() is None

    def test_no_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            assert jira._get_auth() is None


class TestGetHeaders:
    def test_with_token(self):
        with patch.dict(os.environ, {"JIRA_TOKEN": "mytoken"}):
            headers = jira._get_headers()
            assert headers["Authorization"] == "Bearer mytoken"

    def test_without_token(self):
        with patch.dict(os.environ, {}, clear=True):
            headers = jira._get_headers()
            assert "Authorization" not in headers

    def test_basic_auth_no_bearer(self):
        with patch.dict(os.environ, {"JIRA_USERNAME": "user", "JIRA_TOKEN": "tok"}, clear=True):
            headers = jira._get_headers()
            assert "Authorization" not in headers


class TestJqlEscaping:
    @patch.object(jira, "_session")
    def test_jql_escaping_backslash(self, mock_session, config):
        with patch.dict(os.environ, {"JIRA_TOKEN": "tok"}):
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"issues": []}
            mock_resp.raise_for_status = MagicMock()
            mock_session.get.return_value = mock_resp

            jira.search_bugs("job\\with\\backslash", config)

            call_args = mock_session.get.call_args
            jql = call_args.kwargs.get("params", call_args[1].get("params", {}))["jql"]
            assert "job\\\\with\\\\backslash" in jql


class TestSearchBugs:
    @patch.object(jira, "_session")
    def test_basic_auth_passes_tuple(self, mock_session, config):
        with patch.dict(os.environ, {"JIRA_USERNAME": "user", "JIRA_TOKEN": "tok"}, clear=True):
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"issues": []}
            mock_resp.raise_for_status = MagicMock()
            mock_session.get.return_value = mock_resp

            jira.search_bugs("job", config)

            call_kwargs = mock_session.get.call_args.kwargs
            assert call_kwargs["auth"] == ("user", "tok")
            assert "Authorization" not in call_kwargs["headers"]

    @patch.object(jira, "_session")
    def test_returns_bugs(self, mock_session, config):
        with patch.dict(os.environ, {"JIRA_TOKEN": "tok"}):
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "issues": [{
                    "key": "OCPBUGS-123",
                    "fields": {
                        "summary": "SNO test failing",
                        "status": {"name": "New"},
                        "assignee": {"displayName": "Alice"},
                        "priority": {"name": "Critical"},
                        "components": [{"name": "SNO"}],
                    },
                }]
            }
            mock_resp.raise_for_status = MagicMock()
            mock_session.get.return_value = mock_resp

            bugs = jira.search_bugs("periodic-sno-test", config, topology="SNO")
            assert len(bugs) == 1
            assert bugs[0].key == "OCPBUGS-123"
            assert bugs[0].assignee == "Alice"
            assert bugs[0].component == "SNO"

    def test_no_auth_returns_empty(self, config):
        with patch.dict(os.environ, {}, clear=True):
            assert jira.search_bugs("job", config) == []

    @patch.object(jira, "_session")
    def test_http_error_raises(self, mock_session, config):
        with patch.dict(os.environ, {"JIRA_TOKEN": "tok"}):
            mock_session.get.side_effect = requests.RequestException("fail")
            with pytest.raises(requests.RequestException):
                jira.search_bugs("job", config)

    @patch.object(jira, "_session")
    def test_handles_null_assignee(self, mock_session, config):
        with patch.dict(os.environ, {"JIRA_TOKEN": "tok"}):
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "issues": [{
                    "key": "OCPBUGS-456",
                    "fields": {
                        "summary": "Bug",
                        "status": {"name": "Open"},
                        "assignee": None,
                        "priority": None,
                        "components": [],
                    },
                }]
            }
            mock_resp.raise_for_status = MagicMock()
            mock_session.get.return_value = mock_resp

            bugs = jira.search_bugs("job", config)
            assert bugs[0].assignee == "Unassigned"
            assert bugs[0].priority == ""
            assert bugs[0].component == ""


class TestSearchBugsForJobs:
    def test_no_auth(self, config):
        with patch.dict(os.environ, {}, clear=True):
            jobs = [JobRun("j1", "url", JobResult.FAILURE, JobType.BLOCKING, "SNO")]
            results, errors = jira.search_bugs_for_jobs(jobs, config)
            assert results == {}
            assert errors == []

    @patch.object(jira, "search_bugs")
    def test_returns_matches(self, mock_search, config):
        from payload_monitor.models import JiraBug

        mock_search.return_value = [
            JiraBug(key="OCPBUGS-1", summary="bug", status="New")
        ]
        with patch.dict(os.environ, {"JIRA_TOKEN": "tok"}):
            jobs = [JobRun("j1", "url", JobResult.FAILURE, JobType.BLOCKING, "SNO")]
            results, errors = jira.search_bugs_for_jobs(jobs, config)
            assert "j1" in results
            assert errors == []

    @patch.object(jira, "search_bugs")
    def test_collects_errors(self, mock_search, config):
        mock_search.side_effect = requests.RequestException("connection refused")
        with patch.dict(os.environ, {"JIRA_TOKEN": "tok"}):
            jobs = [JobRun("j1", "url", JobResult.FAILURE, JobType.BLOCKING, "SNO")]
            results, errors = jira.search_bugs_for_jobs(jobs, config)
            assert results == {}
            assert len(errors) == 1
            assert "j1" in errors[0]


class TestCreateBugUrl:
    def test_generates_url(self, config):
        url = jira.create_bug_url("title", "desc", config, component="SNO")
        assert "CreateIssue" in url
        assert "summary=title" in url
        assert "component=SNO" in url

    def test_no_component(self, config):
        url = jira.create_bug_url("title", "desc", config)
        assert "component" not in url


class TestSuggestBug:
    def test_suggest_bug(self, config):
        job = JobRun(
            name="periodic-sno-test",
            prow_url="https://prow/123",
            result=JobResult.FAILURE,
            job_type=JobType.BLOCKING,
            topology="SNO",
            failing_tests=[
                FailingTest(name="test1", error_message="err1"),
                FailingTest(name="test2", error_message="err2"),
            ],
            error_summary="test1: err1",
        )
        suggestion = jira.suggest_bug(job, ["4.18", "4.19"], config, component="SNO")
        assert "SNO" in suggestion.title
        assert "4.18, 4.19" in suggestion.title
        assert suggestion.job_name == "periodic-sno-test"
        assert suggestion.topology == "SNO"
        assert suggestion.versions == ["4.18", "4.19"]
        assert len(suggestion.failing_tests) == 2
        assert "CreateIssue" in suggestion.create_url
        assert "test1" in suggestion.full_description

    def test_suggest_bug_no_tests(self, config):
        job = JobRun(
            name="periodic-sno-test",
            prow_url="https://prow/123",
            result=JobResult.FAILURE,
            job_type=JobType.BLOCKING,
            topology="SNO",
        )
        suggestion = jira.suggest_bug(job, ["4.19"], config)
        assert suggestion.failing_tests == []
