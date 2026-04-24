<!-- TNF context: repo's role in the TNF ecosystem. Always distributed as TNF-CONTEXT.md. -->

# dev-scripts — TNF Context

**Category**: Development

**Purpose**: Development and testing environment scripts for deploying OpenShift on libvirt VMs with virtualbmc

**TNF Relevance**: Primary tool for creating TNF development and testing clusters locally or in CI:
- Configures libvirt VMs with virtualbmc to simulate baremetal nodes
- Enables full TNF deployment including Redfish-based fencing

**Relationship to two-node-toolbox**:
- `two-node-toolbox` (TNT) **wraps dev-scripts** for simplified deployment
- This repo is for development/modification of the deployment scripts themselves
- When TNT deployments fail, often need to look here to understand what went wrong

**TNF-specific configuration**:

The `AGENT_E2E_TEST_SCENARIO` variable supports TNF scenarios:
- `TNF_IPV4` - TNF cluster with IPv4 networking
- `TNF_IPV6` - TNF cluster with IPv6 networking
- `TNF_IPV4_DHCP` - TNF cluster with IPv4 DHCP
- `TNF_IPV6_DHCP` - TNF cluster with IPv6 DHCP

**Key TNF variables**:
```bash
# Automatically set when NUM_MASTERS=2 and NUM_ARBITERS=0
export ENABLE_TWO_NODE_FENCING="true"

# TNF requires redfish BMC driver (only supported driver for TNF)
export BMC_DRIVER=redfish
```

**TNF scenario VM specs**:
```bash
export NUM_MASTERS=2
export MASTER_VCPU=8
export MASTER_DISK=100
export MASTER_MEMORY=32768
export NUM_WORKERS=0
export ENABLE_TWO_NODE_FENCING="true"
```

**Key TNF code paths**:
- `common.sh` - Auto-detection of TNF topology, TNF scenario settings
- `utils.sh` - `node_map_to_install_config_fencing_credentials()` function generates fencing credentials
- `agent/roles/manifests/templates/install-config_baremetal_yaml.j2` - Jinja2 template for fencing credentials
- `ocp_install_env.sh` - Injects fencing block into install-config

**Commands**:
```bash
make agent                  # Full agent-based installation (TNF)
make agent_requirements     # Install software dependencies
make agent_build_installer  # Build/extract openshift-install
make agent_configure        # Configure network, create manifests
make agent_create_cluster   # Generate ISO and boot VMs
make agent_cleanup          # Remove agent artifacts
make clean                  # Full cleanup (VMs, network, artifacts)
make realclean              # Deep cleanup including cache
make agent_gather           # Collect logs from agent install
```

**MCE deployment for TNF testing**:
```bash
export AGENT_DEPLOY_MCE=true
make agent
```

**Prerequisites**:
- CentOS/RHEL 8+ host with at least 64GB RAM (32GB minimum)
- Libvirt and virtualbmc for VM management
- `CI_TOKEN` from console-openshift-console.apps.ci.l2s4.p1.openshiftapps.com
- Pull secret from cloud.redhat.com
