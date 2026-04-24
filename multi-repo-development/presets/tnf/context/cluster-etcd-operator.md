<!-- TNF context: repo's role in the TNF ecosystem. Always distributed as TNF-CONTEXT.md. -->

# cluster-etcd-operator — TNF Context

**Category**: Development

**Purpose**: Manages etcd scaling during cluster bootstrap and operation, provisions TLS certificates

**TNF Relevance**: **This is the heart of TNF**. Contains the TNF controller code that runs on the cluster after installation and orchestrates the transition to Pacemaker-managed etcd:
- Initializes the Pacemaker cluster configuration
- Transitions etcd management from CEO to RHEL-HA
- Configures fencing using BMC credentials
- Handles the handover of etcd to the podman-etcd OCF agent

**Key paths**:
- `pkg/tnf/` - TNF-specific controllers and utilities
  - `pkg/tnf/operator/starter.go` - TNF operator entry point
  - `pkg/tnf/auth/runner.go` - Authentication phase
  - `pkg/tnf/setup/runner.go` - Setup phase
  - `pkg/tnf/fencing/runner.go` - Fencing configuration phase
  - `pkg/tnf/after-setup/runner.go` - Post-setup phase
  - `pkg/tnf/pkg/pcs/` - Pacemaker integration
    - `cluster.go` - Cluster initialization
    - `etcd.go` - etcd resource configuration
    - `fencing.go` - STONITH/fencing setup
    - `types.go` - Type definitions
  - `pkg/tnf/pkg/config/` - Cluster configuration
  - `pkg/tnf/pkg/etcd/` - etcd management
  - `pkg/tnf/pkg/jobs/` - Job controller
  - `pkg/tnf/pkg/tools/` - Utilities (conditions, secrets, redact, etc.)
- `docs/HACKING.md` - Development guide

**TNF controller phases** (executed in order):
1. **auth** - Handles Pacemaker authentication between nodes (pcsd tokens)
2. **setup** - Initializes Pacemaker cluster, configures resources
3. **fencing** - Configures STONITH with BMC credentials
4. **after-setup** - Post-setup tasks, hands etcd management to Pacemaker

**TNF-related test files**:
- `pkg/tnf/operator/starter_test.go`
- `pkg/tnf/pkg/pcs/fencing_test.go`
- `pkg/tnf/pkg/pcs/types_test.go`
- `pkg/tnf/pkg/config/cluster_test.go`
- `pkg/tnf/pkg/etcd/etcd_test.go`
- `pkg/tnf/pkg/jobs/jobcontroller_test.go`
- `pkg/tnf/pkg/tools/redact_test.go`

**Commands**:
```bash
make build                    # Build binaries
hack/generate.sh              # Regenerate alerts from jsonnet
make test                     # Run tests

# OTE (OpenShift Tests Extension) framework
./cluster-etcd-operator-tests-ext run-suite openshift/cluster-etcd-operator/all
./cluster-etcd-operator-tests-ext run-test "test-name"
./cluster-etcd-operator-tests-ext list suites
```
