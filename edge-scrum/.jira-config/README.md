# Jira Configuration

This directory contains configuration files for Jira board and project management.

## Overview

This folder tracks API-manageable metadata for Jira entities used by the OpenShift Edge team. Each JSON file contains structured data that can be queried and modified via Jira REST APIs. Configuration elements that require the Jira UI are documented but not stored.

## Files

- `boards.json` - Board metadata (ID, name, type, filter, properties)
- `filters.json` - Filter metadata (ID, name, JQL, permissions)
- `projects.json` - Project metadata (ID, key, name, lead, roles)
- `components.json` - Component metadata (name, description, project associations)
- `plans.json` - Advanced Roadmaps plan metadata (ID, title, issue sources, configuration)
- `labels.json` - Team-specific label definitions and their purposes

## Current Configuration

### Boards

- **OpenShift Edge Scrum** (11479) - Scrum board for main team workflow
- **OCPEDGE Scrum** (8557) - Legacy scrum board
- **OpenShift Edge RHEL Verification Tickets** (11551) - Kanban board for RHEL verification

### Filters

**Workstream Filters:**

- OpenShift Edge Workstream - Adaptable Topology (105160)
- OpenShift Edge Workstream - SNO (105147)
- OpenShift Edge Workstream - TNA (105141)
- OpenShift Edge Workstream - TNF (105142)
- OpenShift Edge Workstream - MicroShift (105139)
- OpenShift Edge Workstream - MicroShift to SNO (105161)
- OpenShift Edge Workstream - LVMS (105140)
- OpenShift Edge Workstream - Bandwidth Reduction (105164)
- OpenShift Edge Workstream - RHEL Ticket Verification (105719)

**Utility Filters:**

- OpenShift Edge - Core Backlog (104868)
- OpenShift Edge - Bugs and CVEs (104860)
- OpenShift Edge - Components (104874)
- OpenShift Edge - Labels (104872)
- OpenShift Edge - Team Assigned (104844)
- OpenShift Edge - QE Assigned (104857)
- OpenShift Edge - External Projects (104866)
- OpenShift Edge - Scrum Board (104882)

### Projects

- **OCPEDGE** (11690) - OpenShift Edge Enablement

### Components

- Microshift
- Topology Transition
- Bandwidth Reduction
- Logical Volume Manager Storage
- Planning
- SNO
- TNF (Two Node with Fencing)
- TNA (Two Node with Arbiter)

### Plans

- **OpenShift Edge Unified Plan** (1379) - Advanced Roadmaps plan using filter 104341

### Labels

- **OCPEDGE:Docs** - Docs specific tasks
- **OCPEDGE:QE** - QE specific tasks
- **OCPEDGE:RHEL-Verification** - RHEL Verification tasks for TNF
- **OCPEDGE:CI** - CI bugs and automation tasks
- **OCPEDGE:Payload-Manager** - Payload manager duties and automation
- **OCPEDGE:Tooling** - Team tooling improvements
- **OCPEDGE:Process-Improvement** - Process investigation and improvements

## Board Configuration Details

### OpenShift Edge Scrum (Board 11479)

**Properties:**

- `jsw-roadmaps-classic-board-enable-roadmaps`: `true` - Roadmaps feature enabled
- `jsw-roadmaps-cmp-enable-child-issue-planning`: `true` - Child issue planning enabled

**Board Features:**

- **Type**: Scrum
- **Project**: OCPEDGE (OpenShift Edge)
- **Columns**: To Do → In Progress → Tech Review → QE Review → Done
- **Estimation**: Story Points (customfield_10028)
- **Ranking**: Custom field 10019
- **Constraint Type**: None

## Board Management

### API Capabilities

**Read Operations:**

- List boards
- Get board details
- Get board configuration (columns, estimation, ranking)
- Get/list board properties

**Write Operations:**

- Create board
- Delete board
- Set/delete board properties (custom metadata)

### UI-Only Configuration

**Board configuration modifications require the Jira web interface:**

- Column mappings and status assignments
- Estimation field configuration
- Ranking field settings
- Quick filters
- Card layout and colors
- Swimlane configuration
- Working days and time tracking

**Note:** While board properties (custom metadata) are writable via API, structural configuration elements are read-only and must be modified through the Jira UI.

## Filter Management

### API Capabilities

**Read Operations:**

- List filters
- Get filter details

**Write Operations:**

- Create filter (name, description, JQL, permissions)
- Update filter (name, description, JQL)
- Delete filter
- Update edit/share permissions

### UI-Only Configuration

**Filter ownership transfers** - Must use Jira UI to change filter owner

## Project Management

### API Capabilities

**Read Operations:**

- List projects
- Get project details

**Write Operations:**

- Create project (key, name, description, lead, assigneeType)
- Update project (name, description, lead, assigneeType)
- Delete project

### UI-Only Configuration

**Project configuration:**

- Issue types and workflows
- Custom fields
- Screens and field configurations
- Permissions and roles
- Versions and releases

**Note:** Components are managed via separate API endpoints (see Components section)

## Component Management

### API Capabilities

**Read Operations:**

- List project components
- Get component details

**Write Operations:**

- Create component (name, description, project, lead, assignee)
- Update component (name, description, lead, assignee)
- Delete component

## Plan Management

### API Capabilities

**Read Operations:**

- Get plan details (via `/rest/jpo/1.0/plans/{planId}`)
- List issue sources and calculation configuration

**Write Operations:**

Plans (Advanced Roadmaps) have limited API support. Most plan configuration requires the Jira UI.

### UI-Only Configuration

**Plan configuration:**

- Issue source filters
- Planning unit (Days/Weeks/Months)
- Non-working days
- Calculation settings (sprints, teams, releases)
- Date field mappings
- Completed issue retention

## Label Management

Labels in Jira are lightweight tags without formal metadata or API endpoints. The `labels.json` file serves as team documentation for label naming conventions and purposes.

**Note:** Labels are applied directly to issues via the standard issue update API. There is no separate label management API.

## REST API Endpoints

```bash
# Get board configuration (read-only)
GET /rest/agile/1.0/board/{boardId}/configuration

# List board properties
GET /rest/agile/1.0/board/{boardId}/properties

# Set board property
PUT /rest/agile/1.0/board/{boardId}/properties/{propertyKey}
```
