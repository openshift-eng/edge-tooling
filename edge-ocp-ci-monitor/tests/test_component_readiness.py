"""Edge-only Component Readiness lookup, rendering and legacy isolation."""

from unittest.mock import patch

from edge_ocp_ci_monitor import component_readiness as cr
from edge_ocp_ci_monitor.report import generate_html
from payload_monitor.collectors import component_readiness as shared_cr
from payload_monitor.models import ComponentRegression, MonitorReport


@patch.object(shared_cr, "_session")
def test_collects_all_exact_views_and_parses_all_three_topologies(mock_session):
    mock_session.get.return_value.json.return_value = {
        "rows": [{
            "component": "network",
            "columns": [{
                "status": -400,
                "regressed_tests": [{"test_name": "edge-test", "test_id": "test-id"}],
            }],
        }],
    }

    findings = cr.collect()

    expected = {f"{version}-ha-vs-single" for version in ("4.18", "4.19", "4.20", "4.21")}
    expected.update(
        f"{version}-ha-vs-{topology}"
        for version in ("4.22", "4.23", "5.0", "5.1")
        for topology in ("single", "two-node-arbiter", "two-node-fencing")
    )
    calls = mock_session.get.call_args_list
    assert len(calls) == 16
    assert {call.kwargs["params"]["view"] for call in calls} == expected
    assert all(call.args == (shared_cr.CR_API_URL,) for call in calls)
    assert len(findings) == 16
    assert {finding.comparison for finding in findings} == {"SNO", "TNA", "TNF"}
    assert {finding.detail_url.split("view=", 1)[1].split("&", 1)[0] for finding in findings} == expected
    assert {(finding.version, finding.comparison) for finding in findings} == {
        (view["version"], view["topology"]) for view in cr.CR_VIEWS
    }


@patch.object(cr, "fetch_component_regressions")
@patch.object(cr, "CR_VIEWS", [])
def test_empty_lookup_makes_no_requests(mock_fetch):
    assert cr.collect() == []
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
        assert cr.collect() == [finding]


def test_edge_lookup_does_not_change_the_original_collector():
    with (
        patch.object(cr, "fetch_component_regressions", return_value=[]) as edge_fetch,
        patch.object(shared_cr, "fetch_component_regressions", return_value=[]) as original_fetch,
    ):
        cr.collect()
        shared_cr.collect(["4.19"])

    assert edge_fetch.call_count == 16
    assert {call.args for call in original_fetch.call_args_list} == {
        ("4.19", "{version}-ha-vs-single", "SNO"),
        ("4.19", "{version}-ha-vs-two-node-fencing", "TNF"),
    }
    assert all("pattern" in view and "view" not in view for view in shared_cr.CR_VIEWS)


def test_edge_report_filters_include_tna_and_releases_outside_payload_scope():
    report = MonitorReport(generated_at="now", streams=[], component_regressions=[
        ComponentRegression(
            component="network", test_name="sno-test", test_suite="s",
            test_id="sno", capability="", version="4.22", comparison="SNO",
        ),
        ComponentRegression(
            component="network", test_name="arbiter-regression", test_suite="s",
            test_id="tna", capability="", version="5.1", comparison="TNA",
        ),
        ComponentRegression(
            component="network", test_name="fencing-test", test_suite="s",
            test_id="tnf", capability="", version="4.22", comparison="TNF",
        ),
    ])

    html = generate_html(report)

    assert 'data-group="crcomparison" data-value="TNA"' in html
    assert 'data-group="crversion" data-value="5.1"' in html
    assert "arbiter-regression" in html
