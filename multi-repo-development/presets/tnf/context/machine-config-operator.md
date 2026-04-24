<!-- TNF context: repo's role in the TNF ecosystem. Always distributed as TNF-CONTEXT.md. -->

# machine-config-operator — TNF Context

**Category**: Development

**Purpose**: Manages operating system configuration and updates (systemd, cri-o/kubelet, kernel, NetworkManager, etc.)

**TNF Relevance**: Prepares nodes for Pacemaker **BEFORE** CEO TNF controller runs (Day 1 setup):
- Directory structure for PCS and Corosync (`/var/lib/pcsd`, `/var/lib/corosync`, `/var/log/pcsd`, `/var/log/cluster`)
- Systemd units to enable and start PCSD service
- Fencing validator script for cluster health checking
- Installs HA packages via rpm-ostree extensions

**Key TNF paths**:
- `templates/master/00-master/two-node-with-fencing/` - TNF-specific templates
  - `units/ha-00-directories.service.yaml` - Creates directories for PCS/Corosync
  - `units/ha-01-enable-services.service.yaml` - Enables and starts PCSD
  - `files/fencing-validator.yaml` - Fencing validation script
  - `extensions/two-node-ha` - MCO extension trigger file

**MCO Extensions concept**: Extensions install RPM packages on RHCOS via rpm-ostree:
- The `two-node-ha` extension installs pacemaker, corosync, pcs, fence-agents, and related packages
- Extensions are triggered by filename presence (the file content doesn't matter, just the file existing)
- This happens during node configuration, before CEO runs

**TNF-related test files**:
- `test/e2e-2of2/extension_test.go`
- `pkg/daemon/update_test.go`
- `pkg/controller/common/helpers_test.go`

**Commands**:
```bash
make build        # Build binaries
make test-unit    # Run unit tests
make verify       # Run verification checks
```

**Inspecting MCO in a cluster**:
```bash
oc describe clusteroperator/machine-config
oc describe machineconfigpool
```
