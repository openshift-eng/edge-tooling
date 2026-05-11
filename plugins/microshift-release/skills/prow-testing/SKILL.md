---
name: microshift-release:prow-testing
argument-hint: <version> [create-pr|trigger|status|download]
description: Manage Prow CI release testing — create PR, trigger jobs, check status, download artifacts
user-invocable: true
allowed-tools: Bash
---

# microshift-release:prow-testing

## Synopsis

```bash
/microshift-release:prow-testing <version> [action]
```

## Description

Manages the Prow CI release testing workflow for MicroShift (Phase 2 of the release process). Supports 4.21+ only — earlier versions use Jenkins pipelines (USHIFT-6805).

The workflow has 4 actions that map to the release testing lifecycle:

1. **create-pr** — Create a draft PR with an empty commit to trigger CI
2. **trigger** — Post `/test` comments on the PR to start the 6 CI jobs
3. **status** — Check and display the status of all CI jobs (default)
4. **download** — Download job artifacts from GCS and print the S3 upload command

## Prerequisites

| Requirement | Needed for | Mandatory? |
|---|---|---|
| `gh` CLI (authenticated) | All PR operations | Yes |
| Push access to `openshift/microshift` | `create-pr` action | Yes for create-pr |
| `gsutil` CLI | `download` action | Only for download |
| `aws` CLI (configured) | S3 upload (printed command) | Only if uploading |

## Arguments

- `<version>` (required): MicroShift version. Must be `X.Y.Z`, `X.Y.Z-rc.N`, or `X.Y.Z-ec.N`. Must be 4.21+.
- `[action]` (optional, default: `status`): One of `create-pr`, `trigger`, `status`, `download`.

## Constants

All commands use these values — define them as shell variables at the start of each Bash call:

```bash
GH_REPO="openshift/microshift"
GCS_API="https://storage.googleapis.com/storage/v1/b/test-platform-results/o"
GCS_BASE="https://storage.googleapis.com/test-platform-results"
GCS_PR_PREFIX="pr-logs/pull/openshift_microshift"
PROW_VIEW="https://prow.ci.openshift.org/view/gs/test-platform-results"
S3_BUCKET="s3://release-testing-results/microshift"
```

These match the constants in `plugins/microshift-ci/scripts/prow-jobs-for-pull-requests.sh`.

## CI Jobs

The 6 Prow CI release testing jobs:

```text
e2e-aws-tests-release
e2e-aws-tests-release-arm
e2e-aws-tests-bootc-release-el9
e2e-aws-tests-bootc-release-el10
e2e-aws-tests-bootc-release-arm-el9
e2e-aws-tests-bootc-release-arm-el10
```

In GCS, these appear with a full prefix like `pull-ci-openshift-microshift-release-4.21-e2e-aws-tests-release`. Match jobs by checking if the full GCS job name **ends with** one of the 6 short names above.

## Implementation

### Step 1: Parse & Validate

1. Extract `version` (required) and `action` (default: `status`) from the user's arguments.
2. Normalize version: replace `~` with `-` (e.g., `4.22.0~ec.5` → `4.22.0-ec.5`).
3. Validate the version format matches `X.Y.Z`, `X.Y.Z-rc.N`, or `X.Y.Z-ec.N`.
4. Extract the minor version number (the `Y` in `X.Y.Z`). If `Y < 21`, stop with:

   ```text
   Version 4.XX uses Jenkins pipelines (USHIFT-6805), not Prow CI. This skill only supports 4.21+.
   ```

5. Reject nightly versions (containing `nightly`): "Phase 2 does not apply to nightly versions."
6. Derive:
   - `MINOR` = `X.Y` (e.g., `4.21`)
   - `BRANCH` = `release-X.Y` (e.g., `release-4.21`)
   - `PR_TITLE` = `[release-X.Y] Release Testing VERSION` (e.g., `[release-4.21] Release Testing 4.21.3`)

### Step 2: Find Existing PR

This step runs before every action. Search for an open PR matching the expected title:

```bash
gh pr list --repo openshift/microshift --state open --limit 100 \
  --json number,title,url,headRefName \
  | jq -c '[.[] | select(.title == "'"${PR_TITLE}"'")]'
```

Store the result:

- If the array is empty → no PR exists
- If exactly one match → use that PR number and URL
- If multiple matches → report all and ask the user which one to use

### Step 3: Execute Action

Branch into the appropriate action based on the parsed `action` argument.

---

### Action: `create-pr`

**Mutating action — show the user what will happen and ask for confirmation before executing.**

1. **Check for existing PR** (from Step 2). If a PR already exists, report:

   ```text
   Release testing PR already exists:
     PR #NNNN: PR_TITLE
     URL: https://github.com/openshift/microshift/pull/NNNN

   Use 'trigger' to start CI jobs, or 'status' to check progress.
   ```

   Stop here — do not create a duplicate.

2. **Show the plan and ask confirmation.** Display what will be done:

   ```text
   Will create release testing PR for VERSION:
     Repository: openshift/microshift
     Base branch: release-MINOR
     Head branch: release-testing-VERSION
     Title: [release-MINOR] Release Testing VERSION
     Mode: Draft

   This will:
   1. Fetch the latest release-MINOR branch in _output/microshift
   2. Create branch 'release-testing-VERSION' with an empty commit
   3. Push to origin
   4. Create a draft PR

   Proceed?
   ```

3. **Execute** (only after user confirms):

   ```bash
   # Ensure local clone exists and is up to date
   REPO_DIR="_output/microshift"
   if [[ -d "${REPO_DIR}/.git" ]]; then
     git -C "${REPO_DIR}" fetch origin "release-MINOR"
   else
     mkdir -p _output
     git clone --filter=blob:none https://github.com/openshift/microshift.git "${REPO_DIR}"
   fi

   # Create the temporary branch with an empty commit
   git -C "${REPO_DIR}" checkout -b "release-testing-VERSION" "origin/release-MINOR"
   git -C "${REPO_DIR}" commit --allow-empty -m "Release testing for VERSION"
   git -C "${REPO_DIR}" push origin "release-testing-VERSION"
   ```

   ```bash
   # Create the draft PR
   gh pr create --repo openshift/microshift \
     --base "release-MINOR" \
     --head "release-testing-VERSION" \
     --title "[release-MINOR] Release Testing VERSION" \
     --body "Release testing PR for MicroShift VERSION. Do not merge.

   Created by /microshift-release:prow-testing" \
     --draft
   ```

4. Report the PR URL on success.

**Error handling:**

- If the branch `release-testing-VERSION` already exists on the remote, the push will fail. In this case, suggest the user delete it first or use the existing PR.
- If `gh pr create` fails, show the error. Common cause: insufficient permissions.

---

### Action: `trigger`

**Mutating action — show the user what will happen and ask for confirmation before executing.**

1. **Check for existing PR** (from Step 2). If no PR exists, stop with:

   ```text
   No release testing PR found for VERSION.
   Run '/microshift-release:prow-testing VERSION create-pr' to create one first.
   ```

2. **Show what will be posted and ask confirmation:**

   ```text
   Will post the following comment on PR #NNNN to trigger CI jobs:

   /test e2e-aws-tests-release
   /test e2e-aws-tests-release-arm
   /test e2e-aws-tests-bootc-release-el9
   /test e2e-aws-tests-bootc-release-el10
   /test e2e-aws-tests-bootc-release-arm-el9
   /test e2e-aws-tests-bootc-release-arm-el10

   Proceed?
   ```

3. **Execute** (only after user confirms):

   ```bash
   gh pr comment PR_NUMBER --repo openshift/microshift --body "/test e2e-aws-tests-release
   /test e2e-aws-tests-release-arm
   /test e2e-aws-tests-bootc-release-el9
   /test e2e-aws-tests-bootc-release-el10
   /test e2e-aws-tests-bootc-release-arm-el9
   /test e2e-aws-tests-bootc-release-arm-el10"
   ```

4. Report success:

   ```text
   CI jobs triggered on PR #NNNN.
   Run '/microshift-release:prow-testing VERSION status' to check progress.
   ```

---

### Action: `status` (default)

**Read-only action — no confirmation needed.**

1. **Check for existing PR** (from Step 2). If no PR exists, stop with:

   ```text
   No release testing PR found for VERSION.
   Run '/microshift-release:prow-testing VERSION create-pr' to create one first.
   ```

2. **List all jobs for this PR from GCS:**

   ```bash
   PR_NUMBER=NNNN
   GCS_API="https://storage.googleapis.com/storage/v1/b/test-platform-results/o"
   GCS_PR_PREFIX="pr-logs/pull/openshift_microshift"

   curl -s --max-time 60 --retry 3 --retry-delay 5 \
     "${GCS_API}?prefix=${GCS_PR_PREFIX}/${PR_NUMBER}/&delimiter=/" \
     | jq -r '.prefixes[]? // empty' \
     | sed "s|${GCS_PR_PREFIX}/${PR_NUMBER}/||; s|/$||"
   ```

3. **Match GCS jobs to the 6 expected jobs.** For each of the 6 expected short names, find the corresponding full GCS job name by checking if it ends with the short name. For example, `pull-ci-openshift-microshift-release-4.21-e2e-aws-tests-release` matches `e2e-aws-tests-release`.

4. **For each matched job, get the latest build status:**

   ```bash
   GCS_BASE="https://storage.googleapis.com/test-platform-results"
   PROW_VIEW="https://prow.ci.openshift.org/view/gs/test-platform-results"
   JOB="full-gcs-job-name"

   BUILD_ID=$(curl -s --max-time 60 --retry 3 --retry-delay 5 \
     "${GCS_BASE}/${GCS_PR_PREFIX}/${PR_NUMBER}/${JOB}/latest-build.txt")

   FINISHED=$(curl -s --max-time 60 --retry 3 --retry-delay 5 \
     "${GCS_BASE}/${GCS_PR_PREFIX}/${PR_NUMBER}/${JOB}/${BUILD_ID}/finished.json")
   ```

   Parse the status:

   - If `finished.json` is missing or contains `NoSuchKey` or HTML → status is `PENDING`
   - Otherwise, extract `.result` from the JSON (typically `SUCCESS` or `FAILURE`)
   - Build the Prow URL: `${PROW_VIEW}/pr-logs/pull/openshift_microshift/${PR_NUMBER}/${JOB}/${BUILD_ID}`

   **Run all 6 job status checks in parallel** using background subshells and `wait`, writing results to temp files. This matches the parallelization pattern in `prow-jobs-for-pull-requests.sh` `fetch_pr_results()`.

5. **Display results as a table:**

   ```text
   Release Testing CI Status for VERSION
   PR: #NNNN (https://github.com/openshift/microshift/pull/NNNN)

   Job                                     | Status  | Prow URL
   -----------------------------------------|---------|--------------------------------------------------
   e2e-aws-tests-release                   | SUCCESS | https://prow.ci.openshift.org/view/gs/...
   e2e-aws-tests-release-arm               | SUCCESS | https://prow.ci.openshift.org/view/gs/...
   e2e-aws-tests-bootc-release-el9         | PENDING | https://prow.ci.openshift.org/view/gs/...
   e2e-aws-tests-bootc-release-el10        | FAILURE | https://prow.ci.openshift.org/view/gs/...
   e2e-aws-tests-bootc-release-arm-el9     | --      | (not started)
   e2e-aws-tests-bootc-release-arm-el10    | --      | (not started)

   Summary: 2 SUCCESS, 1 PENDING, 1 FAILURE, 2 not started
   ```

   Use `--` for jobs that have no GCS entry (not yet triggered or never ran).

6. **If all 6 jobs are SUCCESS**, add:

   ```text
   All CI jobs passed. Run '/microshift-release:prow-testing VERSION download' to download artifacts.
   ```

---

### Action: `download`

**Read-only action (writes to local filesystem only).**

1. **Run the status check first** (same as `status` action, Steps 2-4) to get the list of jobs with their build IDs and statuses.

2. **Filter to completed jobs** (SUCCESS or FAILURE). Skip PENDING and not-started jobs.

3. **Download artifacts for each completed job:**

   ```bash
   VERSION="X.Y.Z"
   PR_NUMBER=NNNN
   DOWNLOAD_DIR="_output/release-testing-${VERSION}"
   mkdir -p "${DOWNLOAD_DIR}"

   # For each completed job:
   gsutil -q -m cp -r \
     "gs://test-platform-results/pr-logs/pull/openshift_microshift/${PR_NUMBER}/${FULL_JOB_NAME}/${BUILD_ID}/" \
     "${DOWNLOAD_DIR}/"
   ```

   Run downloads sequentially (each is already parallelized internally by `gsutil -m`).

4. **Report download paths** for each job.

5. **Print the S3 upload command** (do NOT execute it — just print):

   ```text
   Artifacts downloaded to: _output/release-testing-VERSION/

   To upload to S3, run:
     aws s3 cp --recursive _output/release-testing-VERSION/ s3://release-testing-results/microshift/VERSION/
   ```

---

### Step 4: Handle Errors

For any action, handle these common errors:

| Error | Detection | Message |
|---|---|---|
| Version < 4.21 | Minor version check | "Version 4.XX uses Jenkins (USHIFT-6805). This skill supports 4.21+ only." |
| Nightly version | Contains "nightly" | "Phase 2 does not apply to nightly versions." |
| `gh` not authenticated | `gh` exit code or stderr | "GitHub CLI not authenticated. Run: gh auth login" |
| PR not found | Empty result from Step 2 | "No release testing PR found. Run create-pr first." |
| GCS API error | curl timeout or non-JSON response | "Could not fetch job status for JOB_NAME — GCS API error." |
| Branch already exists | git push failure | "Branch release-testing-VERSION already exists. Delete it or use the existing PR." |
| `gsutil` not installed | command not found | "gsutil not found. Install Google Cloud SDK." |

## Examples

```bash
/microshift-release:prow-testing 4.21.3                    # check status (default)
/microshift-release:prow-testing 4.21.3 status             # same as above
/microshift-release:prow-testing 4.21.3 create-pr          # create draft PR
/microshift-release:prow-testing 4.21.3 trigger            # trigger CI jobs
/microshift-release:prow-testing 4.21.3 download           # download artifacts
/microshift-release:prow-testing 4.22.0-rc.1 status        # RC version
/microshift-release:prow-testing 4.22.0-ec.5 create-pr     # EC version
```

## Notes

- **Mutating actions** (`create-pr`, `trigger`) always show what will happen and ask for confirmation before executing
- The `download` action prints the S3 upload command but does not execute it — the user runs it manually
- The PR is created as a **draft** with an empty commit on a `release-testing-VERSION` branch
- Job matching uses suffix matching: GCS stores full names like `pull-ci-openshift-microshift-release-4.21-e2e-aws-tests-release`, but we match by the short suffix `e2e-aws-tests-release`
- The `status` action fetches all 6 job results **in parallel** for speed
