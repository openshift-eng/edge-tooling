#!/bin/bash
#
# Dev Environment Setup Script
# ============================
#
# Usage:
#   ./setup.sh              # Clone all repos (first time setup)
#   ./setup.sh clone        # Clone all repos
#   ./setup.sh update       # Update all repos (git pull)
#   ./setup.sh clone <dir>  # Clone specific repo by directory name
#   ./setup.sh update <dir> # Update specific repo by directory name
#   ./setup.sh status       # Show status of all repos
#   ./setup.sh list         # List configured repos
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOS_DIR="$SCRIPT_DIR/repos"
REPOS_FILE="$SCRIPT_DIR/repos.txt"
REPOS_TEMPLATE="$SCRIPT_DIR/repos.txt.template"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Ensure repos.txt exists
ensure_repos_file() {
    if [[ ! -f "$REPOS_FILE" ]]; then
        if [[ -f "$REPOS_TEMPLATE" ]]; then
            log_info "Creating repos.txt from template..."
            cp "$REPOS_TEMPLATE" "$REPOS_FILE"
            log_success "Created repos.txt - edit it to customize repo branches/paths"
        else
            log_error "Neither repos.txt nor repos.txt.template found!"
            exit 1
        fi
    fi
}

# Parse a line from repos.txt
# Returns: url, dir, branch (in global variables)
parse_repo_line() {
    local line="$1"

    # Skip comments and empty lines
    [[ "$line" =~ ^[[:space:]]*# ]] && return 1
    [[ -z "${line// }" ]] && return 1

    # Parse pipe-separated fields
    IFS='|' read -r url dir branch <<< "$line"

    # Trim whitespace
    url="$(echo "$url" | xargs)"
    dir="$(echo "$dir" | xargs)"
    branch="$(echo "$branch" | xargs)"

    [[ -z "$url" ]] && return 1
    return 0
}

# Clone a single repository (blobless clone for faster downloads)
clone_repo() {
    local url="$1"
    local dir="$2"
    local branch="$3"

    local target="$REPOS_DIR/$dir"

    if [[ -d "$target/.git" ]]; then
        log_warn "$dir already exists, skipping (use 'update' to pull)"
        return 0
    fi

    # Ensure repos directory exists
    mkdir -p "$REPOS_DIR"

    log_info "Cloning $dir (blobless)..."

    # Blobless clone: all commits/trees downloaded, blobs fetched on-demand
    git clone --filter=blob:none --branch "$branch" "$url" "$target"

    log_success "Cloned $dir (branch: $branch)"
}

# Update a single repository
update_repo() {
    local dir="$1"
    local target="$REPOS_DIR/$dir"

    if [[ ! -d "$target/.git" ]]; then
        log_warn "$dir not cloned yet, skipping"
        return 0
    fi

    log_info "Updating $dir..."

    cd "$target"

    # Check for local changes
    if ! git diff --quiet HEAD 2>/dev/null; then
        log_warn "  $dir has local changes, stashing..."
        git stash
    fi

    git pull --rebase
    cd "$SCRIPT_DIR"

    log_success "Updated $dir"
}

# Clone all repositories
clone_all() {
    log_info "Cloning all repositories..."
    echo

    while IFS= read -r line; do
        if parse_repo_line "$line"; then
            clone_repo "$url" "$dir" "$branch"
        fi
    done < "$REPOS_FILE"

    echo
    log_success "All repositories cloned!"
}

# Update all repositories
update_all() {
    log_info "Updating all repositories..."
    echo

    while IFS= read -r line; do
        if parse_repo_line "$line"; then
            if [[ -d "$REPOS_DIR/$dir/.git" ]]; then
                update_repo "$dir"
            fi
        fi
    done < "$REPOS_FILE"

    echo
    log_success "All repositories updated!"
}

# Clone or update a specific repo
handle_specific_repo() {
    local action="$1"
    local target_dir="$2"
    local found=false

    while IFS= read -r line; do
        if parse_repo_line "$line"; then
            if [[ "$dir" == "$target_dir" ]]; then
                found=true
                if [[ "$action" == "clone" ]]; then
                    clone_repo "$url" "$dir" "$branch"
                else
                    update_repo "$dir"
                fi
                break
            fi
        fi
    done < "$REPOS_FILE"

    if [[ "$found" == "false" ]]; then
        log_error "Repository '$target_dir' not found in repos.txt"
        echo "Available repositories:"
        list_repos
        exit 1
    fi
}

# Show status of all repos
show_status() {
    log_info "Repository status:"
    echo
    printf "%-30s %-12s %-20s %s\\n" "DIRECTORY" "STATUS" "BRANCH" "LAST COMMIT"
    printf "%-30s %-12s %-20s %s\\n" "---------" "------" "------" "-----------"

    while IFS= read -r line; do
        if parse_repo_line "$line"; then
            local target="$REPOS_DIR/$dir"
            local status branch_info last_commit

            if [[ -d "$target/.git" ]]; then
                cd "$target"
                status="${GREEN}cloned${NC}"
                branch_info="$(git branch --show-current 2>/dev/null || echo 'detached')"
                last_commit="$(git log -1 --format='%h %s' 2>/dev/null | cut -c1-40)"
                cd "$REPOS_DIR"
            else
                status="${YELLOW}not cloned${NC}"
                branch_info="-"
                last_commit="-"
            fi

            printf "%-30s %b%-1s %-20s %s\\n" "$dir" "$status" "" "$branch_info" "$last_commit"
        fi
    done < "$REPOS_FILE"
}

# List configured repos
list_repos() {
    echo
    printf "%-30s %-50s %-12s\\n" "DIRECTORY" "URL" "BRANCH"
    printf "%-30s %-50s %-12s\\n" "---------" "---" "------"

    while IFS= read -r line; do
        if parse_repo_line "$line"; then
            local short_url="${url#https://github.com/}"
            printf "%-30s %-50s %-12s\\n" "$dir" "$short_url" "$branch"
        fi
    done < "$REPOS_FILE"
}

# Print usage
usage() {
    echo "Dev Environment Setup Script"
    echo
    echo "Usage:"
    echo "  ./setup.sh              Clone all repos (first time setup)"
    echo "  ./setup.sh clone        Clone all repos"
    echo "  ./setup.sh update       Update all repos (git pull)"
    echo "  ./setup.sh clone <dir>  Clone specific repo by directory name"
    echo "  ./setup.sh update <dir> Update specific repo by directory name"
    echo "  ./setup.sh status       Show status of all repos"
    echo "  ./setup.sh list         List configured repos"
    echo "  ./setup.sh help         Show this help"
    echo
    echo "Configuration:"
    echo "  Edit repos.txt to customize which repos to clone"
    echo "  and which branches to use."
    echo
    echo "Notes:"
    echo "  All repos are cloned with --filter=blob:none (blobless)."
    echo "  Full structure is visible, blobs fetched on-demand."
}

# Main
main() {
    ensure_repos_file

    local action="${1:-clone}"
    local target="${2:-}"

    case "$action" in
        clone)
            if [[ -n "$target" ]]; then
                handle_specific_repo "clone" "$target"
            else
                clone_all
            fi
            ;;
        update)
            if [[ -n "$target" ]]; then
                handle_specific_repo "update" "$target"
            else
                update_all
            fi
            ;;
        status)
            show_status
            ;;
        list)
            list_repos
            ;;
        help|--help|-h)
            usage
            ;;
        *)
            log_error "Unknown action: $action"
            usage
            exit 1
            ;;
    esac
}

main "$@"