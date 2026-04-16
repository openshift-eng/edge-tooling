# Edge Scrum Law: Key Roles

> The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED", "MAY", and "OPTIONAL" in this document are to be interpreted as described in [RFC 2119](https://datatracker.ietf.org/doc/html/rfc2119).

## SME

Engineering lead for a Feature/Initiative. Responsible for refinement, understanding desired outcomes, and raising questions with PM.

## Epic Assignee

Accountable for epic refinement into deliverable stories, story pointing, sprint planning, and maintaining accurate epic description.

## Scrum Master

Operational role. Leads all sprint ceremonies; ensures 100% of sprint stories are SP'd; ensures 100% of epics are sized; manages Jira dashboards, filters, and automations; unblocks Jira workflow issues.

## Team Lead

Technical role. Drives architectural alignment; advises on release backlog ranking; ensures bugs are triaged; monitors CI; responsible for at least one component lead role in OCPBUGS.

## Payload Manager (Rotational)

**Daily:** Check nightly release status for 4.18–current nightlies; check Jira tickets; monitor CI Slack channels (`#ci-single-node`, `#ci-two-node-arbiter`, `#ci-two-node-fencing`, `#forum-ocp-release-oversight`, `#announce-testplatform`); attend TRT standup; debug and file Jira tickets for CI failures.

**Weekly:** Update Payload Manager doc; check component readiness regressions via Sippy (4.18–4.21 HA vs single comparisons); sync with next rotation person.

**Sprintly:** Hand off to next rotation; update `edge-enablement-payload-manager` Slack alias.
