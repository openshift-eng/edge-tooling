# MicroShift Release Activities

Automate MicroShift Release Testing Activities — from pre-release evaluation through build validation, CI verification, advisory promotion, and post-release checks.

## Release workflow

Run the scripts in the order below. The optional LLM skills provide an
assistant-guided way to run the same activities. All script paths are relative
to `plugins/microshift-release/scripts/`; the numbers describe execution order,
not new command names.

| Steps | Script | Activity | When and what it does | Optional LLM skill |
|---|---|---|---|---|
| 0a | `analyze_zstream-to-release.sh` | **Decide whether to release** | Before requesting builds, typically every Friday. Evaluate scheduled OCP releases, CVEs and other criteria; `ASK ART` means builds should be requested. Supports `xyz`, `nightly` and `ecrc`. | `/microshift-release:analyze-zstream-to-release` |
| 0b | `request_release-to-art.sh` | **Request builds** | For each z-stream marked `ASK ART`. Create or reuse ART and USHIFT tracking tickets and send the ART request; preview with `--dry-run`. | `/microshift-release:request-release-to-art` |
| 1 | `check_artifacts.sh` | **Validate ART builds** | Once ART has built RPMs and bootc images, check their contents, provenance and availability. | `/microshift-release:check-artifacts` |
| 2 | `run_ci-testing.sh` | **Run release tests** | After step 1. Check the S3 RPM cache and AWS/GitHub/Google Cloud tools, then run Prow CI (4.21+): draft PR, jobs, results and upload. | `/microshift-release:run-ci-testing` |
| 3 | `check_bootc-advisory.sh` | **Check bootc advisory for QE sign-off** | Check the bootc advisory and stage/prod catalogs. Checks may start alongside step 1, but wait for step 2 before signing off Shipment. | `/microshift-release:check-bootc-advisory` |
| 4 | `check_rpm-errata.sh` | **Check RPM Advisory in Errata for QE sign-off** | Check the RPM Advisory in Errata Tool. Checks may start alongside step 1, but wait for step 2 before signing off Errata. | `/microshift-release:check-rpm-errata` |
| 5 | `check_published-release.sh` | **Verify the published release** | After shipping a GA or z-stream release, verify public artifacts, errata, documentation and lifecycle information. | `/microshift-release:check-published-release` |
| Optional, extra info | — (skill only) | **Find artifact locations** | Look up availability and locations of RPMs and bootc images at any time. Informational; does not replace step 1. | `/microshift-release:release-versions` |

## Pre-check decision rules

- Already shipped/non-applicable CVEs are excluded. For the remaining trackers,
  explicit `release-required` / `release-not-required` labels take precedence.
- A CVE in `Verified` with an empty resolution needs human review; the report
  highlights the missing resolution rather than automatically requesting a release.
- A known release-required fix takes priority over another CVE needing review:
  recommend `ASK ART` when OCP is available, or `BLOCKED` otherwise, and retain
  the review warning.
- Zero MicroShift source commits do not rule out a release: compiler fixes or
  updated bootc image dependencies can require rebuilding the same source commit.
  Unknown advisory data or CVEs pending Jira enrichment result in `NEEDS REVIEW`,
  even without source changes. Use `SKIP` only after assessment finds no
  release-required fixes or other release gate.

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

#### Step 0a: Decide whether to release

Evaluate the target version before requesting builds:

```bash
bash "$SCRIPTS_DIR/analyze_zstream-to-release.sh" xyz "$VERSION"
```

For other release types, use `analyze_zstream-to-release.sh nightly`, `analyze_zstream-to-release.sh ecrc EC`, or
`analyze_zstream-to-release.sh ecrc RC`.

#### Step 0b: Request builds

For each z-stream marked `ASK ART`, use `request_release-to-art.sh` to create or reuse
matching ART and USHIFT tickets, link them, and post the Slack request. Preview
first with `--dry-run`:

```bash
ART_DUE_DATE=2026-11-18
RELEASE_DATE=2026-11-20
REASON="CVE fixes"
bash "$SCRIPTS_DIR/request_release-to-art.sh" "$VERSION" "$ART_DUE_DATE" "$RELEASE_DATE" --reason "$REASON" --dry-run
```

Use dates in `YYYY-MM-DD` format and explain why the release is needed. After
reviewing the preview, remove `--dry-run` to execute. Use the ART and USHIFT keys
printed by the run; do not clone additional tickets. See
[Requirements](#requirements) for secrets and use `--help` for all flags.

For the manual alternative and how to track release-testing activities, use
the [request-release-to-art skill](skills/request-release-to-art/SKILL.md).

#### Step 1: Validate ART builds

Wait for ART to produce the RPMs and bootc images, then validate them:

```bash
bash "$SCRIPTS_DIR/check_artifacts.sh" "$VERSION"
```

#### Step 2: Run release tests

After artifact validation, call `run_ci-testing.sh <action> <version>` in
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
bash "$SCRIPTS_DIR/run_ci-testing.sh" preflight "$VERSION"
```

Prow actions that modify state default to a plan; inspect it and add `--execute`
to perform the action. For example, preview PR creation, then execute it:

```bash
bash "$SCRIPTS_DIR/run_ci-testing.sh" create-pr "$VERSION"
bash "$SCRIPTS_DIR/run_ci-testing.sh" create-pr "$VERSION" --execute
```

Continue with the remaining actions in order, checking the output before moving
on. Do not proceed to artifact collection until CI jobs and scenario checks pass.

#### Step 3: Check bootc advisory for QE sign-off

Check the bootc advisory and stage/prod catalogs:

```bash
bash "$SCRIPTS_DIR/check_bootc-advisory.sh" "$VERSION" -s -p
```

These checks can start alongside step 1, but wait for step 2 to finish before
signing off Shipment.

#### Step 4: Check RPM advisory for QE sign-off

Check the RPM advisory in Errata Tool:

```bash
ADVISORY_ID=12345 # Replace with the release's advisory ID
bash "$SCRIPTS_DIR/check_rpm-errata.sh" "$VERSION" "$ADVISORY_ID"
```

These checks can start alongside step 1, but wait for step 2 to finish before
signing off Errata.

#### Step 5: Verify the published release

Only after the GA or z-stream release has shipped, verify public availability:

```bash
bash "$SCRIPTS_DIR/check_published-release.sh" "$VERSION"
```

#### Optional, extra info: Find artifact locations

There is no standalone script for this lookup. Use
`/microshift-release:release-versions <version>` through an LLM assistant to find
RPM and bootc image locations. This is informational and does not replace step 1.

### Run through an LLM assistant

With the `microshift-release` plugin available in your assistant, invoke a skill
in the conversation instead of running its script in a terminal. For example:

```text
# Step 0a: Decide whether to release
/microshift-release:analyze-zstream-to-release 4.21.8

# Step 0b: Request builds
/microshift-release:request-release-to-art 4.21.8 2026-11-18 2026-11-20 --reason "CVE fixes"

# Step 1: Validate ART builds
/microshift-release:check-artifacts 4.21.8

# Step 2: Run release tests
/microshift-release:run-ci-testing 4.21.8

# Step 3: Check bootc advisory for QE sign-off
/microshift-release:check-bootc-advisory 4.21.8 -s -p

# Step 4: Check RPM advisory for QE sign-off
/microshift-release:check-rpm-errata 4.21.8 <advisory_id>

# Step 5: Verify the published release
/microshift-release:check-published-release 4.21.8

# Optional, extra info: Find artifact locations
/microshift-release:release-versions 4.21.8
```

Invoke only the skill for the current activity. The assistant runs the underlying
scripts; `run-ci-testing` guides you through the Prow actions and requests
confirmation before executing changes. The same credentials and tools are still
required in the assistant's environment.

The `analyze-zstream-to-release` skill can additionally resolve natural-language time ranges via
Jira, for example `/microshift-release:analyze-zstream-to-release this week`. Time ranges require
the assistant's Jira MCP search tool (`mcp__jira__jira_search`); without it,
provide explicit versions, such as `/microshift-release:analyze-zstream-to-release 4.21.8`.
For an optional
artifact-location lookup, use `/microshift-release:release-versions 4.21.8`.
There is no standalone script for that lookup. The `request-release-to-art` skill
previews the build request, asks for confirmation before writing to Jira or
Slack, and includes a manual alternative and release-testing tracking guidance.

## Unit tests

Run both unit-test suites without VPN access or service credentials.

Requires `jq` and Kerberos development headers/tools (`krb5-config`) to build
the `gssapi` dependency. On Ubuntu/Debian, install them with
`sudo apt-get install -y jq libkrb5-dev`.

```shell
cd plugins/microshift-release/scripts
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements-test.txt
python3 -m pytest unit_tests/ -q
bash unit_tests/test_request_release-to-art.sh
```

## Requirements

- VPN connection (for Brew RPM checks, advisory reports)
- Red Hat CA in the system trust store, for the GitLab checks (shipment MR,
  advisory YAML). Red Hat-managed machines have it; a container or CI image
  must add it. GitLab requests carry `GITLAB_API_TOKEN`, so TLS is verified
  and there is no `verify=False` fallback.
- Kerberos login: `kinit`
- GitLab API token: `GITLAB_API_TOKEN`
- Jira API token: `JIRA_API_TOKEN` and `JIRA_USERNAME`
- `gh` CLI (for PR operations in automated testing)
- `aws` CLI (for S3 build cache and artifact upload)
- `gsutil` CLI (for GCS artifact download)
- `jq`, `curl`, `JIRA_USERNAME` and `JIRA_API_TOKEN` for requesting builds;
  `SLACK_WEBHOOK_URL` is also needed unless using `--dry-run` or `--skip-slack`

### AWS credentials for Prow testing

The preflight uses the AWS CLI's standard credential provider chain. For setup
and troubleshooting, see
[`skills/run-ci-testing/SKILL.md`](skills/run-ci-testing/SKILL.md).
