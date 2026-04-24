<!-- TNF context: repo's role in the TNF ecosystem. Always distributed as TNF-CONTEXT.md. -->

# api — TNF Context

**Category**: Development

**TNF Relevance**: Defines the `DualReplica` FeatureGate that controls TNF API availability across cluster profiles:
- FeatureGate definition in `features/features.go` gates TNF-specific APIs
- CRD manifests for `PacemakerCluster` resource (used by CEO's TNF controller)
- Feature set configuration (Default, TechPreview, DevPreview) per cluster profile (Hypershift, SelfManagedHA)

**Key TNF paths**:
- `features/features.go` - `FeatureGateDualReplica` definition
- `etcd/v1alpha1/` - PacemakerCluster API types and CRD manifests
- `features.md` - Generated feature gate status matrix (do NOT edit directly)

**Important**: This repo has its own `CLAUDE.md` with detailed guidance on API conventions, testing framework, FeatureGate patterns, and the code generation workflow. **That file takes priority over this one.**
