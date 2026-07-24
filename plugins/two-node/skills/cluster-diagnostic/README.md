# TNF Cluster Diagnostic — Getting Started

A Claude Code skill that diagnoses shutdown/recovery issues on Two-Node with
Fencing (TNF) clusters. It gathers live cluster state via SSH, matches it
against tested failure scenarios, and reports findings with severity
classification. Read-only — it never modifies the cluster.

## Install

1. Install [Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview)
   if you haven't already.

2. Add the edge-tooling plugin marketplace:

   ```text
   /plugin marketplace add openshift-eng/edge-tooling
   ```

3. Install the `two-node` plugin:

   ```text
   /plugin install two-node
   ```

## Set Up Cluster Access

The skill connects to your TNF cluster over SSH. Pick whichever access pattern
matches your setup:

### Dev-scripts (hypervisor hop)

```bash
export HYPERVISOR=10.0.0.5   # your EC2 / hypervisor IP
```

Or, if you have the `two-node-toolbox/` submodule checked out, it auto-detects.

### Bare metal (direct SSH)

```bash
export NODE_0=10.1.155.141
export NODE_1=10.1.155.142
```

### Tip: use an env file

Create `~/.tnf-cluster.env` with all your settings to avoid repeating exports:

```bash
export NODE_0=10.1.155.141
export NODE_1=10.1.155.142
export SSH_KEY=~/.ssh/my-key         # defaults to ~/.ssh/id_rsa
export KUBECONFIG=/path/to/kubeconfig

# If your cluster requires a proxy for oc commands, point PROXY_ENV
# at the file instead of sourcing it directly. The diagnostic script
# sources it internally — only for oc calls — so proxy vars don't
# leak into Claude Code's shell and break its API/MCP connections.
export PROXY_ENV=/path/to/auth/proxy.env

# BMC vars — see "Debugging Fencing / BMC Issues" below
export BMC_0=bmc-node0.example.com
export BMC_1=bmc-node1.example.com
export BMC_USER=admin
export BMC_PASS="add BMC password here"
```

Source it before launching Claude Code: `source ~/.tnf-cluster.env`

## Debugging Fencing / BMC Issues

If you're debugging fencing failures, add the BMC variables to your env file
(see above). Without them the skill still runs — it just skips BMC checks.
With them, the
diagnostic tests each layer of the fencing path from each node to its peer's
BMC:

1. **DNS resolution** — can the node resolve the peer BMC hostname?
2. **Network reachability** — can it ping the peer BMC?
3. **Redfish API** — is the BMC's HTTPS endpoint responding?
4. **`fence_redfish --action=status`** — does the actual fence agent work
   end-to-end with the configured credentials?
5. **Power state** — what does the BMC report (On / Off / unknown)?

The skill also checks STONITH configuration, fencing history, and failcounts
from Pacemaker (these don't need BMC vars). Common issues it catches:

- **BMC unreachable from the peer node** — fencing will fail even if the BMC
  responds from your workstation. The test must run from the surviving node.
- **Stale failcount after a transient BMC timeout** — the BMC recovered but
  Pacemaker won't retry fencing until you run `pcs resource cleanup`.
- **STONITH timeout too short** — must exceed `pcmk_delay_base` plus worst-case
  Redfish response time.
- **DNS or VLAN/routing failures** — management network can fail independently
  of the cluster (Corosync) network.

To debug a BMC issue, source your env file and ask in Claude Code:

```text
Can you help debug the BMC not being connected
```

The skill runs the full cluster diagnostic and the BMC sections will show
which layer is failing.

## Usage

### Diagnose a live cluster

```text
/two-node:cluster-diagnostic
```

Connects via SSH, collects pacemaker/etcd/corosync/OCP state, and produces a
diagnostic report with severity-classified findings and a scenario match.

### Validate a proposed procedure

```text
/two-node:cluster-diagnostic validate "cordon all nodes, drain, then shutdown -h 1 on each"
```

Checks your procedure against known failure modes. No SSH required.

### Get a recovery guide

```text
/two-node:cluster-diagnostic recovery-guide full-shutdown
/two-node:cluster-diagnostic recovery-guide standby
/two-node:cluster-diagnostic recovery-guide split-brain
```

Returns the correct step-by-step recovery procedure. No SSH required. Run
without a scenario name to see the full list.

### Interactive training

```text
/two-node:cluster-diagnostic game
```

Practice diagnosing TNF issues in quiz, scenario, or rapid-fire mode. No SSH
required.
