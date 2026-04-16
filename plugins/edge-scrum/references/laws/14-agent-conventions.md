# Edge Scrum Law: Jira Conventions for Agents

> The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED", "MAY", and "OPTIONAL" in this document are to be interpreted as described in [RFC 2119](https://datatracker.ietf.org/doc/html/rfc2119).

- **Issue ownership:** An issue belongs to the team if the assignee **or** QA Contact (`customfield_10470`) is a roster member.
- **Bugs always 0 SP.** MUST NOT suggest non-zero SP for a bug.
- **Epics use T-shirt sizes**, not SP. Stories/Spikes/Tasks/Bugs use SP.
- **Target version format:** MUST always use `4.x.0` (e.g., `4.16.0`) or `5.x.0`. MUST NOT use `4.x` or `5.x` alone.
- **LVMS bugs:** MUST NOT set Release Blocker to anything other than "Rejected".
- **Backlog entry point:** Apply `ocpedge-plan` label to Outcomes, Features, or Epics to include them in the Jira Plan.
- **OCPBUGS PR title format:** `OCPBUGS-<number>: <description>` — REQUIRED to trigger status automation.
- **Epic Status field:** Does not auto-update on closure. The team has automation for this — MUST NOT manually change unless directed.
- **Automation actor:** MUST use "Automation for Jira" as the rule Actor (not personal accounts or bot accounts) for new automation rules.
- **Sprint SP goal:** 8 SP per person. Verify this when analyzing sprint health or recommending tickets to add.
- **Stories not linked to an epic** are non-compliant with team conventions. Flag these during grooming.
- **Telco-relevant epics** MUST have the **Market Portfolio** field set at the epic level to appear on Telco dashboards.
