# Edge Scrum Law: Bug Triage

> The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED", "MAY", and "OPTIONAL" in this document are to be interpreted as described in [RFC 2119](https://datatracker.ietf.org/doc/html/rfc2119).

**Goal:** Move a bug from `NEW` to `ASSIGNED` (i.e., triaged and being worked) or determine next steps (reassign, close).

## Step 1: Check required fields

- **Severity** — SHOULD be set by reporter; estimate if missing.
- **Priority** — set by triager; reflects engineering view of importance (does not need to match severity).
- **Affects Version** — all versions where the bug is known to exist.
- **Release Blocker**:
  - LVMS bugs: MUST be **Rejected** (LVMS does not block OCP releases).
  - SNO/TNA/TNF bugs: assess during triage; set to Proposed if potentially blocking.

## Step 2a: LVMS bugs

MUST require a must-gather at minimum (confirms deployment topology and LVMS CRs).

## Step 2b: SNO / TNA / TNF bugs

MUST require a must-gather; sosreport MAY be needed for OS-level issues. Verify the issue is actually topology-specific before accepting the bug.

## Step 3: Working the bug

- MUST set **Target Version** (e.g., `4.16.0` — use full y.z.0, not `4.16`).
- SHOULD set **Target Backport Versions** after consulting reporter and PM; minimize unnecessary backports.
- MUST include the bug number in the PR title: `OCPBUGS-12345: Fix description`. This triggers Jira automation to update bug status as the PR progresses.

**Churning bugs into a sprint:** Bugs of sufficient severity/priority or that are release blocking SHOULD be churned into the sprint per the Sprint Churn policy.
