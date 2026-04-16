---
name: edge-scrum:create-epic
description: Use when creating a new Epic in Jira for the OpenShift Edge team — enforces team conventions for required fields, description template, sizing, and parent linkage from Edge Scrum Laws
allowed-tools: mcp__atlassian__jira_create_issue, mcp__atlassian__jira_get_issue, mcp__atlassian__jira_search, AskUserQuestion, Read
user-invocable: true
---

# Create Epic

You are creating a new Epic in Jira for the OpenShift Edge team. All conventions come from the Edge Scrum Laws. Read `plugins/edge-scrum/references/laws/` files listed under "Create Epic" in `plugins/edge-scrum/references/Edge-Scrum-Laws.md` before proceeding.

## Configuration

```yaml
projects: [OCPEDGE, USHIFT]
issue_type: Epic

fields:
  parent_link:   customfield_10018   # Epic → Feature/Initiative
  qa_contact:    customfield_10470   # User picker
  docs_approver: customfield_10473   # User picker (Doc Contact)
  t_shirt_size:  customfield_10795   # T-shirt size for Epics (XS/S/M/L/XL)
```

## Components (Workstreams)

Use one of these exactly:

- `MicroShift`
- `Two Node with Arbiter`
- `Two Node with Fencing`
- `SNO`
- `Logical Volume Manager Storage`
- `Bandwidth Reduction`
- `Topology Transition`

Work outside these workstreams uses a scoped label instead of a component.

## T-Shirt Sizes

| Size | Expected Duration |
|---|---|
| XS | ~1 sprint (consider using a Story instead) |
| S | ~2 sprints |
| M | ~3 sprints |
| L | ~4 sprints |
| XL | ~5 sprints (likely too big; split or create a Feature/Initiative) |

## User Arguments

The user may provide arguments: `$ARGUMENTS`

Arguments can include any combination of:

- Summary text
- `--parent OCPSTRAT-XXX` — parent Feature/Initiative key
- `--project OCPEDGE` or `--project USHIFT` — target project
- `--component "Component Name"` — workstream component
- `--size M` — T-shirt size (XS/S/M/L/XL)
- `--assignee username` — epic assignee
- `--qa username` — QA Contact
- `--docs username` — Doc Contact
- No arguments → ask for all inputs

---

## Workflow

### Step 0: Load Edge Scrum Laws

Read these law files from `plugins/edge-scrum/references/laws/`:

- `01-jira-projects.md` — valid components (workstreams) and labels
- `04-jira-epics.md` — Epic conventions and T-shirt sizing
- `06-jira-fields.md` — custom field IDs
- `07-workflow-states.md` — valid Epic states
- `13-roles.md` — role assignments (QA Contact, Doc Contact)
- `14-agent-conventions.md` — Jira conventions for agents

The Laws are authoritative. When this skill and the Laws conflict, the Laws win.

---

### Step 1: Gather Required Fields

Parse any provided arguments. Use `AskUserQuestion` for every missing value — do not assume defaults for description fields:

1. **Summary** — concise epic title
2. **Project** — `OCPEDGE` or `USHIFT`
3. **Component** — must be one of the valid workstreams listed above
4. **Parent Feature/Initiative** — OCPSTRAT key (recommended, not required)
5. **Size** — T-shirt size (XS/S/M/L/XL)
6. **Assignee** — person responsible for refining this epic into deliverable stories
7. **QA Contact** — set to `unassigned_jira` if no QE needed (do not leave blank)
8. **Doc Contact** — set to `unassigned_jira` if no docs needed (do not leave blank)
9. **Goal** — what this epic aims to achieve (for description); always ask
10. **Dependencies** — external dependencies, blockers, or prerequisites (for description); always ask, user may answer "None"
11. **Completion Criteria** — what "done" looks like for this epic (for description); always ask

If a parent key is provided, verify it exists using `jira_get_issue`. Warn if the parent is in "Closed" or "Done" state.

---

### Step 2: Validate

Before creating, confirm:

- Component is in the valid workstreams list
- Size is a valid T-shirt size (XS/S/M/L/XL)
- QA Contact and Doc Contact are set (even if to `unassigned_jira`)
- If parent provided, it is a Feature or Initiative (not an Epic or Story)

If validation fails, report the issue and ask the user to correct it.

---

### Step 3: Create Epic

Build the description from the template:

```markdown
## Goal

{goal}

## Dependencies

{dependencies}

## Completion Criteria

{completion_criteria}
```

Create the issue using `jira_create_issue` with:

- **Project**: selected project (OCPEDGE or USHIFT)
- **Issue Type**: Epic
- **Summary**: provided summary
- **Description**: templated description above
- **Components**: [provided component]
- **Labels**: [`ocpedge-plan`]
- **Assignee**: provided assignee
- **QA Contact** (`customfield_10470`): provided QA contact
- **Doc Contact** (`customfield_10473`): provided doc contact
- **T-shirt Size** (`customfield_10795`): provided size (XS/S/M/L/XL)
- **Parent Link** (`customfield_10018`): parent key (if provided)

---

### Step 4: Confirm

Report to the user:

- Created epic key and summary
- Link to the epic: `https://redhat.atlassian.net/browse/{KEY}`
- Parent linkage status
- Reminder: epic starts in **Planning** state — refine into stories before moving to To Do

---

## Edge Cases

- **No parent provided**: Create without parent link. Note that unlinked epics should eventually be linked to a Feature or Initiative.
- **Parent is an Epic**: Warn — epics should link to Features or Initiatives, not other epics.
- **XL size**: Warn that XL epics (~5 sprints) may need splitting per Laws.
- **XS size**: Warn that XS epics (~1 sprint) might be better as Stories.
- **Component not in list**: Reject. Only valid workstream components are allowed.
- **USHIFT project**: MicroShift epics live here. Component should be `MicroShift`.

## Important Notes

- This skill **creates** Jira data. Confirm all details with the user before calling `jira_create_issue`.
- Epics start in **Planning** state. The assignee is responsible for refinement into deliverable stories.
- The `ocpedge-plan` label is added by default to include the epic in the Jira Plan.
