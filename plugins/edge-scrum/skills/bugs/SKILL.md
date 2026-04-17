---
name: edge-scrum:bugs
description: Query active bugs from OCPBUGS and OCPEDGE projects by topology
allowed-tools: Read, mcp__atlassian__jira_search, AskUserQuestion
user-invocable: true
---

# Edge Scrum: Bugs

Query active bugs from OCPBUGS and OCPEDGE projects, filtered by topology.

## Arguments

Parse the user's command for optional arguments:
- `--topology=<type>` - Topology filter: `sno`, `tnf`, `arbiter`, `microshift`, or `all` (prompts if not specified)
- `--priority=<level>` - Filter to specific priority level(s) (comma-separated)
- `--format=<type>` - Output format: `table` (default), `simple`, `keys-only`
- `--status=<status>` - Filter by status (comma-separated list, mutually exclusive with `--untriaged`). Multi-word statuses must be quoted (e.g., `"In Progress"`)
- `--untriaged` - Show only untriaged bugs (mutually exclusive with `--status`)

**Important:** `--status` and `--untriaged` are mutually exclusive. If both are provided, return an error.

**Untriaged criteria (project-specific):**
- **OCPBUGS**: `status = "New"`
- **OCPEDGE**: `status = "To Do" AND assignee is EMPTY`
- **USHIFT**: `status = "New"`

The `--untriaged` flag applies the correct logic for all projects automatically.

## Topology Selection

If `--topology` is not provided, use `AskUserQuestion` to prompt:

```yaml
Question: "Which topology do you want to query for bugs?"
Options:
  - "all" - All edge topologies (SNO + TNF + Arbiter + MicroShift)
  - "sno" - Single Node OpenShift
  - "tnf" - Two-Node with Fencing
  - "arbiter" - Two-Node with Arbiter
  - "microshift" - MicroShift
```

## Component Mapping (Dynamic from Edge Scrum Laws)

**IMPORTANT:** Before building component filters, read `../../references/laws/01-jira-projects.md` to get the canonical list of components for each project.

**Extract from laws:**
- OCPBUGS components (line 10): Parse the components we own from the table
- OCPEDGE components (lines 30-41): Parse the "Components (Workstreams)" section
- USHIFT: Whole project (no component filtering needed)

**Topology-to-Component Mapping:**

Use the extracted component names from the laws to build these filters:

- **sno**: 
  - OCPBUGS: component matching "Single Node" (typically "Installer / Single Node OpenShift")
  - OCPEDGE: component = "SNO"
  
- **tnf**: 
  - Both projects: component = "Two Node Fencing" (exact match in both OCPBUGS and OCPEDGE)
  
- **arbiter**: 
  - Both projects: component = "Two Node with Arbiter" (exact match in both OCPBUGS and OCPEDGE)
  
- **microshift**: 
  - USHIFT: whole project
  - OCPEDGE: component = "MicroShift"
  
- **all**: 
  - OCPBUGS: All topology-related components from laws (exclude "Logical Volume Manager Storage" - not a topology)
  - OCPEDGE: "SNO", "Two Node Fencing", "Two Node with Arbiter", "MicroShift" (exclude LVMS, Bandwidth Reduction, Topology Transition - not edge topologies)
  - USHIFT: whole project

**Build JQL dynamically:**

After reading the laws file, construct the JQL component filters using the exact component names found. For example:

```jql
# SNO
((project = OCPBUGS AND component = "<extracted-sno-component-name>") OR (project = OCPEDGE AND component = "SNO"))

# All topologies
((project = OCPBUGS AND component IN ("<comp1>", "<comp2>", "<comp3>")) OR (project = OCPEDGE AND component IN ("SNO", "Two Node Fencing", "Two Node with Arbiter", "MicroShift")) OR project = USHIFT)
```

## JQL Query

**Validation:** If both `--status` and `--untriaged` are provided, return error: "Cannot use --status and --untriaged together. Use one or the other."

Base query template:

```jql
<component-filter>
AND issuetype = Bug 
AND <status-filter>
<priority-filter>
ORDER BY priority DESC
```

**Component filter** - Use mapping above based on topology

**Status filter** - One of:
- Default: `status != Closed AND statusCategory != Done`
- With `--status`: `status IN (<comma-separated-list>)` (quote multi-word statuses, e.g., `"In Progress"`)
- With `--untriaged`: `((project = OCPBUGS AND status = "New") OR (project = OCPEDGE AND status = "To Do" AND assignee is EMPTY) OR (project = USHIFT AND status = "New"))`

**Priority filter** - Optional, only include if `--priority` is specified:
- With `--priority`: `AND priority IN (<comma-separated-list>)`
- Without `--priority`: Omit this clause entirely

## Implementation

1. **Read Edge Scrum Laws to extract components**:
   - Read `../../references/laws/01-jira-projects.md`
   - From line 10 (OCPBUGS row in Projects table): Extract components we own
   - From lines 30-41 (Components section): Extract OCPEDGE component names
   - Store the exact component names for use in JQL queries
   
2. **Parse arguments from user input**
   - For `--status`: Accept quoted or unquoted status tokens
   - When building JQL, automatically wrap multi-word statuses in double quotes
   - Example: User provides `In Progress,ON_QA` → JQL becomes `status IN ("In Progress", "ON_QA")`

3. If `--topology` not specified, use `AskUserQuestion` to prompt

4. **Build component filter based on topology**:
   - Use the component names extracted from the laws file
   - Apply the topology-to-component mapping described above
   - For `--topology=all`: Include only topology-related components (SNO, TNF, Arbiter, MicroShift)
   - For specific topology: Use that topology's component filter

5. Use `mcp__atlassian__jira_search` tool with the JQL query

6. Request fields: `key,priority,status,assignee,summary`

7. **Pagination**: Use `maxResults=50` and iterate with `startAt` to fetch all results
   - Initial request: `startAt=0, maxResults=50`
   - Continue fetching: Increment `startAt` by 50 until fewer than 50 results returned
   - Aggregate all pages before presenting results
   - This ensures summaries reflect all bugs, not just the first page

8. **Format output based on --format**:

   **table format (default)**:

   ```markdown
   ## Bugs: <Topology>

   ### <Priority> (<count> bugs)

   | Key | Status | Assignee | Summary |
   |-----|--------|----------|---------|
   | ... | ...    | ...      | ...     |

   **Total: <count> bugs** (<status breakdown>)
   ```

   **simple format**:

   ```markdown
   Bugs: <Topology>

   <Priority> (<count>):
   - <KEY>: <Summary>

   Total: <count> bugs
   ```

   **keys-only format**:

   ```markdown
   <KEY>
   <KEY>
   <KEY>
   ```

## Example Usage

```bash
/edge-scrum:bugs
/edge-scrum:bugs --topology=sno
/edge-scrum:bugs --topology=tnf --priority=Critical
/edge-scrum:bugs --topology=microshift
/edge-scrum:bugs --topology=all --untriaged
/edge-scrum:bugs --topology=arbiter --priority=Critical,Major --format=simple
/edge-scrum:bugs --topology=all --status="In Progress",ON_QA
```

## Edge Scrum Laws Reference

This skill uses conventions from:
- [`references/laws/03-jira-bugs.md`](../../references/laws/03-jira-bugs.md) - Bug conventions and OCPBUGS handling
- [`references/laws/01-jira-projects.md`](../../references/laws/01-jira-projects.md) - Jira project keys, components, and filters
- [`references/laws/10-bug-triage.md`](../../references/laws/10-bug-triage.md) - Bug triage process and untriaged criteria
