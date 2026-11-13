"""Bounded MCP investigations and validation of the existing analysis contract."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import sys
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from payload_monitor.__main__ import _collect_jobs_by_type
from payload_monitor.job_health import priority_sippy_findings, sippy_findings
from payload_monitor.models import JobType, MonitorReport

from .jira import JIRA_SCHEMA, validate_jira

logger = logging.getLogger(__name__)
_HEARTBEAT_SECONDS = 30


@dataclass
class AnalysisResult:
    data: dict = field(default_factory=lambda: {"by_prow_url": {}, "by_sippy_job": {}})
    warnings: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    requested: int = 0
    completed: int = 0

    def warn(self, reason: str, *, target: str) -> None:
        self.reasons.append(reason)
        self.warnings.append(f"{target}: {reason}")

    @property
    def reason_counts(self) -> dict[str, int]:
        return dict(Counter(self.reasons or self.warnings))

    @property
    def status(self) -> str:
        if self.warnings:
            return "partial" if self.completed else "unavailable"
        return "complete" if self.requested else "not_needed"


@dataclass(frozen=True)
class Settings:
    url: str
    token: str = field(repr=False)
    timeout: float = 2700
    concurrency: int = 2

    @classmethod
    def from_environment(cls, *, timeout: float, concurrency: int) -> tuple[Settings | None, list[str]]:
        values = {name: os.environ.get(name, "").strip() for name in ("CHAI_MCP_URL", "CHAI_MCP_TOKEN")}
        missing = [name for name, value in values.items() if not value]
        if missing:
            return None, [f"Missing environment variable(s): {', '.join(missing)}. Export them and rerun for Chai analysis."]
        try:
            parsed = urlparse(values["CHAI_MCP_URL"])
            _ = parsed.port
        except ValueError:
            return None, ["CHAI_MCP_URL is malformed; use the full persona MCP URL from Chai's setup."]
        if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password:
            return None, ["CHAI_MCP_URL must be an absolute HTTP(S) MCP URL without embedded credentials."]
        return cls(values["CHAI_MCP_URL"], values["CHAI_MCP_TOKEN"], timeout=timeout, concurrency=concurrency), []


class ChaiUnavailable(Exception):
    """An expected remote, credential, or dependency failure, safe to display."""


class InvalidAnalysis(ValueError):
    """The returned answer does not meet the dashboard analysis contract."""


def _safe_detail(text: str, token: str) -> str:
    """Keep actionable diagnostics, excluding credentials and URL parameters."""
    if token:
        text = text.replace(token, "[REDACTED]")
    text = re.sub(r"(?i)\bBearer\s+[^\s'\"<>]+", "Bearer [REDACTED]", text)

    def redact_url(match):
        # Drop all userinfo, query values and fragments, not just named keys.
        value = match.group(0)
        value = re.sub(r"(?<=//)[^/\s]*@", "[REDACTED]@", value)
        return re.split(r"[?#]", value, maxsplit=1)[0]

    text = re.sub(r"https?://[^\s'\"<>]+", redact_url, text)
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    return " ".join(text.split())[:800]


def build_targets(report: MonitorReport) -> dict:
    """Use exactly the existing blocking scope and five-job Sippy selection."""
    blocking = {}
    known_bugs = {}
    for stream in report.streams:
        for finding in stream.regressions:
            if finding.jira_bug:
                known_bugs.setdefault((stream.version, finding.test_name), []).append(finding.jira_bug)
    for job in _collect_jobs_by_type(report, JobType.BLOCKING):
        # One URL can occur in more than one sampled payload.
        if job["prow_url"] in blocking:
            previous = blocking[job["prow_url"]]["previous_attempt_urls"]
            previous.extend(url for url in job["previous_attempt_urls"] if url not in previous)
            versions = blocking[job["prow_url"]]["versions"]
            if job["version"] not in versions:
                versions.append(job["version"])
            blocking[job["prow_url"]]["known_existing_bugs"].extend(
                known_bugs.get((job["version"], job["name"]), [])
            )
        else:
            blocking[job["prow_url"]] = {
                **job, "previous_attempt_urls": list(job["previous_attempt_urls"]), "versions": [job["version"]],
                "known_existing_bugs": list(known_bugs.get((job["version"], job["name"]), [])),
            }
    sippy = []
    for finding in priority_sippy_findings(sippy_findings(report))[:5]:
        runs = {}
        for stream in report.streams:
            if stream.version != finding.version:
                continue
            for payload in stream.payloads:
                for job in payload.failing_edge_jobs:
                    if job.name == finding.test_name and job.prow_url:
                        runs[job.prow_url] = {
                            "prow_url": job.prow_url,
                            "previous_attempt_urls": [a.prow_url for a in job.previous_attempts],
                        }
        sippy.append({
            "analysis_key": finding.analysis_key, "job_name": finding.test_name,
            "version": finding.version, "topology": finding.topology,
            "triage_url": finding.triage_url, "current_pass_rate": finding.sample_pass_rate,
            "previous_pass_rate": finding.basis_pass_rate, "relative_change": finding.relative_change,
            "current_runs": finding.current_runs, "known_failing_runs": list(runs.values()),
            "known_existing_bugs": list(known_bugs.get((finding.version, finding.test_name), [])),
        })
    return {"blocking_jobs": list(blocking.values()), "sippy_jobs": sippy}


_SCHEMA = {
    "job_name": "exact requested job name",
    "root_cause": "diagnosis, evidence links and uncertainty",
    "failure_type": "Test regression",
    "impact": "affected topology and release; distinguish historical job health from payload blocking",
    "suspect_prs": [], "recommendation": "specific next action",
    "same_root_cause": True, "attempt_analyses": [],
}


def build_prompt(
    targets: list[dict], *, sippy: bool, evidence: dict, timeout: float = 2700,
    deferred_notes: dict | None = None, final_attempt: bool = False,
) -> str:
    """Let the persona drive investigation; specify the task and output contract."""
    group = "by_sippy_job" if sippy else "by_prow_url"
    key_field = "analysis_key" if sippy else "prow_url"
    task = (
        "Analyze these Sippy job-health findings and their failed Prow runs. "
        "Use the supplied pass-rate history. This is job health, not a Component Readiness "
        "test regression; improving low rates do not establish a new regression."
        if sippy else
        "Analyze these Prow job failures."
    )
    shape = {
        group: {f"<exact completed or failed {key_field}>": {**_SCHEMA, "jira": JIRA_SCHEMA}},
        "deferred": {f"<exact unfinished {key_field}>": {
            "reason": "budget_exhausted", "notes": "evidence found so far and the next investigation step",
        }},
    }
    completed = ("\n\nPreviously completed investigations:\n" + json.dumps(evidence, sort_keys=True, separators=(",", ":"))
                 if evidence else "")
    notes = ("\n\nPrevious investigation notes for deferred targets:\n"
             + json.dumps(deferred_notes, sort_keys=True, separators=(",", ":")) if deferred_notes else "")
    investigation_seconds = timeout * 8 / 9
    stop_at = (datetime.now(UTC) + timedelta(seconds=investigation_seconds)).isoformat(timespec="seconds")
    budget = (
        f"\n\nTime budget: stop investigating within {investigation_seconds:g} seconds, "
        f"by {stop_at} (UTC), whichever comes first. The request has a hard timeout of {timeout:g} seconds. "
        "Use the remaining time to return complete, valid JSON with all findings gathered so far. "
        "Do not start work that cannot finish within this budget. "
        + ("This is the final attempt; no further follow-up will be made. " if final_attempt else "")
    )
    return (
        task + " Explain why each job and its tests failed, distinguish the root cause from "
        "downstream failures, and recommend next steps. Include evidence links and flag "
        "anything uncertain. Reuse evidence for repeated runs. Investigate without making changes."
        " Use Jira read-only: never create, edit, comment on or transition an issue. "
        "For each finding, search Jira for existing bugs covering the diagnosed root cause, including "
        "known_existing_bugs and relevant resolved bugs. Record search_status, the queries and results "
        "in search_summary, and matching issue keys in existing_bugs. If access fails or the search is "
        "incomplete, use unavailable or not_checked and new_bug=null; absence of a supplied bug is not "
        "proof that none exists. Recommend a new bug only when no existing bug covers the failure, "
        "the evidence justifies filing, and it relates to the target's SNO (Single Node OpenShift), "
        "TNF (two node fencing) or TNA (two node arbiter) topology. General HA or unrelated failures "
        "must not get drafts. A low pass rate or blocking/persistent status alone is not a reason to file. "
        "Otherwise set new_bug=null, even though the template illustrates an object. For a recommendation, "
        "fill new_bug with a concise title, a complete description (root cause, topology-specific impact, "
        "reproduction/failing tests, evidence links and duplicate-search results), and target_versions. "
        "Read the OCPBUGS project version metadata to resolve each target version's actual name and numeric "
        "ID; never invent IDs. Use only releases present in the target (X.Y.0 or X.Y.z), not unrelated releases. "
        "If version metadata cannot be verified, return new_bug=null and explain in search_summary. "
        "This is a recommendation for human review, never authorization to create a bug."
        + budget + "\n\nTargets:\n" + json.dumps(targets, sort_keys=True, separators=(",", ":"))
        + completed + notes + "\n\nReturn only JSON in this format:\n" + json.dumps(shape, separators=(",", ":"))
        + f"\nInclude every target, keyed by its exact {key_field}, with no extra keys. "
        + ("Copy job_name from the target. " if sippy else "Copy the target's name into job_name. ")
        + "failure_type must be Infrastructure flake, Test regression, Install failure, Platform issue or Analysis error. "
        "Compare supplied attempts. If causes differ, set same_root_cause=false and include every previous "
        "attempt then the latest in attempt_analyses, each with prow_url, root_cause and failure_type. "
        "Otherwise use same_root_cause=true and an empty list. For access, missing-evidence or processing failures, "
        "return an Analysis error entry explaining what failed and the next step. Only targets unfinished solely "
        "because the investigation time budget ran out belong in deferred, with reason=budget_exhausted and "
        "nonempty notes (at most 4000 characters) containing partial evidence and the next step. "
        "Each target must appear in exactly one map; do not omit targets. Use an empty deferred map when done. "
        "Reuse the supplied completed findings and notes; this request has no access to the previous session."
    )


def _https_url(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlparse(value)
        return parsed.scheme == "https" and bool(parsed.netloc)
    except ValueError:
        return False


def _parse_json(text: str) -> dict:
    # Accept a single JSON fence, but never heuristically extract JSON from prose.
    text = text.strip()
    fence = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InvalidAnalysis("Chai returned duplicate JSON keys.")
            result[key] = value
        return result

    try:
        data = json.loads(text, object_pairs_hook=unique_keys)
    except json.JSONDecodeError as exc:
        raise InvalidAnalysis(
            f"Chai returned non-JSON analysis: {exc.msg} at line {exc.lineno}, column {exc.colno}."
        ) from None
    if not isinstance(data, dict):
        raise InvalidAnalysis("Chai analysis must be a JSON object.")
    return data


def validate_analysis(text: str, target: dict, *, sippy: bool) -> dict:
    """Validate a single analysis, also used by the per-entry batch validator."""
    data = _parse_json(text)
    result = _validate_entry(data, target, sippy=sippy)
    if "jira" in data:
        try:
            result["jira"] = validate_jira(data["jira"], target)
        except ValueError as exc:
            raise InvalidAnalysis(str(exc)) from None
    return result


def _validate_entry(data: dict, target: dict, *, sippy: bool) -> dict:
    """Reject wrong jobs, incomplete attempts and malformed fields independently."""
    if not isinstance(data, dict):
        raise InvalidAnalysis("Chai analysis entry must be a JSON object.")
    expected_name = target["job_name"] if sippy else target["name"]
    for key in ("job_name", "root_cause", "failure_type", "impact", "recommendation"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise InvalidAnalysis(f"Chai analysis has a missing or invalid {key}.")
    if data["job_name"] != expected_name:
        raise InvalidAnalysis("Chai returned analysis for a different job.")
    if data["failure_type"] not in {
        "Infrastructure flake", "Test regression", "Install failure", "Platform issue", "Analysis error",
    }:
        raise InvalidAnalysis("Chai returned an unsupported failure_type.")
    prs = data.get("suspect_prs")
    if not isinstance(prs, list) or any(not _https_url(p) for p in prs):
        raise InvalidAnalysis("Chai analysis has invalid suspect_prs.")
    if type(data.get("same_root_cause")) is not bool:
        raise InvalidAnalysis("Chai analysis has invalid same_root_cause.")
    attempts = data.get("attempt_analyses", [])
    if not isinstance(attempts, list):
        raise InvalidAnalysis("Chai analysis has invalid attempt_analyses.")
    for attempt in attempts:
        if not isinstance(attempt, dict) or any(
            not isinstance(attempt.get(k), str) or not attempt[k].strip()
            for k in ("prow_url", "root_cause", "failure_type")
        ):
            raise InvalidAnalysis("Chai analysis has an incomplete attempt entry.")
        if not _https_url(attempt["prow_url"]):
            raise InvalidAnalysis("Chai analysis has an invalid attempt URL.")
    if not sippy:
        expected_urls = list(dict.fromkeys([*target["previous_attempt_urls"], target["prow_url"]]))
        if (attempts or not data["same_root_cause"]) and [a["prow_url"] for a in attempts] != expected_urls:
            raise InvalidAnalysis("Chai analysis does not cover the requested attempts in order.")
    data["attempt_analyses"] = attempts
    return {key: data[key] for key in _SCHEMA}


@dataclass
class BatchResult:
    accepted: dict = field(default_factory=dict)
    errors: dict = field(default_factory=dict)
    deferred: dict = field(default_factory=dict)


def validate_batch(text: str, targets: list[dict], *, sippy: bool) -> BatchResult:
    """Retain valid requested entries and explain every missing/invalid entry."""
    group = "by_sippy_job" if sippy else "by_prow_url"
    key_field = "analysis_key" if sippy else "prow_url"
    data = _parse_json(text)
    if group not in data or set(data) - {group, "deferred"} or not isinstance(data[group], dict):
        raise InvalidAnalysis(f"Chai batch response must contain '{group}' and optionally 'deferred', both maps.")
    expected = {target[key_field]: target for target in targets}
    entries = data[group]
    result = BatchResult()
    accepted, errors = result.accepted, result.errors
    deferred = data.get("deferred", {})
    if not isinstance(deferred, dict):
        errors["deferred response"] = "Chai deferred targets must be a map."
        deferred = {}
    extra = sorted((entries.keys() | deferred.keys()) - expected.keys())
    if extra:
        errors[f"{group} response"] = f"Unexpected response key(s) discarded: {', '.join(extra)}."
    for key, target in expected.items():
        if key in deferred:
            item = deferred[key]
            if key in entries:
                errors[key] = "Chai returned this target in both the analysis and deferred maps."
            elif (not isinstance(item, dict) or set(item) != {"reason", "notes"}
                  or item["reason"] != "budget_exhausted" or not isinstance(item["notes"], str)
                  or not item["notes"].strip() or len(item["notes"]) > 4000):
                errors[key] = "Chai returned an invalid budget deferral; expected reason=budget_exhausted and notes."
            else:
                result.deferred[key] = item
            continue
        if key not in entries:
            errors[key] = "Chai batch response omitted this requested target."
            continue
        try:
            analysis = _validate_entry(entries[key], target, sippy=sippy)
        except InvalidAnalysis as exc:
            errors[key] = str(exc)
            continue
        if analysis["failure_type"] == "Analysis error":
            errors[key] = ("Chai reported Analysis error. Reason: " + analysis["root_cause"]
                           + " Next action: " + analysis["recommendation"])
            continue
        if "jira" in entries[key]:
            try:
                analysis["jira"] = validate_jira(entries[key]["jira"], target)
            except ValueError as exc:
                # Keep a useful diagnosis even when its filing recommendation
                # is incomplete or unsafe to display as a new-bug draft.
                errors[f"{key} Jira"] = str(exc)
                raw = entries[key]["jira"]
                if isinstance(raw, dict):
                    try:
                        analysis["jira"] = validate_jira({**raw, "new_bug": None}, target)
                    except ValueError:
                        pass
        accepted[key] = analysis
    return result


def _duration(seconds: float) -> str:
    minutes, seconds = divmod(int(max(0, seconds)), 60)
    return f"{minutes:02d}:{seconds:02d}"


@dataclass
class _BatchProgress:
    category: str
    settings: Settings
    started: float = field(default_factory=time.monotonic)
    last_update: float | None = None
    heartbeat: asyncio.Task | None = field(default=None, init=False, repr=False)

    async def __aenter__(self):
        self.started = time.monotonic()
        self.heartbeat = asyncio.create_task(self._heartbeat(), name=f"chai-heartbeat-{self.category}")
        return self

    async def __aexit__(self, *exc):
        self.heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await self.heartbeat

    async def on_progress(self, progress: float, total: float | None, message: str | None) -> None:
        self.last_update = time.monotonic()
        detail = _safe_detail(message or "", self.settings.token) or "Progress update received"
        logger.info("Chai [%s]: %s", self.category, detail)

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(_HEARTBEAT_SECONDS)
            now = time.monotonic()
            last = (f"{_duration(now - self.last_update)} ago" if self.last_update is not None
                    else "none received")
            logger.info("Chai [%s]: waiting; elapsed %s / %s; last Chai update %s",
                        self.category, _duration(now - self.started), _duration(self.settings.timeout), last)


async def ask_chai(settings: Settings, question: str, *, progress: _BatchProgress | None = None) -> str:
    """Keep the SDK optional so missing MCP dependencies still allow a report."""
    try:
        import anyio
        import httpx2
        from fastmcp import Client
        from fastmcp.client.auth import BearerAuth
        from fastmcp.exceptions import McpError
    except ImportError as exc:
        install = shlex.join([sys.executable, "-m", "pip", "install", "-r",
                              str(Path(__file__).resolve().parents[1] / "requirements.txt")])
        reason = _safe_detail(f"{type(exc).__name__}: {exc}", settings.token)
        raise ChaiUnavailable(
            f"MCP dependencies are missing or incompatible. Reason: {reason}. Install using this interpreter: {install}"
        ) from None

    def expected(exc: Exception) -> bool:
        if isinstance(exc, ExceptionGroup):
            return all(expected(child) for child in exc.exceptions)
        if isinstance(exc, RuntimeError) and exc.__cause__ is not None:
            return expected(exc.__cause__)
        return isinstance(exc, (
            httpx2.HTTPError, httpx2.InvalidURL, McpError, OSError, TimeoutError,
            anyio.BrokenResourceError, anyio.ClosedResourceError, anyio.EndOfStream,
        ))

    def diagnostic(exc: Exception) -> str:
        if isinstance(exc, ExceptionGroup):
            return "; ".join(dict.fromkeys(diagnostic(child) for child in exc.exceptions))
        if isinstance(exc, RuntimeError) and exc.__cause__ is not None:
            return diagnostic(exc.__cause__)
        if isinstance(exc, httpx2.HTTPStatusError):
            # Do not print response bodies, headers or the request URL.
            status = exc.response.status_code
            hint = {
                401: "Authentication was rejected; check token validity.",
                403: "Access was denied; check persona permissions.",
                404: "Endpoint or session not found; check the full persona MCP URL.",
                405: "HTTP method rejected; check that the URL is an MCP endpoint.",
                429: "The service is at its request/capacity limit.",
            }.get(status, "The server returned an HTTP error.")
            return f"HTTP {status} {exc.response.reason_phrase}. {hint}"
        return _safe_detail(f"{type(exc).__name__}: {exc}", settings.token)

    try:
        async with asyncio.timeout(settings.timeout):
            async with Client(
                settings.url, auth=BearerAuth(settings.token), timeout=settings.timeout, mode="legacy",
            ) as client:
                result = await client.call_tool(
                    "ask_persona", {"question": question}, timeout=settings.timeout,
                    raise_on_error=False, progress_handler=progress.on_progress if progress else None,
                )
    except TimeoutError:
        raise ChaiUnavailable(f"Chai exceeded its {settings.timeout:g}-second request budget.") from None
    except Exception as exc:
        if not expected(exc):
            raise
        raise ChaiUnavailable(
            f"Chai MCP could not complete the request. Reason: {diagnostic(exc)}"
        ) from None
    text = "\n".join(block.text for block in result.content if block.type == "text")
    if result.is_error:
        reason = _safe_detail(text, settings.token) or "Server returned isError=true without an explanation."
        raise ChaiUnavailable(f"Chai refused the MCP tool call. Reason: {reason}")
    if text.startswith(("Authentication required", "Authentication failed", "Access denied")):
        raise ChaiUnavailable(
            "Chai authentication or persona access failed. Reason: " + _safe_detail(text, settings.token)
        )
    return text


async def analyze_targets(
    targets: dict, settings: Settings, *,
    ask: Callable[[Settings, str], Awaitable[str]] | None = None,
    on_update: Callable[[AnalysisResult], None] | None = None,
) -> AnalysisResult:
    """Investigate two categories, with one follow-up each for error-free budget deferrals."""
    result = AnalysisResult(requested=len(targets["blocking_jobs"]) + len(targets["sippy_jobs"]))
    semaphore = asyncio.Semaphore(min(settings.concurrency, 2))

    def checkpoint() -> None:
        if on_update is not None:
            on_update(result)

    checkpoint()

    async def investigate(batch: list[dict], *, sippy: bool) -> None:
        if not batch:
            return
        category = "Sippy-tests" if sippy else "blocking-jobs"
        group = "by_sippy_job" if sippy else "by_prow_url"
        key_field = "analysis_key" if sippy else "prow_url"
        deferred_notes = {}

        def warn(reason: str, key: str) -> None:
            if key in deferred_notes:
                reason += " Previous investigation notes: " + deferred_notes[key]["notes"]
            result.warn(_safe_detail(reason, settings.token), target=_safe_detail(key, settings.token))

        for attempt in range(2):
            async with semaphore:
                # A final request can reuse everything already returned by its
                # category, plus completed blocking evidence for Sippy runs.
                evidence = {
                    run["prow_url"]: result.data["by_prow_url"][run["prow_url"]]
                    for target in batch for run in target.get("known_failing_runs", [])
                    if run["prow_url"] in result.data["by_prow_url"]
                }
                evidence.update(result.data[group])
                logger.info("Chai [%s]: investigating batch (%d targets, up to %.0f minutes; attempt %d/2)",
                            category, len(batch), settings.timeout / 60, attempt + 1)
                prompt = build_prompt(batch, sippy=sippy, evidence=evidence, timeout=settings.timeout,
                                      deferred_notes=deferred_notes, final_attempt=bool(attempt))
                try:
                    async with _BatchProgress(category, settings) as progress, asyncio.timeout(settings.timeout):
                        text = (await ask(settings, prompt) if ask is not None
                                else await ask_chai(settings, prompt, progress=progress))
                        response = validate_batch(text, batch, sippy=sippy)
                except TimeoutError:
                    for target in batch:
                        warn(f"Chai {category} batch exceeded the {settings.timeout:g}-second investigation budget.",
                             target[key_field])
                    checkpoint()
                    return
                except (ChaiUnavailable, InvalidAnalysis) as exc:
                    for target in batch:
                        warn(str(exc), target[key_field])
                    checkpoint()
                    return
                result.data[group].update(response.accepted)
                result.completed += len(response.accepted)
                for key, reason in response.errors.items():
                    warn(reason, key)
                retry = bool(response.deferred) and not response.errors and attempt == 0
                if not retry:
                    for key, item in response.deferred.items():
                        reason = ("Chai deferred this target because its investigation budget ran out. "
                                  + ("Final attempt exhausted. " if attempt else "Follow-up skipped because the batch had errors. ")
                                  + "Investigation notes: " + item["notes"])
                        result.warn(_safe_detail(reason, settings.token), target=_safe_detail(key, settings.token))
                checkpoint()
                logger.info("Chai [%s]: completed batch (%d/%d validated; %d/%d targets overall)",
                            category, len(response.accepted), len(batch), result.completed, result.requested)
            if not retry:
                return
            logger.info("Chai [%s]: making final request for %d budget-deferred targets", category, len(response.deferred))
            batch = [target for target in batch if target[key_field] in response.deferred]
            deferred_notes = response.deferred

    await asyncio.gather(
        investigate(targets["blocking_jobs"], sippy=False),
        investigate(targets["sippy_jobs"], sippy=True),
    )
    return result
