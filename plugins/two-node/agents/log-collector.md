# Log Collector Agent

Collect relevant logs from the EC2 hypervisor and cluster nodes based on the bug category, rsync them locally, and generate a findings report.

## Inputs

- `{WORKDIR}` — working directory containing `bug-analysis.json`, `deploy-result.json`, `monitor-result.json`
- `{EC2_IP}` — EC2 instance IP
- `{BUG_ID}` — Jira issue key
- `{LOCAL_LOG_DIR}` — local directory for logs (e.g., `/tmp/two-node-bug-reproduce-{BUG_ID}`)
- `{TNT_REPO_DIR}` — path to `two-node-toolbox/` root
- `{BUG_CATEGORIES}` — list of bug categories from analysis

## Instructions

### 1. Read Previous Phase Outputs

Read `{WORKDIR}/bug-analysis.json` and `{WORKDIR}/monitor-result.json` to understand:
- What bug we're looking for and its categories
- Whether it was reproduced
- What manifests were used
- What evidence was collected during monitoring
- What specific logs to prioritize

### 2. Create Log Directories

```bash
mkdir -p {LOCAL_LOG_DIR}
ssh ec2-user@{EC2_IP} "mkdir -p ~/bug-logs"
```

### 3. Collect Base Logs (always)

These logs are collected regardless of bug category:

```bash
ssh ec2-user@{EC2_IP} "bash -c '
KP=\$(ls ~/dev-scripts/ocp/ostest/auth/kubeconfig 2>/dev/null || ls ~/openshift-metal3/dev-scripts/ocp/ostest/auth/kubeconfig 2>/dev/null || ls ~/.kcli/clusters/ostest/auth/kubeconfig 2>/dev/null)
export KUBECONFIG=\$KP

# Installer log (try multiple known locations)
cp ~/dev-scripts/ocp/ostest/.openshift_install.log ~/bug-logs/openshift_install.log 2>/dev/null || \
cp ~/openshift-metal3/dev-scripts/ocp/ostest/.openshift_install.log ~/bug-logs/openshift_install.log 2>/dev/null || \
echo \"WARNING: installer log not found at known paths\" > ~/bug-logs/openshift_install_missing.txt

# Cluster state snapshot
oc get nodes -o wide > ~/bug-logs/nodes.txt 2>&1
oc get mcp -o yaml > ~/bug-logs/mcp.yaml 2>&1
oc get co > ~/bug-logs/clusteroperators.txt 2>&1
oc get clusterversion -o yaml > ~/bug-logs/clusterversion.yaml 2>&1
oc get events --all-namespaces --sort-by=.lastTimestamp > ~/bug-logs/events.txt 2>&1
'"
```

### 4. Collect Category-Specific Logs

Based on `{BUG_CATEGORIES}`, collect targeted logs:

**If `etcd` in categories:**
```bash
ssh ec2-user@{EC2_IP} "bash -c '
export KUBECONFIG=\$(ls ~/dev-scripts/ocp/ostest/auth/kubeconfig 2>/dev/null || ls ~/openshift-metal3/dev-scripts/ocp/ostest/auth/kubeconfig 2>/dev/null || ls ~/.kcli/clusters/ostest/auth/kubeconfig 2>/dev/null)

# etcd operator and pods
oc get co etcd -o yaml > ~/bug-logs/co-etcd.yaml 2>&1
oc get pods -n openshift-etcd -o wide > ~/bug-logs/etcd-pods.txt 2>&1
oc logs -n openshift-cluster-etcd-operator \$(oc get pods -n openshift-cluster-etcd-operator -o name | head -1) > ~/bug-logs/etcd-operator.log 2>&1

# etcd member list, health, and logs via oc on cluster nodes
for node in \$(oc get nodes -l node-role.kubernetes.io/master -o name | sed "s|node/||"); do
  oc debug node/\$node -- chroot /host bash -c "crictl ps --name etcd -q | head -1 | xargs -I{} crictl exec {} etcdctl member list -w table" > ~/bug-logs/etcd-members-\$node.txt 2>&1
  oc debug node/\$node -- chroot /host bash -c "crictl ps --name etcd -q | head -1 | xargs -I{} crictl exec {} etcdctl endpoint health -w table" > ~/bug-logs/etcd-health-\$node.txt 2>&1
  oc debug node/\$node -- chroot /host bash -c "crictl ps --name etcd -q | head -1 | xargs -I{} crictl exec {} etcdctl endpoint status -w table" > ~/bug-logs/etcd-status-\$node.txt 2>&1
  oc debug node/\$node -- chroot /host bash -c "crictl logs \$(crictl ps --name etcd -q | head -1)" > ~/bug-logs/etcd-container-\$node.log 2>&1
done
'"
```

**If `fencing` in categories:**
```bash
ssh ec2-user@{EC2_IP} "bash -c '
export KUBECONFIG=\$(ls ~/dev-scripts/ocp/ostest/auth/kubeconfig 2>/dev/null || ls ~/openshift-metal3/dev-scripts/ocp/ostest/auth/kubeconfig 2>/dev/null || ls ~/.kcli/clusters/ostest/auth/kubeconfig 2>/dev/null)

# Pacemaker status from each master node
for node in \$(oc get nodes -l node-role.kubernetes.io/master -o name | sed \"s|node/||\"); do
  oc debug node/\$node -- chroot /host pcs status > ~/bug-logs/pcs-status-\$node.txt 2>&1
  oc debug node/\$node -- chroot /host pcs stonith status > ~/bug-logs/stonith-status-\$node.txt 2>&1
  oc debug node/\$node -- chroot /host pcs stonith show --full > ~/bug-logs/stonith-config-\$node.txt 2>&1
  oc debug node/\$node -- chroot /host crm_mon -1 -A > ~/bug-logs/crm-mon-\$node.txt 2>&1
  oc debug node/\$node -- chroot /host journalctl -u pacemaker --since \"2 hours ago\" -n 500 > ~/bug-logs/pacemaker-journal-\$node.log 2>&1
  oc debug node/\$node -- chroot /host journalctl -u corosync --since \"2 hours ago\" -n 200 > ~/bug-logs/corosync-journal-\$node.log 2>&1
done

# BMH and metal3 logs
oc get bmh -n openshift-machine-api -o yaml > ~/bug-logs/bmh.yaml 2>&1
oc logs -n openshift-machine-api deployment/metal3 --all-containers > ~/bug-logs/metal3.log 2>&1
'"
```

**If `mco` in categories:**
```bash
ssh ec2-user@{EC2_IP} "bash -c '
export KUBECONFIG=\$(ls ~/dev-scripts/ocp/ostest/auth/kubeconfig 2>/dev/null || ls ~/openshift-metal3/dev-scripts/ocp/ostest/auth/kubeconfig 2>/dev/null || ls ~/.kcli/clusters/ostest/auth/kubeconfig 2>/dev/null)

# MCP and MC details
oc get mcp -o yaml > ~/bug-logs/mcp-all.yaml 2>&1
oc get mc -o name > ~/bug-logs/mc-list.txt 2>&1
oc get co machine-config -o yaml > ~/bug-logs/co-machine-config.yaml 2>&1

# MCO operator logs
MCO_POD=\$(oc get pods -n openshift-machine-config-operator -l k8s-app=machine-config-operator -o name 2>/dev/null | head -1)
if [ -n \"\$MCO_POD\" ]; then
  oc logs -n openshift-machine-config-operator \$MCO_POD > ~/bug-logs/mco-operator.log 2>&1
fi

# Bootstrap config diff from each master node
for node in \$(oc get nodes -l node-role.kubernetes.io/master -o name | sed \"s|node/||\"); do
  oc debug node/\$node -- chroot /host cat /etc/machine-config-daemon/bootstrapconfigdiff > ~/bug-logs/bootstrapconfigdiff-\$node.txt 2>&1
  oc debug node/\$node -- chroot /host journalctl -u machine-config-daemon -n 200 > ~/bug-logs/mcd-\$node.log 2>&1
done
'"
```

**If `nto` in categories:**
```bash
ssh ec2-user@{EC2_IP} "bash -c '
export KUBECONFIG=\$(ls ~/dev-scripts/ocp/ostest/auth/kubeconfig 2>/dev/null || ls ~/openshift-metal3/dev-scripts/ocp/ostest/auth/kubeconfig 2>/dev/null || ls ~/.kcli/clusters/ostest/auth/kubeconfig 2>/dev/null)

# NTO controller logs
NTO_POD=\$(oc get pods -n openshift-cluster-node-tuning-operator -l name=cluster-node-tuning-operator -o name 2>/dev/null | head -1)
if [ -n \"\$NTO_POD\" ]; then
  oc logs -n openshift-cluster-node-tuning-operator \$NTO_POD > ~/bug-logs/nto-controller.log 2>&1
fi

# PerformanceProfile and Tuned status
oc get performanceprofile -o yaml > ~/bug-logs/performanceprofile.yaml 2>&1
oc get tuned -n openshift-cluster-node-tuning-operator -o yaml > ~/bug-logs/tuned.yaml 2>&1

# Kernel args on each master
for node in \$(oc get nodes -l node-role.kubernetes.io/master -o name | sed \"s|node/||\"); do
  oc debug node/\$node -- chroot /host cat /proc/cmdline > ~/bug-logs/cmdline-\$node.txt 2>&1
  oc debug node/\$node -- chroot /host tuned-adm active > ~/bug-logs/tuned-active-\$node.txt 2>&1
done

# Bootkube journal (if bootstrap VM still exists)
BOOTSTRAP_IP=\$(sudo virsh domifaddr ostest_bootstrap 2>/dev/null | grep ipv4 | awk \"{print \\\$4}\" | cut -d/ -f1)
if [ -n \"\$BOOTSTRAP_IP\" ]; then
  if ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o BatchMode=yes -o ConnectTimeout=5 core@\$BOOTSTRAP_IP \"echo OK\" >/dev/null 2>&1; then
    ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null core@\$BOOTSTRAP_IP \"journalctl -u bootkube.service\" > ~/bug-logs/bootkube-journal.log 2>&1
    ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null core@\$BOOTSTRAP_IP \"journalctl -u bootkube.service\" 2>/dev/null | grep -iE \"nto|tuned|node-tuning|performanceprofile\" > ~/bug-logs/bootkube-nto-filtered.log 2>&1
  else
    echo \"WARNING: Cannot SSH to bootstrap VM at \$BOOTSTRAP_IP — skipping bootkube journal collection\" > ~/bug-logs/bootkube-skipped.txt
  fi
fi
'"
```

**If `networking` in categories:**
```bash
ssh ec2-user@{EC2_IP} "bash -c '
export KUBECONFIG=\$(ls ~/dev-scripts/ocp/ostest/auth/kubeconfig 2>/dev/null || ls ~/openshift-metal3/dev-scripts/ocp/ostest/auth/kubeconfig 2>/dev/null || ls ~/.kcli/clusters/ostest/auth/kubeconfig 2>/dev/null)

# OVN and network operator
oc get co network -o yaml > ~/bug-logs/co-network.yaml 2>&1
oc get pods -n openshift-ovn-kubernetes -o wide > ~/bug-logs/ovn-pods.txt 2>&1
oc get pods -n openshift-dns -o wide > ~/bug-logs/dns-pods.txt 2>&1

# OVN logs from each node
for pod in \$(oc get pods -n openshift-ovn-kubernetes -o name 2>/dev/null); do
  podname=\$(echo \$pod | sed \"s|pod/||\")
  oc logs -n openshift-ovn-kubernetes \$pod --all-containers --tail=200 > ~/bug-logs/ovn-\$podname.log 2>&1
done

# DNS logs
for pod in \$(oc get pods -n openshift-dns -o name 2>/dev/null); do
  podname=\$(echo \$pod | sed \"s|pod/||\")
  oc logs -n openshift-dns \$pod --all-containers --tail=200 > ~/bug-logs/dns-\$podname.log 2>&1
done

# Network config
oc get network.config cluster -o yaml > ~/bug-logs/network-config.yaml 2>&1
'"
```

**If `kubelet` in categories:**
```bash
ssh ec2-user@{EC2_IP} "bash -c '
export KUBECONFIG=\$(ls ~/dev-scripts/ocp/ostest/auth/kubeconfig 2>/dev/null || ls ~/openshift-metal3/dev-scripts/ocp/ostest/auth/kubeconfig 2>/dev/null || ls ~/.kcli/clusters/ostest/auth/kubeconfig 2>/dev/null)

# Kubelet logs and config from each node
for node in \$(oc get nodes -o name | sed \"s|node/||\"); do
  oc debug node/\$node -- chroot /host journalctl -u kubelet --since \"1 hour ago\" -n 300 > ~/bug-logs/kubelet-\$node.log 2>&1
  oc debug node/\$node -- chroot /host cat /etc/kubernetes/kubelet.conf > ~/bug-logs/kubelet-conf-\$node.json 2>&1
  oc describe node \$node > ~/bug-logs/describe-node-\$node.txt 2>&1
done
'"
```

**If `installer` in categories:**
```bash
ssh ec2-user@{EC2_IP} "bash -c '
# Full installer log (try multiple known locations)
cp ~/dev-scripts/ocp/ostest/.openshift_install.log ~/bug-logs/openshift_install_full.log 2>/dev/null || \
cp ~/openshift-metal3/dev-scripts/ocp/ostest/.openshift_install.log ~/bug-logs/openshift_install_full.log 2>/dev/null || \
echo \"WARNING: installer log not found at known paths\" > ~/bug-logs/openshift_install_full_missing.txt

# Bootstrap VM logs if accessible
BOOTSTRAP_IP=\$(sudo virsh domifaddr ostest_bootstrap 2>/dev/null | grep ipv4 | awk \"{print \\\$4}\" | cut -d/ -f1)
if [ -n \"\$BOOTSTRAP_IP\" ]; then
  if ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o BatchMode=yes -o ConnectTimeout=5 core@\$BOOTSTRAP_IP \"echo OK\" >/dev/null 2>&1; then
    ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null core@\$BOOTSTRAP_IP \"journalctl -u bootkube.service\" > ~/bug-logs/bootkube-journal.log 2>&1
    ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null core@\$BOOTSTRAP_IP \"journalctl -u kubelet\" > ~/bug-logs/bootstrap-kubelet.log 2>&1
  else
    echo \"WARNING: Cannot SSH to bootstrap VM at \$BOOTSTRAP_IP — skipping bootstrap log collection\" > ~/bug-logs/bootstrap-skipped.txt
  fi
fi
'"
```

**If `upgrade` in categories:**
```bash
ssh ec2-user@{EC2_IP} "bash -c '
export KUBECONFIG=\$(ls ~/dev-scripts/ocp/ostest/auth/kubeconfig 2>/dev/null || ls ~/openshift-metal3/dev-scripts/ocp/ostest/auth/kubeconfig 2>/dev/null || ls ~/.kcli/clusters/ostest/auth/kubeconfig 2>/dev/null)

oc get clusterversion -o yaml > ~/bug-logs/clusterversion-full.yaml 2>&1
oc get co -o yaml > ~/bug-logs/co-all.yaml 2>&1
CVO_POD=\$(oc get pods -n openshift-cluster-version -o name 2>/dev/null | head -1)
if [ -n \"\$CVO_POD\" ]; then
  oc logs -n openshift-cluster-version \$CVO_POD --tail=500 > ~/bug-logs/cvo.log 2>&1
fi
'"
```

### 5. Rsync Logs Locally

```bash
rsync -avz ec2-user@{EC2_IP}:~/bug-logs/ {LOCAL_LOG_DIR}/
```

Verify the sync:
```bash
ls -la {LOCAL_LOG_DIR}/
```

### 6. Generate Findings Report

Read the collected logs and previous phase outputs. Write a findings report to `{TNT_REPO_DIR}/docs/{BUG_ID_LOWER}-findings.md` (lowercase the bug ID, e.g., `ocpbugs-66217-findings.md`).

Report structure:

```markdown
## {BUG_ID} — <Summary from Jira>

### Environment
- OCP <version> (`<release_image>`)
- Install method: <IPI|agent-based> via dev-scripts
- Topology: <arbiter|fencing> (<node details>)
- <any relevant config hints applied>

### Manifests Used
<If any — list manifests with YAML for key ones. If none, state "No custom manifests applied.">

### Result: <Reproduced | Not Reproduced | Inconclusive>

<Evidence from monitoring — exact error messages, status output, etc.>

### Timeline
<Key events from logs with timestamps — installer, bootstrap, operator events>

### Analysis
<What the logs show about the root cause. Reference specific log files and lines.>

### Logs
Saved locally at `{LOCAL_LOG_DIR}/`:
<List of collected log files with brief descriptions>
```

### 7. Write Output

Write `{WORKDIR}/collection-result.json`:

```json
{
  "status": "success|partial|failed",
  "logs_collected": ["list of log files"],
  "local_log_dir": "{LOCAL_LOG_DIR}",
  "findings_report": "{TNT_REPO_DIR}/docs/{BUG_ID_LOWER}-findings.md",
  "bug_reproduced": true|false,
  "summary": "one-line summary of findings"
}
```

## Error Handling

- If SSH fails during collection, save whatever was already collected
- If `oc debug` fails (nodes not ready), skip node-level log collection and note it
- If bootstrap VM is already destroyed, skip bootkube journal collection
- Always write the findings report even with partial data — note what's missing
- If a category-specific collection fails, continue with other categories
- **NEVER destroy or clean the cluster** — the cluster must remain running after log collection so the user can inspect it manually
