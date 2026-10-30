# MicroShift Release Activities

Automate MicroShift Release Testing Activities — from pre-release evaluation through build validation, CI verification, advisory promotion, and post-release checks.

## Release workflow

Run the scripts in the order below. The optional LLM skills provide an
assistant-guided way to run the same activities. All script paths are relative
to `plugins/microshift-release/scripts/`; the numbers describe execution order,
not new command names.

| Steps | Script | Activity | When and what it does | Optional LLM skill |
|---|---|---|---|---|
| 0 | `precheck.sh` | **Decide whether to release** | Before requesting builds, typically every Friday. Evaluate scheduled OCP releases, CVEs and other criteria; `ASK ART` means builds should be requested. Supports `xyz`, `nightly` and `ecrc`. | `/microshift-release:pre-check` |
| 0a | `request_art_builds.sh` | **Request builds** | For each z-stream marked `ASK ART`. Create or reuse ART and USHIFT tracking tickets and send the ART request; preview with `--dry-run`. | `/microshift-release:request-art-builds` |
| 1 | `validate_artifacts.sh` | **Validate ART builds** | Once ART has built RPMs and bootc images, check their contents, provenance and availability. | `/microshift-release:validate-artifacts` |
| 2 | `automated_ci_testing.sh` | **Run release tests** | After step 1. Check the S3 RPM cache and AWS/GitHub/Google Cloud tools, then run Prow CI (4.21+): draft PR, jobs, results and upload. | `/microshift-release:automated-testing` |
| 3 | `validate_bootc_advisory.sh` | **Check bootc advisory for QE sign-off** | Check the bootc advisory and stage/prod catalogs. Checks may start alongside step 1, but wait for step 2 before signing off Shipment. | `/microshift-release:advisory-promotion` |
| 3 | `validate_rpm_errata.sh` | **Check RPM advisory for QE sign-off** | Check the RPM advisory in Errata Tool. Checks may start alongside step 1, but wait for step 2 before signing off Errata. | `/microshift-release:advisory-promotion` with `--errata <advisory_id>` |
| 4 | `post_release.sh` | **Verify the published release** | After shipping a GA or z-stream release, verify public artifacts, errata, documentation and lifecycle information. | `/microshift-release:post-release` |
| Optional, extra info | — (skill only) | **Find artifact locations** | Look up availability and locations of RPMs and bootc images at any time. Informational; does not replace step 1. | `/microshift-release:release-versions` |

## How To

### Run scripts directly

Run from the repository root with the [requirements](#requirements) configured
in your shell. Set the scripts directory and target version once:

```bash
SCRIPTS_DIR=plugins/microshift-release/scripts
VERSION=4.21.8
```

Choose the subsection for the current activity; do not run every step at once.
The Python files and `scripts/lib/` implement these commands; they are not
additional release steps. `run_unit_tests.sh` runs developer tests, not release
testing.

#### Step 0: Decide whether to release

Evaluate the target version before requesting builds:

```bash
bash "$SCRIPTS_DIR/precheck.sh" xyz "$VERSION"
```

For other release types, use `precheck.sh nightly`, `precheck.sh ecrc EC`, or
`precheck.sh ecrc RC`.

#### Step 0a: Request builds

For each z-stream marked `ASK ART`, use `request_art_builds.sh` to create or reuse
matching ART and USHIFT tickets, link them, and post the Slack request. Preview
first with `--dry-run`:

```bash
ART_DUE_DATE=2026-11-18
RELEASE_DATE=2026-11-20
REASON="CVE fixes"
bash "$SCRIPTS_DIR/request_art_builds.sh" "$VERSION" "$ART_DUE_DATE" "$RELEASE_DATE" --reason "$REASON" --dry-run
```

Use dates in `YYYY-MM-DD` format and explain why the release is needed. After
reviewing the preview, remove `--dry-run` to execute. Use the ART and USHIFT keys
printed by the run; do not clone additional tickets. See
[Requirements](#requirements) for secrets and use `--help` for all flags.

For the manual alternative and how to track release-testing activities, use
the [request-art-builds skill](skills/request-art-builds/SKILL.md).

#### Step 1: Validate ART builds

Wait for ART to produce the RPMs and bootc images, then validate them:

```bash
bash "$SCRIPTS_DIR/validate_artifacts.sh" "$VERSION"
```

#### Step 2: Run release tests

After artifact validation, call `automated_ci_testing.sh <action> <version>` in
this order:

1. `preflight` — check authentication and the RPM build cache.
2. `create-pr` — create or reuse a draft release-testing PR.
3. `trigger` — trigger CI jobs.
4. `status`, then `scenarios` — wait for jobs and validate their results.
5. `download` — download CI artifacts.
6. `upload` — upload the results to S3.
7. `complete` — post the result, close the PR and clean up.

Start with the preflight check:

```bash
bash "$SCRIPTS_DIR/automated_ci_testing.sh" preflight "$VERSION"
```

Prow actions that modify state default to a plan; inspect it and add `--execute`
to perform the action. For example, preview PR creation, then execute it:

```bash
bash "$SCRIPTS_DIR/automated_ci_testing.sh" create-pr "$VERSION"
bash "$SCRIPTS_DIR/automated_ci_testing.sh" create-pr "$VERSION" --execute
```

Continue with the remaining actions in order, checking the output before moving
on. Do not proceed to artifact collection until CI jobs and scenario checks pass.

#### Step 3: Check bootc advisory for QE sign-off

Check the bootc advisory and stage/prod catalogs:

```bash
bash "$SCRIPTS_DIR/validate_bootc_advisory.sh" "$VERSION" -s -p
```

These checks can start alongside step 1, but wait for step 2 to finish before
signing off Shipment.

#### Step 3: Check RPM advisory for QE sign-off

Check the RPM advisory in Errata Tool:

```bash
ADVISORY_ID=12345 # Replace with the release's advisory ID
bash "$SCRIPTS_DIR/validate_rpm_errata.sh" "$VERSION" "$ADVISORY_ID"
```

These checks can start alongside step 1, but wait for step 2 to finish before
signing off Errata.

#### Step 4: Verify the published release

Only after the GA or z-stream release has shipped, verify public availability:

```bash
bash "$SCRIPTS_DIR/post_release.sh" "$VERSION"
```

#### Optional, extra info: Find artifact locations

There is no standalone script for this lookup. Use
`/microshift-release:release-versions <version>` through an LLM assistant to find
RPM and bootc image locations. This is informational and does not replace step 1.

### Run through an LLM assistant

With the `microshift-release` plugin available in your assistant, invoke a skill
in the conversation instead of running its script in a terminal. For example:

```text
# Step 0: Decide whether to release
/microshift-release:pre-check 4.21.8

# Step 0a: Request builds
/microshift-release:request-art-builds 4.21.8 2026-11-18 2026-11-20 --reason "CVE fixes"

# Step 1: Validate ART builds
/microshift-release:validate-artifacts 4.21.8

# Step 2: Run release tests
/microshift-release:automated-testing 4.21.8

# Step 3: Check bootc advisory for QE sign-off
/microshift-release:advisory-promotion 4.21.8 -s -p

# Step 3: Check RPM advisory for QE sign-off
/microshift-release:advisory-promotion 4.21.8 --errata <advisory_id>

# Step 4: Verify the published release
/microshift-release:post-release 4.21.8

# Optional, extra info: Find artifact locations
/microshift-release:release-versions 4.21.8
```

Invoke only the skill for the current activity. The assistant runs the underlying
scripts; `automated-testing` guides you through the Prow actions and requests
confirmation before executing changes. The same credentials and tools are still
required in the assistant's environment.

The `pre-check` skill can additionally resolve natural-language time ranges via
Jira, for example `/microshift-release:pre-check this week`. Time ranges require
the assistant's Jira MCP search tool (`mcp__jira__jira_search`); without it,
provide explicit versions, such as `/microshift-release:pre-check 4.21.8`.
For an optional
artifact-location lookup, use `/microshift-release:release-versions 4.21.8`.
There is no standalone script for that lookup. The `request-art-builds` skill
previews the build request, asks for confirmation before writing to Jira or
Slack, and includes a manual alternative and release-testing tracking guidance.

## Requirements

- VPN connection (for Brew RPM checks, advisory reports)
- Kerberos login: `kinit`
- GitLab API token: `GITLAB_API_TOKEN`
- Jira API token: `JIRA_API_TOKEN` and `JIRA_USERNAME`
- `gh` CLI (for PR operations in automated testing)
- `aws` CLI (for S3 build cache and artifact upload)
- `gsutil` CLI (for GCS artifact download)
- `jq`, `curl`, `JIRA_USERNAME` and `JIRA_API_TOKEN` for requesting builds;
  `SLACK_WEBHOOK_URL` is also needed unless using `--dry-run` or `--skip-slack`
