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

### Scripts (from repo root, paths use **`scripts/`** and **`runs/`**)

| Script | Purpose |
|--------|---------|
| `scripts/run-test.sh` | Generic test runner with captures (default: node replacement). Flags: `--test "<focus>"`, `--repeat N`, `--wait-for-cluster`, `--no-captures`. See `--help`. |
| `scripts/run-all-captures.sh` | **Cluster-side logs:** virsh, Pacemaker (both masters), **ovn-chassis-trace** (timestamped: API + host OVS + **SB Chassis** + **virsh dumpxml** summary + l3-gateway-config + nnid tail + grep’d ovn-k tails), **oc logs -f** ovnkube-node + ovnkube-control-plane + network-node-identity, **BMO**, **CEO** → `runs/*-<timestamp>.*` (not openshift-tests). Tune: `OVN_CHASSIS_POLL_INTERVAL_SEC`, `OVN_CHASSIS_SB_EVERY`, `OVN_CHASSIS_VIRSH_EVERY`, `OVN_CHASSIS_IDENTITY_EVERY`, `OVN_CHASSIS_TRACE_EXTRA_EVERY`, `OVN_CHASSIS_L3GW_EVERY`. |
| `scripts/stop-all-captures.sh` | Pass capture timestamp or uses latest PID file |
| `scripts/capture-*.sh` | Used by `run-all-captures.sh` |

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

Build **`openshift-tests`** in **origin**: `make openshift-tests`. Copy to **`tests-bin/`** or set **`OPENSHIFT_TESTS`**.

### Two different things

| | **openshift-tests `--monitor`** | **`run-all-captures.sh`** |
|--|----------------------------------|---------------------------|
| **What** | Extra in-process test observers in openshift-tests | **CEO** + **BMO** + Pacemaker + **OVN chassis trace** + **OVN follow logs** + virsh (shell/`oc logs`) |
| **TNF** | Do not enable dozens of them | **Recommended** for correlating provision/etcd with the NR test |

### Do not use “all monitors” (openshift-tests) for TNF

- **Never** run node replacement with every **`openshift-tests --monitor`**. Wrong for TNF and has **crashed**.
- Use **`run-test.sh`** without mass `--monitor`. Optional: up to **two** extra via **`OPENSHIFT_TESTS_EXTRA_ARGS`**.
- For **CEO / BMO / Pacemaker / hypervisor** context, use **`run-all-captures.sh`** (enabled by default in `run-test.sh`) — that is **not** monitors; it is operational log capture.

**Stage timing:** successful runs emit `[stage timing] …` in the test log under **`runs/`**.

---

## For AI agents

Source **`proxy.env`** and run **`oc`** before claiming the cluster is unreachable; report real errors.
