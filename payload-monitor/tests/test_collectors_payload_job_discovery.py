"""Tests for payload_monitor.collectors.payload_job_discovery."""

import json
from unittest.mock import patch

import pytest
import yaml

from payload_monitor.collectors import payload_job_discovery
from payload_monitor.collectors.payload_job_discovery import (
    DiscoveryResult,
    PayloadJobSuggestion,
    _parse_pr_ref,
    detect_version,
    discover_payload_jobs,
    format_discovery_json,
    format_discovery_markdown,
    load_nightly_job_names,
    load_repo_ci_config,
    match_changed_files,
    validate_existing_command,
)


class TestParsePrRef:
    def test_github_url(self):
        result = _parse_pr_ref("https://github.com/openshift/origin/pull/31276")
        assert result == ("openshift", "origin", "31276")

    def test_short_ref(self):
        result = _parse_pr_ref("openshift/origin#31276")
        assert result == ("openshift", "origin", "31276")

    def test_url_with_hash(self):
        result = _parse_pr_ref("openshift/installer#10546")
        assert result == ("openshift", "installer", "10546")

    def test_invalid_string(self):
        assert _parse_pr_ref("not-a-ref") is None

    def test_empty_string(self):
        assert _parse_pr_ref("") is None

    def test_url_missing_number(self):
        assert _parse_pr_ref("https://github.com/openshift/origin/pull/") is None


class TestDetectVersion:
    def test_release_branch(self, tmp_path):
        assert detect_version(str(tmp_path), "release-4.19") == "4.19"

    def test_release_branch_older(self, tmp_path):
        assert detect_version(str(tmp_path), "release-4.17") == "4.17"

    def test_main_branch_latest_version(self, tmp_path):
        nightly_dir = tmp_path / "ci-operator/config/openshift/release"
        nightly_dir.mkdir(parents=True)
        (nightly_dir / "openshift-release-main__nightly-4.18.yaml").write_text(
            yaml.dump({"tests": [{"as": "e2e-test"}]})
        )
        (nightly_dir / "openshift-release-main__nightly-4.19.yaml").write_text(
            yaml.dump({"tests": [{"as": "e2e-test"}]})
        )
        (nightly_dir / "openshift-release-main__nightly-4.17.yaml").write_text(
            yaml.dump({"tests": [{"as": "e2e-test"}]})
        )
        result = detect_version(str(tmp_path), "main")
        assert result == "4.19"

    def test_main_no_nightly_dir(self, tmp_path):
        assert detect_version(str(tmp_path), "main") is None

    def test_main_empty_nightly_dir(self, tmp_path):
        nightly_dir = tmp_path / "ci-operator/config/openshift/release"
        nightly_dir.mkdir(parents=True)
        assert detect_version(str(tmp_path), "main") is None


class TestLoadRepoCiConfig:
    def test_valid_config(self, tmp_path):
        config_dir = tmp_path / "ci-operator/config/openshift/origin"
        config_dir.mkdir(parents=True)
        config = {
            "tests": [
                {"as": "e2e-test", "pipeline_run_if_changed": "pkg/"},
                {"as": "unit", "run_if_changed": "test/"},
            ]
        }
        (config_dir / "openshift-origin-main.yaml").write_text(yaml.dump(config))

        result = load_repo_ci_config(str(tmp_path), "openshift", "origin", "main")
        assert len(result) == 2
        assert result[0]["as"] == "e2e-test"

    def test_missing_file(self, tmp_path):
        result = load_repo_ci_config(str(tmp_path), "openshift", "origin", "main")
        assert result == []

    def test_no_tests_key(self, tmp_path):
        config_dir = tmp_path / "ci-operator/config/openshift/origin"
        config_dir.mkdir(parents=True)
        (config_dir / "openshift-origin-main.yaml").write_text(yaml.dump({"zz_generated_metadata": {}}))

        result = load_repo_ci_config(str(tmp_path), "openshift", "origin", "main")
        assert result == []

    def test_empty_yaml(self, tmp_path):
        config_dir = tmp_path / "ci-operator/config/openshift/origin"
        config_dir.mkdir(parents=True)
        (config_dir / "openshift-origin-main.yaml").write_text("")

        result = load_repo_ci_config(str(tmp_path), "openshift", "origin", "main")
        assert result == []


class TestMatchChangedFiles:
    def test_matching_files(self):
        entries = [
            {"as": "e2e-fencing", "pipeline_run_if_changed": r"test/extended/two_node/"},
        ]
        changed = ["test/extended/two_node/fencing_test.go", "README.md"]
        result = match_changed_files(entries, changed)
        assert len(result) == 1
        assert result[0]["as"] == "e2e-fencing"
        assert result[0]["_matched_files"] == ["test/extended/two_node/fencing_test.go"]

    def test_no_match(self):
        entries = [
            {"as": "e2e-fencing", "pipeline_run_if_changed": r"test/extended/two_node/"},
        ]
        changed = ["pkg/util/helper.go"]
        assert match_changed_files(entries, changed) == []

    def test_no_regex_entries(self):
        entries = [{"as": "unit", "steps": {}}]
        assert match_changed_files(entries, ["file.go"]) == []

    def test_bad_regex(self):
        entries = [
            {"as": "bad", "pipeline_run_if_changed": "[invalid(regex"},
        ]
        result = match_changed_files(entries, ["anything"])
        assert result == []

    def test_run_if_changed_fallback(self):
        entries = [
            {"as": "e2e", "run_if_changed": r"vendor/"},
        ]
        changed = ["vendor/k8s/client.go"]
        result = match_changed_files(entries, changed)
        assert len(result) == 1

    def test_multiple_matches(self):
        entries = [
            {"as": "e2e-a", "pipeline_run_if_changed": r"pkg/"},
            {"as": "e2e-b", "pipeline_run_if_changed": r"test/"},
        ]
        changed = ["pkg/foo.go", "test/bar.go"]
        result = match_changed_files(entries, changed)
        assert len(result) == 2


class TestLoadNightlyJobNames:
    def test_valid_config(self, tmp_path):
        nightly_dir = tmp_path / "ci-operator/config/openshift/release"
        nightly_dir.mkdir(parents=True)
        config = {
            "tests": [
                {"as": "e2e-metal-two-node-fencing"},
                {"as": "e2e-metal-single-node"},
            ]
        }
        (nightly_dir / "openshift-release-main__nightly-4.19.yaml").write_text(
            yaml.dump(config)
        )
        result = load_nightly_job_names(str(tmp_path), "4.19")
        assert result == {"e2e-metal-two-node-fencing", "e2e-metal-single-node"}

    def test_missing_file(self, tmp_path):
        assert load_nightly_job_names(str(tmp_path), "4.19") == set()

    def test_no_tests_key(self, tmp_path):
        nightly_dir = tmp_path / "ci-operator/config/openshift/release"
        nightly_dir.mkdir(parents=True)
        (nightly_dir / "openshift-release-main__nightly-4.19.yaml").write_text(
            yaml.dump({"metadata": {}})
        )
        assert load_nightly_job_names(str(tmp_path), "4.19") == set()


class TestValidateExistingCommand:
    def test_matching_periodic_name(self):
        suggestions = [
            PayloadJobSuggestion(
                as_name="e2e-metal-two-node-fencing",
                periodic_name="periodic-ci-openshift-release-main-nightly-4.19-e2e-metal-two-node-fencing",
                trigger_command="/payload-job periodic-ci-openshift-release-main-nightly-4.19-e2e-metal-two-node-fencing",
                matched_by="regex",
            ),
        ]
        result = validate_existing_command(
            "/payload-job periodic-ci-openshift-release-main-nightly-4.19-e2e-metal-two-node-fencing",
            suggestions,
        )
        assert result["is_valid"] is True

    def test_matching_as_name_substring(self):
        suggestions = [
            PayloadJobSuggestion(
                as_name="e2e-metal-two-node-fencing",
                periodic_name="periodic-ci-openshift-release-main-nightly-4.19-e2e-metal-two-node-fencing",
                trigger_command="/payload-job ...",
                matched_by="regex",
            ),
        ]
        result = validate_existing_command(
            "/payload-job some-prefix-e2e-metal-two-node-fencing",
            suggestions,
        )
        assert result["is_valid"] is True

    def test_no_match(self):
        suggestions = [
            PayloadJobSuggestion(
                as_name="e2e-metal-two-node-fencing",
                periodic_name="periodic-ci-openshift-release-main-nightly-4.19-e2e-metal-two-node-fencing",
                trigger_command="/payload-job ...",
                matched_by="regex",
            ),
        ]
        result = validate_existing_command(
            "/payload-job some-completely-different-job",
            suggestions,
        )
        assert result["is_valid"] is False
        assert "e2e-metal-two-node-fencing" in result["note"]

    def test_empty_suggestions(self):
        result = validate_existing_command("/payload-job foo", [])
        assert result["is_valid"] is False


class TestDiscoverPayloadJobs:
    @patch.object(payload_job_discovery, "_get_pr_branch", return_value="main")
    def test_full_discovery(self, mock_branch, tmp_path):
        # Set up CI config for openshift/origin
        ci_dir = tmp_path / "ci-operator/config/openshift/origin"
        ci_dir.mkdir(parents=True)
        ci_config = {
            "tests": [
                {
                    "as": "e2e-metal-two-node-fencing",
                    "pipeline_run_if_changed": r"test/extended/two_node/",
                },
                {
                    "as": "e2e-unrelated",
                    "pipeline_run_if_changed": r"vendor/",
                },
            ]
        }
        (ci_dir / "openshift-origin-main.yaml").write_text(yaml.dump(ci_config))

        # Set up nightly config
        nightly_dir = tmp_path / "ci-operator/config/openshift/release"
        nightly_dir.mkdir(parents=True)
        nightly_config = {
            "tests": [
                {"as": "e2e-metal-two-node-fencing"},
                {"as": "e2e-metal-single-node"},
            ]
        }
        (nightly_dir / "openshift-release-main__nightly-4.19.yaml").write_text(
            yaml.dump(nightly_config)
        )

        result = discover_payload_jobs(
            "openshift/origin#31276",
            ["test/extended/two_node/fencing_test.go"],
            str(tmp_path),
        )

        assert result.org == "openshift"
        assert result.repo == "origin"
        assert result.error is None
        assert len(result.suggestions) == 1
        assert result.suggestions[0].as_name == "e2e-metal-two-node-fencing"
        assert "periodic-ci-openshift-release-main-nightly-4.19" in result.suggestions[0].periodic_name

    def test_invalid_pr_ref(self, tmp_path):
        result = discover_payload_jobs("invalid", ["file.go"], str(tmp_path))
        assert result.error is not None
        assert "Could not parse" in result.error

    @patch.object(payload_job_discovery, "_get_pr_branch", return_value="main")
    def test_no_matching_files(self, mock_branch, tmp_path):
        ci_dir = tmp_path / "ci-operator/config/openshift/origin"
        ci_dir.mkdir(parents=True)
        ci_config = {
            "tests": [
                {"as": "e2e", "pipeline_run_if_changed": r"vendor/"},
            ]
        }
        (ci_dir / "openshift-origin-main.yaml").write_text(yaml.dump(ci_config))

        nightly_dir = tmp_path / "ci-operator/config/openshift/release"
        nightly_dir.mkdir(parents=True)
        (nightly_dir / "openshift-release-main__nightly-4.19.yaml").write_text(
            yaml.dump({"tests": [{"as": "e2e"}]})
        )

        result = discover_payload_jobs(
            "openshift/origin#1",
            ["README.md"],
            str(tmp_path),
        )
        assert result.error is None
        assert result.suggestions == []

    @patch.object(payload_job_discovery, "_get_pr_branch", return_value="main")
    def test_job_not_in_nightly(self, mock_branch, tmp_path):
        ci_dir = tmp_path / "ci-operator/config/openshift/origin"
        ci_dir.mkdir(parents=True)
        ci_config = {
            "tests": [
                {"as": "e2e-custom", "pipeline_run_if_changed": r"pkg/"},
            ]
        }
        (ci_dir / "openshift-origin-main.yaml").write_text(yaml.dump(ci_config))

        nightly_dir = tmp_path / "ci-operator/config/openshift/release"
        nightly_dir.mkdir(parents=True)
        (nightly_dir / "openshift-release-main__nightly-4.19.yaml").write_text(
            yaml.dump({"tests": [{"as": "e2e-other"}]})
        )

        result = discover_payload_jobs(
            "openshift/origin#1",
            ["pkg/foo.go"],
            str(tmp_path),
        )
        assert result.suggestions == []


class TestFormatDiscoveryJson:
    def test_with_suggestions(self):
        result = DiscoveryResult(
            pr_ref="openshift/origin#1",
            org="openshift",
            repo="origin",
            branch="main",
            version="4.19",
            suggestions=[
                PayloadJobSuggestion(
                    as_name="e2e-fencing",
                    periodic_name="periodic-ci-openshift-release-main-nightly-4.19-e2e-fencing",
                    trigger_command="/payload-job periodic-ci-...",
                    matched_by="regex",
                    matched_files=["test/foo.go"],
                ),
            ],
        )
        output = json.loads(format_discovery_json(result))
        assert output["version"] == "4.19"
        assert len(output["suggestions"]) == 1
        assert "error" not in output

    def test_with_error(self):
        result = DiscoveryResult(
            pr_ref="bad", org="", repo="", branch="", version="",
            error="Could not parse",
        )
        output = json.loads(format_discovery_json(result))
        assert output["error"] == "Could not parse"
        assert output["suggestions"] == []


class TestFormatDiscoveryMarkdown:
    def test_with_suggestions(self):
        result = DiscoveryResult(
            pr_ref="openshift/origin#1",
            org="openshift",
            repo="origin",
            branch="main",
            version="4.19",
            suggestions=[
                PayloadJobSuggestion(
                    as_name="e2e-fencing",
                    periodic_name="periodic-...",
                    trigger_command="/payload-job periodic-...",
                    matched_by="regex",
                    matched_files=["test/foo.go"],
                ),
            ],
        )
        md = format_discovery_markdown(result)
        assert "Payload Job Discovery" in md
        assert "e2e-fencing" in md
        assert "1" in md  # "Found 1 matching"

    def test_no_suggestions(self):
        result = DiscoveryResult(
            pr_ref="openshift/origin#1",
            org="openshift",
            repo="origin",
            branch="main",
            version="4.19",
        )
        md = format_discovery_markdown(result)
        assert "No matching" in md

    def test_with_error(self):
        result = DiscoveryResult(
            pr_ref="bad", org="", repo="", branch="", version="",
            error="Something broke",
        )
        md = format_discovery_markdown(result)
        assert "Something broke" in md
