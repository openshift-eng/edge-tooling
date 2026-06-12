"""Tests for payload_monitor.collectors.sippy."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from payload_monitor.collectors import sippy
from payload_monitor.config import Config
from payload_monitor.models import Regression


class TestFetchEdgeJobs:
    def test_filters_edge_jobs(self, config):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"name": "periodic-sno-test", "id": 1},
            {"name": "periodic-aws-test", "id": 2},
            {"name": "periodic-arbiter-test", "id": 3},
        ]
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp

        jobs = sippy.fetch_edge_jobs("4.19", config, session=mock_session)
        assert len(jobs) == 2
        names = {j["name"] for j in jobs}
        assert "periodic-sno-test" in names
        assert "periodic-arbiter-test" in names
        # Topology should be annotated
        sno_job = [j for j in jobs if j["name"] == "periodic-sno-test"][0]
        assert sno_job["_topology"] == "SNO"

    def test_http_error_returns_empty(self, config):
        mock_session = MagicMock()
        mock_session.get.side_effect = requests.RequestException("timeout")
        assert sippy.fetch_edge_jobs("4.19", config, session=mock_session) == []

    def test_unexpected_response_format(self, config):
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": "bad request"}
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp

        assert sippy.fetch_edge_jobs("4.19", config, session=mock_session) == []


class TestIdentifyRegressions:
    def test_detects_regression(self):
        jobs = [{
            "name": "periodic-sno-test",
            "current_pass_percentage": 50.0,
            "previous_pass_percentage": 90.0,
            "net_improvement": -40.0,
            "current_runs": 10,
            "_topology": "SNO",
            "jira_component": "",
            "id": 1,
        }]
        regressions = sippy.identify_regressions(jobs)
        assert len(regressions) == 1
        assert regressions[0].test_name == "periodic-sno-test"
        assert regressions[0].sample_pass_rate == 50.0
        assert regressions[0].basis_pass_rate == 90.0
        assert regressions[0].topology == "SNO"

    def test_skips_improvement(self):
        jobs = [{
            "name": "job1",
            "current_pass_percentage": 95.0,
            "previous_pass_percentage": 80.0,
            "net_improvement": 15.0,
            "current_runs": 10,
            "_topology": "SNO",
            "jira_component": "",
            "id": 1,
        }]
        assert sippy.identify_regressions(jobs) == []

    def test_skips_too_few_runs(self):
        jobs = [{
            "name": "job1",
            "current_pass_percentage": 0.0,
            "previous_pass_percentage": 100.0,
            "net_improvement": -100.0,
            "current_runs": 2,
            "_topology": "SNO",
            "jira_component": "",
            "id": 1,
        }]
        assert sippy.identify_regressions(jobs, min_runs=3) == []

    def test_skips_zero_net_improvement(self):
        jobs = [{
            "name": "job1",
            "current_pass_percentage": 80.0,
            "previous_pass_percentage": 80.0,
            "net_improvement": 0,
            "current_runs": 10,
            "_topology": "SNO",
            "jira_component": "",
            "id": 1,
        }]
        assert sippy.identify_regressions(jobs) == []

    def test_multiple_regressions(self):
        jobs = [
            {
                "name": f"job{i}",
                "current_pass_percentage": 50.0,
                "previous_pass_percentage": 90.0,
                "net_improvement": -40.0,
                "current_runs": 10,
                "_topology": "SNO",
                "jira_component": "",
                "id": i,
            }
            for i in range(3)
        ]
        assert len(sippy.identify_regressions(jobs)) == 3


class TestCollect:
    @patch.object(sippy, "fetch_edge_jobs")
    def test_collect_all_versions(self, mock_fetch, config):
        mock_fetch.return_value = [{
            "name": "periodic-sno-test",
            "current_pass_percentage": 50.0,
            "previous_pass_percentage": 90.0,
            "net_improvement": -40.0,
            "current_runs": 10,
            "_topology": "SNO",
            "jira_component": "",
            "id": 1,
        }]
        results = sippy.collect(config, ["4.18", "4.19"])
        assert "4.18" in results
        assert "4.19" in results
        assert len(results["4.18"]) == 1

    @patch.object(sippy, "fetch_edge_jobs")
    def test_collect_no_regressions(self, mock_fetch, config):
        mock_fetch.return_value = []
        results = sippy.collect(config, ["4.19"])
        assert results["4.19"] == []
