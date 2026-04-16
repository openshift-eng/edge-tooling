"""Shared fixtures for payload-monitor tests."""

import pytest

from payload_monitor.config import Config
from payload_monitor.models import (
    ComponentRegression,
    DeepAnalysis,
    FailingTest,
    JobResult,
    JobRun,
    JobType,
    JiraBug,
    MonitorReport,
    Payload,
    PayloadStatus,
    Regression,
    StreamReport,
    SuggestedBug,
    Topology,
)


@pytest.fixture
def config():
    return Config()


@pytest.fixture
def sample_topology():
    return Topology("SNO", ["sno", "single-node"], ["telco"], "SNO")


@pytest.fixture
def failing_job():
    return JobRun(
        name="periodic-ci-openshift-release-master-nightly-4.19-e2e-metal-single-node-live-iso",
        prow_url="https://prow.ci.openshift.org/view/gs/test-platform-results/logs/periodic-ci-openshift-release-master-nightly-4.19-e2e-metal-single-node-live-iso/12345",
        result=JobResult.FAILURE,
        job_type=JobType.BLOCKING,
        topology="SNO",
        failing_tests=[
            FailingTest(
                name="openshift-tests.[sig-auth] OAuth server",
                error_message="expected 200, got 503",
                duration_seconds=12.5,
            ),
        ],
        error_summary="oauth: expected 200, got 503",
    )


@pytest.fixture
def success_job():
    return JobRun(
        name="periodic-ci-openshift-release-master-nightly-4.19-e2e-metal-single-node-live-iso",
        prow_url="https://prow.ci.openshift.org/view/gs/test-platform-results/logs/job/99999",
        result=JobResult.SUCCESS,
        job_type=JobType.BLOCKING,
        topology="SNO",
    )


@pytest.fixture
def sample_payload(failing_job, success_job):
    return Payload(
        tag="4.19.0-0.nightly-2026-03-25-085944",
        stream="4.19.0-0.nightly",
        version="4.19",
        status=PayloadStatus.REJECTED,
        url="https://amd64.ocp.releases.ci.openshift.org/releasestream/4.19.0-0.nightly/release/4.19.0-0.nightly-2026-03-25-085944",
        jobs=[failing_job, success_job],
    )


@pytest.fixture
def sample_stream(sample_payload):
    return StreamReport(
        stream="4.19.0-0.nightly",
        version="4.19",
        payloads=[sample_payload],
    )


@pytest.fixture
def sample_report(sample_stream):
    return MonitorReport(
        generated_at="2026-03-25 12:00 UTC",
        streams=[sample_stream],
    )
