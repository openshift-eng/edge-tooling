# CLAUDE.md — fence-agents

## What This Repo Is

A collection of **83 fence agents** — scripts that Pacemaker's STONITH daemon invokes to power-control unresponsive cluster nodes via BMC, hypervisor, or cloud APIs. Each agent implements a standard interface (on/off/reboot/status/monitor/metadata) and communicates with a specific management protocol.

**Upstream project**: ClusterLabs (same org as Pacemaker and resource-agents)
**Language**: Python (most agents), C (kdump, zvm, virt)
**License**: GPL-2.0+ (agents), LGPL-2.1+ (libraries)

## Repository Structure

```text
agents/           # 83 fence agent implementations (one directory each)
  redfish/        #   fence_redfish — Redfish BMC (TNF production)
  virsh/          #   fence_virsh — libvirt VMs (TNF CI/dev)
  ipmilan/        #   fence_ipmilan — IPMI (also symlinked as fence_ilo3/4/5, fence_idrac, fence_imm)
  ...
lib/              # Shared libraries
  fencing.py.py   #   Core library (~1200 lines) — option parsing, action routing, metadata
  fencing_snmp.py.py  # SNMP helpers (PDU agents)
  azure_fence.py.py   # Azure authentication
doc/              # Developer documentation
  fa-dev-guide.md #   How to write a new agent
  FenceAgentAPI.md#   Fence agent API specification
tests/            # Test framework
  fence_testing.py#   Test harness
  devices.d/      #   Device config files for testing
make/             # Build utilities (fencebuild.mk, agentpycheck.mk)
m4/               # Autoconf macros
systemd/          # systemd unit files
```

## Key Agents for OpenShift TNF

| Agent | Protocol | Used In | Path |
|-------|----------|---------|------|
| `fence_redfish` | Redfish REST API | Production baremetal | `agents/redfish/fence_redfish.py` |
| `fence_virsh` | libvirt SSH | Dev clusters (dev-scripts) | `agents/virsh/fence_virsh.py` |
| `fence_ipmilan` | IPMI (ipmitool) | Legacy baremetal | `agents/ipmilan/fence_ipmilan.py` |

`fence_ipmilan` also serves as the base for symlinked agents: `fence_ilo3`, `fence_ilo4`, `fence_ilo5`, `fence_imm`, `fence_idrac` — same code, different defaults.

## Agent Anatomy

Every fence agent implements this contract:

```python
# Required
def get_power_status(conn, options):   # Returns "on" or "off"
def set_power_status(conn, options):   # Executes power action

# Optional
def get_list(conn, options):           # Enumerates outlets/VMs
def reboot_cycle(conn, options):       # Single-command reboot (vs off+on)

def main():
    device_opt = ["ipaddr", "login", "passwd", ...]
    options = check_input(device_opt, process_input(device_opt))
    docs = {"shortdesc": "...", "longdesc": "...", "vendorurl": "..."}
    show_docs(options, docs)
    run_delay(options)
    result = fence_action(None, options, set_power_status, get_power_status, get_list)
    sys.exit(result)
```

The library's `fence_action()` handles: action routing, state verification after power ops, retries, timeout enforcement, and Pacemaker-compatible exit codes.

### Standard Actions

| Action | Purpose | Called By |
|--------|---------|-----------|
| `off` | Power off node (primary fencing operation) | Pacemaker STONITH |
| `on` | Power on node (un-fence) | Pacemaker STONITH |
| `reboot` | Off + On sequence | Pacemaker STONITH |
| `status` | Query current power state | Manual check, monitoring |
| `monitor` | Health check (is BMC reachable?) | Pacemaker resource monitor |
| `list` | Enumerate all managed outlets/VMs | Discovery |
| `metadata` | Output XML parameter description | Pacemaker config |

### Exit Codes

| Code | Constant | Meaning |
|------|----------|---------|
| 0 | `EC_OK` | Success |
| 1 | `EC_GENERIC_ERROR` | Generic error |
| 2 | `EC_BAD_ARGS` | Invalid arguments |
| 3 | `EC_LOGIN_DENIED` | Authentication failure |
| 5 | `EC_TIMED_OUT` | Operation timed out |
| 7 | `EC_WAITING_OFF` | Failed to power off |

## Shared Library (lib/fencing.py.py)

The core library provides:

- **`all_opt` dictionary**: ~100+ parameter definitions with getopt/longopt/help/default/validation
- **`process_input()`**: Parses CLI args or stdin key=value pairs
- **`check_input()`**: Validates, sets defaults, configures logging
- **`fence_action()`**: Orchestrates the full fencing workflow
- **`fence_login()`** / **`fence_logout()`**: pexpect-based SSH/Telnet session management
- **`show_docs()`**: Generates help text or XML metadata for Pacemaker
- **`fspawn`**: Enhanced pexpect.spawn with syslog integration

### Pacemaker Integration

Pacemaker passes parameters via stdin (key=value format):

```text
agent=fence_redfish
ipaddr=bmc.example.com
username=admin
password=secret
port=node1
action=reboot
```

The agent outputs XML metadata (via `--action=metadata`) so Pacemaker knows what parameters to provide.

## Build

```bash
./autogen.sh
./configure
make
```

Source files use `.py.py` extension — autotools substitutes build-time variables (`@PYTHON@`, `@FENCEAGENTSLIBDIR@`, `@RELEASE_VERSION@`) to produce final `.py` scripts. You cannot run agents directly from source without building.

### Other Build Targets

```bash
make install              # Install to /usr/sbin/ (agents) and /usr/share/fence-agents/lib/
make xml-check            # Validate metadata XML against RelaxNG schema
make check                # Run delay-check + xml-check tests
make rpm                  # Build RPM package
```

## Testing

Tests live in `tests/` and use a ConfigObj-based harness:

```ini
# tests/devices.d/example.cfg
name = "Example device"
agent = "/usr/sbin/fence_redfish"
[options]
  login = [ "admin", "--username", "-l" ]
  passwd = [ "secret", "--password", "-p" ]
  ipaddr = [ "10.0.0.1", "--ip", "-a" ]
  action = [ "off" ]
```

The harness (`fence_testing.py`) tests each agent via stdin, short opts, and long opts.

Metadata validation uses `xmllint --relaxng lib/metadata.rng`.

## Key fence_redfish Details

Since this is the production TNF agent:

- **178 lines** — minimal agent, delegates to Redfish REST API
- **Default port**: 443 (HTTPS)
- **Default URI**: `/redfish/v1`
- **Systems URI**: Auto-discovered from `/redfish/v1/Systems`
- **Power actions**: Maps to Redfish ResetType — `ForceOff`, `On`, `ForceRestart`, `Nmi`
- **SSL**: Supports `--ssl-insecure` to skip cert validation
- **Dependencies**: Python `requests` library

## Documentation

- `doc/fa-dev-guide.md` — How to write a new fence agent
- `doc/FenceAgentAPI.md` — API specification (actions, parameters, conventions)
- Agent man pages are auto-generated from metadata XML

## Conventions

- Commit messages: `fence_<name>: description` (agent-specific changes)
- New agents: follow `doc/fa-dev-guide.md`, model after `fence_redfish` (REST) or `fence_virsh` (SSH)
- Parameters: reuse `all_opt` entries before defining custom ones
- All agents must pass `make xml-check` (metadata validation)
