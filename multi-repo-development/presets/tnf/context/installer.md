<!-- TNF context: repo's role in the TNF ecosystem. Always distributed as TNF-CONTEXT.md. -->

# installer — TNF Context

**Category**: Development

**TNF Relevance**: Main repo for **standalone Agent-Based Installation (ABI)** without MCE/ACM:
- Reads install-config.yaml with fencing credentials
- Generates ignition configs for the cluster
- Handles bootstrap process where one node serves as temporary bootstrap
- Contains its own fencing credentials handling for ABI flow

**Key TNF paths**:
- `pkg/asset/agent/` - Agent-Based Installer implementation
- `pkg/asset/agent/manifests/fencingcredentials.go` - Fencing credentials handling for ABI
- `pkg/types/validation/installconfig.go` - Install config validation
- `pkg/types/machinepools.go` - Machine pool definitions

**Note**: Two installation paths exist for TNF:
1. **Standalone ABI** (this repo) - Direct installation without MCE/ACM, uses `openshift-install` directly
2. **MCE/ACM with assisted-service** - Uses assisted-service to orchestrate installation

Both paths require fencing credentials in install-config.yaml.

**TNF-related test files**:
- `pkg/asset/agent/manifests/fencingcredentials_test.go`
- `pkg/asset/agent/installconfig_test.go`
- `pkg/types/validation/installconfig_test.go`
- `pkg/asset/machines/master_test.go`

**Commands**:
```bash
hack/build.sh                        # Build installer
bin/openshift-install create cluster # Create cluster
openshift-install destroy cluster    # Destroy cluster
```
