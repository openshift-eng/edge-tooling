"""Data models for payload monitoring."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class PayloadStatus(Enum):
    ACCEPTED = "Accepted"
    REJECTED = "Rejected"
    PENDING = "Pending"


class JobType(Enum):
    BLOCKING = "blocking"
    INFORMING = "informing"


class JobResult(Enum):
    SUCCESS = "S"
    FAILURE = "F"
    PENDING = "P"
    UNKNOWN = "U"


@dataclass
class Topology:
    name: str
    job_patterns: list[str]
    exclude_patterns: list[str] = field(default_factory=list)
    jira_component: str = ""

    def matches(self, job_name: str) -> bool:
        job_lower = job_name.lower()
        if any(p in job_lower for p in self.exclude_patterns):
            return False
        return any(
            re.search(rf'(?:^|[-_]){re.escape(p)}(?:[-_]|$)', job_lower)
            for p in self.job_patterns
        )


@dataclass
class DeepAnalysis:
    root_cause: str = ""
    failure_type: str = ""
    impact: str = ""
    suspect_prs: list[str] = field(default_factory=list)
    recommendation: str = ""


@dataclass
class FailingTest:
    name: str
    error_message: str = ""
    duration_seconds: float = 0.0


@dataclass
class JobRun:
    name: str
    prow_url: str
    result: JobResult
    job_type: JobType
    topology: Optional[str] = None
    failing_tests: list[FailingTest] = field(default_factory=list)
    error_summary: str = ""
    deep_analysis: Optional[DeepAnalysis] = None


@dataclass
class Payload:
    tag: str
    stream: str
    version: str
    status: PayloadStatus
    url: str = ""
    jobs: list[JobRun] = field(default_factory=list)

    @property
    def edge_jobs(self) -> list[JobRun]:
        return self.jobs

    @property
    def failing_edge_jobs(self) -> list[JobRun]:
        return [j for j in self.jobs if j.result == JobResult.FAILURE]

    @property
    def blocking_edge_failures(self) -> list[JobRun]:
        return [j for j in self.jobs if j.result == JobResult.FAILURE and j.job_type == JobType.BLOCKING]

    @property
    def informing_edge_failures(self) -> list[JobRun]:
        return [j for j in self.jobs if j.result == JobResult.FAILURE and j.job_type == JobType.INFORMING]


@dataclass
class Regression:
    test_name: str
    test_id: str
    component: str
    capability: str
    basis_pass_rate: float
    sample_pass_rate: float
    variant: str = ""
    version: str = ""
    topology: str = ""
    triage_url: str = ""
    jira_bug: str = ""
    current_runs: int = 0


@dataclass
class ComponentRegression:
    component: str
    test_name: str
    test_suite: str
    test_id: str
    capability: str
    version: str
    variants: dict = field(default_factory=dict)
    status: int = 0
    sample_success: int = 0
    sample_failure: int = 0
    sample_pass_rate: float = 0.0
    base_success: int = 0
    base_failure: int = 0
    base_pass_rate: float = 0.0
    fisher_exact: float = 0.0
    last_failure: str = ""
    detail_url: str = ""
    explanation: str = ""


@dataclass
class JiraBug:
    key: str
    summary: str
    status: str
    assignee: str = ""
    priority: str = ""
    url: str = ""
    component: str = ""


@dataclass
class SuggestedBug:
    title: str
    description: str
    job_name: str
    topology: str
    versions: list[str] = field(default_factory=list)
    failing_tests: list[str] = field(default_factory=list)
    create_url: str = ""
    prow_url: str = ""
    full_description: str = ""


@dataclass
class StreamReport:
    stream: str
    version: str
    payloads: list[Payload] = field(default_factory=list)
    regressions: list[Regression] = field(default_factory=list)

    @property
    def latest_payload(self) -> Optional[Payload]:
        return self.payloads[0] if self.payloads else None

    @property
    def total_edge_failures(self) -> int:
        return sum(len(p.failing_edge_jobs) for p in self.payloads)

    @property
    def total_blocking_edge_failures(self) -> int:
        return sum(len(p.blocking_edge_failures) for p in self.payloads)

    @property
    def total_informing_edge_failures(self) -> int:
        return sum(len(p.informing_edge_failures) for p in self.payloads)


@dataclass
class MonitorReport:
    generated_at: str = ""
    streams: list[StreamReport] = field(default_factory=list)
    jira_bugs: list[JiraBug] = field(default_factory=list)
    suggested_bugs: list[SuggestedBug] = field(default_factory=list)
    component_regressions: list[ComponentRegression] = field(default_factory=list)
    skip_prow: bool = False
    skip_sippy: bool = False
    skip_jira: bool = False
    skip_timing: bool = False
    timing_report: Optional[TimingReport] = None
    data_errors: list[str] = field(default_factory=list)


@dataclass
class TimingRun:
    job_name: str
    topology: str
    release: str
    start_time: str
    duration_seconds: int
    result: str  # "S" or "F"
    run_type: str  # "install" or "upgrade"
    variant: dict = field(default_factory=dict)
    step_durations: dict[str, float] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return self.result == "S"

    @property
    def duration_minutes(self) -> float:
        return self.duration_seconds / 60.0

    @property
    def install_duration_seconds(self) -> float:
        """Return install step duration if available, else 0."""
        for key in ("install", "setup"):
            if key in self.step_durations:
                return self.step_durations[key]
        return 0.0


@dataclass
class TimingReport:
    last_updated: str = ""
    runs: dict[str, TimingRun] = field(default_factory=dict)
    phase_durations: dict[str, dict[str, float]] = field(default_factory=dict)

    @property
    def successful_runs(self) -> list[TimingRun]:
        return [r for r in self.runs.values() if r.is_success]
