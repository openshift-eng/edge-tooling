# Edge Scrum Law: Bugs

> The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED", "MAY", and "OPTIONAL" in this document are to be interpreted as described in [RFC 2119](https://datatracker.ietf.org/doc/html/rfc2119).

## Bug Issue Type

| Type | Purpose | Sizing | PR Required |
|---|---|---|---|
| **Bug** | Error, flaw, or fault in software | Always **0 SP** | Usually yes |

All bugs MUST be pointed at **0**. No exceptions.

## OCPBUGS Project

The team owns these components in OCPBUGS:

- `Installer / Single Node OpenShift`
- `Two Node with Arbiter`
- `Two Node Fencing`
- `Logical Volume Manager Storage`

LVMS bugs: MUST set Release Blocker to **Rejected**. LVMS does not block OCP releases.

LVMS bugs do **not** automatically transition from MODIFIED to ON_QA via ART automation. Engineers MUST move LVMS bugs manually.

## Bug Workflow States

| State | Meaning |
|---|---|
| NEW | Reported; not yet triaged |
| ASSIGNED | Triaged; being actively worked (equivalent to In Progress) |
| MODIFIED | Fix merged; awaiting QA |
| ON_QA | In QA verification |
| Verified | QA confirmed fix |
| Closed | Complete |

## OCPBUGS PR Title Format

PR titles MUST follow this format to trigger Jira status automation:

```text
OCPBUGS-<number>: <description>
```

Example: `OCPBUGS-12345: Fix SNO boot failure on low-memory nodes`
