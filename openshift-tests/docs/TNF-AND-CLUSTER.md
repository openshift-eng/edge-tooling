# Cluster access + TNF workflows

## API / `oc` (two-node-toolbox)

```bash
PROXY="<two-node-toolbox>/deploy/openshift-clusters/proxy.env"
set -a && source "$PROXY" && set +a
oc get nodes
```

- **KUBECONFIG** — file next to `proxy.env`.
- **HTTP(S)_PROXY** — Squid on hypervisor `EC2_PUBLIC_IP:PROXYPORT` (often **8213**).

## SSH to masters

Masters: **192.168.111.x**. Jump via hypervisor:

```bash
source "$PROXY"
ssh -i ~/.ssh/id_redhat -J "ec2-user@${EC2_PUBLIC_IP}" core@192.168.111.21
```

---

## TNF node replacement — runbook

**Do not interrupt:** the extended test often runs **20–30+ minutes**. Transient Pacemaker / etcd noise is common until pass/fail.

**Rough phases:** destroy/fencing → quorum recovery → VM recreate → BMH/Machine provision → settle.

### Scripts (from `<repo>/openshift-tests`)

| Script | Purpose |
|--------|---------|
| `scripts/run-test.sh` | Generic test runner with captures (default: node replacement). Flags: `--test "<focus>"`, `--repeat N`, `--wait-for-cluster`, `--no-captures`. See `--help`. |
| `scripts/captures/run-all-captures.sh` | **Cluster-side logs:** virsh VM history, Pacemaker/Corosync (both masters), **ovn-chassis-trace** (API + host OVS + **SB Chassis** + gateway annotations + pod placement), continuous OVN/identity logs, **BMO**, **CEO**, Machine API, fencing, and update-job state. Standalone output: `runs/*-<timestamp>.*`. Runners place captures in each run's `captures/` directory under `runs/`; see the layout in `RECOVERY-TESTS-README.md`. Tune OVN sampling with `OVN_CHASSIS_POLL_INTERVAL_SEC`, `OVN_CHASSIS_SB_EVERY`, `OVN_CHASSIS_TRACE_EXTRA_EVERY`, `OVN_CHASSIS_L3GW_EVERY`. |
| `scripts/captures/stop-all-captures.sh` | Pass capture timestamp or uses latest PID file |
| `scripts/captures/capture-*.sh` | Used by `scripts/captures/run-all-captures.sh` |

**Examples:**

```bash
# Single node replacement run
scripts/run-test.sh

# Three runs
scripts/run-test.sh --repeat 3

# Wait for cluster, then run
scripts/run-test.sh --wait-for-cluster

# Different test
scripts/run-test.sh --test "etcd recovery should recover from network disruption"

# Multiple tests, no captures
scripts/run-test.sh --no-captures \
  --test "etcd recovery should recover from network disruption" \
  --test "etcd recovery should recover from graceful node shutdown"
```

**Toolbox Makefile note:** the clean target is **`full-clean`** (hyphen), not `fullclean`.

Runners extract **`openshift-tests`** from the cluster payload by default. To use
an **origin** build (`make openshift-tests`), explicitly set
**`OPENSHIFT_TESTS=/absolute/path/to/openshift-tests`**; it is used unchanged.

### Monitor policy

Some monitor tests are excluded on TNF because fencing and recovery cause known
side effects in etcd. Local runs also exclude monitors with dependencies on CI.

For SNO, TNA, and TNF, the source of truth for supported monitors is each suite's
job and workflow definitions on the main development branch of `openshift/release`.
Follow the job's workflow and inherited test steps:

- <https://github.com/openshift/release/blob/main/ci-operator/config/openshift/origin/openshift-origin-main.yaml>
- <https://github.com/openshift/release/tree/main/ci-operator/step-registry/baremetalds>

These helpers default to the `baremetalds-two-node-fencing-recovery` monitor
settings. Override `OPENSHIFT_TESTS_DISABLE_MONITORS` and
`OPENSHIFT_TESTS_CLUSTER_STABILITY` when matching another suite's configuration.

### Capture cost

`scripts/run-test.sh` enables diagnostic captures by default. Captures add API
polling, SSH sessions, and log I/O, so they can be expensive during recovery.
For quick verification, use `--no-captures`. To collect selected diagnostics, use
`--no-captures` and start only the required `scripts/captures/capture-*.sh`
collectors separately.

`scripts/run-suite.sh` defaults to captures off; enable them with `--with-captures`.

**Stage timing:** successful runs emit `[stage timing] …` in the test log under **`runs/`**.

---

## For AI agents

Source **`proxy.env`** and run **`oc`** before claiming the cluster is unreachable; report real errors.
