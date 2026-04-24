<!-- TNF context: repo's role in the TNF ecosystem. Always distributed as TNF-CONTEXT.md. -->

# fence-agents — TNF Context

**Category**: Troubleshooting

**Purpose**: Collection of STONITH fence agent scripts that Pacemaker invokes to power-control unresponsive nodes

**TNF Relevance**: These are the scripts that execute the actual BMC power operations during fencing:
- `fence_redfish` is the production agent — talks to Redfish BMC API over HTTPS to power off/on nodes
- `fence_virsh` is used in dev-scripts environments — SSHs to hypervisor and runs `virsh destroy/start`
- `fence_ipmilan` (and symlinks `fence_ilo3/4/5`, `fence_idrac`) handles legacy IPMI hardware
- CEO configures STONITH with the appropriate agent; Pacemaker's `fenced` daemon invokes it
- Understanding agent behavior is essential for debugging fence failures (e.g., retry storms, timeout tuning)

**Key files**:
- `agents/redfish/fence_redfish.py` — Redfish agent (production TNF, 178 lines)
- `agents/virsh/fence_virsh.py` — libvirt agent (dev/CI environments)
- `agents/ipmilan/fence_ipmilan.py` — IPMI agent (also serves fence_ilo3/4/5/idrac/imm)
- `lib/fencing.py.py` — Shared library (~1200 lines): option parsing, action routing, exit codes

**Cross-repo relationships**:
- **cluster-etcd-operator** (CEO) configures STONITH resources with fence agent parameters (BMC address, credentials)
- **pacemaker** `fenced` daemon invokes the fence agent as a subprocess, passing parameters via stdin
- **resource-agents** provides *resource* agents (podman-etcd manages etcd); fence-agents provides *fence* agents (power control) — different layers, both consumed by Pacemaker

**Debugging a fence failure**:

```bash
sudo pcs stonith config        # Show configured fence device and agent
sudo fence_redfish --ip=<bmc-ip> --username=<user> --password=<pass> --action=status
fence_redfish -o metadata      # View agent parameters
```

**Note**: Like pacemaker, this is upstream code included for **reference and debugging**. OpenShift ships RHEL-packaged fence-agents. Patches go through RHEL, not upstream.
