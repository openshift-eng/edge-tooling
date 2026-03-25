#!/usr/bin/env bash
#
# sync-projects.sh
#
# Synchronizes project metadata from .jira-config/projects.json to Jira via REST API.
# Updates project name, description, lead, and assigneeType for existing projects.
#
# Usage:
#   ./sync-projects.sh [--dry-run]
#
# Options:
#   --dry-run    Print what would be updated without making API calls
#
# Prerequisites:
#   - jq installed
#   - curl installed
#   - .env file with JIRA_URL, JIRA_USERNAME, JIRA_API_TOKEN
#
# Note:
#   - Only updates existing projects (does not create or delete)
#   - Role configuration is UI-only and cannot be updated via API
#

set -euo pipefail

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Configuration files
PROJECTS_JSON="${REPO_ROOT}/.jira-config/projects.json"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Track errors
EXIT_CODE=0

# Parse command line arguments
DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
fi

#
# Print functions
#
info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $*"
}

warning() {
    echo -e "${YELLOW}[WARNING]${NC} $*"
}

error() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
    EXIT_CODE=1
}

#
# Validation
#
validate_prerequisites() {
    local missing=()

    # Check for required commands
    if ! command -v jq &> /dev/null; then
        missing+=("jq")
    fi

    if ! command -v curl &> /dev/null; then
        missing+=("curl")
    fi

    if [[ ${#missing[@]} -gt 0 ]]; then
        error "Missing required commands: ${missing[*]}"
        error "Please install missing dependencies and try again"
        exit 1
    fi

    # Check for required files
    if [[ ! -f "${PROJECTS_JSON}" ]]; then
        error "Projects configuration not found: ${PROJECTS_JSON}"
        exit 1
    fi
}

#
# Validate required environment variables
#
validate_env() {
    JIRA_URL="${JIRA_URL:-https://redhat.atlassian.net}"
    JIRA_URL="${JIRA_URL%/}"
    : "${JIRA_USERNAME:?JIRA_USERNAME must be set}"
    : "${JIRA_API_TOKEN:?JIRA_API_TOKEN must be set}"
    JIRA_AUTH=$(printf '%s:%s' "${JIRA_USERNAME}" "${JIRA_API_TOKEN}" | base64 -w0)
}

#
# Update a single project
#
update_project() {
    local project_key="$1"
    local project_name="$2"
    local project_description="$3"
    local project_lead_id="$4"
    local project_assignee_type="$5"

    info "Processing project: ${project_key}"

    # Build the JSON payload
    local payload
    payload=$(jq -n \
        --arg name "${project_name}" \
        --arg description "${project_description}" \
        --arg leadAccountId "${project_lead_id}" \
        --arg assigneeType "${project_assignee_type}" \
        '{
            name: $name,
            description: $description,
            leadAccountId: $leadAccountId,
            assigneeType: $assigneeType
        }')

    # Print what will be updated
    echo "  Name: ${project_name}"
    echo "  Description: ${project_description}"
    echo "  Lead ID: ${project_lead_id}"
    echo "  Assignee Type: ${project_assignee_type}"

    if [[ "${DRY_RUN}" == true ]]; then
        warning "  [DRY-RUN] Would update project ${project_key}"
        return 0
    fi

    # Make the API call
    local response
    local http_code

    response=$(curl -s -w "\n%{http_code}" \
        -X PUT \
        -H "Content-Type: application/json" \
        -H "Authorization: Basic ${JIRA_AUTH}" \
        -d "${payload}" \
        "${JIRA_URL}/rest/api/2/project/${project_key}")

    # Extract HTTP status code (last line)
    http_code=$(echo "${response}" | tail -n 1)
    # Extract response body (all but last line)
    local response_body
    response_body=$(echo "${response}" | sed '$d')

    # Check response
    if [[ "${http_code}" -ge 200 && "${http_code}" -lt 300 ]]; then
        success "  Updated project ${project_key}"
    else
        error "  Failed to update project ${project_key} (HTTP ${http_code})"
        if [[ -n "${response_body}" ]]; then
            error "  Response: ${response_body}"
        fi
    fi
}

#
# Main processing
#
process_projects() {
    local project_count
    project_count=$(jq '.projects | length' "${PROJECTS_JSON}")

    info "Found ${project_count} project(s) in ${PROJECTS_JSON}"
    echo ""

    # Iterate over each project
    for i in $(seq 0 $((project_count - 1))); do
        local key
        local name
        local description
        local lead_id
        local assignee_type

        # Extract project fields
        key=$(jq -r ".projects[${i}].key" "${PROJECTS_JSON}")
        name=$(jq -r ".projects[${i}].name" "${PROJECTS_JSON}")
        description=$(jq -r ".projects[${i}].description // \"\"" "${PROJECTS_JSON}")
        lead_id=$(jq -r ".projects[${i}].lead.id" "${PROJECTS_JSON}")
        assignee_type=$(jq -r ".projects[${i}].assigneeType" "${PROJECTS_JSON}")

        # Validate required fields
        if [[ -z "${key}" || "${key}" == "null" ]]; then
            error "Project at index ${i} is missing 'key' field"
            continue
        fi

        if [[ -z "${name}" || "${name}" == "null" ]]; then
            error "Project ${key} is missing 'name' field"
            continue
        fi

        if [[ -z "${lead_id}" || "${lead_id}" == "null" ]]; then
            error "Project ${key} is missing 'lead.id' field"
            continue
        fi

        # Update the project
        update_project "${key}" "${name}" "${description}" "${lead_id}" "${assignee_type}"
        echo ""
    done

    # Note about roles
    if [[ "${project_count}" -gt 0 ]]; then
        info "Note: Role configuration is listed in projects.json but is UI-only"
        info "Role assignments cannot be updated via the Jira REST API"
    fi
}

#
# Main execution
#
main() {
    if [[ "${DRY_RUN}" == true ]]; then
        warning "Running in DRY-RUN mode - no changes will be made"
        echo ""
    fi

    validate_prerequisites
    validate_env
    process_projects

    if [[ "${EXIT_CODE}" -ne 0 ]]; then
        error "Some projects failed to update"
        exit 1
    fi

    success "Project sync completed successfully"
}

main
