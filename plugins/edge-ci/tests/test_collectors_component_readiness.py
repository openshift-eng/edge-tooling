"""Tests for payload_monitor.collectors.component_readiness."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from payload_monitor.collectors import component_readiness as cr
from payload_monitor.models import ComponentRegression


SAMPLE_API_RESPONSE = {
    "rows": [
        {
            "component": "kube-apiserver",
            "columns": [
                {
                    "status": -400,
                    "regressed_tests": [
                        {
                            "component": "kube-apiserver",
                            "test_name": "API server responds within SLO",
                            "test_suite": "openshift-tests",
                            "test_id": "test-123",
                            "capability": "Other",
                            "variants": {"Platform": "metal"},
                            "status": -400,
                            "sample_stats": {
                                "success_count": 5,
                                "failure_count": 15,
                                "success_rate": 0.25,
                            },
                            "base_stats": {
                                "success_count": 95,
                                "failure_count": 5,
                                "success_rate": 0.95,
                            },
                            "fisher_exact": 0.001,
                            "last_failure": "2026-03-25",
                            "explanations": ["Known infra issue"],
                            "links": {
                                "test_details": "https://sippy.dptools.openshift.org/api/component_readiness/test_details?view=4.19-ha-vs-single&testId=test-123",
                            },
                        }
                    ],
                    "variants": {"Platform": "metal"},
                },
            ],
        },
    ]
}


class TestFetchComponentRegressions:
    @patch.object(cr, "_session")
    def test_parses_regressions(self, mock_session):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_API_RESPONSE
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp

        regs = cr.fetch_component_regressions("4.19")
        assert len(regs) == 1
        r = regs[0]
        assert r.component == "kube-apiserver"
        assert r.test_name == "API server responds within SLO"
        assert r.version == "4.19"
        assert r.sample_pass_rate == 25.0
        assert r.base_pass_rate == 95.0
        assert r.fisher_exact == 0.001
        assert r.explanation == "Known infra issue"
        assert "test_details" in r.detail_url

    @patch.object(cr, "_session")
    def test_skips_non_regression_status(self, mock_session):
        data = {
            "rows": [{
                "component": "c1",
                "columns": [{
                    "status": 0,
                    "regressed_tests": [],
                    "variants": {},
                }],
            }]
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = data
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp

        assert cr.fetch_component_regressions("4.19") == []

    @patch.object(cr, "_session")
    def test_http_error(self, mock_session):
        mock_session.get.side_effect = requests.RequestException("timeout")
        assert cr.fetch_component_regressions("4.19") == []

    @patch.object(cr, "_session")
    def test_empty_rows(self, mock_session):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"rows": []}
        mock_resp.raise_for_status = MagicMock()
        mock_session.get.return_value = mock_resp

        assert cr.fetch_component_regressions("4.19") == []


class TestCollect:
    @patch.object(cr, "fetch_component_regressions")
    def test_collects_across_versions(self, mock_fetch):
        r1 = ComponentRegression(
            component="c1", test_name="t1", test_suite="s",
            test_id="id1", capability="", version="4.18",
        )
        r2 = ComponentRegression(
            component="c2", test_name="t2", test_suite="s",
            test_id="id2", capability="", version="4.19",
        )
        mock_fetch.side_effect = [[r1], [r2]]

        result = cr.collect(["4.18", "4.19"])
        assert len(result) == 2

    @patch.object(cr, "fetch_component_regressions")
    def test_handles_individual_failure(self, mock_fetch):
        r1 = ComponentRegression(
            component="c1", test_name="t1", test_suite="s",
            test_id="id1", capability="", version="4.19",
        )

        def side_effect(version):
            if version == "4.18":
                raise RuntimeError("boom")
            return [r1]

        mock_fetch.side_effect = side_effect
        result = cr.collect(["4.18", "4.19"])
        # 4.18 failed but 4.19 should still be present
        assert len(result) == 1
        assert result[0].version == "4.19"

    @patch.object(cr, "fetch_component_regressions")
    def test_empty_versions(self, mock_fetch):
        assert cr.collect([]) == []


class TestTestDetailUrl:
    def test_converts_api_url(self):
        links = {
            "test_details": "https://sippy.dptools.openshift.org/api/component_readiness/test_details?view=4.19-ha-vs-single&testId=abc"
        }
        url = cr._test_detail_url("4.19", "abc", links)
        assert url.startswith("https://sippy.dptools.openshift.org/sippy-ng/component_readiness/test_details?")
        assert "testId=abc" in url

    def test_fallback_to_test_id(self):
        url = cr._test_detail_url("4.19", "abc", {})
        assert "view=4.19-ha-vs-single" in url
        assert "testId=abc" in url

    def test_no_test_id_or_links(self):
        assert cr._test_detail_url("4.19", "", {}) == ""
