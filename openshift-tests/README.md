# openshift-tests (Two-Node / TNF)

Scripts for running the `openshift-tests` e2e suite against Two-Node OpenShift
with Fencing (TNF) clusters, plus log-capture tooling for debugging
node-replacement, fencing, and network-disruption scenarios.

## Layout

| Path | Contents |
|------|----------|
| `scripts/` | Test and suite runners |
| `scripts/captures/` | Diagnostic collectors and capture start/stop scripts |
| `scripts/utils/` | Binary extraction, version checks, and shared helpers |
| `docs/` | Cluster-access + TNF test workflow notes |

Generated artifacts (`runs/`, `tests-bin/`, `scripts/debug/`, `*.log`) are git-ignored.

See [AGENTS.md](AGENTS.md) for guidance on analyzing raw diagnostics and preparing
sanitized summaries or evidence for Jira, Google Drive, and other sharing destinations.

## Prerequisites

- A deployed TNF cluster (via [two-node-toolbox](https://github.com/openshift-eng/two-node-toolbox)).
- `oc` with access to the cluster and its release images for default binary
  extraction, or a preinstalled `openshift-tests` executable (see below).
- `jq` for JSON configuration and diagnostic collectors.
- Python 3 and `python3-libvirt` on the hypervisor for DHCP lookup and VM
  snapshots.

## Required environment variables

The scripts do **not** assume any personal paths; set these before running:

| Variable | Required by | Meaning |
|----------|-------------|---------|
| `PROXY_ENV` | runners, captures, extract | Path to your cluster's `proxy.env` (e.g. `<two-node-toolbox>/deploy/openshift-clusters/proxy.env`). Sourced for `KUBECONFIG` + `HTTP(S)_PROXY`. |

If a required variable is unset, the script exits immediately with a message
naming the variable (`${VAR:?...}` guards).

Commonly overridden optional variables: `HYPERVISOR_IP`, `SSH_USER`,
`SSH_KEY_PATH`, `UPGRADE_TO_IMAGE` (upgrade profile), `CAPTURE_LOG_DIR`.
Set the repeat count with `--repeat N`.

`OPENSHIFT_TESTS_EXTRA_ARGS` accepts one line of whitespace-separated arguments.
Values cannot contain whitespace; shell quoting and pathname expansion are not interpreted.

## Binary source

Both runners extract `openshift-tests` from the current cluster's release payload
by default. The extractor clears its managed `tests-bin/payload/` directory before
extracting. You can also invoke `scripts/utils/extract-tests-binary.sh` directly.

To use a preinstalled or locally built binary, explicitly set
`OPENSHIFT_TESTS=/absolute/path/to/openshift-tests`. The runners require that path
to be executable and use it unchanged, skipping extraction. Unset or empty
`OPENSHIFT_TESTS` selects payload extraction. Interactive suites reuse their
selected binary for each test.

## Profiles

`scripts/run-suite.sh` takes a `--profile NAME` that maps a friendly
name to a suite (plus an optional default filter or run mode). Profiles are
defined once in `scripts/utils/test-helpers.sh` (`resolve_profile`). Any explicit
`--suite`/`--filter` overrides the profile's defaults.

| Profile | Suite | Notes |
|---------|-------|-------|
| `e2e` | `openshift/conformance/parallel` | Full conformance |
| `recovery` | `openshift/two-node` | Two-node recovery tests |
| `ra-verification` | `openshift/etcd/certrotation` + `openshift/two-node` | Resource-agent verification without node replacement |
| `dualreplica` | `all`, filtered to `[OCPFeatureGate:DualReplica]` | Finds DualReplica tests in **any** suite |
| `cert-rotation` | `openshift/etcd/certrotation` | Verify suite name against cluster once |
| `upgrade` | `all`, via `run-upgrade` | Requires `--to-image` / `UPGRADE_TO_IMAGE` |

Run `scripts/run-suite.sh --list-profiles` for the current list, or
`scripts/run-suite.sh --profile recovery --list-only` to discover tests.

## Common entrypoints

Run these commands from `<repo>/openshift-tests`.

```bash
export PROXY_ENV=<two-node-toolbox>/deploy/openshift-clusters/proxy.env

# Run a suite by profile; the matching payload binary is extracted automatically
scripts/run-suite.sh --profile recovery
scripts/run-suite.sh --profile ra-verification
scripts/run-suite.sh --profile upgrade --to-image <release-image>

# Run a single test with a preinstalled binary
OPENSHIFT_TESTS=/absolute/path/to/openshift-tests \
scripts/run-test.sh --test "<test name or regex>"

# Start/stop parallel log capture during a run (node replacement, fencing, OVN, etc.)
scripts/captures/run-all-captures.sh
scripts/captures/stop-all-captures.sh
```

The runners print their output directories under `runs/`. Inspect the generated
`summary.tsv`, test logs, and JUnit files there.

`run-test.sh` enables captures by default; `run-suite.sh` requires
`--with-captures`. Captures add API polling, SSH sessions, and log I/O. For quick
verification, use `--no-captures`; start individual `scripts/captures/capture-*.sh`
collectors separately if you only need selected diagnostics.

File-writing collectors print their log paths. BMO and update-setup job diagnostics
are written only to `.log` files; use `tail -f` to watch them while collecting.

## Reviewing captures before sharing

Keep original captures private for approved analysis, including the IP addresses
and node identities needed for Pacemaker debugging. When a destination requires
sanitized evidence, make a private copy of only the files you intend to share.
With Gitleaks installed, scan that copy locally:

```bash
umask 077
gitleaks dir /absolute/path/to/export-copy --redact=100
```

Treat any nonzero result as requiring attention before sharing. `--redact=100`
hides secrets in the scanner's output; it does not sanitize the files. Remove
credentials from the export copy with local tools, review it, and rescan. A clean
scan does not guarantee that all sensitive content was found. Retain diagnostic
identifiers only when appropriate for the destination's audience; follow
`AGENTS.md` for the full sharing policy. If raw contents cannot be sent to the
analysis service, complete this local review before its agent accesses the files.

Gitleaks is optional and separate from collection:
<https://github.com/gitleaks/gitleaks>

See [docs/RECOVERY-TESTS-README.md](docs/RECOVERY-TESTS-README.md) for the recovery-test
workflow and [docs/TNF-AND-CLUSTER.md](docs/TNF-AND-CLUSTER.md) for cluster access +
the TNF test workflow.
