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
bash plugins/microshift-release/scripts/prow_testing.sh <version>
```

## Prerequisites

| Requirement | Needed for | Mandatory? |
|---|---|---|
| `gh` CLI (authenticated) | All PR operations | Yes |
| Push access to `openshift/microshift` | Step 1 (create PR) | Yes |
| `gsutil` CLI | Step 6 (download) | Yes |
| `aws` CLI (configured) | Step 7 (upload) | Yes |

## Arguments

- `<version>` (required): MicroShift version (`X.Y.Z`, `X.Y.Z-rc.N`, or `X.Y.Z-ec.N`). Must be 4.21+.

## Scripts Directory

```bash
SCRIPTS_DIR=plugins/microshift-release/scripts
```

## Implementation

Execute each step in order. Redirect stderr to `/dev/null` for all commands — stderr only contains progress messages. On non-zero exit, re-run **without** suppressing stderr and display the error.

### Step 1: Create PR (Draft)

The PR is **always** created in draft state. Run `bash ${SCRIPTS_DIR}/prow_testing.sh create-pr <version>` **without** `--execute` first. Parse the JSON output:

- If `"status": "exists"` — display the message and continue to Step 2.
- If `"status": "plan"` — display the plan and ask for confirmation.
  - If confirmed, re-run with `--execute` and display the result.
  - If declined, stop the workflow.

### Step 2: Trigger Jobs

Run `bash ${SCRIPTS_DIR}/prow_testing.sh trigger <version>` **without** `--execute` first. Parse the JSON output:

- If `"status": "skip"` — no jobs to trigger, display the message and continue to Step 3.
- If `"status": "plan"` — display the plan and ask for confirmation.
  - If confirmed, re-run with `--execute` and display the result, then continue to Step 3.
  - If declined, continue to Step 3.

### Step 3: Check Status

Run `bash ${SCRIPTS_DIR}/prow_testing.sh status <version>`.

Display the output **verbatim** — it is a pre-formatted table. Do not reformat it.

If all jobs passed, continue to Step 4. Otherwise, stop the workflow.

### Step 4: Merge PR

Run `bash ${SCRIPTS_DIR}/prow_testing.sh merge <version>` **without** `--execute` first. Parse the JSON output:

- If `"status": "already-merged"` — display the message and continue to Step 5.
- If `"status": "plan"` — display the plan and ask for confirmation.
  - If confirmed, re-run with `--execute` and display the result, then continue to Step 5.
  - If declined, stop the workflow.

**Note:** The merge step does NOT post `/lgtm`. A human must review and post `/lgtm` manually to approve the merge.

### Step 5: Check Merge Status

Run `bash ${SCRIPTS_DIR}/prow_testing.sh merge-status <version>`. Parse the JSON output:

- If `"status": "merged"` — display the message and continue to Step 6.
- If `"status": "open"` — display the message and stop the workflow (waiting for human `/lgtm` and Tide merge).

### Step 6: Download Artifacts

Run `bash ${SCRIPTS_DIR}/prow_testing.sh download <version>` **without** `--execute` first. Parse the JSON output:

- If `"status": "plan"` — display the plan (jobs to download, destination directory) and ask for confirmation.
  - If confirmed, re-run with `--execute` and display the result.
  - If declined, stop the workflow.

### Step 7: Upload Artifacts

Run `bash ${SCRIPTS_DIR}/prow_testing.sh upload <version>` **without** `--execute` first. Parse the JSON output:

- If `"status": "plan"` — display the plan (tar.gz name, S3 destination) and ask for confirmation.
  - If confirmed, re-run with `--execute` and display the result (public URL).
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
