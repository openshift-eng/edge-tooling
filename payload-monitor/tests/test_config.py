"""Tests for payload_monitor.config."""

from payload_monitor.config import (
    Config,
    CR_VIEWS,
    VERSIONS,
    PAYLOADS_PER_STREAM,
    JIRA_PROJECT,
    RECURRING_THRESHOLD,
    PERSISTENT_THRESHOLD,
)


class TestConfig:
    def test_default_versions(self, config):
        assert config.versions == VERSIONS

    def test_default_payloads_per_stream(self, config):
        assert config.payloads_per_stream == PAYLOADS_PER_STREAM

    def test_default_jira_project(self, config):
        assert config.jira_project == JIRA_PROJECT

    def test_classify_topology_sno(self, config):
        assert config.classify_topology("periodic-ci-e2e-metal-single-node-test") == "SNO"

    def test_classify_topology_tna(self, config):
        assert config.classify_topology("periodic-ci-e2e-metal-ipi-arbiter-test") == "TNA"

    def test_classify_topology_tnf(self, config):
        assert config.classify_topology("periodic-ci-e2e-metal-fencing-test") == "TNF"

    def test_classify_topology_none(self, config):
        assert config.classify_topology("periodic-ci-e2e-aws-ovn-upgrade") is None

    def test_jira_component_for_known(self, config):
        assert config.jira_component_for("SNO") == "SNO"

    def test_jira_component_for_unknown(self, config):
        assert config.jira_component_for("NONEXISTENT") == ""

    def test_recurring_threshold_constant(self):
        assert RECURRING_THRESHOLD == 2

    def test_persistent_threshold_constant(self):
        assert PERSISTENT_THRESHOLD == 3

    def test_cr_views_include_sno_and_tnf(self):
        topologies = {v["topology"] for v in CR_VIEWS}
        assert "SNO" in topologies
        assert "TNF" in topologies

    def test_cr_views_have_pattern_and_topology(self):
        for view in CR_VIEWS:
            assert "pattern" in view
            assert "topology" in view
            assert "{version}" in view["pattern"]

    def test_versions_are_independent(self):
        c1 = Config()
        c2 = Config()
        c1.versions.append("9.99")
        assert "9.99" not in c2.versions
