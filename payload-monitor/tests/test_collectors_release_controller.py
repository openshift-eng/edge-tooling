"""Tests for payload_monitor.collectors.release_controller."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from payload_monitor.collectors import release_controller as rc
from payload_monitor.config import Config
from payload_monitor.models import (
    JobResult,
    JobType,
    PayloadStatus,
    PreviousAttempt,
    StreamReport,
)


class TestStreamName:
    def test_stream_name(self):
        assert rc._stream_name("4.19") == "4.19.0-0.nightly"

    def test_stream_name_major(self):
        assert rc._stream_name("5.0") == "5.0.0-0.nightly"


class TestParseJobResult:
    def test_succeeded(self):
        assert rc._parse_job_result("Succeeded") == JobResult.SUCCESS

    def test_failed(self):
        assert rc._parse_job_result("Failed") == JobResult.FAILURE

    def test_pending(self):
        assert rc._parse_job_result("Pending") == JobResult.PENDING

    def test_triggered(self):
        assert rc._parse_job_result("Triggered") == JobResult.PENDING

    def test_unknown(self):
        assert rc._parse_job_result("SomethingElse") == JobResult.UNKNOWN

    def test_case_insensitive(self):
        assert rc._parse_job_result("SUCCEEDED") == JobResult.SUCCESS
        assert rc._parse_job_result("failed") == JobResult.FAILURE


class TestParsePhase:
    def test_accepted(self):
        assert rc._parse_phase("Accepted") == PayloadStatus.ACCEPTED

    def test_rejected(self):
        assert rc._parse_phase("Rejected") == PayloadStatus.REJECTED

    def test_pending(self):
        assert rc._parse_phase("Ready") == PayloadStatus.PENDING

    def test_case_insensitive(self):
        assert rc._parse_phase("ACCEPTED") == PayloadStatus.ACCEPTED


class TestParseJobs:
    def test_filters_by_topology(self, config):
        jobs_dict = {
            "periodic-sno-test": {"url": "https://prow/1", "state": "Failed"},
            "periodic-aws-test": {"url": "https://prow/2", "state": "Succeeded"},
        }
        result = rc._parse_jobs(jobs_dict, JobType.BLOCKING, config)
        assert len(result) == 1
        assert result[0].name == "periodic-sno-test"
        assert result[0].result == JobResult.FAILURE
        assert result[0].job_type == JobType.BLOCKING
        assert result[0].topology == "SNO"

    def test_empty_dict(self, config):
        assert rc._parse_jobs({}, JobType.BLOCKING, config) == []

    def test_multiple_topologies(self, config):
        jobs_dict = {
            "periodic-sno-test": {"url": "https://prow/1", "state": "Failed"},
            "periodic-arbiter-test": {"url": "https://prow/2", "state": "Succeeded"},
        }
        result = rc._parse_jobs(jobs_dict, JobType.INFORMING, config)
        assert len(result) == 2
        topologies = {j.topology for j in result}
        assert topologies == {"SNO", "TNA"}

    def test_parses_retries_and_previous_attempts(self, config):
        jobs_dict = {
            "periodic-sno-test": {
                "url": "https://prow/2",
                "state": "Failed",
                "retries": 1,
                "previousAttemptURLs": ["https://prow/1"],
            },
        }
        result = rc._parse_jobs(jobs_dict, JobType.BLOCKING, config)
        assert len(result) == 1
        job = result[0]
        assert job.retries == 1
        assert len(job.previous_attempts) == 1
        assert job.previous_attempts[0].prow_url == "https://prow/1"
        assert job.previous_attempts[0].result == JobResult.FAILURE

    def test_no_retry_fields_defaults(self, config):
        jobs_dict = {
            "periodic-sno-test": {"url": "https://prow/1", "state": "Succeeded"},
        }
        result = rc._parse_jobs(jobs_dict, JobType.BLOCKING, config)
        assert result[0].retries == 0
        assert result[0].previous_attempts == []

    def test_multiple_previous_attempts(self, config):
        jobs_dict = {
            "periodic-sno-test": {
                "url": "https://prow/3",
                "state": "Succeeded",
                "retries": 2,
                "previousAttemptURLs": ["https://prow/1", "https://prow/2"],
            },
        }
        result = rc._parse_jobs(jobs_dict, JobType.BLOCKING, config)
        job = result[0]
        assert job.retries == 2
        assert len(job.previous_attempts) == 2
        assert job.previous_attempts[0].prow_url == "https://prow/1"
        assert job.previous_attempts[1].prow_url == "https://prow/2"


    def test_retries_null_defaults_to_zero(self, config):
        jobs_dict = {
            "periodic-sno-test": {"url": "https://prow/1", "state": "Succeeded", "retries": None},
        }
        result = rc._parse_jobs(jobs_dict, JobType.BLOCKING, config)
        assert result[0].retries == 0

    def test_retries_non_int_defaults_to_zero(self, config):
        jobs_dict = {
            "periodic-sno-test": {"url": "https://prow/1", "state": "Succeeded", "retries": "bad"},
        }
        result = rc._parse_jobs(jobs_dict, JobType.BLOCKING, config)
        assert result[0].retries == 0

    def test_previous_attempts_null_defaults_to_empty(self, config):
        jobs_dict = {
            "periodic-sno-test": {
                "url": "https://prow/1", "state": "Succeeded",
                "previousAttemptURLs": None,
            },
        }
        result = rc._parse_jobs(jobs_dict, JobType.BLOCKING, config)
        assert result[0].previous_attempts == []

    def test_previous_attempts_non_list_defaults_to_empty(self, config):
        jobs_dict = {
            "periodic-sno-test": {
                "url": "https://prow/1", "state": "Succeeded",
                "previousAttemptURLs": "not-a-list",
            },
        }
        result = rc._parse_jobs(jobs_dict, JobType.BLOCKING, config)
        assert result[0].previous_attempts == []

    def test_previous_attempts_filters_null_entries(self, config):
        jobs_dict = {
            "periodic-sno-test": {
                "url": "https://prow/2", "state": "Failed",
                "retries": 2,
                "previousAttemptURLs": [None, "https://prow/1", ""],
            },
        }
        result = rc._parse_jobs(jobs_dict, JobType.BLOCKING, config)
        assert len(result[0].previous_attempts) == 1
        assert result[0].previous_attempts[0].prow_url == "https://prow/1"


class TestDiscoverStreams:
    def test_discover_streams(self, config):
        config.versions = ["4.18", "4.19"]
        streams = rc.discover_streams(config)
        assert streams == ["4.18.0-0.nightly", "4.19.0-0.nightly"]


class TestFetchTags:
    @patch.object(rc, "_session")
    def test_fetch_tags_success(self, mock_session):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "tags": [
                {"name": "tag1", "phase": "Accepted"},
                {"name": "tag2", "phase": "Rejected"},
                {"name": "tag3", "phase": "Ready"},
                {"name": "tag4", "phase": "Accepted"},
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp

        tags = rc.fetch_tags("4.19.0-0.nightly", limit=3)
        assert len(tags) == 3
        assert tags[2]["name"] == "tag4"

    @patch.object(rc, "_session")
    def test_fetch_tags_skips_non_terminal(self, mock_session):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "tags": [
                {"name": "tag1", "phase": "Ready"},
                {"name": "tag2", "phase": "Pending"},
                {"name": "tag3", "phase": "Accepted"},
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp

        tags = rc.fetch_tags("4.19.0-0.nightly", limit=5)
        assert len(tags) == 1
        assert tags[0]["name"] == "tag3"

    @patch.object(rc, "_session")
    def test_fetch_tags_http_error(self, mock_session):
        mock_session.get.side_effect = requests.RequestException("timeout")
        tags = rc.fetch_tags("4.19.0-0.nightly")
        assert tags == []


class TestFetchReleaseDetail:
    @patch.object(rc, "_session")
    def test_success(self, mock_session):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"results": {"blockingJobs": {}}}
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp

        result = rc.fetch_release_detail("stream", "tag")
        assert result == {"results": {"blockingJobs": {}}}

    @patch.object(rc, "_session")
    def test_http_error(self, mock_session):
        mock_session.get.side_effect = requests.RequestException("fail")
        assert rc.fetch_release_detail("stream", "tag") is None


class TestFetchPayload:
    @patch.object(rc, "fetch_release_detail")
    def test_builds_payload(self, mock_detail, config):
        mock_detail.return_value = {
            "results": {
                "blockingJobs": {
                    "periodic-sno-test": {"url": "https://prow/1", "state": "Failed"},
                },
                "informingJobs": {},
            }
        }
        tag_data = {"name": "4.19.0-0.nightly-2026-03-25-085944", "phase": "Rejected"}
        payload = rc.fetch_payload("4.19.0-0.nightly", tag_data, config)

        assert payload.tag == "4.19.0-0.nightly-2026-03-25-085944"
        assert payload.version == "4.19"
        assert payload.status == PayloadStatus.REJECTED
        assert len(payload.jobs) == 1
        assert payload.jobs[0].topology == "SNO"

    @patch.object(rc, "fetch_release_detail")
    def test_no_detail(self, mock_detail, config):
        mock_detail.return_value = None
        tag_data = {"name": "tag1", "phase": "Accepted"}
        payload = rc.fetch_payload("4.19.0-0.nightly", tag_data, config)
        assert payload.jobs == []


class TestCollectStream:
    @patch.object(rc, "fetch_payload")
    @patch.object(rc, "fetch_tags")
    def test_collect_stream(self, mock_tags, mock_payload, config):
        mock_tags.return_value = [
            {"name": "tag1", "phase": "Rejected"},
            {"name": "tag2", "phase": "Accepted"},
        ]

        from payload_monitor.models import Payload, PayloadStatus

        def make_payload(stream, tag_data, cfg):
            return Payload(
                tag=tag_data["name"], stream=stream, version="4.19",
                status=PayloadStatus.ACCEPTED, jobs=[],
            )

        mock_payload.side_effect = make_payload
        report = rc._collect_stream("4.19.0-0.nightly", config)

        assert report.version == "4.19"
        assert len(report.payloads) == 2

    @patch.object(rc, "fetch_tags")
    def test_collect_stream_no_tags(self, mock_tags, config):
        mock_tags.return_value = []
        report = rc._collect_stream("4.19.0-0.nightly", config)
        assert report.payloads == []

    @patch.object(rc, "fetch_payload")
    @patch.object(rc, "fetch_tags")
    def test_collect_stream_handles_individual_failure(self, mock_tags, mock_payload, config):
        mock_tags.return_value = [
            {"name": "tag1", "phase": "Rejected"},
            {"name": "tag2", "phase": "Accepted"},
        ]

        from payload_monitor.models import Payload, PayloadStatus

        call_count = 0

        def side_effect(stream, tag_data, cfg):
            nonlocal call_count
            call_count += 1
            if tag_data["name"] == "tag1":
                raise RuntimeError("fetch failed")
            return Payload(tag="tag2", stream=stream, version="4.19",
                           status=PayloadStatus.ACCEPTED, jobs=[])

        mock_payload.side_effect = side_effect
        report = rc._collect_stream("4.19.0-0.nightly", config)
        # tag1 failed but tag2 should still be collected
        assert len(report.payloads) == 1
        assert report.payloads[0].tag == "tag2"


class TestCollect:
    @patch.object(rc, "_collect_stream")
    def test_collect_preserves_order(self, mock_collect, config):
        config.versions = ["4.18", "4.19"]

        def make_report(stream, cfg):
            version = stream.split(".0-0.nightly")[0]
            return StreamReport(stream=stream, version=version, payloads=[])

        mock_collect.side_effect = make_report
        reports = rc.collect(config)
        assert [r.version for r in reports] == ["4.18", "4.19"]

    @patch.object(rc, "_collect_stream")
    def test_collect_handles_stream_failure(self, mock_collect, config):
        config.versions = ["4.18", "4.19"]

        def side_effect(stream, cfg):
            if "4.18" in stream:
                raise RuntimeError("stream failed")
            return StreamReport(stream=stream, version="4.19", payloads=[])

        mock_collect.side_effect = side_effect
        reports = rc.collect(config)
        assert len(reports) == 2
        # Failed stream should still appear as empty
        assert reports[0].payloads == []
        assert reports[1].version == "4.19"

    @patch.object(rc, "_collect_stream")
    def test_collect_caps_thread_pool(self, mock_collect, config):
        config.versions = [f"4.{i}" for i in range(20)]
        mock_collect.side_effect = lambda s, c: StreamReport(stream=s, version="", payloads=[])
        # Should not crash with many versions — pool capped at 10
        reports = rc.collect(config)
        assert len(reports) == 20
