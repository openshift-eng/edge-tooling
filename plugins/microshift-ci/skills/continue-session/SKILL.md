---
name: microshift-ci:continue-session
argument-hint: <prow-job-url>
description: Download CI Doctor artifacts from a completed prow job and set up a local workdir for continued analysis
user-invocable: true
allowed-tools: Bash, Read, Glob, Grep
---

# microshift-ci:continue-session

## Synopsis

```bash
/microshift-ci:continue-session https://prow.ci.openshift.org/view/gs/test-platform-results/logs/periodic-ci-openshift-eng-edge-tooling-main-microshift-ci-doctor/2053152146479648768
```

## Description

Downloads CI Doctor analysis artifacts (per-job reports, summaries, bug mappings, HTML report) from a completed prow job into a local workdir, preserving the source directory structure. The workdir date is derived from the prow job's start timestamp, matching the layout the doctor skill creates. This lets you pick up where the CI agent left off — inspect reports, re-run aggregation, create bugs, or do further investigation.

## Arguments

- `<ARGUMENTS>` (required): A Prow job URL or GCS web URL pointing to a CI Doctor job run

## Workflow

1. Run the download script:

   ```text
   bash plugins/microshift-ci/scripts/continue-session.sh <ARGUMENTS>
   ```

   The script:
   - Parses the URL and converts it to a GCS path
   - Fetches `started.json` to derive the job date → workdir path
   - Fails if the workdir already exists (prevents clobbering local data)
   - Downloads analysis files into subdirectories preserving the source structure:
     - `<WORKDIR>/jobs/` — job analysis files (`release-*`, `prs-*`)
     - `<WORKDIR>/bugs/` — bug correlation files (`bugs-*`, `bug-candidates-*`)
     - `<WORKDIR>/` — final reports (HTML report, claude logs)
   - Outputs a JSON summary to stdout

2. Read the JSON summary. It contains:
   - `workdir` — local path where files were downloaded
   - `releases` — array of releases with job report counts, summary/bugs availability
   - `prs` — PR job report info (or null)
   - `html_report` — path to the HTML report (or null)
   - `files_downloaded` — total file count

3. Present the summary to the user:
   - List each release with its job report count
   - Note whether summaries and bug mappings are available
   - Show the HTML report path

4. Suggest next actions based on what's available:

   - **View the HTML report**: `open <workdir>/report-microshift-ci-doctor.html`
   - **Re-generate the HTML report** (e.g., after modifying job reports):

     ```text
     bash plugins/microshift-ci/scripts/doctor.sh finalize --component microshift --workdir <WORKDIR> <RELEASES>
     ```

     where `<RELEASES>` is a comma-separated list of the releases found in the summary.

   - **Create Jira bugs from the analysis**:

     ```text
     /microshift-ci:create-bugs <VERSION> --create
     ```

   - **Read individual job reports** for deeper investigation:

     ```text
     <WORKDIR>/jobs/release-<VERSION>-job-<N>-<BUILD_ID>.txt
     ```

   - **Re-analyze a specific prow job** (downloads fresh artifacts):

     ```text
     /microshift-ci:prow-job <PROW_URL>
     ```

## Prerequisites

- `gsutil` CLI must be installed (uses anonymous access on public GCS buckets)
- `jq` for JSON processing

## Notes

- Only analysis files are downloaded — raw prow job artifacts (build logs, SOS reports) are not included. Use `/microshift-ci:prow-job` or `download-jobs.sh` to fetch those for specific jobs.
- The workdir uses the same layout as the doctor skill (`/tmp/microshift-ci-claude-workdir.<YYMMDD>`), with job analysis files under `jobs/` and bug correlation files under `bugs/`. All doctor scripts (`finalize`, `aggregate.py`, `create-report.py`) work directly on the downloaded data.
- If the workdir for the job's date already exists, the script exits with an error. Remove the existing workdir first if you want to replace it with CI data.
