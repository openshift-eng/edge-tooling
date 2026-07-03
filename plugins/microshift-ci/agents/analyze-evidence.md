# Analyze Evidence Agent

Analyze a group of MicroShift Prow CI jobs that share the same deterministic failure fingerprint — they failed the same way, so ONE analysis covers all of them. Your goal is the UNDERLYING root cause, not the first error in the log. Follow the drill-down and causal-chain requirements below, consulting the sosreport and performance graphs when relevant.

## Inputs

Jobs in this group (same failure fingerprint):

{GROUP_JOBS}

Output file for the analysis report: `{OUTPUT_FILE}`

## Instructions

### 1. Read the evidence and references

Read `plugins/microshift-ci/agents/references/microshift-ci-primer.md` and every evidence pack listed above. The packs describe the same failure in different jobs (often different releases) — note what varies between them (release, OS, scenario set); it constrains the root cause.

If an evidence pack is missing, work from that job's raw artifacts directory instead and record the gap in `analysis_gaps`.

### 2. Assess the failure

- `scenario-e2e` → examine each scenario's alerts, failures, and journal. Use `failure_timeline` to distinguish cascade from independent failures.
- `conformance` → examine `conformance_failures`.
- `build`/`config`/`rebase` → examine `build_errors`.
- `infrastructure_indicators.is_infra_failure` true alongside test evidence → weigh whether infrastructure caused the test failures (shared-hypervisor contention, CI capacity) before blaming product or tests.
- No failure evidence anywhere → severity 1, `infrastructure_failure: false`, note it in `analysis_gaps`. Do NOT drill down.

### 3. Drill down

Drill down in the group member with the most complete evidence (journal + sosreport + graphs). Iterate hypothesis → evidence until the cause is actionable. When the group has more than one job, spot-check your conclusion against a second member's evidence pack — if it does not hold there, say so in `analysis_gaps`.

**Mandatory raw-log verification** — BEFORE concluding, even when the evidence pack looks sufficient:

- Read ~200 lines of raw journal around the failure timestamp — look for patterns NOT in the evidence pack (authorization denials, scheduler errors, admission failures, kubelet sandbox errors).
- When a sosreport exists, check **kube-apiserver** pod logs for authorization/admission/scheduling decisions.
- "Timed out waiting for X" is a symptom — read raw logs to find WHY X was slow or absent.

**Deeper investigation** via raw artifacts:

- **Sosreport pod logs**: read from `extracted_sosreport_dirs` when available, or run `bash plugins/microshift-ci/scripts/extract-sosreport.sh <tarball>` on paths in `sosreport_paths`.
- **PCP graphs**: read PNGs listed in `pcp_graphs` when the failure involves timeouts, slowness, or resource exhaustion.
- **Source code**: use `source_checkout.path` to read `test/suites/` or product code. Check `recent_commits` for related changes.

**Critical rules**:

- A test-layer fix is never the bottom when a product component misbehaved — reconstruct the component's story from journal and pod logs before concluding.
- Two `Created container` events for the same pod = the first instance died. Read `previous.log` for the exit reason.
- Multiple scenario failures: decide cascade vs independent using the **timeline**, not error-text similarity.
- **Every causal-chain link MUST cite an artifact file path** (e.g., `artifacts/.../boot_and_run.log:4629`). Do NOT cite the evidence JSON, general knowledge, or architectural statements. The evidence pack includes `file` and `line` for each match — trace back to those. Drop unsupported links or record as analysis gaps.

### 4. Validate causal chain

Before producing the report, validate every causal-chain link:

- Every link MUST have an `evidence` field containing an artifact file path with `:line` (e.g., `artifacts/.../boot_and_run.log:4629`).
- Every link MUST have a `quote` field with verbatim text from that file.
- If any link cites the evidence JSON, general knowledge, or architectural statements instead of an artifact file — fix it now by finding the actual artifact file, or drop the link.

### 5. Produce the report

Write the report per `plugins/microshift-ci/agents/references/structured-summary.md`. Include both the human-readable analysis and the `--- STRUCTURED SUMMARY ---` JSON block.

Group handling in the structured summary:

- Emit one entry per INDEPENDENT failure (not per job — the group shares the failures).
- Fill `job_name`, `job_url`, `release`, `finished` from the FIRST job listed above; tooling fans the report out to every group member and patches these fields per job.
- Do NOT emit a `fingerprint` field; tooling injects it.

### 6. Save and reply

Save the FULL report output (including the `--- STRUCTURED SUMMARY ---` block) to `{OUTPUT_FILE}` using the Write tool. The file must contain the complete analysis report.

After saving, reply with EXACTLY one line: `DONE {OUTPUT_FILE}`. Do NOT include the report text in your reply.
