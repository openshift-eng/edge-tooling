# Edge OCP CI Monitor

Generate the existing Edge OCP payload dashboard with AI investigations performed by Chai Bot over MCP. The tool imports the unchanged `payload-monitor` collectors, report models, topology filtering, failure intelligence, Sippy selection, Component Readiness and timing insights. Collection orchestration, Chai dashboard templates, rendering, Jira recommendations and in-memory analysis merging live in `edge_ocp_ci_monitor/`. It does not require Claude Code or the Claude marketplace plugins.

The original `payload-monitor/` directory remains unmodified, including its CLI, renderer, templates and documentation. The existing `edge-ocp-ci` Claude plugin and release repository workflow remain available with their existing defaults while the new entry point is adopted.

## Prerequisites

- Python 3.11 or newer.
- This repository checkout, including the sibling `payload-monitor/` directory.
- The same collector prerequisites as [Payload Monitor](../payload-monitor/README.md#data-sources), including Google Cloud SDK/`gsutil` for Prow artifacts.
- Your existing MCP token and full persona MCP URL exported as **`CHAI_MCP_TOKEN`** and **`CHAI_MCP_URL`**. The token must admit you to the persona in that URL. A webhook URL is not an MCP URL.

The tool reads environment variables; it does not create tokens, read Chai server secrets, or automatically load `.env` files. Export variables from your existing local secret configuration before starting it. Never put token values in command arguments, Git, report artifacts or shared terminal logs.

The token is sent only in the `Authorization: Bearer ...` header to the configured HTTPS persona endpoint. Endpoint URLs containing credentials, query parameters, fragments or the token are rejected, and authenticated requests do not follow redirects. Request guards reject credentials in payloads or other headers. Use the exact MCP endpoint URL, including any required trailing slash.

Credential redaction covers application and SDK log messages, arguments and exception tracebacks while the command or MCP call is active, including verbose logging and concurrent requests. Raw and commonly encoded token echoes are removed from prompts, returned analyses, local imports, HTML and JSON exports. Authentication still requires transmitting the header to Chai; the client cannot control how the remote server handles that header.

## Setup and usage

From this directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

# CHAI_MCP_URL and CHAI_MCP_TOKEN are already exported in this shell.
python -m edge_ocp_ci_monitor --payloads 3 --with-timing
```

The standalone requirements pin Requests to **2.34.2**, Jinja2 to **3.1.6**, Click to **8.5.1** and FastMCP to **4.1.8**; they do not inherit the original tool's dependency ranges. To update an existing virtual environment, activate it and rerun `python -m pip install -r requirements.txt`. The client uses FastMCP's `legacy` handshake mode for compatibility with the existing Chai MCP endpoint and progress notifications.

Run the module from this directory with the virtual environment activated. Output paths are relative to the current directory, as with `payload-monitor`.

```bash
python -m edge_ocp_ci_monitor --versions 4.19,5.1 --payloads 3 \
  --with-timing --json --output reports/edge-ci.html
```

The Chai frontend always collects fresh dashboard data and does not accept `--from-json`. The optional `--json` export remains available for external consumers. Explicit `--merge-analysis` can still patch a compatible externally supplied analysis file into an existing HTML dashboard without starting new MCP investigations. The original `payload-monitor` retains its `--from-json` option.

Analysis-card rendering and local HTML imports are owned by this tool. Imports update existing blocking cards by their Prow URL as well as empty placeholders, preserving unrelated cards. Attempt links are restricted to safe HTTPS URLs in both imported and directly rendered cards. These fixes do not change the original `payload-monitor` renderer.

## Configuration

| Variable | Required for AI | Meaning |
|---|---|---|
| `CHAI_MCP_URL` | Yes | Full persona URL, such as `https://<host>/personas/openshift_edge_internal/mcp` |
| `CHAI_MCP_TOKEN` | Yes | Existing bearer token, without the `Bearer` prefix and its separating space |

All existing `payload-monitor` CLI options except `--from-json` are accepted, with two additions:

| Option | Default | Meaning |
|---|---|---|
| `--ai-timeout SECONDS` | `2700` | Hard timeout per request, including connection setup; range 1–2700 seconds (45 minutes). Each category may make one final request. |
| `--ai-concurrency N` | `2` | Maximum active `ask_persona` calls overall; range 1–2 |

Chai can take several minutes for one answer. The adapter starts with two category batches: one containing the complete selected blocking-job list and one containing the complete selected Sippy job/test-finding list. They run concurrently by default. An empty category makes no call. Each category may make **one final request for budget-deferred targets**, for a maximum of **four `ask_persona` calls per run**, with at most two active calls overall. There are no per-target or format-correction calls.

Each request gets a **45-minute hard timeout** for all its targets. The prompt asks Chai to stop investigating after **40 minutes**, or at an explicit UTC deadline, whichever comes first, and use the remaining five minutes to return valid JSON containing its findings so far. For a custom timeout, the investigation budget is eight ninths of that timeout, reserving one ninth for the response. This is prompt guidance: a blocked tool or model may still reach the hard timeout without returning usable findings.

A final request is allowed only when the first response passes validation and has targets explicitly deferred solely because the investigation budget ran out. It receives only those targets, the category's completed findings, and the deferred investigation notes. Chai starts a fresh session for every request; the supplied evidence and notes provide continuity. Any processing, access, missing-evidence or validation error in the first response prevents that category's final request. Hard timeouts and transport failures are not retried. A final response can also defer targets, but this produces warnings and no further request.

With both categories running in parallel, the AI stage can take up to about **90 minutes** if both need a final request, plus connection cleanup and checkpoint rendering; collection adds its own runtime. `--ai-concurrency 1` serializes all requests, potentially taking about **180 minutes**. A run whose initial responses finish all targets still makes only two calls. Set any enclosing terminal, CI step or job timeout accordingly. Existing release step timeouts are not changed by this tool.

The client timeout does not extend Chai's server-side limits. The checked-in Edge persona inherits a 45-minute cap from `ocp_ai_helpdesk`; other personas may retain Chai's 30-minute default. Use an `--ai-timeout` compatible with the selected persona's cap. At the hard timeout the monitor cancels its wait and closes the MCP client; it cannot retrieve intermediate findings from that session afterward. External work already started may still be running. Chai's per-user limit normally allows two calls, shared with other MCP activity under the same identity. Capacity refusals retain the warning/fallback behavior. Batches are not split to evade limits; an oversized prompt refused by Chai is reported as an unavailable batch.

The adapter uses the normal initialized MCP HTTP protocol, keeping long responses open through the SDK. It does not depend on the sandbox-only background task protocol. MCP initialization/discovery traffic is separate from the two `ask_persona` calls.

At the normal INFO log level, each active request reports Chai's MCP progress messages as they arrive and a local heartbeat every 30 seconds. All investigation logs use `[blocking-jobs]` or `[Sippy-tests]` to distinguish the categories. Request starts include the attempt number, and a final request announces its deferred-target count. The heartbeat includes elapsed time, the configured timeout and time since the last Chai progress notification (or `none received`). It stops when that request completes, fails, times out or is cancelled. Batch completion includes validated-target counts; failures retain their diagnostic warnings.

```text
INFO Chai [blocking-jobs]: investigating batch (6 targets, up to 45 minutes; attempt 1/2)
INFO Chai [Sippy-tests]: Thinking
INFO Chai [blocking-jobs]: waiting; elapsed 03:30 / 45:00; last Chai update 00:40 ago
INFO Chai [Sippy-tests]: completed batch (5/5 validated; 5/11 targets overall)
```

Chai progress is activity information, not a completion percentage or an estimated finish time. Notifications can be irregular during long operations; the local heartbeat only confirms the monitor is still waiting, not that the remote investigation is making progress. These updates make no additional MCP requests, do not change the final JSON response contract and redact credentials from progress messages using the same safeguards as error diagnostics.

## Investigation scope and response contract

1. Run the shared Python collectors and write the base HTML dashboard first.
2. Select every failing blocking run, deduplicated by latest Prow URL, plus exactly the dashboard's selected Sippy job/release findings (up to five). Sippy findings are investigated even when no blocking failures exist.
3. Send the blocking list in one request and the selected Sippy list in another. Concise prompts ask why each job and its tests failed, distinguish the root cause from downstream failures, and request next steps, evidence links and explicit uncertainty. Chai chooses its investigation procedure using the persona's configured context, tools and credentials, not your local files or collector credentials.
4. Run the two category calls together. Chai is instructed to reuse investigations of identical Prow runs within each batch. Calls can inspect overlapping runs independently; a Sippy request receives matching blocking results that have already returned. Informing runs are investigated when included in the selected Sippy findings; other informing runs keep a manual Chai investigation prompt.
5. Validate every entry of each returned map and merge successful results directly into the report in memory. The HTML and optional full JSON export are refreshed before another request is awaited; no analysis or target sidecars are written. An in-progress warning identifies an unfinished run. A missing, invalid or explicit `Analysis error` entry does not discard valid entries in the same response.
6. If the response has budget-deferred targets and no errors, send one final request for those targets, carrying the completed findings and deferred notes. Preserve all previously validated results if the final request fails, times out or is interrupted. An error in one category does not prevent an eligible final request in the other.

The configured persona needs appropriate Prow/artifact, Sippy and Jira read capabilities. A valid token and reachable endpoint do not guarantee that the persona can obtain all the evidence. The requests instruct it to investigate without changing external systems.

The blocking prompt starts with: "Analyze these Prow job failures. Explain why each job and its tests failed, distinguish the root cause from downstream failures, and recommend next steps. Include evidence links and flag anything uncertain." The Sippy prompt asks the equivalent question for job-health findings with pass-rate history. Both append the complete target list, any available completed evidence, one JSON response template and compact rules for exact keys, failure types and attempt coverage. The schema is shown only once per batch, rather than repeated for every target.

The blocking call returns `{"by_prow_url": {"<exact latest Prow URL>": {"...": "analysis fields"}}, "deferred": {}}`. The Sippy call uses `by_sippy_job`, keyed by exact `analysis_key`. Each requested target must appear exactly once, either in the category map as a completed or failed investigation, or in the `deferred` map. The adapter also accepts responses that omit the `deferred` map when there are no deferrals.

For a target left unfinished solely because the investigation time budget ran out, use `"deferred": {"<exact target key>": {"reason": "budget_exhausted", "notes": "Partial evidence and the next investigation step"}}`. Notes must be nonempty and at most 4000 characters. Missing or inaccessible evidence and processing failures belong in an `Analysis error` entry instead. Unrecognized deferral reasons, duplicate placement across maps and unexpected target keys are validation errors that prevent a follow-up. Only completed analyses are merged into the dashboard; unresolved deferrals appear in dashboard warnings with their notes.

Each map value must contain `job_name`, `root_cause`, `failure_type`, `impact`, `suspect_prs`, `recommendation`, `same_root_cause` and optional `attempt_analyses`. The prompt also requests a structured `jira` decision as described below. Failure types are `Infrastructure flake`, `Test regression`, `Install failure`, `Platform issue` or `Analysis error`. When attempts have different root causes, every supplied previous attempt and then the latest attempt must be represented by `prow_url`, `root_cause` and `failure_type` in order.

The adapter checks the batch wrapper, duplicate keys, required target coverage, field types, job identity and attempt coverage. It accepts plain JSON or a single JSON code fence. No follow-up format-correction call is made: a malformed batch gets a warning, while an otherwise valid map retains its valid entries and warns about missing or invalid entries. Unexpected target keys are discarded with a warning. Explicit `Analysis error` entries preserve the missing-evidence explanation and recommended next action and are not marked AI analyzed. Targets deferred again on the final attempt receive a budget-exhaustion warning with the remaining investigation notes.

Validated analysis is held in memory in the following structure and merged directly into the report:

```json
{
  "by_prow_url": {
    "<exact latest Prow URL>": {
      "job_name": "<job name>",
      "root_cause": "<evidence-based diagnosis>",
      "failure_type": "Test regression",
      "impact": "<impact>",
      "suspect_prs": [],
      "recommendation": "<next action>",
      "same_root_cause": true,
      "attempt_analyses": []
    }
  },
  "by_sippy_job": {}
}
```

Chai copies the exact output keys supplied in the batch request; the adapter accepts only requested Prow URLs or Sippy `analysis_key` values. Summary/status counts refer to unique targets, not MCP calls; a target completed on the final attempt is counted once. Schema validation establishes format and target coverage, not the factual correctness of a diagnosis. Review the cited evidence before taking action.

## Jira bug recommendations

New-bug drafts are generated only from Chai's explicit structured recommendation during a blocking or selected Sippy investigation. Blocking, recurring, persistent and unstable failure thresholds still inform the dashboard, but never independently produce a bug suggestion. Missing Chai configuration, failed investigations, older answers without a `jira` decision and deferred findings produce no new-bug drafts.

Chai is instructed to use Jira read-only and never create, edit, comment on or transition an issue. Before recommending a bug, it must search for existing issues covering the root cause, including supplied known bugs and relevant resolved issues. It records the queries and results in `search_summary`. If a covering bug exists, or the search cannot be completed, `new_bug` must be `null`. An empty supplied bug list is not evidence of a completed duplicate search.

Recommendations are limited to failures related to **SNO (Single Node OpenShift)**, **TNF (two node fencing)** or **TNA (two node arbiter)**. The validator checks the collected target's topology; a model cannot make an unrelated target eligible by naming an allowed topology in its answer. Chai must explain why the evidence warrants a new bug; a low pass rate alone is insufficient.

A completed analysis can include this decision:

```json
{
  "jira": {
    "search_status": "checked",
    "search_summary": "Queries and results explaining why no existing bug covers this failure",
    "existing_bugs": [],
    "new_bug": {
      "title": "[SNO] API readiness fails after node reboot",
      "description": "Complete diagnosis, topology-specific impact, reproduction or failing tests, evidence URLs and duplicate-search findings",
      "target_versions": [{"name": "4.19.0", "id": "12345"}]
    }
  }
}
```

`search_status` is `checked`, `unavailable` or `not_checked`. `existing_bugs` contains matching Jira issue keys. The version ID above is illustrative: Chai must read actual OCPBUGS project version metadata, use full version names (`X.Y.0` or `X.Y.z`) belonging to the target's releases and never invent IDs. If metadata cannot be verified, it returns `new_bug: null` and explains why. Malformed or ineligible recommendations are suppressed with a warning while retaining the valid failure diagnosis.

Recommended drafts appear at the **bottom of the dashboard, below the tab panels**, as compact cards showing title, affected topology and versions. Clicking a card opens a small dialog with the full description, target versions and the `issue-for-agent` label prefilled. The dialog offers only **Review draft in Jira**, **Gather details with Chai** and **Copy full description**. Gather details reveals a prompt to paste into Chai; copying copies the description alone. Press Esc or click outside to close the dialog. The monitor always adds this label, independently of Chai's output. The Jira link carries the title, full description, `labels=issue-for-agent` and repeated `customfield_10855` Target Version IDs, rather than substituting Fix Versions. It only opens Jira's creation form; filing remains a manual action. Jira Cloud may ignore legacy creation-link defaults ([Atlassian's documented limitations](https://confluence.atlassian.com/jirakb/how-to-create-issues-using-direct-html-links-in-jira-159474.html)); the dashboard always retains the complete draft in its dialog and a Copy full description button.

Equivalent recommendations with the same title and topology are combined across batches and releases, retaining their descriptions and target versions. If a later batch finds an existing bug for that job, the earlier draft is removed. Full JSON exports include the resulting drafts under `suggested_bugs`. The local `--merge-analysis` operation patches analysis cards and removes prior bug suggestions, since an HTML-only import cannot revalidate their Jira decisions against fresh targets. It does not generate new bug recommendations from an external file. The original `payload-monitor` Jira suggestion behavior is unchanged.

## Dashboard without AI

Missing or empty variables, an invalid URL, missing MCP dependencies, connection/TLS errors, an invalid or unauthorized token, MCP tool errors, timeouts and invalid answers produce warnings in both the terminal and dashboard. Missing configuration names the absent variables. A failed target is left without an AI card; successful targets can still enrich the report.

The final `Chai analysis: unavailable/partial` summary includes the specific reasons, grouped with occurrence counts. Import failures include the underlying import error and the install command for the Python interpreter actually running the tool. HTTP failures identify the status code, MCP refusals preserve the server's explanation, and `Analysis error` answers preserve Chai's reported missing evidence and recommended next action. Credentials, bearer tokens and URL query/fragment values are removed from diagnostics. The same details appear in the dashboard warning. Status, counts and grouped reasons remain in memory during execution; no separate status JSON is written.

The process succeeds when the underlying dashboard was produced, even if AI is unavailable. Inspect the warning banner and terminal summary to distinguish complete, partial and unavailable analysis. Collector and local file errors retain the existing behavior; unexpected programming errors are not silently swallowed.

If both variables are configured and there are no blocking or selected Sippy targets, no MCP call is made and the AI status is `not_needed`. Missing variables are still reported even when no investigations are required.

## Output and release compatibility

For an actual output filename of `reports/edge-ci.html`:

| File | Contents |
|---|---|
| `edge-ci.html` | Same interactive dashboard, with Chai labels and any AI warning banner |
| `edge-ci.sippy-summary.json` | Existing fresh-collection per-release counts consumed by the Slack step |
| `edge-ci.json` | Existing full data export when `--json` is used; includes successful AI results |

The Chai command does not generate `.chai-targets.json`, `.analysis.json`, `.ai-targets.json` or `.chai-status.json`: targets, validated analysis and execution status are kept in memory. Completed findings are saved in the HTML dashboard and optional full JSON export as requests finish. The original `payload-monitor` and Claude plugin retain their existing `.ai-targets.json` export. Previously generated files are not automatically deleted.

Existing filenames receive the same collision-avoidance timestamp as `payload-monitor`; all sidecars use the actual HTML stem. Fresh runs retain the `Report:` log line and `BLOCKING_JOBS_START` / `INFORMING_JOBS_START` stdout markers. Reports remain self-contained HTML; no token is embedded in them and viewing a report never calls MCP.

The release repository continues to invoke its existing Claude workflow until explicitly changed. This tool does not change its image, credential mounts, JUnit reporting or Slack post steps. For a future switch, invoke this executable directly, capture collector stdout instead of Claude stream JSON for `failing-jobs.txt`, copy the HTML under the existing `edge-ci-monitor-summary.html` artifact name, and preserve `sippy-summary.json`. The current Claude session archive/continuation steps would also need replacing. The MCP URL and token must be supplied to the job environment separately.

## Tests

From this directory, using the activated virtual environment:

```bash
python -m pip install pytest ruff
python -m pytest tests ../payload-monitor/tests
python -m ruff check edge_ocp_ci_monitor tests
```

Tests use synthetic credentials, mocked collectors and an in-memory MCP server. They do not call deployed Chai, create Jira issues, post to Slack or require your token.
