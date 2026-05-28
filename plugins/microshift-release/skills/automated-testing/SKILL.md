---
name: microshift-release:automated-testing
argument-hint: <version>
description: Run the full Prow CI release testing workflow — create PR, trigger jobs, check status, merge PR, download and upload artifacts
user-invocable: true
allowed-tools: Bash
---

# microshift-release:automated-testing

## Synopsis

```bash
/microshift-release:automated-testing <version>
```

## Description

Runs the full Prow CI release testing workflow for MicroShift (Phase 2 of the release process). Supports 4.21+ only — earlier versions use Jenkins pipelines.

The skill walks through all steps sequentially, skipping steps that are already complete. For running individual actions manually, use the bash script directly:

```bash
bash plugins/microshift-release/scripts/prow_testing.sh <action> <version> [--execute]
```

## Prerequisites

| Requirement | Needed for | Mandatory? |
|---|---|---|
| `aws` CLI (configured) | Step 0 (preflight), Step 5 (upload) | Yes |
| `gh` CLI (authenticated) | All PR operations | Yes |
| Push access to `openshift/microshift` | Step 1 (create PR) | Yes |
| `gsutil` CLI | Step 4 (download) | Yes |

## Arguments

- `<version>` (required): MicroShift version (`X.Y.Z`, `X.Y.Z-rc.N`, or `X.Y.Z-ec.N`). Must be 4.21+.

## Scripts Directory

```bash
SCRIPTS_DIR=plugins/microshift-release/scripts
```

## Implementation

Execute each step in order. Redirect stderr to `/dev/null` for all commands — stderr only contains progress messages. On non-zero exit, re-run **without** suppressing stderr and display the error.

### Step 0: Validate Build Artifact are in the Cache

Run `bash ${SCRIPTS_DIR}/prow_testing.sh preflight <version>`. Parse the JSON output:

- If `"status": "pass"` — display the message and continue to Step 1.
- If `"status": "warn"` — display each check's status and reason. Ask the user if they want to proceed.
  - If confirmed, continue to Step 1.
  - If declined, stop the workflow.
- If `"status": "fail"` — display each check's status, reason, and details. Stop the workflow.
  - For `s3_rpms` failure: tell the user to refresh the build cache by posting these comments on the PR (create the PR first if needed):

    ```text
    /test e2e-aws-tests-cache
    /test e2e-aws-tests-cache-arm
    ```

### Step 1: Create Release Testing Draft PR

The PR is **always** created in draft state. Run `bash ${SCRIPTS_DIR}/prow_testing.sh create-pr <version>` **without** `--execute` first. Parse the JSON output:

- If `"status": "exists"` — display the message and continue to Step 2.
- If `"status": "plan"` — display the plan and ask for confirmation.
  - If confirmed, re-run with `--execute` and display the result.
  - If declined, stop the workflow.

### Step 2: Trigger CI Jobs

Run `bash ${SCRIPTS_DIR}/prow_testing.sh trigger <version>` **without** `--execute` first. Parse the JSON output:

- If `"status": "skip"` — no jobs to trigger, display the message and continue to Step 3.
- If `"status": "plan"` — display the plan and ask for confirmation.
  - If confirmed, re-run with `--execute` and display the result, then continue to Step 3.
  - If declined, continue to Step 3.

### Step 3: Verify CI Jobs and Scenarios results

Run `bash ${SCRIPTS_DIR}/prow_testing.sh status <version>`.

Display the output **verbatim** — it is a pre-formatted table. Do not reformat it.

If all jobs finished, run scenario validation:

Run `bash ${SCRIPTS_DIR}/prow_testing.sh scenarios <version>`. Parse the JSON output:

- Display the `message` field (summary of jobs, scenarios, pass/fail/skip counts).
- For each job, display `release_under_test` and scenario counts.
- If any `skipped_scenarios` — display them as warnings.
- If any `failed_scenarios` — display them.
- If `"status": "fail"` — display the message and stop the workflow. Possible causes:
  - `release_under_test` does not match the target version for a job.
  - Scenarios failed.
  - Individual scenario versions don't match the target (excluding nightlies).
- If `"status": "pass"` — continue to Step 4.

If not all jobs passed, stop the workflow.

### Step 4: Download CI Jobs results

Run `bash ${SCRIPTS_DIR}/prow_testing.sh download <version>` **without** `--execute` first. Parse the JSON output:

- If `"status": "plan"` — display the plan (jobs to download, destination directory) and ask for confirmation.
  - If confirmed, re-run with `--execute` and display the result.
  - If declined, stop the workflow.

### Step 5: Upload CI Jobs results to S3

Run `bash ${SCRIPTS_DIR}/prow_testing.sh upload <version>` **without** `--execute` first. Parse the JSON output:

- If `"status": "plan"` — display the plan (tar.gz name, S3 destination) and ask for confirmation.
  - If confirmed, re-run with `--execute` and display the result (public URL).
  - If declined, stop the workflow.

### Step 6: Close PR and Clean Up

Run `bash ${SCRIPTS_DIR}/prow_testing.sh complete <version>` **without** `--execute` first. Parse the JSON output:

- If `"status": "plan"` — display the plan (post comment, close PR, delete branch) and ask for confirmation.
  - If confirmed, re-run with `--execute` and display the result.
  - If declined, stop the workflow.

## Errors

The script exits non-zero with a JSON `message` field. Common errors:

| Error | Cause |
|---|---|
| Version < 4.21 | Jenkins pipelines, not Prow CI |
| Nightly version | Phase 2 does not apply |
| `gh` failure | Not authenticated or no permissions |
| Branch exists | Delete it or use existing PR |
| `gsutil` not found | Install Google Cloud SDK |
| `aws` not found | Install and configure AWS CLI |

## Examples

```bash
/microshift-release:automated-testing 4.21.3            # run full workflow
/microshift-release:automated-testing 4.22.0-rc.1       # RC version
/microshift-release:automated-testing 4.22.0-ec.5       # EC version
```
