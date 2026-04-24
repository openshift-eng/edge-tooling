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
#   ./setup.sh init <preset># Initialize from a preset (copies dev-env.yaml)
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPOS_DIR="$SCRIPT_DIR/repos"
DEV_ENV_YAML="$SCRIPT_DIR/dev-env.yaml"
DEV_ENV_TEMPLATE="$SCRIPT_DIR/dev-env.yaml.template"
REPOS_FILE="$SCRIPT_DIR/repos.txt"
REPOS_TEMPLATE="$SCRIPT_DIR/repos.txt.template"
PRESETS_DIR="$SCRIPT_DIR/presets"

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

# ─── YAML Parsing ─────────────────────────────────────────────────────────────
# Parse dev-env.yaml into pipe-separated lines: url|directory|branch|name|category|summary
# Uses yq if available, falls back to python3 with PyYAML, then to basic awk parsing.

parse_yaml_repos() {
    local yaml_file="$1"

    if command -v yq &>/dev/null; then
        yq -r '.repos[] | [.url, (.directory // .name), .branch, .name, .category, .summary] | join("|")' "$yaml_file" 2>/dev/null
        return $?
    fi

    if python3 -c "import yaml" 2>/dev/null; then
        python3 -c "
import yaml, sys
with open('$yaml_file') as f:
    data = yaml.safe_load(f)
for r in data.get('repos', []):
    print('|'.join([
        r.get('url',''), r.get('directory', r.get('name','')), r.get('branch','main'),
        r.get('name',''), r.get('category',''), r.get('summary','')
    ]))
" 2>/dev/null
        return $?
    fi

    log_error "Cannot parse dev-env.yaml: no YAML parser available."
    log_info "Install one of the following:"
    log_info "  yq          — https://github.com/mikefarah/yq"
    log_info "  python3-pyyaml — dnf install python3-pyyaml  (or pip install pyyaml)"
    return 1
}

# ─── Repo Source Detection ────────────────────────────────────────────────────
# Determines which config file to use and sets REPO_SOURCE and REPO_FORMAT.

REPO_SOURCE=""
REPO_FORMAT=""

detect_repo_source() {
    if [[ -f "$DEV_ENV_YAML" ]]; then
        REPO_SOURCE="$DEV_ENV_YAML"
        REPO_FORMAT="yaml"
    elif [[ -f "$REPOS_FILE" ]]; then
        log_warn "Using legacy repos.txt — consider migrating to dev-env.yaml"
        log_warn "  Run: ./setup.sh init <preset>  (or copy dev-env.yaml.template)"
        REPO_SOURCE="$REPOS_FILE"
        REPO_FORMAT="txt"
    elif [[ -f "$REPOS_TEMPLATE" ]]; then
        log_warn "No dev-env.yaml or repos.txt found."
        log_warn "A legacy repos.txt.template exists with preset-specific repos."
        log_info "Recommended: ./setup.sh init <preset>  (or copy dev-env.yaml.template)"
        read -rp "Use legacy template to create repos.txt? [y/N] " answer
        if [[ "$answer" == "y" || "$answer" == "Y" ]]; then
            cp "$REPOS_TEMPLATE" "$REPOS_FILE"
            log_success "Created repos.txt from template"
            REPO_SOURCE="$REPOS_FILE"
            REPO_FORMAT="txt"
        else
            log_info "Aborted. To get started:"
            log_info "  ./setup.sh init <preset>  — Initialize from a preset"
            log_info "  cp dev-env.yaml.template dev-env.yaml  — Start from template"
            exit 0
        fi
    else
        log_error "No repo configuration found!"
        log_info "Options:"
        log_info "  ./setup.sh init <preset>  — Initialize from a preset"
        log_info "  cp dev-env.yaml.template dev-env.yaml  — Start from template"
        exit 1
    fi
}

# ─── Line Iteration ──────────────────────────────────────────────────────────
# Iterates over repos from the detected source, calling a callback with:
#   url, dir, branch (set as globals for backward compat)

iterate_repos() {
    local callback="$1"

    if [[ "$REPO_FORMAT" == "yaml" ]]; then
        while IFS='|' read -r url dir branch _name _cat _summary; do
            if [[ -z "$url" || -z "$dir" ]]; then
                [[ -n "$_name" || -n "$url" ]] && log_warn "Skipping entry with missing url or name: ${_name:-${url:-unknown}}"
                continue
            fi
            "$callback"
        done < <(parse_yaml_repos "$REPO_SOURCE")
    else
        while IFS= read -r line; do
            if parse_repo_line "$line"; then
                "$callback"
            fi
        done < "$REPO_SOURCE"
    fi
}

# Parse a line from repos.txt (legacy format)
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
    if [[ -z "$dir" ]]; then
        log_warn "Skipping entry with missing directory: $url"
        return 1
    fi
    return 0
}

# ─── Clone / Update ──────────────────────────────────────────────────────────

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

    # Always distribute TNF context file as TNF-CONTEXT.md
    for ctx in "$PRESETS_DIR"/*/context/"$dir".md; do
        if [[ -f "$ctx" ]]; then
            cp "$ctx" "$target/TNF-CONTEXT.md"
            log_info "  Added TNF-CONTEXT.md"
            break
        fi
    done

    # Distribute supplemental CLAUDE.md only if repo has no native one
    if [[ ! -f "$target/CLAUDE.md" ]]; then
        for sup in "$PRESETS_DIR"/*/supplemental/"$dir".md; do
            if [[ -f "$sup" ]]; then
                cp "$sup" "$target/CLAUDE.md"
                log_info "  Added supplemental CLAUDE.md"
                break
            fi
        done
    fi
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

    _clone_callback() {
        clone_repo "$url" "$dir" "$branch"
    }
    iterate_repos _clone_callback

    echo
    log_success "All repositories cloned!"
}

# Update all repositories
update_all() {
    log_info "Updating all repositories..."
    echo

    _update_callback() {
        if [[ -d "$REPOS_DIR/$dir/.git" ]]; then
            update_repo "$dir"
        fi
    }
    iterate_repos _update_callback

    echo
    log_success "All repositories updated!"
}

# Clone or update a specific repo
handle_specific_repo() {
    local action="$1"
    local target_dir="$2"
    local found=false

    _find_callback() {
        if [[ "$dir" == "$target_dir" ]]; then
            found=true
            if [[ "$action" == "clone" ]]; then
                clone_repo "$url" "$dir" "$branch"
            else
                update_repo "$dir"
            fi
        fi
    }
    iterate_repos _find_callback

    if [[ "$found" == "false" ]]; then
        log_error "Repository '$target_dir' not found in $REPO_SOURCE"
        echo "Available repositories:"
        list_repos
        exit 1
    fi
}

# ─── Status / List ───────────────────────────────────────────────────────────

# Show status of all repos
show_status() {
    log_info "Repository status:"
    echo
    printf "%-30s %-12s %-20s %s\n" "DIRECTORY" "STATUS" "BRANCH" "LAST COMMIT"
    printf "%-30s %-12s %-20s %s\n" "---------" "------" "------" "-----------"

    _status_callback() {
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

        printf "%-30s $(echo -e $status)%-1s %-20s %s\n" "$dir" "" "$branch_info" "$last_commit"
    }
    iterate_repos _status_callback
}

# List configured repos
list_repos() {
    echo
    if [[ "$REPO_FORMAT" == "yaml" ]]; then
        printf "%-30s %-10s %-50s %-12s\n" "DIRECTORY" "CATEGORY" "URL" "BRANCH"
        printf "%-30s %-10s %-50s %-12s\n" "---------" "--------" "---" "------"

        while IFS='|' read -r url dir branch name cat summary; do
            [[ -z "$url" ]] && continue
            local short_url="${url#https://github.com/}"
            printf "%-30s %-10s %-50s %-12s\n" "$dir" "$cat" "$short_url" "$branch"
        done < <(parse_yaml_repos "$REPO_SOURCE")
    else
        printf "%-30s %-50s %-12s\n" "DIRECTORY" "URL" "BRANCH"
        printf "%-30s %-50s %-12s\n" "---------" "---" "------"

        while IFS= read -r line; do
            if parse_repo_line "$line"; then
                local short_url="${url#https://github.com/}"
                printf "%-30s %-50s %-12s\n" "$dir" "$short_url" "$branch"
            fi
        done < "$REPO_SOURCE"
    fi
}

# ─── Init from Preset ────────────────────────────────────────────────────────

init_preset() {
    local preset_name="$1"

    if [[ -z "$preset_name" ]]; then
        log_info "Available presets:"
        echo
        for preset_dir in "$PRESETS_DIR"/*/; do
            [[ ! -d "$preset_dir" ]] && continue
            local name
            name="$(basename "$preset_dir")"
            local desc=""
            if [[ -f "$preset_dir/preset.yaml" ]]; then
                desc=$(grep '^description:' "$preset_dir/preset.yaml" | sed 's/^description: *"*//;s/"*$//')
            fi
            printf "  %-15s %s\n" "$name" "$desc"
        done
        echo
        log_info "Usage: ./setup.sh init <preset-name>"
        exit 0
    fi

    local preset_dir="$PRESETS_DIR/$preset_name"
    if [[ ! -d "$preset_dir" ]]; then
        log_error "Preset '$preset_name' not found in $PRESETS_DIR/"
        log_info "Available presets:"
        for d in "$PRESETS_DIR"/*/; do
            [[ -d "$d" ]] && echo "  $(basename "$d")"
        done
        exit 1
    fi

    if [[ ! -f "$preset_dir/dev-env.yaml" ]]; then
        log_error "Preset '$preset_name' has no dev-env.yaml"
        exit 1
    fi

    if [[ -f "$DEV_ENV_YAML" ]]; then
        log_warn "dev-env.yaml already exists"
        read -rp "Overwrite? [y/N] " answer
        [[ "$answer" != "y" && "$answer" != "Y" ]] && { log_info "Aborted."; exit 0; }
    fi

    cp "$preset_dir/dev-env.yaml" "$DEV_ENV_YAML"
    log_success "Initialized dev-env.yaml from preset '$preset_name'"

    local settings_dir="$SCRIPT_DIR/.claude"
    local settings_file="$settings_dir/settings.local.json"
    if [[ ! -f "$settings_file" ]]; then
        local settings_tpl=""
        if [[ -f "$preset_dir/settings.local.json.tpl" ]]; then
            settings_tpl="$preset_dir/settings.local.json.tpl"
        elif [[ -f "$SCRIPT_DIR/settings.local.json.tpl" ]]; then
            settings_tpl="$SCRIPT_DIR/settings.local.json.tpl"
        fi
        if [[ -n "$settings_tpl" ]]; then
            mkdir -p "$settings_dir"
            cp "$settings_tpl" "$settings_file"
            log_success "Created .claude/settings.local.json from template"
        fi
    else
        log_warn ".claude/settings.local.json already exists, skipping"
    fi

    echo
    log_info "Next steps:"
    log_info "  ./setup.sh clone    — Clone all repositories"
    log_info "  ./setup.sh status   — Check repo status"
}

# ─── Usage ────────────────────────────────────────────────────────────────────

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
    echo "  ./setup.sh init <preset> Initialize from a preset"
    echo "  ./setup.sh help         Show this help"
    echo
    echo "Configuration:"
    echo "  dev-env.yaml (preferred) — YAML format with metadata"
    echo "  repos.txt (legacy)       — Pipe-separated format"
    echo
    echo "Presets:"
    echo "  Run './setup.sh init' to see available presets."
    echo
    echo "Notes:"
    echo "  All repos are cloned with --filter=blob:none (blobless)."
    echo "  Full structure is visible, blobs fetched on-demand."
}

# ─── Main ─────────────────────────────────────────────────────────────────────

main() {
    local action="${1:-clone}"
    local target="${2:-}"

    case "$action" in
        init)
            init_preset "$target"
            ;;
        clone)
            detect_repo_source
            if [[ -n "$target" ]]; then
                handle_specific_repo "clone" "$target"
            else
                clone_all
            fi
            ;;
        update)
            detect_repo_source
            if [[ -n "$target" ]]; then
                handle_specific_repo "update" "$target"
            else
                update_all
            fi
            ;;
        status)
            detect_repo_source
            show_status
            ;;
        list)
            detect_repo_source
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
