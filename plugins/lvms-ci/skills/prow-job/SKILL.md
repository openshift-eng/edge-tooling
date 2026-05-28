---
name: lvms-ci:prow-job
argument-hint: <prow-job-url-or-artifacts-dir>
description: Download Prow job artifacts, identify root cause of failure, and produce a structured error report
user-invocable: true
allowed-tools: Skill, Bash, Read, Write, Glob, Grep, Agent
---

# lvms-ci:prow-job

## Synopsis

```bash
/lvms-ci:prow-job <prow-job-url>
/lvms-ci:prow-job <artifacts-dir>
```

## Description

Analyzes a single Prow CI test job by scanning artifacts for errors and producing a structured failure report. Accepts either a Prow job URL (downloads artifacts) or a local directory path (uses pre-downloaded artifacts).

## Arguments

- `<ARGUMENTS>` (required): Either a job URL or a local artifacts directory path:
  - **Prow URL**: `https://prow.ci.openshift.org/view/gs/test-platform-results/logs/periodic-ci-openshift-lvm-operator-main-e2e-aws-sno-qe-integration-tests/1984108354347208704`
  - **GCS web URL**: `https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/logs/periodic-ci-openshift-lvm-operator-main-e2e-aws-sno-qe-integration-tests/1984108354347208704`
  - **Local artifacts directory**: `/tmp/lvm-operator-ci-claude-workdir.260404/artifacts/1984108354347208704` (must contain `build-log.txt` and `finished.json`)

## Goal

Reduce noise for developers by processing large logs from a CI test pipeline and correctly classifying fatal errors with a false-positive rate of 0.01% and false-negative rate of 0.5%.

## Audience

Software Engineer

## Glossary

- **ci-config**: Top level configuration file specifying build inputs, versions, and test workflows to execute. Periodic tests are suffixed with `__periodic.yaml`.
- **test**: The set of configurations and commands that specify how to execute the test. Can be defined in-line in ci-config, or as individual "steps" (see below).
- **step-registry**: Root directory where all openshift-ci test step configs and commands are stored.
- **step**: Smallest component of the test infrastructure. A step yaml specifies the command or script to execute, environmental variables and default values, and step metadata. Also called "ref" or "step ref".
- **chain**: A yaml configuration specifying 1 or more steps or chains in an array. Steps and chains are exploded and executed serially by index. May override step environment variable values.
- **workflow**: A yaml configuration specifying 1 or more steps, chains, or workflows in an array. Steps, chains, and workflows are exploded and executed serially. May override chain or step environmental variable values. Typically referenced by a test in a ci-config.
- **LVMS**: Logical Volume Manager Storage — an operator that manages local storage on OpenShift clusters using LVM thin provisioning via TopoLVM.
- **CatalogSource**: An OLM resource that defines an index of operator bundles. LVMS CI jobs create a CatalogSource to install the operator under test.

## Job Name and Job ID

The Job Name and Job ID are encoded in the URL. There are two URL formats depending on the job type:

**Periodic/postsubmit jobs:**

```text
https://prow.ci.openshift.org/view/gs/test-platform-results/logs/{JOB_NAME}/{JOB_ID}
https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/logs/{JOB_NAME}/{JOB_ID}
```

GCS path: `gs://test-platform-results/logs/{JOB_NAME}/{JOB_ID}/`

**Presubmit (PR) jobs:**

```text
https://prow.ci.openshift.org/view/gs/test-platform-results/pr-logs/pull/openshift_lvm-operator/{PR_NUMBER}/{JOB_NAME}/{JOB_ID}
https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/test-platform-results/pr-logs/pull/openshift_lvm-operator/{PR_NUMBER}/{JOB_NAME}/{JOB_ID}
```

GCS path: `gs://test-platform-results/pr-logs/pull/openshift_lvm-operator/{PR_NUMBER}/{JOB_NAME}/{JOB_ID}/`

To determine the GCS path from any job URL, strip the web prefix and replace with `gs://`:

- Prow URL: strip `https://prow.ci.openshift.org/view/gs/`
- GCS web URL: strip `https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/`

## Important Files

> These files are available after artifacts are downloaded (via the download script or workflow step 0).

- `<TMP>/build-log.txt`: Log containing prow job output and most likely place to identify AWS infra related errors.
- `<STEP>/build-log.txt`: Each step in the CI job is individually logged in a build-log.txt file.
- `<TMP>/artifacts/<TEST_NAME>/lvms-catalogsource/build-log.txt`: CatalogSource creation step log.
- `<TMP>/artifacts/<TEST_NAME>/operatorhub-subscribe-lvm-operator/build-log.txt`: LVMS operator subscription step log.
- `<TMP>/artifacts/<TEST_NAME>/storage-create-lvm-cluster/build-log.txt`: LVMCluster creation step log.
- `<TMP>/artifacts/<TEST_NAME>/lvms-sno-integration-test/build-log.txt`: Integration test execution step log (SNO variant; MNO variant uses `lvms-mno-integration-test`).

## Important Links

**Step Diagram URL** (found at the end of the main build-log):

```text
https://steps.ci.openshift.org/job?org=openshift&repo=lvm-operator&branch=main&test=e2e-aws-sno-qe-integration-tests
```

This link provides a diagram of the steps that make up the test. Think about reading this diagram when identifying step failures because not all fatal errors cause the current step to fail but may cause the next step to fail.

## Work Directory

Compute once at the start by running `date +%y%m%d` and substituting into the path below. In all commands, replace `<WORKDIR>` with the computed path — do not store the work directory in a shell variable.

```text
/tmp/lvm-operator-ci-claude-workdir.<YYMMDD>
```

## Common Commands

Scan the build log for arbitrary text:

```bash
grep '${SOME_TEXT}' ${GREP_OPTS} ${TMP}/build-log.txt
```

Download all prow job artifacts (only needed when given a URL, not a local path):

```bash
GCS_PATH=$(echo "${PROW_URL}" | sed -e 's|https://prow.ci.openshift.org/view/gs/|gs://|' -e 's|https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/|gs://|')
gsutil -q -m cp -r "${GCS_PATH}/" ${TMP}/
```

## Workflow

The user argument is: `<ARGUMENTS>`

0. **Determine input type and set up artifacts directory**:
   - If `<ARGUMENTS>` is a **local directory path** (starts with `/` and contains `build-log.txt`): set `TMP` to that directory. Skip step 1.
   - If `<ARGUMENTS>` is a **URL** (starts with `http`): create a temporary working directory with `mktemp -d <WORKDIR>/openshift-ci-analysis-XXXX`, set `TMP` to that directory, and proceed to step 1.

1. **Download all artifacts** (skip if using pre-downloaded artifacts from step 0):
   Download all prow job artifacts using `gsutil -q -m cp -r` into the temporary working directory. Derive the GCS path by stripping the web prefix from the job URL (handles both Prow and GCS web URL formats):

   ```bash
   GCS_PATH=$(echo "${PROW_URL}" | sed -e 's|https://prow.ci.openshift.org/view/gs/|gs://|' -e 's|https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/|gs://|')
   gsutil -q -m cp -r "${GCS_PATH}/" ${TMP}/
   ```

   This works for both periodic (`logs/...`) and presubmit PR (`pr-logs/pull/...`) job URLs, and for both Prow and GCS web URL formats.
   This makes all build logs, step logs, and SOS reports available locally for analysis.

2. **Scan for errors**: Start by scanning the top level `build-log.txt` file for errors and determine the step where the error occurred. Record each error with the filepath and line number for later reference.

3. **Read context**: Iterate over each recorded error, locate the log file and line number, then read 50 lines before and 50 lines after the error. Use this information to characterize the error. Think about whether this error is transient and think about where in the stack the error occurs. Does it occur in the cloud infra, the openshift or prow ci-config, the hypervisor, or is it a legitimate test failure? If it is a legitimate test failure, determine what stage of the test failed: setup, testing, teardown.

4. **Analyze the error**: Based on the context of the error, think hard about whether this error caused the test to fail, is a transient error, or is a red herring.

    4.1 If it is a legitimate test error, analyze the test logs to determine the source of the error.
    4.2 If the source of the error appears to be related to the LVMS operator or its components (TopoLVM, LVMCluster), check the operator and controller logs in the step artifacts.

5. **Produce a report**: Create a concise report of the error. The report MUST specify:
   - Where in the pipeline the error occurred
   - The specific step the error occurred in
   - Whether the test failure was legitimate (i.e., a test failed) or due to an infrastructure failure (i.e., build image was not found, AWS infra failed due to quota, hypervisor failed to create test host VM, etc.)

## Prerequisites

- `gsutil` CLI must be installed for GCS access (uses anonymous access on public buckets)
- Internet access to fetch job data from Prow/GCS
- Bash shell

## Tips

1. There are many setup and teardown stages so fatal errors may be buried by log output from the teardown phase. It is not common to find the fatal error at the end of the log.
2. You can quickly determine the failed step from the build-log.txt by reading the last `Running step ...` line before the container logs appear.
3. Check the CatalogSource and operator setup steps (`lvms-catalogsource`, `operatorhub-subscribe-lvm-operator`, `storage-create-lvm-cluster`) early — if any failed, the operator was never fully deployed and all downstream test failures are secondary.

## Output Template

Use this template for your error analysis reports:

### Severity Guide

| Severity | Meaning | Examples |
|----------|---------|----------|
| 1 | Cosmetic or informational, no action needed | Flaky teardown warning, non-fatal log noise |
| 2 | Transient infrastructure flake, retrigger likely fixes | AWS quota, image pull timeout, CI registry blip |
| 3 | Infrastructure or CI config issue, not LVMS code | CatalogSource image unavailable, base image build failure (`PullBuilderImageFailed`), cluster provisioning failure |
| 4 | Genuine test failure in LVMS code | Integration test assertion failure, regression in operator logic |
| 5 | LVMS operator or setup issue | LVMCluster not ready, operator subscription failure, storage class misconfiguration |

```text
Error Severity: {1-5, see Severity Guide above}
Stack Layer: {AWS Infra, External Infrastructure, build phase, deploy phase, test setup phase, Test Configuration, test, teardown}
Step Name: {The specific step where the error occurred}
Error: {The exact error, including additional log context if it relates to the failure}
Suggested Remediation: {Based on where the error occurs, think hard about how to correct the error ONLY if it requires fixing. Infrastructure failures may not require code changes.}
```

After the human-readable report above, append a machine-readable block for downstream automation. This block MUST appear at the very end of the report, after all prose and analysis:

```text
--- STRUCTURED SUMMARY ---
SEVERITY: {1-5, same as Error Severity above}
STACK_LAYER: {AWS Infra, External Infrastructure, build phase, deploy phase, test setup phase, Test Configuration, test, teardown - same as Stack Layer above}
STEP_NAME: {same as Step Name above}
ERROR_SIGNATURE: {a concise, unique one-line description of the root cause - not the full error, just enough to identify and deduplicate this failure}
ROOT_CAUSE: {one-line description of WHY the failure happened — the underlying mechanism, not the surface symptom. ~80 chars max. See rules below}
RAW_ERROR: {the primary error message copied VERBATIM from the log file - see rules below}
INFRASTRUCTURE_FAILURE: {true if Stack Layer is AWS Infra or the failure is due to CI infrastructure rather than product code, false otherwise}
JOB_URL: {the full prow job URL — when given a URL as input, use it directly; when given a local artifacts dir, reconstruct from the build-log.txt "Link to job on registry info site" line or from the directory path structure}
JOB_NAME: {the full job name — extract from the JOB_URL path, or from the build-log.txt "Running step" lines, or from the artifacts directory structure}
RELEASE: {the release branch — extract from JOB_NAME (e.g. 4.22 from release-4.22), or from finished.json metadata repos field, or default to "main"}
FINISHED: {the job finish date in YYYY-MM-DD format, extracted from finished.json timestamp field or build log timestamps}
--- END STRUCTURED SUMMARY ---
```

### RAW_ERROR rules

The `RAW_ERROR` field is used by downstream scripts for deterministic grouping. Two runs analyzing the same job MUST produce the same RAW_ERROR. Keep it simple — fewer rules mean less room for variation.

1. **Copy-paste the exact error text** from the log — do NOT paraphrase, summarize, or reword
2. **Pick only ONE error** — the primary error that caused the step to fail. If multiple errors exist, pick the first fatal one.
3. **Only strip timestamps** — remove leading timestamps like `2026-04-01T06:21:48Z`. Keep everything else verbatim, including prefixes like `An error occurred...` or `error:`.
4. **Never concatenate multiple errors** — pick ONE error, not a semicolon-separated list
5. **Truncate to ~150 characters** if the raw message is very long — keep the distinctive part

Examples of good RAW_ERROR values (copied verbatim from logs):

- `An error occurred (InvalidClientTokenId) when calling the CreateStack operation: The security token included in the request is invalid.`
- `panic: runtime error: index out of range [6] with length 6`
- `Process did not finish before 4h0m0s timeout`
- `error: the server doesn't have a resource type "clusterversion"`
- `package github.com/opencontainers/runc/libcontainer/cgroups: module github.com/opencontainers/runc@latest found, but does not contain package`

The ERROR_SIGNATURE field remains as a human-readable description for reports and Jira bug titles.

### ROOT_CAUSE rules

The `ROOT_CAUSE` field captures the underlying mechanism behind the failure — used by downstream scripts alongside `RAW_ERROR` for cross-release deduplication. Two jobs that fail with different surface errors but the same root cause should produce the same `ROOT_CAUSE`.

**How it differs from the other fields:**

- `ERROR_SIGNATURE` = WHAT failed (human-readable, used for bug titles)
- `ROOT_CAUSE` = WHY it failed (mechanism-focused, used for dedup)
- `RAW_ERROR` = verbatim log text (deterministic anchor)

**Rules:**

1. **One line, ~80 characters max** — short enough for token-based matching
2. **Focus on the mechanism**, not the symptom — ask "why did this happen?" not "what error appeared?"
3. **Be consistent across releases** — the same underlying problem in 4.20 and 4.22 MUST produce the same ROOT_CAUSE even if the error messages differ
4. **Use stable terms** — avoid version numbers, timestamps, job names, or other run-specific details

**Examples:**

| ERROR_SIGNATURE | ROOT_CAUSE |
|---|---|
| CatalogSource not ready — operator bundle image pull failure | index image unavailable or registry authentication failure |
| LVMCluster not ready within timeout | TopoLVM node agent failed to initialize volume group |
| e2e test PVC provisioning timeout on SNO | LVM thin pool exhausted or volume group misconfigured |
| InvalidClientTokenId when calling CreateStack | expired or invalid AWS credentials in CI environment |
