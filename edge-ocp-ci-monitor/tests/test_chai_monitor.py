"""Offline integration coverage for collection, MCP enrichment and fallbacks."""

import asyncio
import json
import logging
import sys
from copy import deepcopy
from types import SimpleNamespace

import httpx2
import pytest
from click.testing import CliRunner
from edge_ocp_ci_monitor import __main__ as cli
from edge_ocp_ci_monitor import analysis, collection
from edge_ocp_ci_monitor.analysis import (
    ChaiUnavailable,
    InvalidAnalysis,
    Settings,
    analyze_targets,
    ask_chai,
    build_prompt,
    build_targets,
    validate_analysis,
    validate_batch,
)
from edge_ocp_ci_monitor.report import generate_html as generate_chai_html
from payload_monitor import __main__ as core
from payload_monitor.models import (
    DeepAnalysis,
    JobResult,
    JobRun,
    JobType,
    MonitorReport,
    Payload,
    PayloadStatus,
    PreviousAttempt,
    Regression,
    StreamReport,
)
from payload_monitor.report.generator import generate_html, generate_json


@pytest.fixture
def report():
    job = JobRun("job-sno", "https://prow.example/latest", JobResult.FAILURE, JobType.BLOCKING, "SNO",
                 previous_attempts=[PreviousAttempt("https://prow.example/previous", JobResult.FAILURE)])
    payload = Payload("4.19.0-0.nightly-2026-11-13-070000", "nightly", "4.19", PayloadStatus.REJECTED, jobs=[job])
    regression = Regression("job-sno", "id", "", "", 80, 20, topology="SNO", current_runs=5,
                            version="4.19", triage_url="https://sippy.example/job")
    return MonitorReport(generated_at="2026-11-13 07:00 UTC", streams=[
        StreamReport("nightly", "4.19", payloads=[payload], regressions=[regression]),
    ])


@pytest.fixture
def collected_report(report, monkeypatch):
    monkeypatch.setattr(collection, "configured_stream_names", lambda config: ["4.19.0-0.nightly"])
    monkeypatch.setattr(collection, "collect_payloads", lambda *args: report.streams)
    monkeypatch.setattr(collection.sippy, "collect", lambda *args: {"4.19": report.streams[0].regressions})
    monkeypatch.setattr(collection.component_readiness, "collect", lambda *args: [])
    return report


def answer(name="job-sno", **updates):
    return {
        "job_name": name, "root_cause": "Observed test regression in the Prow logs.",
        "failure_type": "Test regression", "impact": "SNO payload acceptance is blocked.",
        "suspect_prs": [], "recommendation": "Investigate the failing test.",
        "same_root_cause": True, "attempt_analyses": [], **updates,
    }


def settings(**updates):
    return Settings("https://chai.example/personas/edge/mcp", "synthetic-secret", **updates)


def prompt_targets(prompt):
    return json.JSONDecoder().raw_decode(prompt.split("Targets:\n", 1)[1])[0]


def batch_answer(prompt, **updates):
    targets = prompt_targets(prompt)
    sippy = "analysis_key" in targets[0]
    key = "analysis_key" if sippy else "prow_url"
    group = "by_sippy_job" if sippy else "by_prow_url"
    return json.dumps({group: {t[key]: answer(name=t["job_name"] if sippy else t["name"], **updates)
                              for t in targets}})


def deferred_answer(prompt, *, completed=0):
    targets = prompt_targets(prompt)
    key = "analysis_key" if "analysis_key" in targets[0] else "prow_url"
    data = json.loads(batch_answer(prompt))
    group = next(iter(data))
    data[group] = {t[key]: data[group][t[key]] for t in targets[:completed]}
    data["deferred"] = {t[key]: {
        "reason": "budget_exhausted", "notes": "Read install logs; next check https://prow.example/evidence",
    } for t in targets[completed:]}
    return json.dumps(data)


@pytest.mark.parametrize("missing", ["CHAI_MCP_TOKEN", "CHAI_MCP_URL", "both"])
def test_missing_environment_names_are_reported_without_values(monkeypatch, missing):
    monkeypatch.setenv("CHAI_MCP_TOKEN", "synthetic-secret")
    monkeypatch.setenv("CHAI_MCP_URL", "https://chai.example/mcp")
    names = ("CHAI_MCP_TOKEN", "CHAI_MCP_URL") if missing == "both" else (missing,)
    for name in names:
        monkeypatch.delenv(name)
    config, warnings = Settings.from_environment(timeout=1800, concurrency=2)
    assert config is None
    assert all(name in warnings[0] for name in names)
    assert "synthetic-secret" not in warnings[0]


@pytest.mark.parametrize("url", ["", "not-a-url", "ftp://example/mcp", "https://[broken", "https://host:broken/mcp"])
def test_invalid_url_falls_back(monkeypatch, url):
    monkeypatch.setenv("CHAI_MCP_TOKEN", "synthetic-secret")
    monkeypatch.setenv("CHAI_MCP_URL", url)
    config, warnings = Settings.from_environment(timeout=1800, concurrency=2)
    assert config is None
    assert "CHAI_MCP_URL" in warnings[0]


def test_token_not_in_settings_repr():
    assert "synthetic-secret" not in repr(settings())


@pytest.mark.parametrize("sippy", [False, True])
def test_concise_prompt_keeps_targets_and_one_complete_response_schema(report, sippy):
    selected = build_targets(report)["sippy_jobs" if sippy else "blocking_jobs"]
    key = "analysis_key" if sippy else "prow_url"
    targets = [{**selected[0], key: f"{selected[0][key]}-{n}"} for n in range(4)]
    prompt = build_prompt(targets, sippy=sippy, evidence={})
    assert prompt_targets(prompt) == targets
    assert "distinguish the root cause from downstream failures" in prompt
    assert "Include evidence links and flag anything uncertain" in prompt
    assert "Previously completed investigations" not in prompt
    shape, _ = json.JSONDecoder().raw_decode(prompt.split("Return only JSON in this format:\n", 1)[1])
    group = "by_sippy_job" if sippy else "by_prow_url"
    assert set(shape) == {group, "deferred"}
    schema = next(iter(shape[group].values()))
    assert set(schema) == set(answer()) | {"jira"}
    # The response schema is supplied once, rather than duplicated per target.
    assert prompt.count('"suspect_prs":') == 1
    assert "with no extra keys" in prompt
    assert "prow_url, root_cause and failure_type" in prompt
    assert "do not omit targets" in prompt
    assert "stop investigating within 2400 seconds" in prompt
    assert "hard timeout of 2700 seconds" in prompt
    assert "reason=budget_exhausted" in prompt
    assert "final attempt" not in prompt


def test_selection_matches_core_and_deduplicates_blocking_runs(report, tmp_path):
    report.streams[0].payloads.append(deepcopy(report.streams[0].payloads[0]))
    report.streams[0].regressions.extend(
        Regression(f"job-{n}", str(n), "", "", 80, n, topology="TNF", current_runs=4, version="4.19")
        for n in range(7)
    )
    path = tmp_path / "report.html"
    generate_html(report, path)
    expected = json.loads(path.with_suffix(".ai-targets.json").read_text())
    targets = build_targets(report)
    assert len(targets["blocking_jobs"]) == 1
    assert len(targets["sippy_jobs"]) == 5
    assert [t["analysis_key"] for t in targets["sippy_jobs"]] == [t["analysis_key"] for t in expected["sippy_jobs"]]


def test_sippy_analysis_runs_without_blockers(report):
    report.streams[0].payloads = []
    seen = []

    async def ask(config, prompt):
        seen.append(prompt)
        return batch_answer(prompt)

    result = asyncio.run(analyze_targets(build_targets(report), settings(), ask=ask))
    assert result.completed == 1
    assert len(seen) == 1
    assert "4.19/job-sno" in result.data["by_sippy_job"]
    assert "job health, not a Component Readiness" in seen[0]


def test_serial_batches_reuse_completed_blocking_evidence(report):
    seen = []

    async def ask(config, prompt):
        seen.append(prompt)
        return batch_answer(prompt)

    result = asyncio.run(analyze_targets(build_targets(report), settings(concurrency=1), ask=ask))
    assert result.completed == 2
    assert result.status == "complete"
    assert "Observed test regression" in seen[1]
    assert "https://prow.example/previous" in seen[0]


@pytest.mark.parametrize("change", [
    {"job_name": "wrong-job"}, {"same_root_cause": "true"}, {"root_cause": ""},
    {"suspect_prs": "https://github.com/org/repo/pull/1"}, {"failure_type": "Unproven flake"},
    {"suspect_prs": ["https://"]}, {"suspect_prs": ["https://[malformed"]},
    {"attempt_analyses": [{"prow_url": "https://[malformed", "root_cause": "error", "failure_type": "Install failure"}]},
    {"attempt_analyses": [None]}, {"same_root_cause": False},
])
def test_malformed_contract_rejected(report, change):
    target = build_targets(report)["blocking_jobs"][0]
    with pytest.raises(InvalidAnalysis):
        validate_analysis(json.dumps(answer(**change)), target, sippy=False)


def test_attempt_coverage_and_json_fence(report):
    target = build_targets(report)["blocking_jobs"][0]
    data = answer(same_root_cause=False, attempt_analyses=[
        {"prow_url": url, "root_cause": "Different failure", "failure_type": "Install failure"}
        for url in ["https://prow.example/previous", "https://prow.example/latest"]
    ])
    assert validate_analysis("```json\n" + json.dumps(data) + "\n```", target, sippy=False) == data
    data["attempt_analyses"].reverse()
    with pytest.raises(InvalidAnalysis):
        validate_analysis(json.dumps(data), target, sippy=False)


def test_batch_validation_retains_good_entries_and_explains_every_failure(report):
    base = build_targets(report)["blocking_jobs"][0]
    targets = [{**base, "prow_url": f"https://prow.example/{name}"}
               for name in ("good", "missing", "wrong", "unavailable")]
    response = {"by_prow_url": {
        "https://prow.example/good": answer(),
        "https://prow.example/wrong": answer(name="another-job"),
        "https://prow.example/unavailable": answer(failure_type="Analysis error", root_cause="Missing artifacts"),
        "https://prow.example/invented": answer(),
    }}
    validated = validate_batch(json.dumps(response), targets, sippy=False)
    accepted, errors = validated.accepted, validated.errors
    assert set(accepted) == {"https://prow.example/good"}
    assert "omitted" in errors["https://prow.example/missing"]
    assert "different job" in errors["https://prow.example/wrong"]
    assert "Missing artifacts" in errors["https://prow.example/unavailable"]
    assert "Unexpected response key" in errors["by_prow_url response"]


@pytest.mark.parametrize("response", [
    "Not JSON", "[]", "{}", '{"by_prow_url": []}', '{"by_sippy_job": {}}',
    '{"job_name": "job-sno", "root_cause": "A single unkeyed analysis is not a batch"}',
])
def test_wrong_batch_shape_is_rejected(report, response):
    with pytest.raises(InvalidAnalysis):
        validate_batch(response, build_targets(report)["blocking_jobs"], sippy=False)


def test_batch_json_fence_and_duplicate_key_rejection(report):
    target = build_targets(report)["blocking_jobs"][0]
    text = json.dumps({"by_prow_url": {target["prow_url"]: answer()}})
    validated = validate_batch("```json\n" + text + "\n```", [target], sippy=False)
    accepted, errors = validated.accepted, validated.errors
    assert set(accepted) == {target["prow_url"]}
    assert not errors
    text = '{"by_prow_url": {}, "by_prow_url": {}}'
    with pytest.raises(InvalidAnalysis, match="duplicate JSON keys"):
        validate_batch(text, [target], sippy=False)


def test_partial_batch_results_are_merged_without_additional_calls(report):
    targets = build_targets(report)
    base = targets["blocking_jobs"][0]
    targets["blocking_jobs"] = [{**base, "prow_url": f"https://prow.example/{name}"}
                                for name in ("good", "missing", "wrong")]
    calls = []

    async def ask(config, prompt):
        calls.append(prompt)
        if "prow_url" in prompt_targets(prompt)[0]:
            return json.dumps({"by_prow_url": {"https://prow.example/good": answer(),
                                              "https://prow.example/wrong": answer(name="wrong-job")}})
        return batch_answer(prompt)

    result = asyncio.run(analyze_targets(targets, settings(), ask=ask))
    assert len(calls) == 2
    assert result.completed == 2
    assert result.requested == 4
    assert result.status == "partial"
    assert len(result.warnings) == 2
    assert set(result.data["by_prow_url"]) == {"https://prow.example/good"}
    assert result.data["by_sippy_job"]


def test_malformed_batch_has_no_retry_and_other_batch_is_retained(report):
    seen = []

    async def ask(config, prompt):
        seen.append((config.timeout, prompt))
        return "Not JSON" if "prow_url" in prompt_targets(prompt)[0] else batch_answer(prompt)

    result = asyncio.run(analyze_targets(build_targets(report), settings(), ask=ask))
    assert result.completed == 1
    assert result.status == "partial"
    assert len(seen) == 2
    assert all(timeout == 2700 for timeout, _ in seen)
    assert result.data["by_sippy_job"]
    assert not result.data["by_prow_url"]
    assert "non-JSON" in result.warnings[0]



def test_batch_timeout_preserves_other_batch(report):
    calls = []

    async def ask(config, prompt):
        calls.append(prompt)
        if "prow_url" in prompt_targets(prompt)[0]:
            await asyncio.sleep(1)
        return batch_answer(prompt)

    result = asyncio.run(analyze_targets(build_targets(report), settings(timeout=0.02), ask=ask))
    assert result.completed == 1
    assert result.status == "partial"
    assert len(calls) == 2
    assert "batch exceeded" in result.warnings[0]


def test_clean_budget_deferrals_get_one_final_request_per_category(report):
    targets = build_targets(report)
    for category, key in (("blocking_jobs", "prow_url"), ("sippy_jobs", "analysis_key")):
        base = targets[category][0]
        targets[category] = [{**base, key: base[key] + str(n)} for n in range(2)]
    calls = {"blocking_jobs": [], "sippy_jobs": []}
    snapshots = []

    async def ask(config, prompt):
        selected = prompt_targets(prompt)
        category = "sippy_jobs" if "analysis_key" in selected[0] else "blocking_jobs"
        key = "analysis_key" if category == "sippy_jobs" else "prow_url"
        calls[category].append(selected)
        await asyncio.sleep(0)
        if len(calls[category]) == 1:
            return deferred_answer(prompt, completed=1)
        assert selected == targets[category][1:]
        group = "by_sippy_job" if category == "sippy_jobs" else "by_prow_url"
        first = targets[category][0][key]
        assert any(first in snapshot[group] for snapshot in snapshots)
        assert "Previously completed investigations" in prompt
        assert first in prompt
        assert "Read install logs; next check" in prompt
        assert "This is the final attempt" in prompt
        return batch_answer(prompt)

    result = asyncio.run(analyze_targets(
        targets, settings(), ask=ask, on_update=lambda partial: snapshots.append(deepcopy(partial.data)),
    ))
    assert result.completed == result.requested == 4
    assert result.status == "complete"
    assert not result.warnings
    assert [len(category) for category in calls.values()] == [2, 2]
    assert snapshots[0] == {"by_prow_url": {}, "by_sippy_job": {}}


@pytest.mark.parametrize("sippy", [False, True])
def test_repeated_deferral_stops_after_final_request(report, sippy):
    targets = build_targets(report)
    targets["blocking_jobs" if sippy else "sippy_jobs"] = []
    calls = []

    async def ask(config, prompt):
        calls.append(prompt)
        return deferred_answer(prompt)

    result = asyncio.run(analyze_targets(targets, settings(), ask=ask))
    assert len(calls) == 2
    assert result.completed == 0
    assert result.status == "unavailable"
    assert len(result.warnings) == 1
    assert "Final attempt exhausted" in result.warnings[0]
    assert "Read install logs; next check" in result.warnings[0]


@pytest.mark.parametrize("failure", ["timeout", "transport", "malformed", "processing", "cancel"])
def test_failed_final_request_preserves_checkpointed_results(report, failure):
    targets = build_targets(report)
    targets["sippy_jobs"] = []
    base = targets["blocking_jobs"][0]
    targets["blocking_jobs"] = [{**base, "prow_url": base["prow_url"] + str(n)} for n in range(2)]
    calls = []
    saved = []

    async def ask(config, prompt):
        calls.append(prompt)
        if len(calls) == 1:
            return deferred_answer(prompt, completed=1)
        assert len(saved[-1]["by_prow_url"]) == 1
        if failure == "timeout":
            await asyncio.sleep(10)
        if failure == "cancel":
            raise asyncio.CancelledError
        if failure == "transport":
            raise ChaiUnavailable("Disconnected")
        if failure == "processing":
            return batch_answer(prompt, failure_type="Analysis error")
        return "Invalid JSON"

    async def run():
        call = analyze_targets(targets, settings(timeout=0.02), ask=ask,
                               on_update=lambda partial: saved.append(deepcopy(partial.data)))
        if failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await call
        else:
            result = await call
            assert result.completed == 1
            assert result.status == "partial"
            assert len(result.warnings) == 1
        assert len(calls) == 2
        assert set(saved[-1]["by_prow_url"]) == {base["prow_url"] + "0"}

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["processing", "missing", "wrong-job", "extra", "bad-notes", "overlap"])
def test_initial_errors_prevent_followup_but_retain_good_entries(report, failure):
    targets = build_targets(report)
    targets["sippy_jobs"] = []
    base = targets["blocking_jobs"][0]
    targets["blocking_jobs"] = [{**base, "prow_url": f"https://prow.example/{name}"}
                                for name in ("good", "deferred", "bad")]
    calls = []

    async def ask(config, prompt):
        calls.append(prompt)
        data = json.loads(deferred_answer(prompt, completed=1))
        bad = "https://prow.example/bad"
        if failure == "processing":
            del data["deferred"][bad]
            data["by_prow_url"][bad] = answer(failure_type="Analysis error")
        elif failure == "missing":
            del data["deferred"][bad]
        elif failure == "wrong-job":
            del data["deferred"][bad]
            data["by_prow_url"][bad] = answer(name="wrong-job")
        elif failure == "extra":
            data["deferred"]["https://prow.example/unrequested"] = data["deferred"][bad]
        elif failure == "bad-notes":
            data["deferred"][bad]["notes"] = ""
        else:
            data["by_prow_url"][bad] = answer()
        return json.dumps(data)

    result = asyncio.run(analyze_targets(targets, settings(), ask=ask))
    assert len(calls) == 1
    assert result.completed == 1
    assert result.status == "partial"
    assert set(result.data["by_prow_url"]) == {"https://prow.example/good"}
    assert any("Follow-up skipped because the batch had errors" in warning for warning in result.warnings)


@pytest.mark.parametrize("deferred", [None, [], {"reason": "budget_exhausted"}, "unfinished"])
def test_malformed_deferral_wrapper_retains_valid_analyses(report, deferred):
    target = build_targets(report)["blocking_jobs"][0]
    response = {"by_prow_url": {target["prow_url"]: answer()}, "deferred": deferred}
    result = validate_batch(json.dumps(response), [target], sippy=False)
    assert set(result.accepted) == {target["prow_url"]}
    assert result.errors
    assert not result.deferred


@pytest.mark.parametrize("update", [
    {"reason": "processing_failed"}, {"reason": None}, {"notes": None}, {"notes": " "},
    {"notes": "x" * 4001}, {"extra": "unsupported"},
])
def test_only_explicit_budget_deferrals_are_eligible(report, update):
    target = build_targets(report)["blocking_jobs"][0]
    response = {"by_prow_url": {}, "deferred": {target["prow_url"]: {
        "reason": "budget_exhausted", "notes": "Evidence and next step", **update,
    }}}
    result = validate_batch(json.dumps(response), [target], sippy=False)
    assert not result.deferred
    assert target["prow_url"] in result.errors


def test_custom_timeout_reserves_time_for_json_response(report):
    prompt = build_prompt(build_targets(report)["blocking_jobs"], sippy=False, evidence={}, timeout=900)
    assert "stop investigating within 800 seconds" in prompt
    assert "hard timeout of 900 seconds" in prompt



@pytest.mark.parametrize("limit", [1, 2, 6])
@pytest.mark.parametrize("defer_first", [False, True])
def test_bounded_category_calls_and_at_most_two_active_calls(report, limit, defer_first):
    targets = build_targets(report)
    targets["blocking_jobs"] = [{**targets["blocking_jobs"][0], "prow_url": f"https://prow.example/{n}"}
                                for n in range(7)]
    targets["sippy_jobs"] = [{**targets["sippy_jobs"][0], "analysis_key": f"4.19/job-{n}"}
                            for n in range(5)]

    async def run():
        ready = asyncio.Event()
        release = asyncio.Event()
        active = 0
        peak = 0
        calls = []

        async def ask(config, prompt):
            nonlocal active, peak
            calls.append(prompt_targets(prompt))
            active += 1
            peak = max(peak, active)
            if active == min(limit, 2):
                ready.set()
            await release.wait()
            active -= 1
            if defer_first and "This is the final attempt" not in prompt:
                return deferred_answer(prompt)
            return batch_answer(prompt)

        task = asyncio.create_task(analyze_targets(targets, settings(concurrency=limit), ask=ask))
        try:
            async with asyncio.timeout(1):
                await ready.wait()
                assert peak == min(limit, 2)
                release.set()
                result = await task
            assert len(calls) == (4 if defer_first else 2)
            assert sorted(map(len, calls)) == ([5, 5, 7, 7] if defer_first else [5, 7])
            assert peak <= min(limit, 2)
            assert result.completed == 12
            assert set(result.data["by_prow_url"]) == {t["prow_url"] for t in targets["blocking_jobs"]}
            assert set(result.data["by_sippy_job"]) == {t["analysis_key"] for t in targets["sippy_jobs"]}
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())







def test_explicit_analysis_error_never_marked_as_analyzed(report):
    async def ask(config, prompt):
        return batch_answer(prompt, failure_type="Analysis error", root_cause="Prow artifacts are inaccessible.",
                            recommendation="Check access to the artifact bucket.")

    result = asyncio.run(analyze_targets(build_targets(report), settings(), ask=ask))
    assert result.status == "unavailable"
    assert result.completed == 0
    assert result.data == {"by_prow_url": {}, "by_sippy_job": {}}
    assert all("Prow artifacts are inaccessible" in warning for warning in result.warnings)
    assert all("Check access to the artifact bucket" in warning for warning in result.warnings)


def test_real_sdk_in_memory_mcp_and_auth_header(monkeypatch):
    import fastmcp
    from fastmcp import Client, FastMCP

    server = FastMCP("test-chai")
    received = []
    options = []

    @server.tool()
    async def ask_persona(question: str) -> str:
        received.append(question)
        return json.dumps(answer())

    def connect(url, **kwargs):
        options.append((url, kwargs))
        return Client(server, mode=kwargs["mode"])

    monkeypatch.setattr(fastmcp, "Client", connect)
    assert json.loads(asyncio.run(ask_chai(settings(timeout=3), "investigate test"))) == answer()
    assert received == ["investigate test"]
    assert options[0][0] == settings().url
    assert options[0][1]["timeout"] == 3
    assert options[0][1]["mode"] == "legacy"
    request = httpx2.Request("POST", settings().url)
    auth_flow = options[0][1]["auth"].auth_flow(request)
    assert next(auth_flow).headers["Authorization"] == "Bearer synthetic-secret"


def test_real_sdk_forwards_progress_for_both_batches(report, monkeypatch, caplog):
    import fastmcp
    from fastmcp import Client, Context, FastMCP

    server = FastMCP("test-chai-progress")

    @server.tool()
    async def ask_persona(question: str, ctx: Context) -> str:
        await ctx.report_progress(progress=1, message="Thinking")
        await ctx.report_progress(progress=2, message=(
            "Reading artifacts synthetic-secret https://prow.example/log?signature=hidden-query "
            "Bearer hidden-bearer\n\x1b[31mworking"
        ))
        return batch_answer(question)

    monkeypatch.setattr(fastmcp, "Client", lambda *args, **kwargs: Client(server, mode=kwargs["mode"]))
    with caplog.at_level(logging.INFO, logger="edge_ocp_ci_monitor"):
        result = asyncio.run(analyze_targets(build_targets(report), settings(timeout=3)))
    assert result.completed == 2
    for category in ("blocking-jobs", "Sippy-tests"):
        assert f"Chai [{category}]: Thinking" in caplog.text
        assert f"Chai [{category}]: Reading artifacts [REDACTED]" in caplog.text
        assert f"Chai [{category}]: completed batch" in caplog.text
    for secret in ("synthetic-secret", "hidden-query", "hidden-bearer", "\x1b"):
        assert secret not in caplog.text


@pytest.mark.parametrize("ending", ["success", "failure", "timeout", "cancel"])
def test_heartbeat_during_silent_batches_and_cleanup(report, monkeypatch, caplog, ending):
    monkeypatch.setattr(analysis, "_HEARTBEAT_SECONDS", 0.005)

    async def ask(config, prompt):
        await asyncio.sleep(0.025 if ending in {"success", "failure"} else 10)
        if ending == "failure":
            raise ChaiUnavailable("Endpoint unavailable")
        return batch_answer(prompt)

    async def run():
        task = asyncio.create_task(analyze_targets(
            build_targets(report), settings(timeout=0.035 if ending == "timeout" else 3), ask=ask,
        ))
        if ending == "cancel":
            await asyncio.sleep(0.025)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            result = await task
            assert result.completed == (2 if ending == "success" else 0)
        count = len(caplog.records)
        await asyncio.sleep(0.015)
        assert len(caplog.records) == count
        assert not any(t.get_name().startswith("chai-heartbeat-") for t in asyncio.all_tasks())

    with caplog.at_level(logging.INFO, logger="edge_ocp_ci_monitor"):
        asyncio.run(run())
    for category in ("blocking-jobs", "Sippy-tests"):
        assert f"Chai [{category}]: waiting; elapsed" in caplog.text
    assert "last Chai update none received" in caplog.text


def test_heartbeat_reports_elapsed_and_last_update_age(monkeypatch, caplog):
    monkeypatch.setattr(analysis, "_HEARTBEAT_SECONDS", 0.005)
    now = [100.0]
    monkeypatch.setattr(analysis, "time", SimpleNamespace(monotonic=lambda: now[0]))

    async def run():
        async with analysis._BatchProgress("blocking-jobs", settings()) as progress:
            now[0] = 110.0
            await progress.on_progress(1, None, "Thinking")
            now[0] = 190.0
            await asyncio.sleep(0.012)

    with caplog.at_level(logging.INFO, logger="edge_ocp_ci_monitor"):
        asyncio.run(run())
    assert "elapsed 01:30 / 45:00; last Chai update 01:20 ago" in caplog.text


def test_http_mcp_handshake_and_response(monkeypatch):
    import fastmcp
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    requests = []

    async def respond(request):
        assert request.headers["Authorization"] == "Bearer synthetic-secret"
        if request.method == "GET":
            return httpx2.Response(405)
        if request.method == "DELETE":
            return httpx2.Response(204)
        body = json.loads(request.content)
        requests.append(body)
        if "id" not in body:
            return httpx2.Response(202)
        if body["method"] == "initialize":
            result = {"protocolVersion": body["params"]["protocolVersion"],
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "chai-test", "version": "1"}}
        elif body["method"] == "tools/list":
            result = {"tools": [{"name": "ask_persona", "inputSchema": {
                "type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"],
            }}]}
        else:
            assert body["method"] == "tools/call"
            assert request.headers["mcp-session-id"] == "test-session"
            result = {"content": [{"type": "text", "text": json.dumps(answer())}], "isError": False}
        return httpx2.Response(200, headers={"mcp-session-id": "test-session"},
                              json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    def connect(url, **kwargs):
        transport = StreamableHttpTransport(
            url, auth=kwargs["auth"],
            httpx_client_factory=lambda **options: httpx2.AsyncClient(
                transport=httpx2.MockTransport(respond), **options,
            ),
        )
        return Client(transport, timeout=kwargs["timeout"], mode=kwargs["mode"])

    monkeypatch.setattr(fastmcp, "Client", connect)
    assert json.loads(asyncio.run(ask_chai(settings(timeout=3), "investigate"))) == answer()
    assert [r["method"] for r in requests[:2]] == ["initialize", "notifications/initialized"]
    call = next(r for r in requests if r["method"] == "tools/call")
    assert call["params"]["name"] == "ask_persona"
    assert call["params"]["arguments"] == {"question": "investigate"}


def test_missing_mcp_dependency_does_not_stop_analysis_pipeline(report, monkeypatch):
    import builtins

    original_import = builtins.__import__

    def import_without_sdk(name, *args, **kwargs):
        if name == "fastmcp":
            raise ModuleNotFoundError("No module named 'fastmcp'", name="fastmcp")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_sdk)
    result = asyncio.run(analyze_targets(build_targets(report), settings()))
    assert result.completed == 0
    assert all("dependencies are missing" in warning for warning in result.warnings)
    assert all("No module named 'fastmcp'" in warning for warning in result.warnings)
    assert all(sys.executable in warning for warning in result.warnings)
    assert list(result.reason_counts.values()) == [2]


@pytest.mark.parametrize("failure", ["transport", "auth", "tool-error", "protocol"])
def test_mcp_failures_have_safe_warnings(monkeypatch, failure):
    import fastmcp
    from fastmcp.exceptions import McpError

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            if failure == "transport":
                raise httpx2.ConnectError("synthetic-secret must not be displayed")
            return self

        async def __aexit__(self, *args):
            pass

        async def call_tool(self, *args, **kwargs):
            if failure == "protocol":
                raise McpError(code=-32603, message="Protocol failure: synthetic-secret")
            return SimpleNamespace(is_error=failure == "tool-error", content=[
                SimpleNamespace(type="text", text="Authentication failed: synthetic-secret"),
            ])

    monkeypatch.setattr(fastmcp, "Client", FakeClient)
    with pytest.raises(ChaiUnavailable) as exc:
        asyncio.run(ask_chai(settings(), "test"))
    assert "synthetic-secret" not in str(exc.value)
    assert "Reason:" in str(exc.value)
    expected = {"transport": "ConnectError", "protocol": "Protocol failure"}.get(failure, "Authentication failed")
    assert expected in str(exc.value)


@pytest.mark.parametrize("status", [401, 403, 404, 429, 503])
def test_http_failure_exposes_status_without_body_or_credentials(monkeypatch, status):
    import fastmcp

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            request = httpx2.Request("POST", "https://chai.example/mcp?token=synthetic-secret")
            response = httpx2.Response(status, request=request, text="sensitive-server-body")
            raise httpx2.HTTPStatusError("synthetic-secret", request=request, response=response)

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(fastmcp, "Client", FakeClient)
    with pytest.raises(ChaiUnavailable) as exc:
        asyncio.run(ask_chai(settings(), "test"))
    message = str(exc.value)
    assert f"HTTP {status}" in message
    assert "synthetic-secret" not in message
    assert "sensitive-server-body" not in message


def test_nested_transport_reason_redacts_url_and_bearer_credentials(monkeypatch):
    import fastmcp

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            cause = ExceptionGroup("connection errors", [httpx2.ConnectError(
                "DNS lookup failed for https://name:hidden-password@chai.example/mcp?signature=hidden-query#hidden-fragment "
                "Authorization: Bearer synthetic-secret",
            )])
            raise RuntimeError("Client failed to connect") from cause

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(fastmcp, "Client", FakeClient)
    with pytest.raises(ChaiUnavailable) as exc:
        asyncio.run(ask_chai(settings(), "test"))
    message = str(exc.value)
    assert "DNS lookup failed" in message
    assert "ConnectError" in message
    for secret in ("hidden-password", "hidden-query", "hidden-fragment", "synthetic-secret"):
        assert secret not in message


def test_summary_contains_grouped_reasons(report, collected_report, tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("CHAI_MCP_URL", "https://chai.example/mcp")
    monkeypatch.setenv("CHAI_MCP_TOKEN", "synthetic-secret")

    async def ask(config, prompt, **kwargs):
        raise ChaiUnavailable("Required MCP client package is not installed.")

    monkeypatch.setattr("edge_ocp_ci_monitor.analysis.ask_chai", ask)
    out = tmp_path / "report.html"
    with caplog.at_level(logging.INFO, logger="edge_ocp_ci_monitor"):
        result = CliRunner().invoke(cli.main, ["--skip-prow", "--output", str(out)])
    assert result.exit_code == 0, result.exception
    summary = next(record.message for record in caplog.records if record.message.startswith("Chai analysis:"))
    assert "unavailable (0/2 targets)" in summary
    assert "Reason(s): Required MCP client package is not installed. (x2)" in summary
    assert not out.with_suffix(".chai-status.json").exists()
    assert not out.with_suffix(".ai-targets.json").exists()
    assert "Required MCP client package is not installed" in out.read_text()


def test_cli_fallback_retains_fresh_dashboard_markers_and_sidecars(report, tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("CHAI_MCP_TOKEN", raising=False)
    monkeypatch.delenv("CHAI_MCP_URL", raising=False)
    monkeypatch.setattr(collection, "configured_stream_names", lambda config: ["4.19.0-0.nightly"])
    monkeypatch.setattr(collection, "collect_payloads", lambda *args: report.streams)
    monkeypatch.setattr(collection.sippy, "collect", lambda *args: {"4.19": report.streams[0].regressions})
    monkeypatch.setattr(collection.component_readiness, "collect", lambda *args: [])
    out = tmp_path / "report.html"
    result = CliRunner().invoke(cli.main, ["--output", str(out), "--skip-prow", "--json"])
    assert result.exit_code == 0, result.exception
    assert "BLOCKING_JOBS_START" in result.output
    assert "BLOCKING|job-sno|https://prow.example/latest|SNO|4.19|" in result.output
    html = out.read_text()
    assert "CHAI_MCP_TOKEN" in html and "CHAI_MCP_URL" in html
    assert "Chai AI analysis unavailable" in html
    assert "AI-enriched via Chai" not in html
    assert "use Claude directly" not in html
    assert "CHAI_MCP_TOKEN" in caplog.text
    assert out.with_suffix(".sippy-summary.json").exists()
    assert not out.with_suffix(".chai-targets.json").exists()
    assert not out.with_suffix(".analysis.json").exists()
    assert not out.with_suffix(".chai-status.json").exists()
    assert not out.with_suffix(".ai-targets.json").exists()
    assert json.loads(out.with_suffix(".json").read_text())["streams"][0]["payloads"]
    assert set(tmp_path.iterdir()) == {out, out.with_suffix(".json"), out.with_suffix(".sippy-summary.json")}


@pytest.mark.parametrize("available", [True, False])
@pytest.mark.parametrize("export_json", [True, False])
def test_cli_enrichment_and_remote_failure(report, collected_report, tmp_path, monkeypatch, available, export_json):
    monkeypatch.setenv("CHAI_MCP_TOKEN", "synthetic-secret")
    monkeypatch.setenv("CHAI_MCP_URL", "https://chai.example/mcp")

    async def ask(config, prompt, **kwargs):
        if not available:
            raise ChaiUnavailable("Endpoint unavailable.")
        return batch_answer(prompt)

    monkeypatch.setattr("edge_ocp_ci_monitor.analysis.ask_chai", ask)
    out = tmp_path / "report.html"
    arguments = ["--skip-prow", "--output", str(out)] + (["--json"] if export_json else [])
    result = CliRunner().invoke(cli.main, arguments)
    assert result.exit_code == 0, result.exception
    html = out.read_text()
    assert ("AI-enriched via Chai" in html) == available
    assert ("Chai AI analysis unavailable" in html) != available
    assert "synthetic-secret" not in html
    assert bool(report.streams[0].payloads[0].jobs[0].deep_analysis) == available
    assert bool(report.streams[0].regressions[0].deep_analysis) == available
    if export_json:
        export = json.loads(out.with_suffix(".json").read_text())
        stream = export["streams"][0]
        assert bool(stream["payloads"][0]["edge_jobs"][0]["deep_analysis"]) == available
        assert bool(stream["regressions"][0]["deep_analysis"]) == available
    assert not out.with_suffix(".analysis.json").exists()
    assert not out.with_suffix(".chai-targets.json").exists()
    assert not out.with_suffix(".chai-status.json").exists()
    assert not out.with_suffix(".ai-targets.json").exists()
    expected = {out, out.with_suffix(".sippy-summary.json")}
    if export_json:
        expected.add(out.with_suffix(".json"))
    assert set(tmp_path.iterdir()) == expected


@pytest.mark.parametrize("interrupted", [False, True])
def test_cli_checkpoints_html_and_json_before_followup(report, collected_report, tmp_path, monkeypatch, interrupted):
    monkeypatch.setenv("CHAI_MCP_TOKEN", "synthetic-secret")
    monkeypatch.setenv("CHAI_MCP_URL", "https://chai.example/mcp")
    report.streams[0].regressions = []
    first = report.streams[0].payloads[0].jobs[0]
    second = deepcopy(first)
    second.prow_url = "https://prow.example/second"
    report.streams[0].payloads[0].jobs.append(second)
    out = tmp_path / "report.html"
    calls = []

    async def ask(config, prompt, **kwargs):
        calls.append(prompt)
        assert not out.with_suffix(".analysis.json").exists()
        assert not out.with_suffix(".chai-targets.json").exists()
        if len(calls) == 1:
            export = json.loads(out.with_suffix(".json").read_text())
            assert all(job["deep_analysis"] is None
                       for job in export["streams"][0]["payloads"][0]["edge_jobs"])
            return deferred_answer(prompt, completed=1)
        html = out.read_text()
        assert "Observed test regression" in html
        assert "Chai analysis is in progress (1/2 targets completed)" in html
        export = json.loads(out.with_suffix(".json").read_text())
        jobs = export["streams"][0]["payloads"][0]["edge_jobs"]
        assert jobs[0]["deep_analysis"]["root_cause"] == answer()["root_cause"]
        assert jobs[1]["deep_analysis"] is None
        if interrupted:
            raise asyncio.CancelledError
        return batch_answer(prompt)

    monkeypatch.setattr("edge_ocp_ci_monitor.analysis.ask_chai", ask)
    arguments = ["--skip-prow", "--output", str(out), "--json"]
    if interrupted:
        with pytest.raises(asyncio.CancelledError):
            CliRunner().invoke(cli.main, arguments)
    else:
        result = CliRunner().invoke(cli.main, arguments)
        assert result.exit_code == 0, result.exception
        assert "analysis is in progress" not in out.read_text()
        assert "Chai AI analysis unavailable" not in out.read_text()
    data = json.loads(out.with_suffix(".json").read_text())
    jobs = data["streams"][0]["payloads"][0]["edge_jobs"]
    assert sum(job["deep_analysis"] is not None for job in jobs) == (1 if interrupted else 2)
    assert not out.with_suffix(".analysis.json").exists()
    assert not out.with_suffix(".chai-targets.json").exists()
    assert len(calls) == 2
    assert not out.with_suffix(".analysis.json.tmp").exists()


def test_collected_report_does_not_relabel_stale_analysis_as_chai(report, collected_report, tmp_path, monkeypatch):
    monkeypatch.delenv("CHAI_MCP_TOKEN", raising=False)
    monkeypatch.delenv("CHAI_MCP_URL", raising=False)
    report.streams[0].payloads[0].jobs[0].deep_analysis = DeepAnalysis(root_cause="Old Claude output")
    out = tmp_path / "report.html"
    result = CliRunner().invoke(cli.main, ["--skip-prow", "--output", str(out)])
    assert result.exit_code == 0, result.exception
    assert "Old Claude output" not in out.read_text()
    assert "AI-enriched via Chai" not in out.read_text()


def test_default_timeout_and_invalid_ranges():
    timeout = next(p for p in cli.main.params if p.name == "ai_timeout")
    concurrency = next(p for p in cli.main.params if p.name == "ai_concurrency")
    assert timeout.default == 2700
    assert concurrency.default == 2
    assert concurrency.type.convert("2", None, None) == 2
    assert CliRunner().invoke(cli.main, ["--ai-timeout", "2701"]).exit_code == 2
    assert CliRunner().invoke(cli.main, ["--ai-concurrency", "3"]).exit_code == 2


def test_from_json_removed_only_from_chai_cli():
    runner = CliRunner()
    help_result = runner.invoke(cli.main, ["--help"])
    assert help_result.exit_code == 0
    assert "--from-json" not in help_result.output
    rejected = runner.invoke(cli.main, ["--from-json", "unused.json"])
    assert rejected.exit_code == 2
    assert "No such option" in rejected.output and "--from-json" in rejected.output
    assert "--from-json" in runner.invoke(core.main, ["--help"]).output


def test_chai_keeps_explicit_local_analysis_import(report, tmp_path, monkeypatch):
    out = tmp_path / "report.html"
    generate_chai_html(report, out)
    source = tmp_path / "external-analysis.json"
    source.write_text(json.dumps({"by_prow_url": {"https://prow.example/latest": answer()}}))

    async def unexpected_call(*args, **kwargs):
        pytest.fail("Explicit local merge must not call Chai")

    monkeypatch.setattr(analysis, "ask_chai", unexpected_call)
    result = CliRunner().invoke(cli.main, ["--merge-analysis", str(source), "--output", str(out)])
    assert result.exit_code == 0, result.exception
    assert "Observed test regression" in out.read_text()
    assert "AI-enriched via Chai" in out.read_text()
    assert "AI-enriched via Claude" not in out.read_text()
    assert set(tmp_path.iterdir()) == {out, source}


def test_chai_renderer_leaves_original_rendering_and_exports_intact(report, tmp_path):
    chai_path = tmp_path / "chai.html"
    chai_html = generate_chai_html(report, chai_path, ai_warnings=["<missing evidence>"])
    assert "<title>Edge OCP CI Monitor</title>" in chai_html
    assert "Investigate this failing Prow job:" in chai_html
    assert "&lt;missing evidence&gt;" in chai_html
    assert "use Claude directly" not in chai_html
    assert not chai_path.with_suffix(".ai-targets.json").exists()

    legacy_path = tmp_path / "legacy.html"
    legacy_html = generate_html(report, legacy_path)
    assert "<title>Edge OCP Payload Monitor</title>" in legacy_html
    assert "use Claude directly" in legacy_html
    assert "chai-analysis-warning" not in legacy_html
    assert legacy_path.with_suffix(".ai-targets.json").exists()


def test_no_targets_does_not_connect():
    async def ask(config, prompt):
        pytest.fail("No MCP request should be made without targets")

    result = asyncio.run(analyze_targets(build_targets(MonitorReport()), settings(), ask=ask))
    assert result.status == "not_needed"
    assert result.requested == 0


def test_single_category_sends_one_call_without_format_retry(report):
    calls = []

    async def ask(config, prompt):
        calls.append(prompt)
        return "Not JSON"

    targets = build_targets(report)
    targets["sippy_jobs"] = []
    result = asyncio.run(analyze_targets(targets, settings(), ask=ask))
    assert result.completed == 0
    assert len(calls) == 1
    assert "non-JSON" in result.warnings[0]



def test_existing_cli_does_not_call_chai(report, tmp_path, monkeypatch):
    snapshot = tmp_path / "input.json"
    generate_json(report, snapshot)
    out = tmp_path / "legacy.html"
    result = CliRunner().invoke(core.main, ["--from-json", str(snapshot), "--output", str(out)])
    assert result.exit_code == 0, result.exception
    assert "use Claude directly" in out.read_text()
    assert out.with_suffix(".ai-targets.json").exists()
    assert not out.with_suffix(".chai-status.json").exists()


# Jira recommendations must come from a validated Chai decision, never failure thresholds.
def jira_decision(**updates):
    return {
        "search_status": "checked",
        "search_summary": "Searched OCPBUGS by failed test and root-cause signature; no covering issue found.",
        "existing_bugs": [],
        "new_bug": {
            "title": "[SNO] API unavailable after reboot",
            "description": "SNO loses API access after reboot.\nEvidence: https://prow.example/latest\n"
                           "Reproduce by rebooting the node; API readiness never recovers.\n"
                           "Searched OCPBUGS for the readiness signature; no covering bug exists.",
            "target_versions": [{"name": "4.19.0", "id": "19400"}],
        }, **updates,
    }


def test_jira_prompt_requires_read_only_duplicate_check_and_edge_scope(report):
    prompt = build_prompt(build_targets(report)["blocking_jobs"], sippy=False, evidence={})
    for requirement in ("never create", "existing_bugs", "new_bug=null", "two node fencing",
                        "two node arbiter", "Single Node OpenShift", "never invent IDs",
                        "low pass rate or blocking/persistent status alone"):
        assert requirement in prompt


@pytest.mark.parametrize("sippy", [False, True])
@pytest.mark.parametrize("topology", ["SNO", "TNA", "TNF"])
def test_chai_recommended_draft_prefills_bottom_section_and_jira_link(report, sippy, topology):
    from html import unescape
    from urllib.parse import parse_qs, urlparse

    from edge_ocp_ci_monitor.report import merge_analysis_data

    report.streams[0].payloads[0].jobs[0].topology = topology
    report.streams[0].regressions[0].topology = topology
    targets = build_targets(report)
    targets["blocking_jobs" if sippy else "sippy_jobs"] = []

    async def ask(config, prompt):
        return batch_answer(prompt, jira=jira_decision())

    result = asyncio.run(analyze_targets(targets, settings(), ask=ask))
    assert result.completed == 1 and not result.warnings
    merge_analysis_data(report, result.data)
    assert len(report.suggested_bugs) == 1
    bug = report.suggested_bugs[0]
    assert bug.topology == topology
    assert bug.versions == ["4.19.0"]
    params = parse_qs(urlparse(bug.create_url).query)
    assert params["summary"] == [bug.title]
    assert params["description"] == [bug.full_description]
    assert params["customfield_10855"] == ["19400"]
    assert params["labels"] == ["issue-for-agent"]
    assert "fixVersions" not in params
    html = generate_chai_html(report)
    assert html.index('id="section-suggested-bugs"') > html.index('</div><!-- /tab-payload-health -->')
    assert "Chai-recommended New Bugs" in html
    assert "No issue has been created" in html
    assert "Create Bug in JIRA" not in html
    assert bug.title in unescape(html)
    assert bug.full_description in unescape(html)
    assert 'value="4.19.0"' in html
    assert 'class="jira-draft-labels" type="text" value="issue-for-agent" readonly' in html


@pytest.mark.parametrize("decision", [
    None,
    jira_decision(new_bug=None),
    jira_decision(search_status="unavailable", new_bug=None),
    jira_decision(search_status="not_checked", new_bug=None),
    jira_decision(existing_bugs=["OCPBUGS-123"], new_bug=None),
])
def test_no_draft_without_explicit_recommendation(report, decision):
    from edge_ocp_ci_monitor.report import merge_analysis_data

    async def ask(config, prompt):
        return batch_answer(prompt, jira=decision)

    result = asyncio.run(analyze_targets(build_targets(report), settings(), ask=ask))
    assert result.completed == 2 and not result.warnings
    merge_analysis_data(report, result.data)
    assert not report.suggested_bugs
    assert 'id="section-suggested-bugs"' not in generate_chai_html(report)


@pytest.mark.parametrize("decision", [
    [],
    jira_decision(search_status=[]),
    jira_decision(search_status="unavailable"),
    jira_decision(search_status="not_checked"),
    jira_decision(search_summary=""),
    jira_decision(existing_bugs=["OCPBUGS-123"]),
    jira_decision(existing_bugs="OCPBUGS-123"),
    jira_decision(new_bug={}),
    jira_decision(new_bug={"title": "", "description": "description", "target_versions": []}),
    jira_decision(new_bug={**jira_decision()["new_bug"], "target_versions": []}),
    jira_decision(new_bug={**jira_decision()["new_bug"], "target_versions": [{"name": "4.20.0", "id": "2000"}]}),
    jira_decision(new_bug={**jira_decision()["new_bug"], "target_versions": [{"name": "4.19.0", "id": "invented"}]}),
    jira_decision(new_bug={**jira_decision()["new_bug"], "target_versions": [{"name": "4.19", "id": "19400"}]}),
])
def test_invalid_jira_decisions_preserve_diagnosis_but_never_show_draft(report, decision):
    from edge_ocp_ci_monitor.report import merge_analysis_data

    async def ask(config, prompt):
        return batch_answer(prompt, jira=decision)

    result = asyncio.run(analyze_targets(build_targets(report), settings(), ask=ask))
    assert result.completed == 2
    assert result.warnings
    merge_analysis_data(report, result.data)
    assert report.streams[0].payloads[0].jobs[0].deep_analysis.root_cause == answer()["root_cause"]
    assert not report.suggested_bugs


@pytest.mark.parametrize("topology", ["HA", "", None, "MicroShift"])
def test_drafts_outside_allowed_topologies_rejected(report, topology):
    target = {**build_targets(report)["blocking_jobs"][0], "topology": topology}
    result = validate_batch(json.dumps({"by_prow_url": {
        target["prow_url"]: answer(jira=jira_decision()),
    }}), [target], sippy=False)
    assert target["prow_url"] in result.accepted
    assert "outside SNO, TNA and TNF" in next(iter(result.errors.values()))
    assert result.accepted[target["prow_url"]]["jira"]["new_bug"] is None


def test_known_bug_from_collector_prevents_new_drafts_in_both_categories(report):
    from edge_ocp_ci_monitor.report import merge_analysis_data

    report.streams[0].regressions[0].jira_bug = "OCPBUGS-123"
    targets = build_targets(report)
    assert all(t["known_existing_bugs"] == ["OCPBUGS-123"] for values in targets.values() for t in values)

    async def ask(config, prompt):
        return batch_answer(prompt, jira=jira_decision())

    result = asyncio.run(analyze_targets(targets, settings(), ask=ask))
    assert result.completed == 2 and result.warnings
    merge_analysis_data(report, result.data)
    assert not report.suggested_bugs


def test_later_existing_bug_retracts_earlier_draft(report):
    from edge_ocp_ci_monitor.report import merge_analysis_data

    snapshots = []

    async def ask(config, prompt):
        if "analysis_key" in prompt_targets(prompt)[0]:
            return batch_answer(prompt, jira=jira_decision(existing_bugs=["OCPBUGS-123"], new_bug=None))
        return batch_answer(prompt, jira=jira_decision())

    def save(result):
        merge_analysis_data(report, result.data)
        snapshots.append(len(report.suggested_bugs))

    result = asyncio.run(analyze_targets(build_targets(report), settings(concurrency=1), ask=ask, on_update=save))
    assert result.completed == 2 and not result.warnings
    assert snapshots == [0, 1, 0]


def test_duplicate_recommendations_merge_versions_and_preserve_json_export(report, tmp_path):
    from urllib.parse import parse_qs, urlparse

    from edge_ocp_ci_monitor.report import merge_analysis_data

    other = deepcopy(report.streams[0])
    other.version = "4.20"
    other.regressions[0].version = "4.20"
    report.streams.append(other)
    targets = build_targets(report)
    assert targets["blocking_jobs"][0]["versions"] == ["4.19", "4.20"]

    async def ask(config, prompt):
        data = json.loads(batch_answer(prompt))
        group = next(iter(data))
        for target in prompt_targets(prompt):
            key = target.get("analysis_key", target.get("prow_url"))
            decision = jira_decision()
            decision["new_bug"]["target_versions"] = [
                {"name": version + ".0", "id": "19400" if version == "4.19" else "20400"}
                for version in target.get("versions", [target["version"]])
            ]
            data[group][key]["jira"] = decision
        return json.dumps(data)

    result = asyncio.run(analyze_targets(targets, settings(), ask=ask))
    assert not result.warnings
    merge_analysis_data(report, result.data)
    assert len(report.suggested_bugs) == 1
    bug = report.suggested_bugs[0]
    assert bug.versions == ["4.19.0", "4.20.0"]
    assert parse_qs(urlparse(bug.create_url).query)["customfield_10855"] == ["19400", "20400"]
    path = tmp_path / "report.json"
    generate_json(report, path)
    assert json.loads(path.read_text())["suggested_bugs"][0]["versions"] == bug.versions


def test_collection_without_chai_never_suggests_threshold_based_bugs(collected_report, tmp_path, monkeypatch):
    monkeypatch.delenv("CHAI_MCP_TOKEN", raising=False)
    monkeypatch.delenv("CHAI_MCP_URL", raising=False)
    out = tmp_path / "report.html"
    result = CliRunner().invoke(cli.main, ["--skip-prow", "--output", str(out), "--json"])
    assert result.exit_code == 0, result.exception
    assert 'id="section-suggested-bugs"' not in out.read_text()
    assert json.loads(out.with_suffix(".json").read_text())["suggested_bugs"] == []
    assert collected_report.streams[0].payloads[0].jobs[0].job_type == JobType.BLOCKING


def test_jira_draft_escapes_model_html_without_losing_description(report):
    from html.parser import HTMLParser

    from edge_ocp_ci_monitor.report import merge_analysis_data

    target = build_targets(report)["blocking_jobs"][0]
    decision = jira_decision()
    decision["new_bug"]["title"] = '<img src=x onerror="alert(1)"> & title'
    decision["new_bug"]["description"] = '</textarea><script>alert(1)</script> &amp; evidence'
    data = {"by_prow_url": {target["prow_url"]: answer(jira=decision)}}
    merge_analysis_data(report, data)
    html = generate_chai_html(report)
    assert '&lt;/textarea&gt;&lt;script&gt;alert(1)&lt;/script&gt; &amp;amp; evidence' in html

    class Images(HTMLParser):
        count = 0

        def handle_starttag(self, tag, attrs):
            if tag == "img":
                self.count += 1

    parser = Images()
    parser.feed(html)
    assert parser.count == 0


@pytest.mark.parametrize("legacy", [False, True])
def test_local_analysis_import_removes_prior_jira_drafts(report, tmp_path, legacy):
    from edge_ocp_ci_monitor.report import merge_analysis_data, patch_analysis_html
    from payload_monitor.collectors.jira import suggest_bug
    from payload_monitor.config import Config

    out = tmp_path / "report.html"
    source = tmp_path / "analysis.json"
    if legacy:
        report.suggested_bugs = [suggest_bug(report.streams[0].payloads[0].jobs[0], ["4.19"], Config())]
        generate_html(report, out)
        assert "Create Bug in JIRA" in out.read_text()
    else:
        merge_analysis_data(report, {"by_sippy_job": {
            "4.19/job-sno": answer(jira=jira_decision()),
        }})
        generate_chai_html(report, out)
        assert "Chai-recommended New Bugs" in out.read_text()
    source.write_text(json.dumps({"by_prow_url": {
        "https://prow.example/latest": answer(root_cause="Updated diagnosis from external analysis"),
    }}))
    patch_analysis_html(out, source)
    html = out.read_text()
    assert 'id="section-suggested-bugs"' not in html
    assert "Create Bug in JIRA" not in html
    assert "Review draft in Jira" not in html
    assert 'id="tab-payload-health"' in html
    assert "Updated diagnosis from external analysis" in html



def test_jira_drafts_have_compact_cards_and_independent_dialogs(report):
    import re

    from payload_monitor.models import SuggestedBug

    report.suggested_bugs = [
        SuggestedBug(title=f"Draft {topology}", description="Full diagnostic details",
                     full_description=f"Full diagnostic details for {topology}", job_name=f"job-{topology}",
                     topology=topology, versions=["4.19.0"], prow_url=f"https://prow.example/{topology}",
                     create_url="https://jira.example/create")
        for topology in ("SNO", "TNA", "TNF")
    ]
    html = generate_chai_html(report)
    summaries = re.findall(r'<button[^>]*class="jira-draft-summary"[^>]*>(.*?)</button>', html, re.DOTALL)
    dialogs = re.findall(r'<dialog([^>]*)>(.*?)</dialog>', html, re.DOTALL)
    assert len(summaries) == len(dialogs) == 3
    for index, (topology, summary, (attributes, dialog)) in enumerate(zip(
        ("SNO", "TNA", "TNF"), summaries, dialogs, strict=True,
    ), 1):
        assert f"Draft {topology}" in summary and topology in summary and "4.19.0" in summary
        assert "Full diagnostic details" not in summary and "textarea" not in summary
        assert f'id="jira-draft-{index}"' in attributes
        assert f'aria-controls="jira-draft-{index}"' in html
        assert " open" not in attributes
        assert f"Full diagnostic details for {topology}" in dialog
        assert 'value="issue-for-agent"' in dialog
        actions = re.findall(r'<(?:button|a)[^>]*class="create-btn[^"]*"[^>]*>(.*?)</(?:button|a)>', dialog)
        assert actions == ["Review draft in Jira", "Gather details with Chai", "Copy full description"]
        assert f"https://prow.example/{topology}" in dialog
        assert 'class="jira-chai-prompt"' in dialog and ' hidden>' in dialog
        assert "Press Esc or click outside to close" in dialog
