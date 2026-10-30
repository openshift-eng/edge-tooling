---
name: microshift-release:request-art-builds
argument-hint: <version> <art-due-date> <release-date> --reason "<text>" [--dry-run] [--skip-slack] [--manual]
description: Step 0a after ASK ART — request MicroShift z-stream builds, create release-tracking tickets, or guide the manual alternative
user-invocable: true
allowed-tools: Bash, AskUserQuestion
---

# microshift-release:request-art-builds

## Synopsis

```text
/microshift-release:request-art-builds <X.Y.Z> <ART_DUE_DATE> <RELEASE_DATE> --reason "<TEXT>"
/microshift-release:request-art-builds <X.Y.Z> <ART_DUE_DATE> <RELEASE_DATE> --reason "<TEXT>" --dry-run
/microshift-release:request-art-builds <X.Y.Z> <ART_DUE_DATE> <RELEASE_DATE> --reason "<TEXT>" --manual
```

Use for each z-stream marked `ASK ART` by `pre-check`. This skill requests builds;
it does not build artifacts or run release tests itself.

## Prerequisites

- Run from the repository root, with `bash`, `jq` and `curl` available.
- `JIRA_USERNAME` and `JIRA_API_TOKEN` must be available to the script, including
  for `--dry-run`, which queries Jira without writing.
- `SLACK_WEBHOOK_URL` is required for execution unless `--skip-slack` is used.
- Manual mode requires the user to have Jira and Slack access, not script tokens.
- Never ask the user to paste secrets or print environment values.

## Steps

### 1. Collect the request

Parse `$ARGUMENTS`: version, ART due date, release date, reason and optional
`--dry-run`, `--skip-slack` or `--manual`. Ask for missing inputs with
AskUserQuestion. Dates must be `YYYY-MM-DD`, and the ART due date must not be
after the release date. Accept only a z-stream version (`X.Y.Z`, with `Z > 0`).

If `--manual` is selected, skip steps 2 and 3 and go directly to the manual
alternative in step 4; never pass that flag to the script.
Do not infer `--force` or `--resend-slack`; both need a separate explicit
user decision after investigating duplicates or notification delivery.

### 2. Preview the automated request

Unless `--manual` was selected, run the following with the collected values as
separate, shell-quoted arguments (never interpolate untrusted text as shell code):

```bash
SCRIPTS_DIR=plugins/microshift-release/scripts
# Example values; substitute the request collected in step 1.
VERSION=4.21.8
ART_DUE_DATE=2026-11-18
RELEASE_DATE=2026-11-20
REASON="CVE fixes"
bash "$SCRIPTS_DIR/request_art_builds.sh" "$VERSION" "$ART_DUE_DATE" "$RELEASE_DATE" --reason "$REASON" --dry-run
```

Forward `--skip-slack` if requested. Display the script's preview and explain
which tickets will be reused or created and whether Slack will be notified.
Dry-run ticket placeholders are not real issue keys. If `--dry-run` was requested,
stop after the preview; do not proceed to execution.

### 3. Confirm and execute

Use AskUserQuestion to confirm the version, dates, reason, ticket actions and
Slack notification before any write. If declined, stop. If confirmed, run the
same command without `--dry-run`, preserving `--skip-slack` when requested.

The script prints the ART and USHIFT keys to stdout. Report their Jira links and
the notification outcome. Reuse these keys; never clone templates on top of
script-created tickets. If `--skip-slack` was used, present the generated message
for the user to post manually and remind them to select actual Slack mentions.

If creation or delivery fails with an unknown outcome, stop and ask the user to
reconcile Jira/Slack before retrying. Do not automatically rerun a write command.

### 4. Manual alternative

When `--manual` is selected, guide the user through these actions rather than
running the script. Ask them to search for existing tickets for the target
version first and reuse matches to avoid duplicates.

1. Clone the [ART template](https://redhat.atlassian.net/browse/ART-11857) if no
   matching request exists. Replace the target version in the title and
   description, set the ART due date, and include the release reason.
2. Clone the [USHIFT template](https://redhat.atlassian.net/browse/USHIFT-6945) if
   no matching release-testing ticket exists. Replace the target version and
   release date in its title and description. Set the parent to
   [USHIFT-4531](https://redhat.atlassian.net/browse/USHIFT-4531), and relate it
   to the ART request.
3. Ask the user for the resulting ticket keys, then prepare this request for
   [#forum-ocp-art](https://redhat.enterprise.slack.com/archives/CB95J6R4N), using
   the real version, due date, ART key/link and reason:

   ```text
   Hello, @release-artists I've opened <ART_KEY> (<ART_URL>) requesting MicroShift X.Y.Z builds (due date <ART_DUE_DATE>). Please help create RPMs and bootc images, thanks!
   Reason: <TEXT>
   cc @Pablo Acevedo @Rama kasturi @AlejandroGullon @Tami Love
   ```

   Have the user select actual mentions and post the message. Do not claim it
   was sent without confirmation, or duplicate a notification already sent.

### 5. Track Release Testing Activities

For every z-stream requested to ART, open the USHIFT ticket created or reused
above. Follow its release-testing checklist, log results and evidence, and mark
each step completed only when its checks have actually finished successfully.

Guide the user through the remaining release activities in order:

1. Wait for ART to produce the artifacts, then use `validate_artifacts.sh`
   (or `/microshift-release:validate-artifacts`).
2. Run `automated_ci_testing.sh` (or `/microshift-release:automated-testing`).
3. Check advisories with `validate_bootc_advisory.sh` and
   `validate_rpm_errata.sh` before QE sign-off.
4. After shipping, use `post_release.sh` (or `/microshift-release:post-release`).

Provide a checklist for the user to record in Jira; this skill does not
automatically mark Jira checklist items or sign off releases.

## Output

- Target version, ART due date, release date and release reason.
- Preview only, executed, manual, declined, or blocked status.
- ART and USHIFT links, when real keys are available.
- Slack delivery status (sent, reused/skipped, manual pending, or unknown).
- Next release-testing activity and the tracking ticket.

## Examples

```text
/microshift-release:request-art-builds 4.21.8 2026-11-18 2026-11-20 --reason "CVE fixes" --dry-run
/microshift-release:request-art-builds 4.21.8 2026-11-18 2026-11-20 --reason "Release cadence" --skip-slack
/microshift-release:request-art-builds 4.21.8 2026-11-18 2026-11-20 --reason "CVE fixes" --manual
```
