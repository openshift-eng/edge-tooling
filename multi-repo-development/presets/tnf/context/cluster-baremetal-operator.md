<!-- TNF context: repo's role in the TNF ecosystem. Always distributed as TNF-CONTEXT.md. -->

# cluster-baremetal-operator — TNF Context

**Category**: Development

**Purpose**: Deploys and manages baremetal server provisioning components (metal3.io)

**TNF Relevance**: Manages bare metal host provisioning. For TNF, BMO must avoid power-management conflicts with Pacemaker fencing on control-plane nodes (Pacemaker handles fencing, not BMO).

**Note**: No TNF-specific code exists in this repo. The TNF relationship is purely about awareness — CBO/BMO should not attempt to power-manage control-plane nodes that are under Pacemaker's control.

**Key paths**:
- `api/v1alpha1/provisioning_types.go` - Provisioning CR definitions
- `config/crd/bases/metal3.io_provisionings.yaml` - CRD definition

**Commands**:
```bash
make build      # Build operator
make test       # Run tests
make generate   # Generate manifests
make deploy     # Deploy to cluster
```
