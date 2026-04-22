# Cluster Deployer Agent

Deploy an OpenShift cluster on the target EC2 hypervisor using dev-scripts via the Two-Node Toolbox (TNT) Ansible playbooks.

## Inputs

- `{WORKDIR}` — working directory containing `bug-analysis.json` and optionally `manifests/`
- `{TOPOLOGY}` — `arbiter` or `fencing`
- `{RELEASE_IMAGE}` — full release image URI (e.g., `quay.io/openshift-release-dev/ocp-release:4.20.14-x86_64`)
- `{EC2_IP}` — EC2 instance IP from inventory.ini
- `{TNT_DEPLOY_DIR}` — path to `two-node-toolbox/deploy/openshift-clusters/`
- `{CONFIG_HINTS}` — JSON string of config hints from bug analysis
- `{MANIFEST_PHASE}` — `day-0`, `day-1`, `unknown`, or `null`
- `{INSTALL_METHOD}` — `ipi`, `agent`, or `kcli` (default: `ipi`)

## Instructions

### 1. Read Bug Analysis

Read `{WORKDIR}/bug-analysis.json` to understand what configuration is needed for this bug.

### 1a. Check for Existing Cluster

Before deploying, check if a previous cluster is still running on the EC2:

```bash
ssh ec2-user@{EC2_IP} "sudo virsh list --all 2>/dev/null | grep -c ostest || echo 0"
```

If VMs named `ostest_*` exist, a previous cluster is present. Check if kubeconfig exists:
```bash
ssh ec2-user@{EC2_IP} "ls ~/dev-scripts/ocp/ostest/auth/kubeconfig 2>/dev/null || ls ~/openshift-metal3/dev-scripts/ocp/ostest/auth/kubeconfig 2>/dev/null || ls ~/.kcli/clusters/ostest/auth/kubeconfig 2>/dev/null && echo EXISTS || echo NONE"
```

If a previous cluster exists:
1. Write a warning in the output: `"previous_cluster_detected": true`
2. Run cleanup before deploying:
```bash
ssh ec2-user@{EC2_IP} "cd ~/dev-scripts && make clean 2>/dev/null; cd ~/openshift-metal3/dev-scripts && make clean 2>/dev/null; kcli delete cluster ostest -y 2>/dev/null; true"
```
3. Wait 30 seconds for cleanup to complete, then verify no VMs remain:
```bash
ssh ec2-user@{EC2_IP} "sudo virsh list --all 2>/dev/null | grep ostest || echo CLEAN"
```

### 2. Update Config File

The config file is at `{TNT_DEPLOY_DIR}/roles/dev-scripts/install-dev/files/config_{TOPOLOGY}.sh`. These config files are **gitignored** (local-only, never committed) — they are the user's working copies. Edit them directly.

If the config file does not exist, read the other topology's config file as a reference for the expected format, and create `config_{TOPOLOGY}.sh` with the correct values.

#### Required updates:

**Always set:**
- `OPENSHIFT_RELEASE_IMAGE` and `OPENSHIFT_INSTALL_RELEASE_IMAGE_OVERRIDE` → `{RELEASE_IMAGE}`

**Topology-specific defaults (ensure these are correct):**

For `arbiter`:
```bash
export NUM_MASTERS=2
export NUM_WORKERS=0
export NUM_ARBITERS=1
export MASTER_MEMORY=32768
export MASTER_VCPU=8
export MASTER_DISK=100
export ARBITER_MEMORY=16384
export ARBITER_VCPU=2
```

For `fencing`:
```bash
export NUM_MASTERS=2
export NUM_WORKERS=0
export NUM_ARBITERS=0
export MASTER_MEMORY=32768
export MASTER_VCPU=8
export MASTER_DISK=100
export BMC_DRIVER=redfish
```

#### Apply config hints from bug analysis:

Check `{CONFIG_HINTS}` and apply any overrides. Common hints:

| Hint | Config change |
|------|--------------|
| `ENABLE_WORKLOAD_PARTITIONING: true` | `export ENABLE_WORKLOAD_PARTITIONING=true` |
| `MASTER_VCPU: N` | `export MASTER_VCPU=N` |
| `MASTER_MEMORY: N` | `export MASTER_MEMORY=N` |
| `IP_STACK: v6` | `export IP_STACK="v6"` |
| `FEATURE_SET: X` | `export FEATURE_SET="X"` |
| `FIPS_MODE: true` | `export FIPS_MODE=true` |
| `BMC_DRIVER: ipmi` | `export BMC_DRIVER=ipmi` |
| `ENABLE_LOCAL_REGISTRY: true` | `export ENABLE_LOCAL_REGISTRY=true` |
| `VM_EXTRADISKS: true` | `export VM_EXTRADISKS=true` + `VM_EXTRADISKS_LIST` + `VM_EXTRADISKS_SIZE` |
| `NETWORK_TYPE: X` | `export NETWORK_TYPE="X"` |
| `PROVISIONING_NETWORK_PROFILE: Disabled` | `export PROVISIONING_NETWORK_PROFILE="Disabled"` |

#### Manifest-related config:

- If `{MANIFEST_PHASE}` is `day-0` and manifests exist: ensure `ASSETS_EXTRA_FOLDER="/home/ec2-user/extra_dir"` is set
- If manifests include a PerformanceProfile with CPU isolation: ensure `MASTER_VCPU` is at least 8 (need enough cores for isolated + reserved)
- If no day-0 manifests: ensure `ASSETS_EXTRA_FOLDER` is commented out or removed

#### Keep unchanged:
- `CI_TOKEN` — user's existing token
- `SSH_PUB_KEY` — user's existing key
- `CI_SERVER` — unless specifically needed
- `OPENSHIFT_CI` — unless specifically needed

### 3. Handle Manifests

If `manifests` is empty in the bug analysis, skip this step entirely.

**If `{MANIFEST_PHASE}` is `day-0`** — upload to EC2 before deployment:

Manifests must be on the EC2 **before** running the Ansible playbook, because the playbook runs `make all` which calls `openshift-install` — that's when `ASSETS_EXTRA_FOLDER` is read and manifests are injected into the cluster.

```bash
ssh ec2-user@{EC2_IP} "mkdir -p ~/extra_dir"
scp {WORKDIR}/manifests/*.yaml ec2-user@{EC2_IP}:~/extra_dir/
```

Verify the upload:
```bash
ssh ec2-user@{EC2_IP} "ls -la ~/extra_dir/"
```

**If `{MANIFEST_PHASE}` is `day-1` or `unknown`** — do NOT upload before deployment. Manifests will be applied after the cluster is up by the monitor agent via `oc apply`.

### 4. Deploy Cluster

The TNT repo supports 3 deployment methods with different playbooks and make targets:

| Method | Playbook | Make Target | Config File |
|--------|----------|-------------|-------------|
| **IPI** | `setup.yml` | `make all` | `config_{topology}.sh` → copied as `config_ec2-user.sh` |
| **Agent** | `setup.yml` | `make agent` | `config_{topology}.sh` → copied as `config_ec2-user.sh` |
| **kcli** | `kcli-install.yml` | `kcli create cluster` | `vars/kcli.yml` (different config system) |

The playbook handles everything: clones dev-scripts on EC2 (IPI/agent), copies the config file, runs requirements, then the make target. After deployment it sets up a Squid proxy, copies auth files to `~/auth/`, and updates the inventory with cluster VM IPs.

**For IPI (default):**
```bash
cd {TNT_DEPLOY_DIR} && ansible-playbook setup.yml -e "topology={TOPOLOGY}" -e "interactive_mode=false" -i inventory.ini
```

**For agent-based:**
```bash
cd {TNT_DEPLOY_DIR} && ansible-playbook setup.yml -e "topology={TOPOLOGY}" -e "method=agent" -e "interactive_mode=false" -i inventory.ini
```

**For kcli:**
```bash
cd {TNT_DEPLOY_DIR} && ansible-playbook kcli-install.yml -e "topology={TOPOLOGY}" -e "interactive_mode=false" -i inventory.ini
```

**Key differences between methods:**
- Agent method adds qemu ACL setup (read+execute permissions on working dir) before running `make agent`
- kcli method uses a completely different deployment path (kcli binary, not dev-scripts), with its own config system (`vars/kcli.yml`), and has its own kubeconfig path (`~/.kcli/clusters/ostest/auth/kubeconfig`)
- For fencing topology, kcli uses `ksushy` BMC simulator instead of real redfish

This is a long-running operation (typically 45-90 minutes).

#### Deployment execution with monitoring

Run the `ansible-playbook` command **in the background** and monitor the deployment periodically:

```bash
# Start deployment in background, log output to a file
cd {TNT_DEPLOY_DIR} && ansible-playbook <playbook> <args> > {WORKDIR}/deploy.log 2>&1 &
DEPLOY_PID=$!
echo $DEPLOY_PID > {WORKDIR}/deploy.pid
```

**Every 10 minutes**, while the deployment is still running, check for signs of progress or failure. Use the saved PID file (not a shell variable) since monitoring checks may run in separate shell invocations:

```bash
# Check if ansible-playbook is still running
DEPLOY_PID=$(cat {WORKDIR}/deploy.pid 2>/dev/null)
kill -0 $DEPLOY_PID 2>/dev/null && echo "RUNNING" || echo "FINISHED"

# Check last 20 lines of deploy log for errors or progress
tail -20 {WORKDIR}/deploy.log

# Check if VMs are being created on EC2
ssh ec2-user@{EC2_IP} "sudo virsh list --all 2>/dev/null | grep ostest; echo '---'; uptime" 2>/dev/null
```

**Early failure detection** — stop waiting and report failure immediately if you see:

| Signal in deploy.log | Meaning | Auto-fixable? |
|---------------------|---------|---------------|
| `fatal:` + `UNREACHABLE` | SSH to EC2 lost | No — EC2 may be down |
| `CI_TOKEN` / `unauthorized` / `401` | CI token expired or invalid | No — user must update token |
| `make requirements` + `FAILED` | Dependency install failed | Maybe — transient network error, retry may work |
| `make all` + `FAILED` | Dev-scripts installation failed | Check further (see below) |
| `No space left on device` | EC2 disk full | No — need larger instance |
| `openshift-install` + `level=fatal` | Installer crashed | Check error message |
| No new output for 20+ minutes | Deployment stalled | Yes — kill and retry |

If the deployment process finishes (PID no longer running), confirm it has exited:
```bash
DEPLOY_PID=$(cat {WORKDIR}/deploy.pid 2>/dev/null)
while kill -0 $DEPLOY_PID 2>/dev/null; do sleep 10; done
echo "Deployment process $DEPLOY_PID has exited"
```

**If deployment failed**, also check the installer log on EC2 for more detail:
```bash
ssh ec2-user@{EC2_IP} "tail -30 ~/dev-scripts/ocp/ostest/.openshift_install.log 2>/dev/null || tail -30 ~/openshift-metal3/dev-scripts/ocp/ostest/.openshift_install.log 2>/dev/null"
```

**Hard timeout: 120 minutes.** If the deployment is still running after 120 minutes, kill it:
```bash
kill $(cat {WORKDIR}/deploy.pid 2>/dev/null) 2>/dev/null
```
Write `status: failed` with `error: "Deployment timed out after 120 minutes"`.

**Classify the failure** in the output:
- `failure_category`: `"timeout"`, `"ssh_lost"`, `"ci_token"`, `"disk_full"`, `"installer_crash"`, `"requirements_failed"`, `"stalled"`, `"unknown"`
- `auto_fixable`: `true` if a retry is likely to help (e.g., transient network error, stalled process), `false` otherwise
- `last_log_lines`: last 50 lines of `deploy.log` for diagnosis

Report the exit code in the output.

### 4a. Post-Deployment: Fencing Configuration (fencing topology only)

If `{TOPOLOGY}` is `fencing`, stonith fencing may need to be configured after deployment. For IPI/agent deployments on OCP 4.19+, the `setup.yml` playbook handles this automatically in its post_tasks. For kcli, the `kcli-install.yml` playbook calls the kcli-redfish role.

If fencing was NOT configured automatically (e.g., non-interactive mode skipped it), run:
```bash
cd {TNT_DEPLOY_DIR} && ansible-playbook redfish.yml -i inventory.ini
```

This discovers BareMetalHost resources and creates PCS stonith resources using `fence_redfish`.

### 5. Write Output

Write `{WORKDIR}/deploy-result.json`:

```json
{
  "status": "success|failed",
  "topology": "{TOPOLOGY}",
  "release_image": "{RELEASE_IMAGE}",
  "install_method": "ipi|agent|kcli",
  "previous_cluster_detected": true|false,
  "previous_cluster_cleaned": true|false,
  "manifests_uploaded": ["list of uploaded manifest files or empty"],
  "manifest_phase": "day-0|day-1|unknown|null",
  "config_changes": {"key": "value pairs of config overrides applied"},
  "deploy_start_time": "ISO8601 timestamp",
  "deploy_end_time": "ISO8601 timestamp",
  "duration_minutes": 65,
  "monitoring_checks": 6,
  "exit_code": 0,
  "error": "error message if failed",
  "failure_category": "timeout|ssh_lost|ci_token|disk_full|installer_crash|requirements_failed|stalled|unknown",
  "auto_fixable": false,
  "last_log_lines": "last 50 lines of deploy.log if failed"
}
```

## Error Handling

- If SSH to EC2 fails, write error and stop — EC2 may not be running
- If `make requirements` fails with token errors, check if `CI_TOKEN` is valid
- If ansible-playbook fails, capture the last 50 lines of output for diagnosis
- If ansible-playbook times out (exit code 124), report `error: "Deployment timed out after 120 minutes"` — the orchestrator will offer clean-and-retry
- If config file doesn't exist and can't be created, stop with clear error
- If previous cluster cleanup fails, report but still attempt deployment — `make all` may handle it

**IMPORTANT: Do NOT destroy the cluster after a successful deployment.** The cluster must remain running for subsequent phases (monitoring, reproduction, log collection) and for the user to inspect.

**TNT FILE SAFETY: Only modify `config_{TOPOLOGY}.sh` (and `vars/kcli.yml` for kcli).** Do NOT modify `inventory.ini`, playbooks, roles, Makefile, or any other TNT repo file. You may run `ansible-playbook` commands and `ssh`/`scp` to EC2, but never `make create`, `make init`, `make inventory`, `make destroy`, or `./configure`.
