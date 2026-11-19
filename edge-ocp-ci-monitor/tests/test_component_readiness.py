"""Edge Component Readiness collection and per-view failures."""

from unittest.mock import patch

import pytest
from payload_monitor.models import ComponentRegression
from requests import HTTPError

from edge_ocp_ci_monitor import component_readiness as cr


@patch.object(cr, "_session")
def test_collects_all_exact_views_and_parses_all_three_topologies(mock_session):
    mock_session.get.return_value.json.return_value = {
        "rows": [{
            "component": "network",
            "columns": [{
                "status": -400,
                "regressed_tests": [{
                    "test_name": "edge-test", "test_id": "test-id",
                    "sample_stats": {"success_rate": 0.25, "failure_count": 3},
                    "base_stats": {"success_rate": 1.0, "success_count": 4},
                }],
            }],
        }],
    }

    findings, errors = cr.collect()

    expected = {f"{version}-ha-vs-single" for version in ("4.18", "4.19", "4.20", "4.21")}
    expected.update(
        f"{version}-ha-vs-{topology}"
        for version in ("4.22", "4.23", "5.0", "5.1")
        for topology in ("single", "two-node-arbiter", "two-node-fencing")
    )
    calls = mock_session.get.call_args_list
    assert len(calls) == 16
    assert {call.kwargs["params"]["view"] for call in calls} == expected
    assert all(call.args == (cr.CR_API_URL,) for call in calls)
    assert len(findings) == 16
    assert not errors
    assert {finding.comparison for finding in findings} == {"SNO", "TNA", "TNF"}
    assert {(finding.sample_pass_rate, finding.base_pass_rate) for finding in findings} == {(25.0, 100.0)}
    assert {(finding.sample_failure, finding.base_success) for finding in findings} == {(3, 4)}
    assert {finding.detail_url.split("view=", 1)[1].split("&", 1)[0] for finding in findings} == expected
    assert {(finding.version, finding.comparison) for finding in findings} == {
        (view["version"], view["topology"]) for view in cr.CR_VIEWS
    }


@patch.object(cr, "fetch_component_regressions")
@patch.object(cr, "CR_VIEWS", [])
def test_empty_lookup_makes_no_requests(mock_fetch):
    assert cr.collect() == ([], ["No saved views configured"])
    assert cr.collect(["9.99"]) == ([], ["No saved views configured for 9.99"])
    mock_fetch.assert_not_called()


@patch.object(cr, "CR_VIEWS", [
    {"version": "4.18", "view": "4.18-ha-vs-single", "topology": "SNO"},
    {"version": "4.19", "view": "4.19-ha-vs-single", "topology": "SNO"},
])
def test_one_failed_view_preserves_other_findings():
    finding = ComponentRegression(
        component="network", test_name="edge-test", test_suite="s",
        test_id="test-id", capability="", version="4.19", comparison="SNO",
    )

    def fetch(version, view, comparison):
        if version == "4.18":
            raise RuntimeError("failed view")
        return [finding]

    with patch.object(cr, "fetch_component_regressions", side_effect=fetch):
        assert cr.collect() == ([finding], ["View 4.18-ha-vs-single unavailable: failed view"])


@pytest.mark.parametrize("failed", [False, True])
def test_no_component_regressions_are_distinguished_from_fetch_failure(failed):
    with patch.object(cr, "_session") as session:
        response = session.get.return_value
        response.json.return_value = {"rows": []}
        if failed:
            response.raise_for_status.side_effect = HTTPError("503 Service Unavailable")
        findings, errors = cr.collect(["4.19"])

    assert findings == []
    assert errors == (["View 4.19-ha-vs-single unavailable: 503 Service Unavailable"] if failed else [])
