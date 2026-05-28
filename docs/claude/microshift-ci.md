# MicroShift CI Plugin

## Installation

```text
/plugin marketplace add openshift-eng/edge-tooling
/plugin install microshift-ci
```

## What Runs in CI

A periodic Prow job (`periodic-ci-openshift-eng-edge-tooling-main-microshift-ci-doctor`)
runs daily and performs three phases automatically:

1. **Analysis** - `/microshift-ci:doctor 4.18,4.19,4.20,4.21,4.22,5.0,main`
   (50 min budget, 100 turns)
2. **Bug creation dry-run** - `/microshift-ci:create-bugs <releases>`
   (10 min budget, 50 turns)
3. **Close duplicate rebase PRs** - closes older rebase PRs superseded by newer ones
4. **Rebase PR restart** - restarts failed rebase bot PR tests

The job produces an HTML report, per-job analysis files, bug mapping JSON,
and a session archive for local continuation. All artifacts are available
in the Prow job's artifact directory.

## Daily Workflow

Start from the CI job results - don't re-run doctor locally.

### 1. Open the CI job

Find the latest run at
[MicroShift CI Doctor](https://prow.ci.openshift.org/?job=periodic-ci-openshift-eng-edge-tooling-main-microshift-ci-doctor).

The Prow Spyglass of a job page contains the `MicroShift CI Doctor Report`
section, which is the main entry point. The report shows all failures grouped
by release with JIRA correlation.

### 2. Continue locally

The Prow Spyglass of a job page contains the `Continue This MicroShift CI Session Locally`
section, containing the command for downloading the CI session artifacts into
a local working directory:

```text
/microshift-ci:continue-session <prow-job-url>
```

This sets up the same working directory layout the CI job used, so all subsequent
commands work on the downloaded data.

> Note: Only analysis files are downloaded - raw prow job artifacts
> (build logs, SOS reports) are not included. Use `/microshift-ci:prow-job`
> to fetch those for specific jobs.

### 3. Review bug candidates

```text
/microshift-ci:create-bugs 4.20,4.21,4.22,5.0,main --auto
```

Dry-run: shows what bugs would be created or skipped, with decisions
(duplicate, stale regression, infrastructure, or new).

### 4. Create bugs

```text
/microshift-ci:create-bugs 4.20,4.21,4.22,5.0,main --auto --create
```

Executes: creates JIRA bugs in USHIFT, skips duplicates and infrastructure failures.
Drop `--auto` for interactive per-candidate prompts.

### 5. Investigate specific failures

```text
/microshift-ci:prow-job <prow-url>
/microshift-ci:test-job <prow-url>
/microshift-ci:test-scenario <prow-url> <scenario-name>
```

- `prow-job` - root cause analysis of a single failed job
- `test-job` - comprehensive job metadata and all scenario results
- `test-scenario` - deep dive into one scenario's test results

### 6. Refresh report after changes

```text
/microshift-ci:doctor-refresh 4.20,4.21,4.22,5.0,main
```

Re-runs JIRA correlation and regenerates the HTML report from existing
job analysis files (does not re-analyze jobs).

## PR Job Management

The CI job automatically closes duplicate rebase PRs and restarts failed
rebase bot PR tests. To run manually:

```bash
# Close duplicate rebase PRs (dry-run)
bash plugins/microshift-ci/scripts/prow-jobs-for-pull-requests.sh \
  --mode close-duplicates --author 'microshift-rebase-script[bot]' \
  --filter 'NO-ISSUE: rebase-release'

# Close duplicate rebase PRs (execute)
bash plugins/microshift-ci/scripts/prow-jobs-for-pull-requests.sh \
  --mode close-duplicates --author 'microshift-rebase-script[bot]' \
  --filter 'NO-ISSUE: rebase-release' --execute

# Restart failed rebase PR jobs (dry-run)
bash plugins/microshift-ci/scripts/prow-jobs-for-pull-requests.sh \
  --mode restart --author 'microshift-rebase-script[bot]'

# Restart failed rebase PR jobs (execute)
bash plugins/microshift-ci/scripts/prow-jobs-for-pull-requests.sh \
  --mode restart --author 'microshift-rebase-script[bot]' --execute
```

## More Info

See the [plugin README](../../plugins/microshift-ci/README.md) for prerequisites,
full skill list, and usage examples.
