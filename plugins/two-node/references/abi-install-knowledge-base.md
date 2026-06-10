# TNF Agent-Based Install Knowledge Base

Reference document for TNF cluster installation via Agent-Based Installer (ABI)
on HPE ProLiant e920t bare metal hardware. Procedures validated on OCP 4.22
release candidates.

Source: `~/Projects/agent-based-install/BARE_METAL_TNF_INSTALL.md`

## TNF Installation Facts

### TNF requires exactly 2 control-plane replicas

`install-config.yaml` must specify `controlPlane.replicas: 2` and
`compute[0].replicas: 0`. Any other combination is not a TNF topology.

### TNF requires TechPreviewNoUpgrade

The `featureSet: TechPreviewNoUpgrade` field is mandatory in
`install-config.yaml` for TNF in OCP 4.20-4.22. This disables in-place
upgrades — a fresh install is required to move between versions.

### Fencing credentials are defined at install time

The `controlPlane.fencing.credentials` section in `install-config.yaml`
provides BMC (Redfish) credentials for each node. The cluster-etcd-operator
(CEO) uses these to configure Pacemaker STONITH resources during installation.

### The installer consumes config files

`openshift-install agent create image` deletes `install-config.yaml`,
`agent-config.yaml`, and the `openshift/` manifests directory after generating
the ISO. Always keep backups in a separate location.

### Nightly vs RC/GA builds require different handling

| Build type | Registry | Needs `OPENSHIFT_INSTALL_RELEASE_IMAGE_OVERRIDE`? | Needs insecure container policy MC? |
|---|---|---|---|
| Nightly | registry.ci.openshift.org | No (if pull secret has CI creds) | **Yes** (images unsigned) |
| RC / GA | quay.io | **Yes** — override to quay.io digest | No (images signed) |

The `OPENSHIFT_INSTALL_RELEASE_IMAGE_OVERRIDE` env var must be set for ALL
`openshift-install` commands (create image, wait-for, etc.), not just the
first one.

### EFI boot entries override Redfish virtual media

A previous RHCOS install writes a UEFI boot entry for `shimx64.efi` that has
higher priority than the Redfish `BootSourceOverrideTarget:Cd`. Without
removing it, the node boots the old OS instead of the ISO. This is the most
common cause of "nodes won't boot from ISO" on reinstall.

### HPE iLO Virtual Media uses Slot 2

On HPE iLO, Virtual Media Slot 2 = CD/DVD. Use this slot for ISO mounting.
iLO returns `Base.1.18.Success` inside a JSON `error` wrapper — this is
normal, check for HTTP 200.

## Cluster Under Install

- **Hardware**: HPE ProLiant e920t (bare metal, 2 nodes)
- **Cluster name**: cluster1
- **Base domain**: metal-platform.eng.rdu2.redhat.com
- **Node 1**: e920t-01 (10.1.155.141), rendezvous/bootstrap
- **Node 2**: e920t-02 (10.1.155.142)
- **BMC 1**: e920t-01-ilo.mgmt.cluster1.metal-platform.eng.rdu2.redhat.com
- **BMC 2**: e920t-02-ilo.mgmt.cluster1.metal-platform.eng.rdu2.redhat.com
- **API VIP**: 10.1.155.143
- **Ingress VIP**: 10.1.155.144
- **Machine network**: 10.1.155.128/26
- **Root disk**: /dev/nvme0n1 (both nodes)
- **Active NIC**: ens1f3 (both nodes)
- **ISO HTTP server**: 10.1.235.49:8080

## Install Procedure

### Step 0: Obtain the openshift-install binary

```bash
VERSION=4.22.0-rc.3
WORKDIR=~/Projects/agent-based-install/tnf-abi/$VERSION
mkdir -p "$WORKDIR"
cd "$WORKDIR"

# Extract from release tarball
tar xf openshift-install-linux-${VERSION}.tar

# Verify
./openshift-install version

# For RC builds: set the release image override
export OPENSHIFT_INSTALL_RELEASE_IMAGE_OVERRIDE=quay.io/openshift-release-dev/ocp-release@sha256:<digest>
```

### Step 1: Restore configuration files

```bash
cd "$WORKDIR"
cp ~/Projects/agent-based-install/tnf-abi-backup/install-config.yaml .
cp ~/Projects/agent-based-install/tnf-abi-backup/agent-config.yaml .
```

### Step 2: Wipe disks and EFI boot entries

**Always do this before reinstalling** to avoid EBUSY errors and EFI boot
override issues.

If nodes are reachable via SSH (from a previous install):

```bash
for NODE in 10.1.155.141 10.1.155.142; do
  ssh core@$NODE "
    # Delete RHEL EFI boot entry
    ENTRY=\$(efibootmgr | grep -i 'shimx64\|rhel\|Red Hat' | head -1 | grep -oP 'Boot\K[0-9A-Fa-f]{4}')
    [ -n \"\$ENTRY\" ] && sudo efibootmgr -b \$ENTRY -B

    # Wipe EFI partition and GPT header
    sudo dd if=/dev/zero of=/dev/nvme0n1p2 bs=1M count=10 2>/dev/null
    sudo dd if=/dev/zero of=/dev/nvme0n1 bs=1M count=10 2>/dev/null

    # Wipe disk signatures and partition table
    sudo wipefs -a /dev/nvme0n1
    sudo sgdisk -Z /dev/nvme0n1
  "
done
```

If nodes are NOT reachable, power them off via Redfish:

```bash
for BMC in "https://e920t-01-ilo.mgmt.cluster1.metal-platform.eng.rdu2.redhat.com" \
           "https://e920t-02-ilo.mgmt.cluster1.metal-platform.eng.rdu2.redhat.com"; do
  curl -sk -u $BMC_USER:$BMC_PASS -X POST \
    "$BMC/redfish/v1/Systems/1/Actions/ComputerSystem.Reset" \
    -H "Content-Type: application/json" \
    -d '{"ResetType": "ForceOff"}'
done
```

Then delete EFI entries from the iLO BIOS setup menu on next boot.

### Step 3: Generate cluster manifests

```bash
cd "$WORKDIR"
./openshift-install agent create cluster-manifests --dir .

# For nightly builds only — inject insecure container policy
cp ~/Projects/agent-based-install/tnf-abi-backup/99-master-zz-container-policy-nightly.yaml openshift/
```

The `zz` prefix ensures the nightly policy sorts after `99-master-generated-registries`
(MCO auto-generates this with `sigstoreSigned`). Skip for RC/GA builds.

### Step 4: Generate the agent ISO

```bash
cd "$WORKDIR"
./openshift-install agent create image --dir .
```

Produces:

- `agent.x86_64.iso` (~1.4 GB)
- `auth/kubeconfig`
- `auth/kubeadmin-password`

### Step 5: Copy ISO to HTTP server

```bash
scp "$WORKDIR/agent.x86_64.iso" \
  10.1.235.49:/home/microshift/libvirt/images/x86_64/rhel96/agent.x86_64.iso

# ALWAYS verify — wrong Content-Length means wrong path or permissions
curl -sI http://10.1.235.49:8080/x86_64/rhel96/agent.x86_64.iso | grep Content-Length
# Expected: ~1400000000 (1.4 GB). If 153, the file is missing (nginx 403).
```

### Step 6: Mount ISO and boot via Redfish

```bash
for BMC in "https://e920t-01-ilo.mgmt.cluster1.metal-platform.eng.rdu2.redhat.com" \
           "https://e920t-02-ilo.mgmt.cluster1.metal-platform.eng.rdu2.redhat.com"; do

  # Eject existing virtual media
  curl -sk -u $BMC_USER:$BMC_PASS -X POST \
    "$BMC/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.EjectMedia" \
    -H "Content-Type: application/json" -d '{}'

  # Insert ISO
  curl -sk -u $BMC_USER:$BMC_PASS -X POST \
    "$BMC/redfish/v1/Managers/1/VirtualMedia/2/Actions/VirtualMedia.InsertMedia" \
    -H "Content-Type: application/json" \
    -d '{"Image": "http://10.1.235.49:8080/x86_64/rhel96/agent.x86_64.iso"}'

  # Set one-time boot from CD
  curl -sk -u $BMC_USER:$BMC_PASS -X PATCH \
    "$BMC/redfish/v1/Systems/1/" \
    -H "Content-Type: application/json" \
    -d '{"Boot": {"BootSourceOverrideTarget": "Cd", "BootSourceOverrideEnabled": "Once"}}'

  # Restart
  curl -sk -u $BMC_USER:$BMC_PASS -X POST \
    "$BMC/redfish/v1/Systems/1/Actions/ComputerSystem.Reset" \
    -H "Content-Type: application/json" \
    -d '{"ResetType": "ForceRestart"}'

  echo "Booted: $BMC"
done
```

### Step 7: Monitor installation

```bash
cd "$WORKDIR"
./openshift-install agent wait-for install-complete --dir . --log-level info
```

Expected phases (~60-90 minutes total):

1. Bootstrap Kube API Initialized (~15-30 min)
2. Bootstrap complete
3. Cluster operators converging (~20-40 min)
4. TNF setup jobs — CEO configures Pacemaker, fencing, etcd handover
5. Install complete

### Step 8: Verify

```bash
export KUBECONFIG="$WORKDIR/auth/kubeconfig"

# All nodes Ready
oc get nodes

# All cluster operators healthy
oc get co

# Pacemaker status
ssh core@10.1.155.141 "sudo pcs status"

# Fencing active
ssh core@10.1.155.141 "sudo pcs stonith status"

# etcd managed by Pacemaker
ssh core@10.1.155.141 "sudo pcs resource status etcd-clone"
```

## Reinstall (short version)

```bash
VERSION=4.22.0-rc.3
WORKDIR=~/Projects/agent-based-install/tnf-abi/$VERSION
cd "$WORKDIR"

# For RC builds:
export OPENSHIFT_INSTALL_RELEASE_IMAGE_OVERRIDE=quay.io/openshift-release-dev/ocp-release@sha256:<digest>

# 1. Wipe disks and EFI (see Step 2)

# 2. Clean and regenerate
rm -f agent.x86_64.iso .openshift_install.log .openshift_install_state.json rendezvousIP
rm -rf auth openshift mirror
cp ~/Projects/agent-based-install/tnf-abi-backup/install-config.yaml .
cp ~/Projects/agent-based-install/tnf-abi-backup/agent-config.yaml .
./openshift-install agent create cluster-manifests --dir .
# For nightly: cp ~/Projects/agent-based-install/tnf-abi-backup/99-master-zz-container-policy-nightly.yaml openshift/
./openshift-install agent create image --dir .

# 3. Copy ISO and verify
scp agent.x86_64.iso 10.1.235.49:/home/microshift/libvirt/images/x86_64/rhel96/agent.x86_64.iso
curl -sI http://10.1.235.49:8080/x86_64/rhel96/agent.x86_64.iso | grep Content-Length

# 4. Mount and boot (see Step 6)

# 5. Monitor
./openshift-install agent wait-for install-complete --dir . --log-level info
```

## Troubleshooting

### Nodes boot old OS instead of ISO

The RHEL UEFI boot entry (`shimx64.efi`) has higher priority than Redfish
`BootSourceOverrideTarget:Cd`. Fix:

1. SSH to node: `efibootmgr -b <id> -B`
2. Wipe EFI partition: `dd if=/dev/zero of=/dev/nvme0n1p2 bs=1M count=10`
3. Wipe GPT header: `dd if=/dev/zero of=/dev/nvme0n1 bs=1M count=10`
4. Power off via Redfish, re-mount ISO, boot from CD

### Bootstrap does not start

- Verify ISO is mounted: check Redfish virtual media status
- Verify network: nodes must reach each other and API/Ingress VIPs
- Check rendezvousIP matches node 1's actual IP on ens1f3

### ISO on HTTP server returns 403 or Content-Length: 153

SCP went to wrong path or wrong permissions. Re-SCP and verify destination
matches nginx config.

### Node pulling from wrong registry

ISO was built without `OPENSHIFT_INSTALL_RELEASE_IMAGE_OVERRIDE`. Regenerate
with the override set.

### EBUSY errors during install

Disk was not wiped before reinstall. See Step 2.

### TNF setup job fails with STONITH "not running on any node"

Can be transient — CEO retries. Check `oc get jobs -n openshift-etcd`.

## Configuration Reference

### install-config.yaml (TNF-specific fields)

```yaml
featureSet: TechPreviewNoUpgrade
controlPlane:
  replicas: 2
  fencing:
    credentials:
    - hostname: <node-fqdn>
      username: <bmc-user>
      password: <bmc-pass>
      address: redfish+https://<bmc-fqdn>/redfish/v1/Systems/1
      certificateVerification: Disabled
compute:
- replicas: 0
platform:
  baremetal:
    apiVIPs:
    - <api-vip>
    ingressVIPs:
    - <ingress-vip>
```

### agent-config.yaml

```yaml
apiVersion: v1beta1
kind: AgentConfig
metadata:
  name: <cluster-name>
rendezvousIP: <node-1-ip>
hosts:
- hostname: <node-1-fqdn>
  role: master
  rootDeviceHints:
    deviceName: /dev/nvme0n1
  interfaces:
  - macAddress: <node-1-mac>
    name: <nic-name>
- hostname: <node-2-fqdn>
  role: master
  rootDeviceHints:
    deviceName: /dev/nvme0n1
  interfaces:
  - macAddress: <node-2-mac>
    name: <nic-name>
```

## Cluster Access (post-install)

| Method | Command |
|--------|---------|
| SSH | `ssh core@10.1.155.141` / `ssh core@10.1.155.142` |
| oc CLI | `export KUBECONFIG=$WORKDIR/auth/kubeconfig` |
| Web console | `https://console-openshift-console.apps.cluster1.metal-platform.eng.rdu2.redhat.com` |
| BMC (iLO) | `https://e920t-01-ilo.mgmt.cluster1.metal-platform.eng.rdu2.redhat.com` |
