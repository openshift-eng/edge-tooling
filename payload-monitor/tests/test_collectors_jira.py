"""Tests for payload_monitor.collectors.jira."""

from payload_monitor.collectors import jira
from payload_monitor.models import JobResult, JobRun, JobType, FailingTest


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
