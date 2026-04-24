<!-- TNF context: repo's role in the TNF ecosystem. Always distributed as TNF-CONTEXT.md. -->

# pacemaker — TNF Context

**Category**: Troubleshooting

**Purpose**: High-availability cluster resource manager from ClusterLabs (upstream)

**TNF Relevance**: Core component of the RHEL-HA stack that provides:
- Cluster resource management and orchestration
- STONITH/fencing daemon for BMC power operations
- Quorum enforcement and split-brain prevention
- Integration with Corosync for membership and messaging
- Execution of OCF resource agents (like podman-etcd)

**Note**: This is **upstream Pacemaker** included for reference only:
- TNF uses Pacemaker but will **NOT modify it** — changes go to RHEL packages
- Useful for understanding HA internals when troubleshooting
- Also helpful for resource-agents development (understanding how Pacemaker invokes OCF agents)
- In production, Pacemaker comes from RHEL-HA packages, not built from this source

**Key paths** (for troubleshooting/understanding):
- `daemons/fenced/` - STONITH/fencing daemon (executes BMC power operations)
- `daemons/controld/` - CRM controller (handles fencing requests, Corosync events)
- `daemons/controld/controld_fencing.c` - Fencing request handling
- `lib/fencing/` - Fencing API library

**Note**: Pacemaker configuration options (stonith-enabled, no-quorum-policy, etc.) are set automatically by CEO's TNF controller — see `cluster-etcd-operator/pkg/tnf/pkg/pcs/`.

**Build** (for reference):
```bash
./autogen.sh
./configure
make
sudo make install
make check                 # Run all tests
```

**Documentation**: https://clusterlabs.org/pacemaker/doc/
