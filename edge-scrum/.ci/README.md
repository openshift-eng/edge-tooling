# CI Scripts

This directory contains CI/CD validation and sync scripts for the edge-scrum project.

## Validation Scripts

### validate-readme-sync.sh

Validates that changes to metadata JSON files in `.jira-config/` are reflected in the README.

**Purpose:** Ensures documentation stays in sync with configuration changes.

**Usage:**

```bash
# Run locally
.ci/scripts/validate-readme-sync.sh

# In GitHub Actions
- name: Validate README sync
  run: ./edge-scrum/.ci/scripts/validate-readme-sync.sh
```

**Behavior:**

- Detects modifications to any of the following files:
  - `boards.json`
  - `filters.json`
  - `projects.json`
  - `components.json`
  - `plans.json`
  - `labels.json`
- If any metadata file changed, verifies that `README.md` was also updated
- Exits with error code 1 if README wasn't updated alongside metadata changes
- Exits with code 0 if no metadata changed or if both metadata and README changed

**Environment Variables:**

- `GITHUB_BASE_REF` - GitHub Actions PR base branch (auto-detected)
- `CI_MERGE_REQUEST_TARGET_BRANCH_NAME` - GitLab CI target branch (auto-detected)
- Falls back to comparing against `origin/main` for local development

## Sync Scripts

These scripts apply metadata changes from JSON files to Jira via REST API.

### apply-changes.sh

**Orchestration script** that detects changed metadata files and applies them to Jira.

**Purpose:** Main entry point for syncing metadata changes to Jira.

**Usage:**

```bash
# Dry run (preview changes)
.ci/scripts/apply-changes.sh --dry-run

# Apply changes
.ci/scripts/apply-changes.sh

# In GitHub Actions
- name: Apply metadata changes to Jira
  run: ./edge-scrum/.ci/scripts/apply-changes.sh
  env:
    JIRA_URL: ${{ secrets.JIRA_URL }}
    JIRA_USERNAME: ${{ secrets.JIRA_USERNAME }}
    JIRA_API_TOKEN: ${{ secrets.JIRA_API_TOKEN }}
```

**Behavior:**

- Detects which metadata files changed (boards.json, filters.json, projects.json, components.json)
- Calls the appropriate sync script for each changed file
- Passes `--dry-run` flag to all sync scripts if provided
- Exits with code 1 if any sync script fails
- Skips `labels.json` (no formal API)

### sync-boards.sh

Updates board properties in Jira.

**API:** `PUT /rest/agile/1.0/board/{boardId}/properties/{propertyKey}`

**Updates:** Board properties (e.g., roadmaps features, child issue planning)

**Note:** Only properties can be updated via API. Structural configuration (columns, estimation, ranking) requires Jira UI.

### sync-filters.sh

Updates filter metadata, JQL, and permissions in Jira.

**API:** `PUT /rest/api/2/filter/{filterId}`

**Updates:** Filter name, description, JQL, edit permissions, share permissions

**Note:** Filter ownership transfers require Jira UI.

### sync-projects.sh

Updates project metadata in Jira.

**API:** `PUT /rest/api/2/project/{projectKey}`

**Updates:** Project name, description, lead, assigneeType

**Note:** Role configuration is listed in JSON but is UI-only (cannot be updated via API).

### sync-components.sh

Updates component metadata in Jira.

**API:** `PUT /rest/api/2/component/{componentId}`

**Updates:** Component name, description

**Note:** Only updates components with `component_id` in the JSON. Entries without `component_id` must be created manually.

## Common Features

All sync scripts share these features:

- **`--dry-run` flag:** Preview changes without applying them
- **Credentials:** Load from `edge-scrum/.env` (JIRA_URL, JIRA_USERNAME, JIRA_API_TOKEN)
- **Error handling:** Exit with code 1 if any updates fail
- **Validation:** Check prerequisites (jq, curl, config files, env vars)
- **Logging:** Clear, color-coded output showing what's being updated
- **Safety:** Only update existing entities (no create/delete operations)

## Prerequisites

```bash
# Install dependencies
sudo dnf install jq curl git  # Fedora/RHEL
sudo apt install jq curl git  # Debian/Ubuntu

# Configure credentials
cat > edge-scrum/.env <<EOF
export JIRA_URL="https://redhat.atlassian.net"
export JIRA_USERNAME="your-email@redhat.com"
export JIRA_API_TOKEN="your-api-token"
EOF
```

## Workflow

1. **Make changes** to metadata JSON files in `.jira-config/`
2. **Update README.md** with the changes (validated by `validate-readme-sync.sh`)
3. **Preview changes** with `apply-changes.sh --dry-run`
4. **Apply changes** with `apply-changes.sh`
5. **Commit and push** to create a PR
