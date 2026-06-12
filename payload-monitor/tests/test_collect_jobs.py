"""Tests for _collect_jobs_by_type and _emit_job_section in payload_monitor.__main__."""

import pytest

from payload_monitor.__main__ import _collect_jobs_by_type, _emit_job_section
from payload_monitor.models import (
    JobResult,
    JobRun,
    JobType,
    MonitorReport,
    Payload,
    PayloadStatus,
    StreamReport,
)


def _make_report(*streams):
    return MonitorReport(streams=list(streams))


def _make_stream(version, payloads):
    return StreamReport(
        stream=f"{version}.0-0.nightly",
        version=version,
        payloads=payloads,
    )


def _make_payload(tag, jobs):
    return Payload(
        tag=tag,
        stream="nightly",
        version="",
        status=PayloadStatus.REJECTED,
        jobs=jobs,
    )


class TestCollectJobsByType:
    def test_collects_blocking_failures(self):
        b_fail = JobRun("block-job", "https://prow/1", JobResult.FAILURE, JobType.BLOCKING, "SNO")
        i_fail = JobRun("inform-job", "https://prow/2", JobResult.FAILURE, JobType.INFORMING, "TNA")
        payload = _make_payload("4.19.0-0.nightly-2026-04-01", [b_fail, i_fail])
        report = _make_report(_make_stream("4.19", [payload]))

        result = _collect_jobs_by_type(report, JobType.BLOCKING)

        assert len(result) == 1
        assert result[0]["name"] == "block-job"
        assert result[0]["version"] == "4.19"
        assert result[0]["topology"] == "SNO"

    def test_collects_informing_failures(self):
        b_fail = JobRun("block-job", "https://prow/1", JobResult.FAILURE, JobType.BLOCKING, "SNO")
        i_fail = JobRun("inform-job", "https://prow/2", JobResult.FAILURE, JobType.INFORMING, "TNA")
        payload = _make_payload("4.19.0-0.nightly-2026-04-01", [b_fail, i_fail])
        report = _make_report(_make_stream("4.19", [payload]))

        result = _collect_jobs_by_type(report, JobType.INFORMING)

        assert len(result) == 1
        assert result[0]["name"] == "inform-job"
        assert result[0]["topology"] == "TNA"

    def test_skips_successful_jobs(self):
        success = JobRun("ok-job", "https://prow/1", JobResult.SUCCESS, JobType.BLOCKING, "SNO")
        payload = _make_payload("4.19.0-0.nightly-2026-04-01", [success])
        report = _make_report(_make_stream("4.19", [payload]))

        assert _collect_jobs_by_type(report, JobType.BLOCKING) == []
        assert _collect_jobs_by_type(report, JobType.INFORMING) == []

    def test_empty_report(self):
        report = _make_report()
        assert _collect_jobs_by_type(report, JobType.BLOCKING) == []

    def test_multiple_streams_and_payloads(self):
        b1 = JobRun("b1", "https://prow/1", JobResult.FAILURE, JobType.BLOCKING, "SNO")
        b2 = JobRun("b2", "https://prow/2", JobResult.FAILURE, JobType.BLOCKING, "TNA")
        i1 = JobRun("i1", "https://prow/3", JobResult.FAILURE, JobType.INFORMING, "SNO")

        p1 = _make_payload("4.19.0-0.nightly-2026-04-01", [b1, i1])
        p2 = _make_payload("4.20.0-0.nightly-2026-04-01", [b2])

        report = _make_report(
            _make_stream("4.19", [p1]),
            _make_stream("4.20", [p2]),
        )

        blocking = _collect_jobs_by_type(report, JobType.BLOCKING)
        assert len(blocking) == 2
        assert {b["version"] for b in blocking} == {"4.19", "4.20"}

        informing = _collect_jobs_by_type(report, JobType.INFORMING)
        assert len(informing) == 1
        assert informing[0]["version"] == "4.19"

    def test_topology_none_becomes_empty_string(self):
        job = JobRun("job", "https://prow/1", JobResult.FAILURE, JobType.BLOCKING, None)
        payload = _make_payload("4.19.0-0.nightly-2026-04-01", [job])
        report = _make_report(_make_stream("4.19", [payload]))

        result = _collect_jobs_by_type(report, JobType.BLOCKING)
        assert result[0]["topology"] == ""

    def test_output_dict_keys(self):
        job = JobRun("j", "https://prow/1", JobResult.FAILURE, JobType.BLOCKING, "SNO")
        payload = _make_payload("4.19.0-0.nightly-tag", [job])
        report = _make_report(_make_stream("4.19", [payload]))

        result = _collect_jobs_by_type(report, JobType.BLOCKING)
        assert set(result[0].keys()) == {"name", "prow_url", "topology", "version", "payload_tag", "previous_attempt_urls"}
        assert result[0]["payload_tag"] == "4.19.0-0.nightly-tag"
        assert result[0]["prow_url"] == "https://prow/1"
        assert result[0]["previous_attempt_urls"] == []


class TestEmitJobSection:
    def test_emits_blocking_section(self, capsys):
        jobs = [{"name": "j1", "prow_url": "https://prow/1", "topology": "SNO", "version": "4.19", "payload_tag": "tag1"}]
        _emit_job_section("BLOCKING", jobs)
        lines = capsys.readouterr().out.strip().splitlines()
        assert lines[0] == "BLOCKING_JOBS_START"
        assert lines[1] == "BLOCKING|j1|https://prow/1|SNO|4.19|tag1|"
        assert lines[2] == "BLOCKING_JOBS_END"

    def test_emits_informing_section(self, capsys):
        jobs = [{"name": "j1", "prow_url": "https://prow/2", "topology": "TNA", "version": "4.20", "payload_tag": "tag2"}]
        _emit_job_section("INFORMING", jobs)
        lines = capsys.readouterr().out.strip().splitlines()
        assert lines[0] == "INFORMING_JOBS_START"
        assert lines[1] == "INFORMING|j1|https://prow/2|TNA|4.20|tag2|"
        assert lines[2] == "INFORMING_JOBS_END"

    def test_emits_previous_attempt_urls(self, capsys):
        jobs = [{"name": "j1", "prow_url": "https://prow/3", "topology": "SNO", "version": "4.19",
                 "payload_tag": "tag1", "previous_attempt_urls": ["https://prow/1", "https://prow/2"]}]
        _emit_job_section("BLOCKING", jobs)
        lines = capsys.readouterr().out.strip().splitlines()
        assert lines[1] == "BLOCKING|j1|https://prow/3|SNO|4.19|tag1|https://prow/1;https://prow/2"

    def test_empty_list_produces_no_output(self, capsys):
        _emit_job_section("BLOCKING", [])
        assert capsys.readouterr().out == ""

    def test_multiple_jobs(self, capsys):
        jobs = [
            {"name": "j1", "prow_url": "https://prow/1", "topology": "SNO", "version": "4.19", "payload_tag": "t1"},
            {"name": "j2", "prow_url": "https://prow/2", "topology": "TNA", "version": "4.20", "payload_tag": "t2"},
        ]
        _emit_job_section("BLOCKING", jobs)
        lines = capsys.readouterr().out.strip().splitlines()
        assert len(lines) == 4
        assert lines[0] == "BLOCKING_JOBS_START"
        assert "j1" in lines[1]
        assert "j2" in lines[2]
        assert lines[3] == "BLOCKING_JOBS_END"