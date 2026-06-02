# MicroShift CI Doctor Plugin

## Installation

```text
/plugin marketplace add openshift-eng/edge-tooling
/plugin install microshift-ci
```

## What Runs In CI

A periodic Prow job (`periodic-ci-openshift-eng-edge-tooling-main-microshift-ci-doctor`)
runs daily and performs these phases automatically:

1. **Close duplicate rebase PRs** - closes older rebase PRs superseded by newer ones
2. **Analysis** - `/microshift-ci:doctor <releases>` (35 min, 100 turns)
3. **Bug creation** - `/microshift-ci:create-bugs <releases> --create`
   (15 min, 50 turns)
4. **Fix test bugs dry-run** - `/microshift-ci:fix-test-bugs --open`
   (5 min, 20 turns) - reports which bugs are eligible for auto-fix
5. **Report refresh** - `/microshift-ci:doctor-refresh <releases>`
   (5 min, 30 turns) - re-generates the HTML report with new bug links
6. **Rebase PR restart** - restarts failed rebase bot PR tests

The job produces an HTML report, per-job analysis files, bug mapping JSON,
and a session archive for local continuation. All artifacts are available
in the Prow job's artifact directory.

## Daily Workflow

The commands below use `<releases>` as a placeholder for the comma-separated
release list. The current CI default is `4.18,4.19,4.20,4.21,4.22,5.0,main`.

Start from the CI job results - don't re-run doctor locally.

### 1. Open The CI Job

Find the latest run at
[MicroShift CI Doctor](https://prow.ci.openshift.org/?job=periodic-ci-openshift-eng-edge-tooling-main-microshift-ci-doctor).

The Prow Spyglass of a job page contains the `MicroShift CI Doctor Report`
section, which is the main entry point. The report shows all failures grouped
by release with JIRA correlation.

### 2. Continue Locally

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

### 3. Review Open Bugs

The CI job automatically creates JIRA bugs for new failures in the `USHIFT`
project with the label `microshift-ci-ai-generated`. The **Bugs** tab in
the HTML report shows all currently open bugs with links to the corresponding
failures.

Alternatively, list all unresolved AI-generated bugs in JIRA using the
[JIRA query](https://redhat.atlassian.net/issues?jql=project%20%3D%20USHIFT%20AND%20labels%20%3D%20%22microshift-ci-ai-generated%22%20AND%20resolution%20%3D%20Unresolved%20ORDER%20BY%20created%20DESC).

To review what was created, skipped (duplicate, stale regression, infrastructure),
or already tracked, check the `analyze-ci-create-bugs-merged.txt` file in the
working directory.

Each bug should be reviewed and either acted on or closed:

- **Actionable** - assign to the appropriate developer or attempt an
  auto-fix (see [Fix Eligible Bugs](#4-fix-eligible-bugs))
- **Duplicate** - close as duplicate, linking to the existing bug
- **Infrastructure / transient** - close as not-a-bug if the failure
  is environmental and not expected to recur
- **Not reproducible** - close if the failure has not been seen in
  subsequent CI runs

To investigate a specific failure in more detail:

```text
/microshift-ci:prow-job <prow-url>
/microshift-ci:test-job <prow-url>
/microshift-ci:test-scenario <prow-url> <scenario-name>
```

- `prow-job` - root cause analysis of a single failed job
- `test-job` - comprehensive job metadata and all scenario results
- `test-scenario` - deep dive into one scenario's test results

### 4. Fix Eligible Bugs

```text
/microshift-ci:fix-test-bugs --open
```

Queries JIRA for all unresolved AI-generated bugs (`labels = microshift-ci-ai-generated`),
evaluates each against eligibility check gates, and reports which bugs can be
auto-fixed in `test/`, `scripts/`, or `docs/`.

Gates:

1. **No existing PR** - checks JIRA links and GitHub for OPEN/MERGED PRs
2. **In-scope files** - fix target must be in `test/`, `scripts/`, or `docs/`
3. **Code-fixable** - root cause is a test/script issue, not a product bug

To attempt fixes (opens draft PRs in openshift/microshift):

```text
/microshift-ci:fix-test-bugs --open --fix
```

`--fix` attempts all eligible fixes without prompting.
Each fix gets its own branch and draft PR for independent review.

Can also target specific bugs:

```text
/microshift-ci:fix-test-bugs USHIFT-1234,USHIFT-5678 --fix
```

## Appendix A: PR Job Management

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

## Appendix B: More Information

See the plugin [README](../../plugins/microshift-ci/README.md) for prerequisites,
full skill list, and usage examples.
