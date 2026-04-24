<!-- TNF context: repo's role in the TNF ecosystem. Always distributed as TNF-CONTEXT.md. -->

# openshift-docs — TNF Context

**Category**: Docs

**Purpose**: Official OpenShift documentation in AsciiDoc format (becomes docs.openshift.com)

**TNF Relevance**: Contains user-facing documentation for TNF installation and operation

**Key paths**:
- `installing/installing_two_node_cluster/` - Two-node cluster installation guides
- `installing/installing_two_node_cluster/installing_tnf/` - TNF-specific docs:
  - `install-tnf.adoc` - Installation guide
  - `install-post-tnf.adoc` - Post-installation tasks
  - `installing-two-node-fencing.adoc` - Fencing setup
- `modules/installation-two-node-*` - Modular content about TNF

**Documentation structure**:
- Uses AsciiDoc (`.adoc`) format
- **Assemblies** (topic directories) include content from **modules** (reusable snippets)
- Modules in `modules/` are included via `include::` directives
- This repo is the source for https://docs.openshift.com
