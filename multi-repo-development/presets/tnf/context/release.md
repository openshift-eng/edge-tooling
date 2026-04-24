<!-- TNF context: repo's role in the TNF ecosystem. Always distributed as TNF-CONTEXT.md. -->

# release — TNF Context

**Category**: Testing

**Purpose**: OpenShift CI/CD configuration repository for Prow jobs and test workflows

**TNF Relevance**: Central orchestration point for TNF CI testing:
- Prow job configurations for TNF tests
- Step registry workflows for TNF scenarios
- Cluster profiles for TNF testing environments

**How OpenShift CI works**:
- **Prow**: Kubernetes-based CI system that runs jobs
- **ci-operator**: OpenShift-specific test orchestrator
- **Step registry**: Reusable test steps, chains, and workflows
- Workflows compose steps for full test scenarios

**Key TNF paths**:
- `ci-operator/step-registry/baremetalds/two-node/fencing/` - TNF step registry workflows:
  - `baremetalds-two-node-fencing-workflow.yaml` - Main TNF workflow
  - `extended/` - Extended test workflow
  - `techpreview/` - Tech preview workflow
  - `upgrade/` - Upgrade workflow
  - `post-install/` - Post-install validation and node degradation tests
- `ci-operator/jobs/openshift/` - Presubmit/periodic job configurations:
  - `cluster-etcd-operator/` - CEO presubmit jobs
  - `machine-config-operator/` - MCO presubmit jobs
  - `installer/` - Installer presubmit jobs
  - `origin/` - Origin presubmit jobs
