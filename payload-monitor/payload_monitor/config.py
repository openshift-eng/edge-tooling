"""Configuration defaults for Edge OCP Payload Monitor."""

from dataclasses import dataclass, field
from typing import Optional

from .models import Topology

# Hardcoded list of OCP versions to monitor
VERSIONS = ["4.18", "4.19", "4.20", "4.21", "4.22", "4.23", "5.0"]

TOPOLOGIES = [
    Topology("SNO", ["sno", "single-node", "metal-single-node"], ["telco"], "SNO"),
    Topology("TNA", ["tna", "arbiter"], ["telco"], "Two Node with Arbiter"),
    Topology("TNF", ["tnf", "fencing"], ["telco"], "Two Node Fencing"),
]

INSTALL_PHASES = [
    "install should succeed: cluster bootstrap",
    "install should succeed: cluster creation",
    "install should succeed: infrastructure",
    "install should succeed: configuration",
    "install should succeed: cluster operator stability",
    "install should succeed: pre configuration",
    "install should succeed: post check",
    "install should succeed: overall",
]

# Component Readiness views — topology comparisons against HA baseline
CR_VIEWS = [
    {"pattern": "{version}-ha-vs-single", "topology": "SNO"},
    {"pattern": "{version}-ha-vs-two-node-fencing", "topology": "TNF"},
]

JIRA_PROJECT = "OCPBUGS"
PAYLOADS_PER_STREAM = 6
REPORT_DIR = "./reports"
ESCALATION_THRESHOLD = 3
RECURRING_THRESHOLD = 2
PERSISTENT_THRESHOLD = 3


@dataclass
class Config:
    versions: list[str] = field(default_factory=lambda: list(VERSIONS))
    topologies: list[Topology] = field(default_factory=lambda: list(TOPOLOGIES))
    payloads_per_stream: int = PAYLOADS_PER_STREAM
    jira_project: str = JIRA_PROJECT
    report_dir: str = REPORT_DIR
    escalation_threshold: int = ESCALATION_THRESHOLD
    recurring_threshold: int = RECURRING_THRESHOLD
    persistent_threshold: int = PERSISTENT_THRESHOLD

    def classify_topology(self, job_name: str) -> Optional[str]:
        """Return the topology name if job_name matches any configured topology."""
        for topo in self.topologies:
            if topo.matches(job_name):
                return topo.name
        return None

    def jira_component_for(self, topology_name: str) -> str:
        """Return the JIRA component for a topology name, or empty string."""
        for topo in self.topologies:
            if topo.name == topology_name:
                return topo.jira_component
        return ""
