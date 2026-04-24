# TNF Debugging Commands

## On TNF Cluster Nodes

### Pacemaker Cluster Status

```bash
sudo pcs status                # Full cluster status (nodes, resources, failures)
sudo pcs config                # Human-readable cluster configuration
sudo pcs resource config       # Resource configuration details
sudo pcs stonith config        # Fencing device configuration
sudo pcs stonith history       # Fence operation history
sudo pcs status corosync       # Corosync membership and quorum
sudo pcs property              # Cluster properties
```

### Fencing / STONITH

```bash
sudo stonith_admin -l          # List STONITH devices
sudo stonith_admin -H          # Show fence history
```

### Fence Agent Debugging

```bash
sudo pcs stonith config              # Show configured fence device and agent
sudo pcs stonith show <device-name>  # Detailed config for a specific device

# Test fence agent manually (safe — queries status only)
sudo fence_redfish --ip=<bmc-ip> --username=<user> --password=<pass> --action=status

# View agent metadata (parameters, actions)
fence_redfish -o metadata

# Test with verbose output
sudo fence_redfish --ip=<bmc-ip> --username=<user> --password=<pass> --action=status --verbose

# For dev-scripts (libvirt) environments
sudo fence_virsh --ip=<hypervisor> --login=<user> --plug=<vm-name> --action=status --use-sudo
```

### Cluster Configuration (Low-Level)

```bash
sudo cibadmin -Q               # Query CIB configuration (XML)
sudo crm_resource -l           # List resources
```

### etcd Container Status

```bash
sudo crictl ps -a | grep etcd  # Check etcd static pod container
sudo podman ps -a | grep etcd  # Check Pacemaker-managed etcd container
```

### Logs

```bash
sudo journalctl -u pacemaker   # Pacemaker daemon logs
sudo journalctl -u corosync    # Corosync daemon logs
sudo cat /var/log/cluster/corosync.log  # Corosync log file
```

## Via oc (From a Machine with kubeconfig)

```bash
oc get nodes                              # Node status
oc get pods -n openshift-etcd             # etcd pods
oc get etcd -o yaml                       # etcd operator CR
oc describe clusteroperator/etcd          # etcd operator status
oc describe clusteroperator/machine-config # MCO status
```

## Diagnostic Commands Reference

### Quick Health Check

```bash
# Run on any TNF node to get a full picture
sudo pcs status && echo "---" && sudo pcs stonith history && echo "---" && sudo podman ps -a | grep etcd
```

### Check Fencing Readiness

```bash
sudo pcs stonith config        # Verify STONITH devices are configured
sudo pcs stonith history       # Check if fencing has been triggered
sudo pcs property | grep stonith  # Verify stonith-enabled=true
```

### Investigate Recovery After Fencing

```bash
# On the surviving node:
sudo pcs status                # Check which node was fenced
sudo pcs stonith history       # When was it fenced
sudo journalctl -u pacemaker --since "10 minutes ago" | grep -i fence
sudo podman logs etcd | tail -50  # Check etcd recovery
```
