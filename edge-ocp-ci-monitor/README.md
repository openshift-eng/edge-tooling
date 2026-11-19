# Edge OCP CI Monitor

Generate an HTML dashboard for OpenShift nightly payloads, Sippy job health, and Component Readiness across edge topologies. Chai Bot can investigate failures over MCP and add evidence, recommended next steps, and Jira bug drafts to the report. You can also run the monitor with just the collected CI data.

## Quick start

### 1. Install

Use Python 3.11 or newer. Local Prow artifact collection requires `gsutil` from the Google Cloud SDK. Use `--skip-prow` to skip that enrichment. See the [Payload Monitor data sources](../payload-monitor/README.md#data-sources).

From the repository root, create and activate a virtual environment and install the dependencies.

```bash
cd edge-ocp-ci-monitor
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Run the remaining commands from this directory in the same shell.

### 2. Generate a dashboard

For local Chai investigations, export `CHAI_MCP_URL` and `CHAI_MCP_TOKEN`. Use the full HTTPS MCP endpoint for a persona your existing token can access.
Replace the example URL and load your existing token into `CHAI_MCP_TOKEN` before exporting it.

```bash
export CHAI_MCP_URL='https://<host>/personas/openshift_edge_internal/mcp'
export CHAI_MCP_TOKEN='<token>'

python -m edge_ocp_ci_monitor --payloads 3 --with-timing --open
```

## Using the dashboard

Start with **Edge OCP Jobs Health Overview** and **Findings Summary**, then use the tabs to drill down.

| View | What to look for |
| --- | --- |
| **Overview** | Up to five blocking jobs by failed payload count and up to five Sippy findings by pass rate. Each list shows the most recent failures first when dates are available. Select a job to see its details and any Chai analysis. |
| **Failure Analysis** | All sampled payload failures and jobs that passed on retry. Filter failures by topology, job type, release, or status and inspect Prow links and previous attempts. |
| **Sippy Job Health Status** | The full Sippy job-health findings with pass rates and run counts. Use **View AI analysis** where available or copy the **Ask chai-bot** prompt for a manual investigation. |
| **Component Readiness** | Test regressions in saved Sippy comparisons of edge topologies against the HA baseline. |
| **Timing Insights** | Install and upgrade durations when collected with `--with-timing`. |

Component Readiness queries the saved views listed in the repository. With no `--versions` filter it queries all 16 configured comparisons. Releases 4.18 through 4.21 have SNO comparisons, and 4.22, 4.23, 5.0 and 5.1 have SNO, TNA and TNF comparisons. For example, `--versions 5.1` queries the three 5.1 views. See the [Component Readiness lookup](edge_ocp_ci_monitor/component_readiness.py).

### Review a Jira draft

Look for **Chai-recommended New Bugs** below the tabs. Select a draft to review its description, topology, Target Versions, and `issue-for-agent` label. **Review draft in Jira** opens the creation form for you to review and submit manually. Check the prefilled fields in Jira. The dashboard keeps the full description available through **Copy full description**, and **Gather details with Chai** gives you a prompt to continue investigating.

Drafts require an explicit Chai recommendation for an SNO (Single Node OpenShift), TNA (two-node arbiter), or TNF (two-node fencing) failure. The prompt requires a duplicate search, an evidence-based reason to file, and verified OCPBUGS Target Version names and IDs for the affected release. A matching issue or incomplete search suppresses the draft. Recurrence and pass-rate thresholds inform triage. Chai is asked to use Jira read-only.

## Command-line options

For example, collect three payloads per release for 4.19 and 5.1, include timing, and write both HTML and full JSON.

```bash
python -m edge_ocp_ci_monitor --versions 4.19,5.1 --payloads 3 \
  --with-timing --json --output reports/edge-ci.html
```

| Option | Effect |
| --- | --- |
| `--versions X.Y,X.Y` | Select releases for payloads, Sippy job health, Component Readiness, and optional timing. The default releases are 4.18 through 4.23, 5.0, and 5.1. |
| `--payloads N` | Fetch 1–10 recent payloads per stream. The default is 5. |
| `--skip-prow` | Skip local Prow artifact enrichment. Chai can still investigate selected targets using the persona's tools. |
| `--skip-sippy` | Skip Sippy job health and Component Readiness. Blocking payload failures still qualify for Chai analysis. |
| `--with-timing` | Include install and upgrade timing insights. Timing collection is off by default. |
| `--output PATH` | Set the HTML path. The default is `reports/report-YYYY-MM-DD.html`. |
| `--open` | Open the dashboard in a browser when the command finishes. |
| `--json` | Also export the full report. |
| `--merge-analysis PATH` | Patch a compatible analysis JSON file into existing HTML. See [Import analysis into an existing report](#import-analysis-into-an-existing-report). |
| `--verbose` / `--help` | Enable debug logging or show CLI usage. |

### Output files

Paths are relative to the current directory. Fresh runs append a timestamp to the requested HTML name if it already exists. Companion files use the actual HTML filename stem.

| File for `--output reports/edge-ci.html` | Contents |
| --- | --- |
| `reports/edge-ci.html` | Self-contained dashboard with collected data, available investigations, and any warnings. |
| `reports/edge-ci.sippy-summary.json` | Per-release counts of Sippy jobs with a relative pass-rate drop greater than 50% and at least three current runs. A `null` count means job-health data was unavailable or skipped. |
| `reports/edge-ci.json` | Full report when `--json` is set, including available analyses and `suggested_bugs`. |

The monitor collects fresh CI data for each new report. It writes the base HTML and optional full JSON first, then rewrites them with validated findings as each batch finishes. Targets, intermediate analysis, and execution status are held in memory. Viewing the saved HTML requires only a browser.

### Import analysis into an existing report

Use `--merge-analysis` to update matching cards in place, without collecting data or calling Chai. Give `--output` the path to the actual existing HTML, including any filename timestamp.

```bash
python -m edge_ocp_ci_monitor --merge-analysis reports/investigations.json \
  --output reports/edge-ci.html
```

The import accepts analysis maps named `by_prow_url` and `by_sippy_job`, keyed by the exact latest Prow URL and the exact Sippy `analysis_key` respectively. See the [analysis fields below](#mcp-response-contract). The import can replace existing cards and clears all Jira drafts and draft actions because it has no fresh targets against which to validate them. Only the HTML is updated. For regenerating HTML from a full data export, use the separate Payload Monitor command with `--from-json`. See the [Payload Monitor CLI reference](../payload-monitor/README.md#cli-reference).

## Chai scope, timing, and troubleshooting

- **Targets.** Chai gets every failing blocking run, deduplicated by latest Prow URL, and up to five Sippy job and release findings from Overview. Sippy findings qualify with at least three current runs and either a relative pass-rate drop greater than 50% or a current pass rate below 50%. The monitor selects them by lowest current pass rate, then largest relative drop. An informing run can be investigated through a selected Sippy finding. Use the manual prompt for other informing runs.
- **Requests.** Each nonempty category starts one `ask_persona` request. The two categories run concurrently. Each request has a 45-minute hard timeout, including connection setup. The prompt reserves the last five minutes for the response. An error-free batch with explicit time-budget deferrals can make one final request for those targets. That allows up to four requests per run and about 90 minutes for the AI stage, plus cleanup and report writes. Chai's server-side limits can end a request earlier.
- **Progress.** Look for `[blocking-jobs]` and `[Sippy-tests]` in the terminal. The monitor relays Chai's progress messages and logs a local heartbeat every 30 seconds while it waits. The heartbeat shows elapsed time and time since the last Chai update. Use batch completion logs and dashboard warnings to check what finished.

The command can succeed with a dashboard even when Chai is unavailable. Check the dashboard warning and the final `Chai analysis:` log line. Missing configuration, connectivity failures, timeouts, and invalid responses are reported with their specific reasons. Valid findings remain available when other targets fail.

### Credentials

Keep the token out of command arguments, Git, and saved reports. The client sends it in the Authorization header to the configured HTTPS endpoint and disables redirects. The monitor redacts token values from prompts, diagnostics, analysis imports, and report exports. See the [MCP adapter](edge_ocp_ci_monitor/analysis.py) and [redaction code](edge_ocp_ci_monitor/security.py).

## Maintainer reference

The monitor imports collectors and report models from the sibling `payload-monitor/` directory. Include both directories when packaging the tool outside the repository checkout.

### MCP response contract

The monitor pins FastMCP to MCP `2026-07-28` and calls `ask_persona` over stateless Streamable HTTP. Chai streams progress notifications and the final result on the tool response. If the endpoint refuses this protocol version, the monitor reports the failure and keeps the collected dashboard. See the [MCP adapter](edge_ocp_ci_monitor/analysis.py).

Each category response is JSON, accepted as plain text or in a single JSON code fence. The blocking batch returns `by_prow_url` and the Sippy batch returns `by_sippy_job`. Every requested key belongs in the analysis map or the optional `deferred` map exactly once.

- Analysis entries require `job_name`, `root_cause`, `failure_type`, `impact`, `suspect_prs`, `recommendation`, and `same_root_cause`. Optional fields are `attempt_analyses` and `jira`. Failure types are `Infrastructure flake`, `Test regression`, `Install failure`, `Platform issue`, and `Analysis error`.
- If blocking attempts have different causes, include each previous attempt followed by the latest. Each attempt entry has `prow_url`, `root_cause`, and `failure_type`. Use HTTPS links for attempts and suspect PRs.
- A deferral uses `{"reason": "budget_exhausted", "notes": "..."}`, with nonempty notes up to 4,000 characters. The final request starts a fresh session with the deferred targets, their notes, and completed findings as context. Transport errors, hard timeouts, and batches with validation errors or `Analysis error` entries end that category's investigation. Other valid entries are retained.
- The Jira decision records `search_status` (`checked`, `unavailable`, or `not_checked`), `search_summary`, `existing_bugs`, and `new_bug`. A draft supplies `title`, `description`, and `target_versions` with full names and numeric IDs encoded as strings. Invalid drafts produce terminal diagnostics while the diagnosis can still be used. Equivalent drafts are combined by title and topology, and an existing bug found for the job can retract an earlier draft. See the [Jira validator](edge_ocp_ci_monitor/jira.py).

### Release integration

Fresh runs emit the `Report:` path in the terminal and pipe-delimited `BLOCKING_JOBS_START` and `INFORMING_JOBS_START` sections on stdout when those categories have failures. For a migration from the release repository's Claude workflow, invoke this module directly, capture its stdout as `failing-jobs.txt`, publish the actual HTML path as `edge-ci-monitor-summary.html`, and preserve the Sippy summary as `sippy-summary.json` for Slack. Supply the MCP credentials in the job environment, budget for collection and Chai requests, and replace the Claude session archive and continuation steps as part of that migration.

### Tests

Run from `edge-ocp-ci-monitor/` with the virtual environment activated. The suites use synthetic credentials and mocked services.

```bash
python -m pip install pytest ruff
python -m pytest tests ../payload-monitor/tests
python -m ruff check edge_ocp_ci_monitor tests
```
