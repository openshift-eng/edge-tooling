#!/usr/bin/env bash
#
# sync-filters.sh - Sync Jira filter metadata from filters.json to Jira API
#
# Usage:
#   ./sync-filters.sh          # Update filters in Jira
#   ./sync-filters.sh --dry-run # Print what would be updated without making changes
#
# Requirements:
#   - jq (JSON processor)
#   - curl (HTTP client)
#   - edge-scrum/.env with JIRA_URL, JIRA_USERNAME, JIRA_API_TOKEN
#   - edge-scrum/.jira-config/filters.json
#
# Exit codes:
#   0 - Success
#   1 - Error (missing dependencies, API failures, etc.)

set -euo pipefail

# Colors for output
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly NC='\033[0m' # No Color

# Determine script directory and repository root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Configuration paths
readonly FILTERS_JSON="${REPO_ROOT}/.jira-config/filters.json"

# Parse command line arguments
DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
fi

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $*"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $*"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $*"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
}

# Check if required commands are available
check_dependencies() {
    local missing_deps=()

    for cmd in jq curl; do
        if ! command -v "$cmd" &> /dev/null; then
            missing_deps+=("$cmd")
        fi
    done

    if [[ ${#missing_deps[@]} -gt 0 ]]; then
        log_error "Missing required dependencies: ${missing_deps[*]}"
        log_error "Please install missing dependencies and try again."
        return 1
    fi
}

# Validate required environment variables
validate_env() {
    JIRA_URL="${JIRA_URL:-https://redhat.atlassian.net}"
    JIRA_URL="${JIRA_URL%/}"
    : "${JIRA_USERNAME:?JIRA_USERNAME must be set}"
    : "${JIRA_API_TOKEN:?JIRA_API_TOKEN must be set}"
    JIRA_AUTH=$(printf '%s:%s' "${JIRA_USERNAME}" "${JIRA_API_TOKEN}" | base64 -w0)
}

# Validate filters.json file exists and is valid JSON
validate_filters_file() {
    if [[ ! -f "$FILTERS_JSON" ]]; then
        log_error "Filters file not found: $FILTERS_JSON"
        return 1
    fi

    if ! jq empty "$FILTERS_JSON" 2>/dev/null; then
        log_error "Invalid JSON in $FILTERS_JSON"
        return 1
    fi
}

# Update a single filter via Jira API
update_filter() {
    local filter_id="$1"
    local filter_name="$2"
    local filter_description="$3"
    local filter_jql="$4"
    local edit_permissions="$5"
    local share_permissions="$6"

    log_info "Processing filter ${filter_id}: ${filter_name}"

    # Build the JSON payload for the filter update
    local payload
    payload=$(jq -n \
        --arg name "$filter_name" \
        --arg description "$filter_description" \
        --arg jql "$filter_jql" \
        --argjson editPermissions "$edit_permissions" \
        --argjson sharePermissions "$share_permissions" \
        '{
            name: $name,
            description: $description,
            jql: $jql,
            editPermissions: $editPermissions,
            sharePermissions: $sharePermissions
        }')

    if $DRY_RUN; then
        echo ""
        log_warn "[DRY RUN] Would update filter ${filter_id}:"
        echo "  Name: ${filter_name}"
        echo "  Description: ${filter_description}"
        echo "  JQL: ${filter_jql}"
        echo "  Edit Permissions: $(echo "$edit_permissions" | jq -c '.')"
        echo "  Share Permissions: $(echo "$share_permissions" | jq -c '.')"
        echo ""
        return 0
    fi

    # Make the API request
    local response
    local http_code

    response=$(curl -s -w "\n%{http_code}" \
        -X PUT \
        -H "Content-Type: application/json" \
        -H "Authorization: Basic ${JIRA_AUTH}" \
        -d "$payload" \
        "${JIRA_URL}/rest/api/2/filter/${filter_id}")

    # Extract HTTP status code (last line)
    http_code=$(echo "$response" | tail -n 1)

    # Extract response body (everything except last line)
    local response_body
    response_body=$(echo "$response" | sed '$d')

    # Check if update was successful
    if [[ "$http_code" -ge 200 ]] && [[ "$http_code" -lt 300 ]]; then
        log_success "Updated filter ${filter_id}: ${filter_name}"
        return 0
    else
        log_error "Failed to update filter ${filter_id}: ${filter_name}"
        log_error "HTTP Status: ${http_code}"
        log_error "Response: ${response_body}"
        return 1
    fi
}

# Main execution
main() {
    local exit_code=0

    log_info "Starting Jira filter sync..."
    echo ""

    # Pre-flight checks
    check_dependencies || exit 1
    validate_env
    validate_filters_file || exit 1

    if $DRY_RUN; then
        log_warn "DRY RUN MODE - No changes will be made to Jira"
        echo ""
    fi

    # Get filter count
    local filter_count
    filter_count=$(jq '.filters | length' "$FILTERS_JSON")
    log_info "Found ${filter_count} filters to sync"
    echo ""

    # Process each filter
    local index=0
    while [[ $index -lt $filter_count ]]; do
        # Extract filter data using jq
        local filter_id filter_name filter_description filter_jql edit_perms share_perms

        filter_id=$(jq -r ".filters[$index].id" "$FILTERS_JSON")
        filter_name=$(jq -r ".filters[$index].name" "$FILTERS_JSON")
        filter_description=$(jq -r ".filters[$index].description" "$FILTERS_JSON")
        filter_jql=$(jq -r ".filters[$index].jql" "$FILTERS_JSON")
        edit_perms=$(jq -c ".filters[$index].editPermissions" "$FILTERS_JSON")
        share_perms=$(jq -c ".filters[$index].sharePermissions" "$FILTERS_JSON")

        # Update the filter
        if ! update_filter "$filter_id" "$filter_name" "$filter_description" "$filter_jql" "$edit_perms" "$share_perms"; then
            exit_code=1
        fi

        ((index++))
    done

    echo ""
    if [[ $exit_code -eq 0 ]]; then
        if $DRY_RUN; then
            log_success "Dry run complete - ${filter_count} filters would be updated"
        else
            log_success "Successfully synced ${filter_count} filters"
        fi
    else
        log_error "Filter sync completed with errors"
    fi

    exit $exit_code
}

# Run main function
main
