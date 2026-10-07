"""Tests for workstream_map.py — the internal workstream -> Jira component map.

Covers happy-path lookups, failure inputs (unknown/None/empty), and the
boundary/anti-cheat cases: case-insensitivity, whitespace, multi-component
aliases, and the explicitly-excluded ``Planning`` component.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import workstream_map  # noqa: E402

# The canonical map, restated here independently of the module so the test is a
# real oracle rather than a mirror of the implementation.
EXPECTED_ALIAS_TO_ACRONYM = {
    "SNO": "SNO",
    "Two Node with Arbiter": "TNA",
    "TNF": "TNF",
    "Two Node Fencing": "TNF",
    "Logical Volume Manager Storage": "LVMS",
    "MicroShift": "USHIFT",
    "Topology Transitions": "TOPO",
    "Mutable Topology": "TOPO",
}
EXPECTED_ACRONYMS_IN_ORDER = ["SNO", "TNA", "TNF", "LVMS", "USHIFT", "TOPO"]


class TestWorkstreamsHappyPath(unittest.TestCase):
    def test_component_to_workstream_maps_exact_names(self):
        assert workstream_map.component_to_workstream("SNO") == "SNO"
        assert workstream_map.component_to_workstream("Two Node Fencing") == "TNF"
        assert workstream_map.component_to_workstream("MicroShift") == "USHIFT"

    def test_workstream_acronyms_returns_six_in_canonical_order(self):
        assert workstream_map.workstream_acronyms() == EXPECTED_ACRONYMS_IN_ORDER

    def test_workstreams_returns_six_entries_with_name_and_components(self):
        streams = workstream_map.workstreams()
        assert len(streams) == 6
        for stream in streams:
            assert stream.acronym
            assert stream.name
            assert len(stream.components) >= 1


class TestWorkstreamsFailureInputs(unittest.TestCase):
    def test_unknown_component_returns_none(self):
        assert workstream_map.component_to_workstream("Foobar") is None

    def test_none_component_returns_none_without_raising(self):
        assert workstream_map.component_to_workstream(None) is None

    def test_empty_string_component_returns_none(self):
        assert workstream_map.component_to_workstream("") is None

    def test_whitespace_only_component_returns_none(self):
        assert workstream_map.component_to_workstream("   ") is None


class TestWorkstreamsEdgeCases(unittest.TestCase):
    def test_lookup_is_case_insensitive(self):
        assert workstream_map.component_to_workstream("sno") == "SNO"
        assert workstream_map.component_to_workstream("microSHIFT") == "USHIFT"

    def test_lookup_trims_surrounding_whitespace(self):
        assert workstream_map.component_to_workstream("  TNF  ") == "TNF"

    def test_tnf_aliases_both_map_to_tnf(self):
        assert workstream_map.component_to_workstream("TNF") == "TNF"
        assert workstream_map.component_to_workstream("Two Node Fencing") == "TNF"

    def test_topology_aliases_both_map_to_topo(self):
        assert workstream_map.component_to_workstream("Topology Transitions") == "TOPO"
        assert workstream_map.component_to_workstream("Mutable Topology") == "TOPO"

    def test_planning_component_is_excluded(self):
        assert workstream_map.component_to_workstream("Planning") is None

    def test_every_known_alias_maps_as_expected(self):
        for alias, acronym in EXPECTED_ALIAS_TO_ACRONYM.items():
            assert workstream_map.component_to_workstream(alias) == acronym, alias


class TestDisplayColumns(unittest.TestCase):
    def test_returns_six_workstreams_plus_shared(self):
        cols = workstream_map.display_columns()
        assert len(cols) == 7

    def test_ends_with_shared_column(self):
        cols = workstream_map.display_columns()
        assert cols[-1] == workstream_map.SHARED_COLUMN

    def test_shared_not_in_workstream_acronyms(self):
        """SHARED is a display column but does not count toward workstreams touched."""
        acronyms = workstream_map.workstream_acronyms()
        assert len(acronyms) == 6
        assert workstream_map.SHARED_COLUMN not in acronyms

    def test_mutating_returned_list_does_not_affect_module_state(self):
        cols1 = workstream_map.display_columns()
        cols1.append("MUTATED")
        cols2 = workstream_map.display_columns()
        assert len(cols2) == 7
        assert "MUTATED" not in cols2


class TestProjectToWorkstream(unittest.TestCase):
    def test_ushift_project_maps_to_ushift(self):
        assert workstream_map.project_to_workstream("USHIFT-6337") == "USHIFT"

    def test_lowercase_issue_key_works(self):
        assert workstream_map.project_to_workstream("ushift-1") == "USHIFT"

    def test_unknown_project_returns_none(self):
        assert workstream_map.project_to_workstream("OCPEDGE-123") is None

    def test_none_returns_none(self):
        assert workstream_map.project_to_workstream(None) is None

    def test_empty_string_returns_none(self):
        assert workstream_map.project_to_workstream("") is None

    def test_malformed_key_no_dash_returns_none(self):
        assert workstream_map.project_to_workstream("NODASH") is None

    def test_only_dash_returns_none(self):
        assert workstream_map.project_to_workstream("-5") is None

    def test_whitespace_tolerant(self):
        assert workstream_map.project_to_workstream("  USHIFT-42  ") == "USHIFT"


class TestRepoToWorkstream(unittest.TestCase):
    def test_lvm_operator_maps_to_lvms(self):
        assert workstream_map.repo_to_workstream("openshift/lvm-operator") == "LVMS"

    def test_topolvm_maps_to_lvms(self):
        assert workstream_map.repo_to_workstream("openshift/topolvm") == "LVMS"

    def test_openshift_microshift_maps_to_ushift(self):
        assert workstream_map.repo_to_workstream("openshift/microshift") == "USHIFT"

    def test_microshift_io_microshift_maps_to_ushift(self):
        assert workstream_map.repo_to_workstream("microshift-io/microshift") == "USHIFT"

    def test_oc_tnf_maps_to_tnf(self):
        assert workstream_map.repo_to_workstream("openshift/oc-tnf") == "TNF"

    def test_case_insensitive(self):
        assert workstream_map.repo_to_workstream("OpenShift/LVM-Operator") == "LVMS"

    def test_unknown_repo_returns_none(self):
        assert workstream_map.repo_to_workstream("openshift/unknown") is None

    def test_none_returns_none(self):
        assert workstream_map.repo_to_workstream(None) is None

    def test_empty_string_returns_none(self):
        assert workstream_map.repo_to_workstream("") is None

    def test_two_node_toolbox_is_deliberately_unmapped(self):
        """Regression pin: two-node-toolbox covers both TNA and TNF, so it must not be mapped."""
        assert workstream_map.repo_to_workstream("openshift-eng/two-node-toolbox") is None


class TestIsSharedRepo(unittest.TestCase):
    def test_openshift_release_is_shared(self):
        assert workstream_map.is_shared_repo("openshift/release") is True

    def test_edge_tooling_is_shared(self):
        assert workstream_map.is_shared_repo("openshift-eng/edge-tooling") is True

    def test_edge_context_is_shared(self):
        assert workstream_map.is_shared_repo("openshift-eng/edge-context") is True

    def test_openshift_docs_is_shared(self):
        assert workstream_map.is_shared_repo("openshift/openshift-docs") is True

    def test_enhancements_is_shared(self):
        assert workstream_map.is_shared_repo("openshift/enhancements") is True

    def test_microshift_not_shared(self):
        assert workstream_map.is_shared_repo("openshift/microshift") is False

    def test_none_returns_false(self):
        assert workstream_map.is_shared_repo(None) is False

    def test_empty_string_returns_false(self):
        assert workstream_map.is_shared_repo("") is False


class TestIsExcludedRepo(unittest.TestCase):
    def test_personal_namespaces_are_excluded(self):
        assert workstream_map.is_excluded_repo("jeff-roche/roundhouse") is True
        assert workstream_map.is_excluded_repo("jaypoulz/edge-tooling") is True
        assert workstream_map.is_excluded_repo("eggfoobar/two-node-toolbox") is True

    def test_openshift_org_not_excluded(self):
        assert workstream_map.is_excluded_repo("openshift/foo") is False

    def test_openshift_eng_org_not_excluded(self):
        assert workstream_map.is_excluded_repo("openshift-eng/bar") is False

    def test_microshift_io_org_not_excluded(self):
        assert workstream_map.is_excluded_repo("microshift-io/baz") is False

    def test_openshift_metal3_org_not_excluded(self):
        assert workstream_map.is_excluded_repo("openshift-metal3/qux") is False

    def test_metal3_io_org_not_excluded(self):
        assert workstream_map.is_excluded_repo("metal3-io/quux") is False

    def test_containers_org_not_excluded(self):
        assert workstream_map.is_excluded_repo("containers/podman") is False

    def test_clusterlabs_org_not_excluded(self):
        assert workstream_map.is_excluded_repo("clusterlabs/pacemaker") is False

    def test_ovn_kubernetes_org_not_excluded(self):
        assert workstream_map.is_excluded_repo("ovn-kubernetes/ovn") is False

    def test_opendatahub_io_org_not_excluded(self):
        assert workstream_map.is_excluded_repo("opendatahub-io/opendatahub") is False

    def test_kubevirt_org_not_excluded(self):
        # Real org, not a personal namespace. Team members carry OCPBUGS work
        # into hyperconverged-cluster-operator; dropping it loses real PRs.
        assert workstream_map.is_excluded_repo("kubevirt/hyperconverged-cluster-operator") is False

    def test_none_returns_false(self):
        assert workstream_map.is_excluded_repo(None) is False

    def test_empty_string_returns_false(self):
        assert workstream_map.is_excluded_repo("") is False

    def test_malformed_repo_no_slash_returns_false(self):
        assert workstream_map.is_excluded_repo("noslash") is False


class TestIndexSanity(unittest.TestCase):
    """Sanity checks on the index constants to catch configuration errors."""

    def test_no_overlap_between_repo_index_and_shared_repos(self):
        """A repo cannot be both workstream-specific and shared."""
        repo_index_repos = {repo.lower() for repo in workstream_map._REPO_INDEX.keys()}
        shared_repos = {repo.lower() for repo in workstream_map._SHARED_REPOS}
        overlap = repo_index_repos & shared_repos
        assert len(overlap) == 0, f"Repos in both _REPO_INDEX and _SHARED_REPOS: {overlap}"

    def test_all_indexed_repos_are_allowed_orgs(self):
        """Every repo in _REPO_INDEX and _SHARED_REPOS must be from an allowed org."""
        all_repos = set(workstream_map._REPO_INDEX.keys()) | workstream_map._SHARED_REPOS
        for repo in all_repos:
            owner = repo.split("/")[0].lower()
            assert owner in workstream_map._ALLOWED_ORGS, \
                f"Repo {repo} has owner {owner} not in _ALLOWED_ORGS"


if __name__ == "__main__":
    unittest.main()
