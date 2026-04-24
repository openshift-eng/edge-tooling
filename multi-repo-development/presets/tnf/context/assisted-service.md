<!-- TNF context: repo's role in the TNF ecosystem. Always distributed as TNF-CONTEXT.md. -->

# assisted-service — TNF Context

**Category**: Development

**TNF Relevance**: Core service orchestrating TNF cluster installation via MCE/ACM:
- Validates TNF cluster requirements (2 CP nodes, no arbiters, OCP >= 4.20)
- Collects and stores fencing credentials (BMC username/password/address) per host
- Generates install-config with fencing credentials for the installer
- Handles TNF-specific network connectivity groups (minimum 2 hosts instead of 3)

**Note**: This repo is also used as part of the **Agent Based Installation (ABI)** flow when installing via MCE/ACM. For standalone ABI without MCE/ACM, see the `installer` repository.

**Key TNF code paths**:
- `internal/common/common.go` - `IsClusterTopologyTwoNodesWithFencing()` detection logic
- `internal/cluster/validator.go` - TNF cluster validation
- `internal/cluster/transition.go` - State machine handling for TNF clusters
- `internal/installcfg/installcfg.go` - `Fencing` and `FencingCredential` structs
- `internal/installcfg/builder/builder.go` - Generates install-config with fencing data
- `internal/featuresupport/features_misc.go` - TNF feature support level checks
- `models/fencing_credentials_params.go` - BMC credential model
- `docs/enhancements/tnf-clusters.md` - Assisted-service TNF enhancement doc

**TNF-related test files**:
- `internal/common/common_test.go`
- `internal/cluster/validator_test.go`
- `internal/cluster/transition_test.go`
- `internal/installcfg/builder/builder_test.go`
- `internal/host/host_test.go`
- `internal/bminventory/inventory_test.go`
- `internal/provider/baremetal/installConfig_test.go`
- `internal/controller/controllers/agent_controller_test.go`
- `internal/controller/controllers/bmh_agent_controller_test.go`
- `cmd/agentbasedinstaller/host_config_test.go`
- `subsystem/kubeapi/kubeapi_test.go`

**Key constants** (from `internal/common/common.go`):
- `MinimumVersionForTwoNodesWithFencing = "4.20"`
- `AllowedNumberOfMasterHostsInTwoNodesWithFencing = 2`

**Commands**:
```bash
skipper make all                    # Build everything
skipper make build-minimal          # Build binary only
skipper make generate-from-swagger  # Regenerate after API changes
skipper make unit-test              # Run unit tests (requires Docker/Podman)
skipper make subsystem-test         # Run subsystem tests
```
