# openshift-tests (Two-Node / TNF)

Scripts for running the `openshift-tests` e2e suite against Two-Node OpenShift
with Fencing (TNF) clusters, plus log-capture tooling for debugging
node-replacement, fencing, and network-disruption scenarios.

## Layout

| Path | Contents |
|------|----------|
| `scripts/` | Test runners, capture tooling, and result helpers |
| `docs/` | Cluster-access + TNF test workflow notes |

Generated artifacts (`runs/`, `tests-bin/`, `scripts/debug/`, `*.log`) are git-ignored.

## Prerequisites

- A deployed TNF cluster (via [two-node-toolbox](https://github.com/openshift-eng/two-node-toolbox)).
- The `openshift-tests` binary — extract it from the cluster payload with
  `scripts/extract-tests-binary.sh` (writes to `tests-bin/`).

## Required environment variables

The scripts do **not** assume any personal paths; set these before running:

| Variable | Required by | Meaning |
|----------|-------------|---------|
| `PROXY_ENV` | runners, captures, extract | Path to your cluster's `proxy.env` (e.g. `<two-node-toolbox>/deploy/openshift-clusters/proxy.env`). Sourced for `KUBECONFIG` + `HTTP(S)_PROXY`. |

If a required variable is unset, the script exits immediately with a message
naming the variable (`${VAR:?...}` guards).

Commonly overridden optional variables: `HYPERVISOR_IP`, `SSH_USER`,
`SSH_KEY_PATH`, `UPGRADE_TO_IMAGE` (upgrade profile), `CAPTURE_LOG_DIR`, `REPEAT`.

## Profiles

`list-tests.sh` and `run-suite.sh` take a `--profile NAME` that maps a friendly
name to a suite (plus an optional default filter or run mode). Profiles are
defined once in `scripts/test-helpers.sh` (`resolve_profile`). Any explicit
`--suite`/`--filter` overrides the profile's defaults.

| Profile | Suite | Notes |
|---------|-------|-------|
| `e2e` | `openshift/conformance/parallel` | Full conformance |
| `recovery` | `openshift/two-node` | Two-node recovery tests |
| `dualreplica` | `all`, filtered to `[OCPFeatureGate:DualReplica]` | Finds DualReplica tests in **any** suite |
| `cert-rotation` | `openshift/etcd/certrotation` | Verify suite name against cluster once |
| `upgrade` | `all`, via `run-upgrade` | Requires `--to-image` / `UPGRADE_TO_IMAGE` |

Run `scripts/run-suite.sh --list-profiles` for the current list.

## Common entrypoints

```bash
export PROXY_ENV=<two-node-toolbox>/deploy/openshift-clusters/proxy.env

# 1. Get the test binary for the current cluster payload
scripts/extract-tests-binary.sh

# 2. Run a whole suite by profile (recovery, e2e, dualreplica, cert-rotation, upgrade)
scripts/run-suite.sh --profile recovery
scripts/run-suite.sh --profile upgrade --to-image <release-image>

# 3. Run a single test
scripts/run-test.sh "<test name or regex>"

# 4. Start/stop parallel log capture during a run (node replacement, fencing, OVN, etc.)
scripts/run-all-captures.sh
scripts/stop-all-captures.sh

# 5. Inspect results
scripts/check-latest-run.sh
scripts/summarize-all-runs.sh
```

See `scripts/RECOVERY-TESTS-README.md` for the recovery-test workflow and
`docs/TNF-AND-CLUSTER.md` for cluster access + the TNF test workflow.
