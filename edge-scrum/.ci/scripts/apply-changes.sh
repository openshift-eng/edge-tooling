#!/bin/bash
# apply-changes.sh
# Orchestrates applying metadata changes to Jira by detecting which files changed
# and calling the appropriate sync scripts

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EDGE_SCRUM_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
JIRA_CONFIG_DIR="${EDGE_SCRUM_DIR}/.jira-config"

# Parse command-line arguments
DRY_RUN=false
for arg in "$@"; do
    case $arg in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
    esac
done

# Metadata files that can be synced to Jira
# Note: plans.json and labels.json are excluded (limited API support / no formal API)
declare -A SYNCABLE_FILES=(
    ["boards.json"]="sync-boards.sh"
    ["filters.json"]="sync-filters.sh"
    ["projects.json"]="sync-projects.sh"
    ["components.json"]="sync-components.sh"
)

# Determine base ref for comparison
# In CI, use the base branch (e.g., main, master)
# Locally, use the merge base with main
if [ -n "${GITHUB_BASE_REF:-}" ]; then
    # GitHub Actions PR context
    BASE_REF="origin/${GITHUB_BASE_REF}"
elif [ -n "${CI_MERGE_REQUEST_TARGET_BRANCH_NAME:-}" ]; then
    # GitLab CI PR context
    BASE_REF="origin/${CI_MERGE_REQUEST_TARGET_BRANCH_NAME}"
elif [ -n "${GIT_BASE_SHA:-}" ]; then
    # GitHub Actions push context (github.event.before)
    BASE_REF="${GIT_BASE_SHA}"
else
    # Local development - compare against main
    BASE_REF="$(git merge-base HEAD origin/main 2>/dev/null || echo 'origin/main')"
fi

echo "Comparing against base: ${BASE_REF}"
echo ""

# Detect which metadata files have changed
changed_files=()

if [[ "${SYNC_ALL:-false}" == "true" ]]; then
    echo "SYNC_ALL mode: syncing all config files"
    changed_files=("${!SYNCABLE_FILES[@]}")
else
    for file in "${!SYNCABLE_FILES[@]}"; do
        if git diff --name-only "${BASE_REF}..HEAD" | grep -q "^edge-scrum/.jira-config/${file}$"; then
            changed_files+=("${file}")
            echo "✓ Detected change in ${file}"
        fi
    done

    # If no syncable files were modified, exit successfully
    if [ ${#changed_files[@]} -eq 0 ]; then
        echo "✓ No syncable metadata file changes detected."
        exit 0
    fi
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Applying changes to Jira"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Track overall success
overall_success=true
declare -A sync_results

# Apply changes for each modified file
for file in "${changed_files[@]}"; do
    sync_script="${SYNCABLE_FILES[$file]}"
    sync_script_path="${SCRIPT_DIR}/${sync_script}"

    echo "────────────────────────────────────────────────────────────────────────────"
    echo "Processing: ${file}"
    echo "Sync script: ${sync_script}"
    echo "────────────────────────────────────────────────────────────────────────────"

    # Check if sync script exists
    if [ ! -f "${sync_script_path}" ]; then
        echo "✗ ERROR: Sync script not found: ${sync_script_path}"
        sync_results["${file}"]="SCRIPT_NOT_FOUND"
        overall_success=false
        echo ""
        continue
    fi

    # Make sure sync script is executable
    if [ ! -x "${sync_script_path}" ]; then
        echo "Making ${sync_script} executable..."
        chmod +x "${sync_script_path}"
    fi

    # Run the sync script
    if [ "${DRY_RUN}" = true ]; then
        echo "Running in DRY-RUN mode..."
        if "${sync_script_path}" --dry-run; then
            sync_results["${file}"]="SUCCESS (dry-run)"
        else
            sync_results["${file}"]="FAILED (dry-run)"
            overall_success=false
        fi
    else
        if "${sync_script_path}"; then
            sync_results["${file}"]="SUCCESS"
        else
            sync_results["${file}"]="FAILED"
            overall_success=false
        fi
    fi

    echo ""
done

# Print summary
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Summary"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

for file in "${changed_files[@]}"; do
    result="${sync_results[$file]}"
    if [[ "${result}" == SUCCESS* ]]; then
        echo "✓ ${file}: ${result}"
    elif [[ "${result}" == "SCRIPT_NOT_FOUND" ]]; then
        echo "✗ ${file}: Sync script not found"
    else
        echo "✗ ${file}: ${result}"
    fi
done

echo ""

if [ "${overall_success}" = true ]; then
    echo "✓ All changes applied successfully"
    exit 0
else
    echo "✗ Some changes failed to apply"
    exit 1
fi
