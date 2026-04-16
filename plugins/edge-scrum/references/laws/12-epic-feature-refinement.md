# Edge Scrum Law: Epic and Feature Refinement

> The key words "MUST", "MUST NOT", "REQUIRED", "SHALL", "SHALL NOT", "SHOULD", "SHOULD NOT", "RECOMMENDED", "MAY", and "OPTIONAL" in this document are to be interpreted as described in [RFC 2119](https://datatracker.ietf.org/doc/html/rfc2119).

## Epic Refinement Process

**Assignee** is responsible for getting an epic from Planning → To Do.

1. **Set baseline fields:** Component, Labels, QA Contact, Doc Contact, Size.
2. **Fill out epic description** using the standard template (goal, dependencies, completion criteria).
3. **Create stories:** Either directly (if implementation is clear) or via a refinement spike to allocate sprint capacity for investigation.
4. Move epic to **To Do** when steps 1–3 are complete.

**QA Contact:** If no QE is needed, MUST set to `unassigned_jira`. MUST NOT leave blank.  
**Doc Contact:** If no docs are needed, MUST set to `unassigned_jira`. MUST NOT leave blank.

## Feature / Initiative Refinement (SME Process)

The **SME** role is responsible for feature refinement. Assigned during release planning.

1. **Verify feature details:** Architect, SME, and Assignee are set; description has a clear goal.
2. **Create a refinement Spike** in OCPEDGE. Set component to match the feature. Spike MUST have a "blocks" relationship to the Feature/Initiative.
3. **Spike completion criteria:** All open questions answered, all dependencies identified, enough knowledge to write implementation stories.
4. **After spike:** Create deliverable Epics. Leave a comment on the Feature/Initiative indicating it's ready to move to "Backlog" state.

**Important:** Features SHOULD only contain epics that are **required** for the feature to complete. Optional or nice-to-have epics belong in an Initiative or as standalone epics.
