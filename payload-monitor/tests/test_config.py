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

    def test_sno_matches_canonical_tokens(self, config):
        for name in [
            "periodic-ci-e2e-metal-sno-test",
            "periodic-ci-e2e-single-node-test",
            "periodic-ci-e2e-metal-single-node-upgrade",
        ]:
            assert config.classify_topology(name) == "SNO"

    def test_sno_excludes_telco(self, config):
        assert config.classify_topology("periodic-ci-telco-sno-test") is None

    def test_sno_excludes_f7(self, config):
        assert config.classify_topology("periodic-ci-e2e-sno-f7-test") is None

    def test_sno_excludes_oidc(self, config):
        assert config.classify_topology("periodic-ci-e2e-sno-oidc-test") is None

    def test_sno_excludes_recert(self, config):
        assert config.classify_topology("periodic-ci-e2e-sno-recert-test") is None

    def test_sno_excludes_multi_a_a(self, config):
        assert config.classify_topology("periodic-ci-e2e-sno-multi-a-a-test") is None

    def test_sno_excludes_csi(self, config):
        assert config.classify_topology("periodic-ci-e2e-sno-csi-test") is None

    def test_sno_excludes_matrix(self, config):
        assert config.classify_topology("periodic-ci-e2e-sno-matrix-test") is None

    def test_sno_excludes_cert_rotation(self, config):
        assert config.classify_topology("periodic-ci-e2e-sno-cert-rotation-test") is None

    def test_sno_excludes_insights_operator(self, config):
        assert config.classify_topology("periodic-ci-e2e-sno-insights-operator-test") is None

    def test_versions_are_independent(self):
        c1 = Config()
        c2 = Config()
        c1.versions.append("9.99")
        assert "9.99" not in c2.versions
