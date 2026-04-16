# Edge Scrum Law: Epics

> The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED", "MAY", and "OPTIONAL" in this document are to be interpreted as described in [RFC 2119](https://datatracker.ietf.org/doc/html/rfc2119).

## Epic Issue Type

| Type | Purpose | Sizing | PR Required |
|---|---|---|---|
| **Epic** | Extra-large work bucket spanning multiple sprints | T-shirt size | N/A |

## Epic Sizing (T-shirt)

| Size | Expected Duration |
|---|---|
| XS | ~1 sprint (consider using a Story instead) |
| S | ~2 sprints |
| M | ~3 sprints |
| L | ~4 sprints |
| XL | ~5 sprints (likely too big; split or create a Feature/Initiative) |

## Epic Workflow States

| State | Meaning |
|---|---|
| Planning | Being refined; stories not yet created |
| To Do | Refinement done; ready for sprint planning |
| In Progress | Stories being actively worked |
| Dev Complete | All implementation PRs merged (docs/QE/CI stories MAY still be open) |
| Closed | All acceptance criteria met |

When an Epic transitions to **Dev Complete**, MUST set the **Fix Version**.  
When transitioning to **In Progress**, MUST set the **Target Version**.  
The **Epic Status** field MUST be set to **Done** when closed (handled by Jira automation).

## Epic Ownership

Stories without an epic are non-compliant. Telco-relevant epics MUST have the **Market Portfolio** field set at the epic level to appear on Telco dashboards.
