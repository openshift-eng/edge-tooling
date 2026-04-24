<!-- TNF context: repo's role in the TNF ecosystem. Always distributed as TNF-CONTEXT.md. -->

# origin — TNF Context

**Category**: Testing

**Purpose**: OpenShift extended test suite and E2E testing framework

**TNF Relevance**: Contains the comprehensive TNF E2E test suite:
- TNF topology validation
- Recovery scenarios after node failures
- Node replacement procedures
- Degraded mode behavior
- Pacemaker/etcd integration testing

**Key TNF paths**:
- `test/extended/two_node/` - Main TNF test directory
  - `tnf_topology.go` - General TNF topology tests
  - `tnf_recovery.go` - Recovery scenario testing
  - `tnf_node_replacement.go` - Node replacement tests
  - `tnf_degraded.go` - Degraded mode testing
- `test/extended/two_node/utils/common.go` - Test utilities

**Running tests**:
- Tests require a **running TNF cluster** — they are E2E tests that interact with real cluster
- Tests are typically run via CI (see `release` repo for job configurations)
- For local execution, need `KUBECONFIG` pointing to a TNF cluster

**Test execution**:
```bash
openshift-tests run openshift/two-node              # Run TNF test suite
openshift-tests run openshift/two-node --run "name" # Run specific test
```
