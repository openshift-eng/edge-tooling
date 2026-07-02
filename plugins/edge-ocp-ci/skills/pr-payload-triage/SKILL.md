---
name: edge-ocp-ci:pr-payload-triage
description: "Triage a PR payload test run — classify failures as PR-caused vs. flaky by matching failing tests against the PR diff"
argument-hint: "<pr-payload-url> <pr-ref>"
user-invocable: true
---

# PR Payload Triage Skill

You are helping a developer triage the results of a PR payload test run triggered by `/payload-job` on a GitHub PR. The goal is to classify each failing job as either **PR-caused** (the failing test was modified by the PR) or **flaky/unrelated** (the failure is in a test the PR did not touch).

## Arguments

The user provides two arguments:

1. **PR payload URL** — the `pr-payload-tests.ci.openshift.org` link from the openshift-ci bot comment on the PR
2. **PR reference** — the GitHub PR in `owner/repo#number` format (e.g., `openshift/origin#31276`) or a full URL

If the user provides only the payload URL, ask for the PR reference. If they provide only a PR URL and mention payload, check the PR comments for the payload link.

## Execution

Run the payload-monitor CLI in PR triage mode:

```bash
cd <edge-tooling-repo>/payload-monitor
python3 -m payload_monitor \
  --pr-payload-url "<payload-url>" \
  --pr "<pr-ref>" \
  --verbose
```

The tool will:
1. Fetch the pr-payload-tests page and extract Prow job URLs
2. Check each job's pass/fail status via GCS `finished.json`
3. For failing jobs, fetch deep junit artifacts to get individual Go test names
4. Fetch the PR diff from GitHub
5. Classify each failure by checking if the failing test description appears in the diff

## Output

The tool prints a markdown triage report to stdout. Present it to the user as-is.

### Interpreting Results

| Verdict | Icon | Meaning |
|---------|------|---------|
| PR CAUSED | 🔴 | The failing test was modified by the PR — investigate |
| FLAKY | 🟡 | The failure is in a test the PR did not touch — safe to `/retest` |

### Follow-up Actions

Based on the results, suggest:

- **All FLAKY**: "All failures are unrelated to your PR. Safe to re-trigger." Then find the original `/payload-job` trigger comment on the PR and suggest re-posting it verbatim.
- **Some PR CAUSED**: "Job X failed in a test your PR modified. Check the error and Prow link before re-triggering."
- **All PR CAUSED**: "All failures are in tests your PR touches. Investigate before re-triggering."

> **Note:** To re-trigger, copy the original `/payload-job <periodic-job-name>` comment from the PR — not just `/payload-job` alone, and never `/retest` (which only re-runs regular CI checks).

## Prerequisites

- `python3` with `requests`, `click` installed
- `gsutil` for GCS artifact access
- `gh` CLI authenticated for GitHub API access
- The `edge-tooling` repository cloned locally

## Examples

```bash
# Triage from a PR payload link
python3 -m payload_monitor \
  --pr-payload-url "https://pr-payload-tests.ci.openshift.org/runs/ci/2a276860-70a0-11f1-9741-fc7a909b3142-0" \
  --pr "openshift/origin#31276"
```
