#!/usr/bin/bash
# Source for read-only queries using HYPERVISOR_SSH and VIRSH_LEASE_NETWORK.
# Requires Python 3 and its libvirt bindings on the hypervisor.

# Print the first live DHCP address for hostname $1, or nothing if absent.
# Requires HYPERVISOR_SSH/VIRSH_LEASE_NETWORK; query/SSH errors return nonzero.
resolve_master_ip_from_leases() {
    local remote_command
    printf -v remote_command 'python3 - %q %q' "${VIRSH_LEASE_NETWORK}" "$1"
    "${HYPERVISOR_SSH[@]}" "${remote_command}" <<'PY'
import sys
from contextlib import closing

import libvirt

with closing(libvirt.openReadOnly("qemu:///system")) as connection:
    network = connection.networkLookupByName(sys.argv[1])
    for lease in network.DHCPLeases():
        if lease.get("hostname") == sys.argv[2]:
            print(lease["ipaddr"])
            break
PY
}

# Print a JSON domain inventory with name/UUID/state and disk/MAC details.
# Domain errors stay in JSON; connection/list/SSH failures return nonzero.
libvirt_domain_snapshot() {
    "${HYPERVISOR_SSH[@]}" "python3 -" <<'PY'
import json
from contextlib import closing
from xml.etree import ElementTree

import libvirt

states = {
    libvirt.VIR_DOMAIN_NOSTATE: "unknown",
    libvirt.VIR_DOMAIN_RUNNING: "running",
    libvirt.VIR_DOMAIN_BLOCKED: "blocked",
    libvirt.VIR_DOMAIN_PAUSED: "paused",
    libvirt.VIR_DOMAIN_SHUTDOWN: "shutdown",
    libvirt.VIR_DOMAIN_SHUTOFF: "shut off",
    libvirt.VIR_DOMAIN_CRASHED: "crashed",
    libvirt.VIR_DOMAIN_PMSUSPENDED: "pmsuspended",
}
snapshots = []
with closing(libvirt.openReadOnly("qemu:///system")) as connection:
    for domain in connection.listAllDomains(0):
        snapshot = {}
        try:
            snapshot["name"] = domain.name()
            snapshot["uuid"] = domain.UUIDString()
            snapshot["state"] = domain.state(0)[0]
            snapshot["state_name"] = states.get(snapshot["state"], "unknown")
            root = ElementTree.fromstring(domain.XMLDesc(0))
            snapshot["disks"] = []
            for disk in root.findall("./devices/disk"):
                source = disk.find("source")
                snapshot["disks"].append({
                    "type": disk.get("type"),
                    "device": disk.get("device"),
                    "source": (
                        source.get("file", source.get("dev"))
                        if source is not None else None
                    ),
                })
            snapshot["macs"] = [
                mac.get("address")
                for mac in root.findall("./devices/interface/mac")
                if mac.get("address")
            ]
        except (libvirt.libvirtError, ElementTree.ParseError) as error:
            snapshot["error"] = str(error)
        snapshots.append(snapshot)
snapshots.sort(key=lambda item: item.get("name", ""))
print(json.dumps(snapshots, sort_keys=True))
PY
}
