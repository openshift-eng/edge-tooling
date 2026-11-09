#!/bin/bash
#
# Workspace Setup Script (workspace plugin)
# =========================================
#
# Ships inside the plugin and self-derives its plugin root. Operates on a
# user-chosen *workspace root* (where dev-env.yaml, repos/, projects/, and
# workspace-local domains/ live), passed via --workspace or resolved from the
# environment / current directory.
#
# Usage:
#   setup.sh --workspace <path> init <name>       Initialize from a bundled domain
#   setup.sh --workspace <path> init <url>        Initialize from an external domain git URL
#   setup.sh --workspace <path> init <url#subdir> External pack repo with multiple domains
#   setup.sh --workspace <path> init --self [<name>] [--summary "..."] [--ignore-projects]
#                                                 Initialize a single-repo self-workspace
#   setup.sh [--workspace <path>] detect          Print the setup mode as JSON
#   setup.sh --workspace <path> clone             Clone all repos, link repo skills,
#                                                 write the root CLAUDE.md
#   setup.sh --workspace <path> clone <dir>       Clone a specific repo by directory name
#   setup.sh --workspace <path> update            Update all repos (git pull)
#   setup.sh --workspace <path> update <dir>      Update a specific repo by directory name
#   setup.sh --workspace <path> status            Show status of all repos
#   setup.sh --workspace <path> list              List configured repos
#   setup.sh --workspace <path> refresh-domain    Re-fetch domain from its recorded source
#
# --workspace may be omitted when WORKSPACE_ROOT / CLAUDE_PROJECT_DIR is set,
# or when run from inside a workspace (a directory containing dev-env.yaml).
#

set -e

# ─── Plugin root (self-derived) ───────────────────────────────────────────────
# This script lives at <plugin>/scripts/setup.sh, so the plugin root is one
# level up. Bundled domains and templates ship alongside it (read-only).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(dirname "$SCRIPT_DIR")"
BUNDLED_DOMAINS_DIR="$PLUGIN_ROOT/domains"
TEMPLATES_DIR="$PLUGIN_ROOT/templates"

# ─── Workspace root (resolved at runtime) ─────────────────────────────────────
# Populated by resolve_workspace_root(); everything mutable hangs off it.

WS_FLAG=""
WORKSPACE_ROOT_RESOLVED=""
REPOS_DIR=""
DEV_ENV_YAML=""
WORKSPACE_DOMAINS_DIR=""

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

# Turn a path into an absolute one without requiring it to exist.
abspath() {
    local p="$1"
    if [[ -d "$p" ]]; then
        (cd "$p" && pwd)
    elif [[ "$p" = /* ]]; then
        echo "$p"
    else
        echo "$PWD/$p"
    fi
}

# ─── Workspace Root Resolution ─────────────────────────────────────────────────
# Order: --workspace flag → WORKSPACE_ROOT env → CLAUDE_PROJECT_DIR env →
# walk up from $PWD to the nearest dir containing dev-env.yaml.
#
# $1 = mode: "create" tolerates a not-yet-existing workspace (used by init);
#            "require" (default) errors when no workspace can be determined.
# Sets WORKSPACE_ROOT_RESOLVED, REPOS_DIR, DEV_ENV_YAML, WORKSPACE_DOMAINS_DIR.
resolve_workspace_root() {
    local mode="${1:-require}"
    local ws=""

    if [[ -n "$WS_FLAG" ]]; then
        ws="$WS_FLAG"
    elif [[ -n "${WORKSPACE_ROOT:-}" ]]; then
        ws="$WORKSPACE_ROOT"
    elif [[ -n "${CLAUDE_PROJECT_DIR:-}" ]]; then
        ws="$CLAUDE_PROJECT_DIR"
    else
        local dir="$PWD"
        while :; do
            if [[ -f "$dir/dev-env.yaml" ]]; then
                ws="$dir"
                break
            fi
            [[ "$dir" == "/" ]] && break
            dir="$(dirname "$dir")"
        done
    fi

    if [[ -z "$ws" ]]; then
        if [[ "$mode" == "create" ]]; then
            ws="$PWD"
        else
            log_error "Could not determine the workspace root."
            log_info "Pass --workspace <path>, set WORKSPACE_ROOT, or run from inside"
            log_info "a workspace (a directory containing dev-env.yaml)."
            exit 1
        fi
    fi

    WORKSPACE_ROOT_RESOLVED="$(abspath "$ws")"
    REPOS_DIR="$WORKSPACE_ROOT_RESOLVED/repos"
    DEV_ENV_YAML="$WORKSPACE_ROOT_RESOLVED/dev-env.yaml"
    WORKSPACE_DOMAINS_DIR="$WORKSPACE_ROOT_RESOLVED/domains"
}

# Locate a domain directory by name: workspace domains/ first, then bundled.
# Echoes the path, or nothing if the domain is not found.
find_domain_dir() {
    local name="$1"
    [[ -z "$name" ]] && return 0
    if [[ -d "$WORKSPACE_DOMAINS_DIR/$name" ]]; then
        echo "$WORKSPACE_DOMAINS_DIR/$name"
    elif [[ -d "$BUNDLED_DOMAINS_DIR/$name" ]]; then
        echo "$BUNDLED_DOMAINS_DIR/$name"
    fi
}

# List available domains (workspace + bundled), workspace shadowing bundled,
# each tagged with its source. Portable (no bash-4 associative arrays).
list_available_domains() {
    local seen=" "
    local pair dirpath label domain_dir name desc
    for pair in "$WORKSPACE_DOMAINS_DIR|workspace" "$BUNDLED_DOMAINS_DIR|bundled"; do
        dirpath="${pair%%|*}"
        label="${pair##*|}"
        [[ -d "$dirpath" ]] || continue
        for domain_dir in "$dirpath"/*/; do
            [[ -d "$domain_dir" ]] || continue
            name="$(basename "$domain_dir")"
            case "$seen" in *" $name "*) continue;; esac
            seen="$seen$name "
            desc=""
            if [[ -f "$domain_dir/domain.yaml" ]]; then
                desc=$(grep '^description:' "$domain_dir/domain.yaml" | sed 's/^description: *"*//;s/"*$//')
            fi
            printf "  %-15s %-48s (%s)\n" "$name" "$desc" "$label"
        done
    done
}

# ─── YAML Parsing ─────────────────────────────────────────────────────────────
# Parse dev-env.yaml into pipe-separated lines: url|directory|branch|name|category|summary
# Uses yq if available, falls back to python3 with PyYAML.

parse_yaml_repos() {
    local yaml_file="$1"

    if command -v yq &>/dev/null; then
        yq -r '.repos[] | [.url, (.directory // .name), .branch, .name, .category, .summary] | join("|")' "$yaml_file" 2>/dev/null
        return $?
    fi

    if python3 -c "import yaml" 2>/dev/null; then
        python3 - "$yaml_file" << 'PYEOF'
import yaml, sys
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
for r in data.get('repos', []):
    print('|'.join([
        r.get('url',''), r.get('directory', r.get('name','')), r.get('branch','main'),
        r.get('name',''), r.get('category',''), r.get('summary','')
    ]))
PYEOF
        return $?
    fi

    log_error "Cannot parse dev-env.yaml: no YAML parser available."
    log_info "Install one of the following:"
    log_info "  yq          — https://github.com/mikefarah/yq"
    log_info "  python3-pyyaml — dnf install python3-pyyaml  (or pip install pyyaml)"
    return 1
}

# Read the domain: block from dev-env.yaml.
# Outputs: name|source|ref|subdir (empty string for missing fields)
parse_yaml_domain() {
    local yaml_file="${1:-$DEV_ENV_YAML}"

    if python3 -c "import yaml" 2>/dev/null; then
        python3 - "$yaml_file" << 'PYEOF'
import yaml, sys
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
p = data.get('domain', {})
if isinstance(p, str):
    p = {'name': p, 'source': 'bundled'}
elif not isinstance(p, dict):
    p = {}
print('|'.join([
    p.get('name',''), p.get('source','bundled'), p.get('ref',''), p.get('subdir','')
]))
PYEOF
        return 0
    fi

    if command -v yq &>/dev/null; then
        local name source ref subdir
        name=$(yq -r '(.domain.name // .domain) // ""' "$yaml_file" 2>/dev/null)
        source=$(yq -r '.domain.source // "bundled"' "$yaml_file" 2>/dev/null)
        ref=$(yq -r '.domain.ref // ""' "$yaml_file" 2>/dev/null)
        subdir=$(yq -r '.domain.subdir // ""' "$yaml_file" 2>/dev/null)
        echo "${name}|${source}|${ref}|${subdir}"
        return 0
    fi

    log_error "Cannot read domain config: no YAML parser available."
    log_info "Install one of the following:"
    log_info "  yq          — https://github.com/mikefarah/yq"
    log_info "  python3-pyyaml — dnf install python3-pyyaml  (or pip install pyyaml)"
    return 1
}

# Returns 0 if a dev-env.yaml declares a top-level self: block (single-repo
# self-workspace). grep-based so it needs no YAML parser (hook-safe).
has_self_block() {
    local yaml_file="${1:-$DEV_ENV_YAML}"
    grep -q '^self:' "$yaml_file" 2>/dev/null
}

# ─── Repo Source Detection ────────────────────────────────────────────────────
# Verifies dev-env.yaml exists, sets REPO_SOURCE and ACTIVE_DOMAIN_NAME.

REPO_SOURCE=""
ACTIVE_DOMAIN_NAME=""

detect_repo_source() {
    if [[ -f "$DEV_ENV_YAML" ]]; then
        REPO_SOURCE="$DEV_ENV_YAML"
        local domain_info
        domain_info=$(parse_yaml_domain "$DEV_ENV_YAML")
        ACTIVE_DOMAIN_NAME="${domain_info%%|*}"
        if [[ -n "$ACTIVE_DOMAIN_NAME" ]] && has_self_block; then
            log_error "dev-env.yaml declares both 'self:' and 'domain:' — they are mutually exclusive."
            log_info "Remove one of the two blocks from: $DEV_ENV_YAML"
            exit 1
        fi
    else
        log_error "No dev-env.yaml found in workspace: $WORKSPACE_ROOT_RESOLVED"
        log_info "Options:"
        log_info "  setup.sh --workspace $WORKSPACE_ROOT_RESOLVED init <domain>  — from a bundled domain"
        log_info "  setup.sh --workspace $WORKSPACE_ROOT_RESOLVED init <url>     — from an external domain git URL"
        exit 1
    fi
}

# ─── Line Iteration ──────────────────────────────────────────────────────────
# Iterates over repos from dev-env.yaml, calling a callback with:
#   url, dir, branch (set as globals for backward compat)

iterate_repos() {
    local callback="$1"
    local repo_lines
    repo_lines=$(parse_yaml_repos "$REPO_SOURCE") || {
        log_error "Failed to parse repositories from $REPO_SOURCE"
        return 1
    }
    if [[ -z "$repo_lines" ]]; then
        log_warn "No repositories found in $REPO_SOURCE"
        return 0
    fi
    while IFS='|' read -r url dir branch _name _cat _summary; do
        if [[ -z "$url" || -z "$dir" ]]; then
            [[ -n "$_name" || -n "$url" ]] && log_warn "Skipping entry with missing url or name: ${_name:-${url:-unknown}}"
            continue
        fi
        "$callback"
    done <<< "$repo_lines"
}

# ─── Clone / Update ──────────────────────────────────────────────────────────

# Distribute the active domain's context/supplemental files into a cloned repo.
distribute_domain_files() {
    local dir="$1"
    local target="$2"

    # Scope to the active domain when known — prevents collisions when multiple
    # installed domains contain files for a same-named repository.
    if [[ -n "$ACTIVE_DOMAIN_NAME" ]]; then
        local domain_base
        domain_base="$(find_domain_dir "$ACTIVE_DOMAIN_NAME")"
        if [[ -n "$domain_base" && -d "$domain_base" ]]; then
            if [[ -f "$domain_base/context/$dir.md" ]]; then
                cp "$domain_base/context/$dir.md" "$target/DOMAIN-CONTEXT.md"
                log_info "  Added DOMAIN-CONTEXT.md"
            fi
            if [[ ! -f "$target/CLAUDE.md" && -f "$domain_base/supplemental/$dir.md" ]]; then
                cp "$domain_base/supplemental/$dir.md" "$target/CLAUDE.md"
                log_info "  Added supplemental CLAUDE.md"
            fi
            return 0
        fi
    fi

    # Fallback for dev-env.yaml files without domain tracking: search workspace
    # domains first, then bundled.
    local base ctx sup
    for base in "$WORKSPACE_DOMAINS_DIR" "$BUNDLED_DOMAINS_DIR"; do
        [[ -d "$base" ]] || continue
        for ctx in "$base"/*/context/"$dir".md; do
            if [[ -f "$ctx" ]]; then
                cp "$ctx" "$target/DOMAIN-CONTEXT.md"
                log_info "  Added DOMAIN-CONTEXT.md"
                break 2
            fi
        done
    done

    if [[ ! -f "$target/CLAUDE.md" ]]; then
        for base in "$WORKSPACE_DOMAINS_DIR" "$BUNDLED_DOMAINS_DIR"; do
            [[ -d "$base" ]] || continue
            for sup in "$base"/*/supplemental/"$dir".md; do
                if [[ -f "$sup" ]]; then
                    cp "$sup" "$target/CLAUDE.md"
                    log_info "  Added supplemental CLAUDE.md"
                    break 2
                fi
            done
        done
    fi
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

    mkdir -p "$REPOS_DIR"

    log_info "Cloning $dir (blobless)..."
    git clone --filter=blob:none --branch "$branch" "$url" "$target"
    log_success "Cloned $dir (branch: $branch)"

    distribute_domain_files "$dir" "$target"
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

    if ! git -C "$target" diff --quiet HEAD 2>/dev/null; then
        log_warn "  $dir has local changes, stashing..."
        git -C "$target" stash
    fi

    git -C "$target" pull --rebase

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
            status="${GREEN}cloned${NC}"
            branch_info="$(git -C "$target" branch --show-current 2>/dev/null || echo 'detached')"
            last_commit="$(git -C "$target" log -1 --format='%h %s' 2>/dev/null | cut -c1-40)"
        else
            status="${YELLOW}not cloned${NC}"
            branch_info="-"
            last_commit="-"
        fi

        printf "%-30s $(echo -e "$status")%-1s %-20s %s\n" "$dir" "" "$branch_info" "$last_commit"
    }
    iterate_repos _status_callback
}

# List configured repos
list_repos() {
    echo
    printf "%-30s %-10s %-50s %-12s\n" "DIRECTORY" "CATEGORY" "URL" "BRANCH"
    printf "%-30s %-10s %-50s %-12s\n" "---------" "--------" "---" "------"

    while IFS='|' read -r url dir branch name cat _summary; do
        [[ -z "$url" ]] && continue
        local short_url="${url#https://github.com/}"
        printf "%-30s %-10s %-50s %-12s\n" "$dir" "$cat" "$short_url" "$branch"
    done < <(parse_yaml_repos "$REPO_SOURCE")
}

# ─── Domain Validation ────────────────────────────────────────────────────────

# Validate that a directory has the required domain layout.
# Returns non-zero and prints errors if validation fails.
validate_domain() {
    local domain_dir="$1"
    local source="${2:-$domain_dir}"
    local errors=0

    if [[ ! -f "$domain_dir/domain.yaml" ]]; then
        log_error "Missing domain.yaml in: $source"
        errors=$((errors + 1))
    fi

    if [[ ! -f "$domain_dir/dev-env.yaml" ]]; then
        log_error "Missing dev-env.yaml in: $source"
        log_info "  A valid domain must contain:"
        log_info "    domain.yaml              — name, description, metadata"
        log_info "    dev-env.yaml             — list of repos to clone"
        log_info "    context/<repo>.md        — (optional) per-repo context files"
        log_info "    supplemental/<repo>.md   — (optional) fallback CLAUDE.md for repos"
        log_info "    docs/                    — (optional) architecture / debugging docs"
        log_info "    settings.local.json.tpl  — (optional) Claude Code settings template"
        errors=$((errors + 1))
    fi

    if [[ $errors -gt 0 ]]; then
        return 1
    fi
    return 0
}

# Read the name field from a domain directory's domain.yaml
get_domain_name_from_dir() {
    local domain_dir="$1"

    if python3 -c "import yaml" 2>/dev/null; then
        python3 - "$domain_dir/domain.yaml" << 'PYEOF'
import yaml, sys
with open(sys.argv[1]) as f:
    data = yaml.safe_load(f)
print(data.get('name', ''))
PYEOF
        return 0
    fi

    if command -v yq &>/dev/null; then
        yq -r '.name // ""' "$domain_dir/domain.yaml" 2>/dev/null
        return 0
    fi

    grep '^name:' "$domain_dir/domain.yaml" | sed 's/^name: *//;s/"//g' | head -1
}

# ─── URL Detection ────────────────────────────────────────────────────────────

# Returns 0 if the argument looks like a git URL (before any #fragment)
is_git_url() {
    local url="${1%%#*}"
    [[ "$url" == https://* || "$url" == git@* || "$url" == ssh://* || \
       "$url" == git://* || "$url" == file://* || "$url" == *.git ]]
}

# ─── External Domain Fetching ─────────────────────────────────────────────────

# Clone an external domain pack into the workspace's domains/<name>/.
# Sets FETCHED_DOMAIN_NAME to the installed domain name.
FETCHED_DOMAIN_NAME=""

fetch_external_domain() {
    local raw_url="$1"
    local url="${raw_url%%#*}"
    local subdir=""
    [[ "$raw_url" == *"#"* ]] && subdir="${raw_url##*#}"

    local tmp_dir
    tmp_dir=$(mktemp -d)

    log_info "Fetching domain from $url ..."
    if ! git clone --depth 1 "$url" "$tmp_dir/pack"; then
        rm -rf "$tmp_dir"
        log_error "Failed to clone $url"
        exit 1
    fi

    local pack_dir="$tmp_dir/pack"
    if [[ -n "$subdir" ]]; then
        pack_dir="$tmp_dir/pack/$subdir"
        if [[ ! -d "$pack_dir" ]]; then
            log_error "Subdirectory '$subdir' not found in $url"
            log_info "Directories available in the pack repo:"
            for d in "$tmp_dir/pack"/*/; do
                [[ -d "$d" ]] || continue
                local dname
                dname="$(basename "$d")"
                [[ "$dname" == .* ]] && continue
                echo "  $dname"
            done
            rm -rf "$tmp_dir"
            exit 1
        fi
    fi

    if ! validate_domain "$pack_dir" "$raw_url"; then
        rm -rf "$tmp_dir"
        exit 1
    fi

    local domain_name
    domain_name=$(get_domain_name_from_dir "$pack_dir")
    if [[ -z "$domain_name" ]]; then
        domain_name="${subdir:-$(basename "${url%.git}")}"
    fi

    local dest="$WORKSPACE_DOMAINS_DIR/$domain_name"
    if [[ -d "$dest" ]]; then
        log_warn "Domain '$domain_name' already installed at domains/$domain_name/"
        if [[ -f "$dest/UPDATES.md" ]]; then
            log_warn "Local domain updates found (domains/$domain_name/UPDATES.md) — overwriting discards them."
            log_info "Run /workspace:update-domain's PR-back flow first to send them upstream."
        fi
        read -rp "Overwrite? [y/N] " answer
        if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
            rm -rf "$tmp_dir"
            log_info "Aborted."
            exit 0
        fi
        rm -rf "$dest"
    fi

    mkdir -p "$dest"
    cp -r "$pack_dir/." "$dest/"
    rm -rf "$dest/.git"

    rm -rf "$tmp_dir"
    log_success "Installed domain '$domain_name' from $url"
    FETCHED_DOMAIN_NAME="$domain_name"
}

# ─── Domain Source Recording ─────────────────────────────────────────────────

# Inject or replace the domain: block in a dev-env.yaml file.
# Uses a regex replace so comments in the rest of the file are preserved.
record_domain_source() {
    local yaml_file="$1"
    local name="$2"
    local source="$3"   # 'bundled' or the git URL
    local ref="${4:-}"
    local subdir="${5:-}"

    if ! python3 -c "import sys" 2>/dev/null; then
        log_warn "python3 not available — domain source not recorded in dev-env.yaml"
        return 0
    fi

    python3 - "$yaml_file" "$name" "$source" "$ref" "$subdir" << 'PYEOF'
import re, sys
yaml_file, name, source, ref, subdir = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]

with open(yaml_file) as f:
    text = f.read()

lines = ['domain:', f'  name: {name}', f'  source: {source}']
if ref:
    lines.append(f'  ref: {ref}')
if subdir:
    lines.append(f'  subdir: {subdir}')
block = '\n'.join(lines)

# Replace an existing domain key — handles both scalar (domain: <name>) and
# mapping (domain:\n  name: <name>\n  ...) forms.
new_text = re.sub(
    r'^domain:[^\n]*(?:\n[ \t]+[^\n]*)*',
    block,
    text,
    count=1,
    flags=re.MULTILINE
)

# If no domain: key existed, prepend the block
if not re.search(r'^domain:', new_text, re.MULTILINE):
    new_text = block + '\n\n' + text

with open(yaml_file, 'w') as f:
    f.write(new_text)
PYEOF
}

# ─── Settings Merge ──────────────────────────────────────────────────────────
# Merge a settings template into an existing settings.local.json.
# Appends new permissions.allow entries and adds missing top-level keys
# without overwriting existing values.

merge_settings_template() {
    local target="$1"
    local template="$2"

    if ! python3 -c "import json" 2>/dev/null; then
        log_warn "python3 not available — cannot merge settings template"
        return 0
    fi

    local result
    if ! result=$(python3 - "$target" "$template" << 'PYEOF'
import json, sys

target_path, template_path = sys.argv[1], sys.argv[2]

with open(target_path) as f:
    target = json.load(f)
with open(template_path) as f:
    template = json.load(f)

changed = []

# Merge permissions.allow: append entries not already present
if "permissions" in template and "allow" in template["permissions"]:
    target.setdefault("permissions", {}).setdefault("allow", [])
    existing = set(target["permissions"]["allow"])
    for entry in template["permissions"]["allow"]:
        if entry not in existing:
            target["permissions"]["allow"].append(entry)
            changed.append(f"  + permission: {entry}")

# Merge top-level keys that don't exist in target (env, etc.)
for key in template:
    if key == "permissions":
        continue
    if key not in target:
        target[key] = template[key]
        changed.append(f"  + {key}")

if changed:
    with open(target_path, "w") as f:
        json.dump(target, f, indent=2)
        f.write("\n")
    print("\n".join(changed))
else:
    print("__NO_CHANGES__")
PYEOF
    ); then
        log_error "Failed to merge settings template"
        return 1
    fi

    if [[ "$result" == "__NO_CHANGES__" ]]; then
        log_success ".claude/settings.local.json already up to date"
    else
        log_success "Merged template into .claude/settings.local.json:"
        echo "$result"
    fi
}

# Copy or merge the settings template into the workspace's .claude/settings.local.json.
apply_settings_template() {
    local domain_dir="$1"

    local settings_dir="$WORKSPACE_ROOT_RESOLVED/.claude"
    local settings_file="$settings_dir/settings.local.json"
    local settings_tpl=""
    if [[ -f "$domain_dir/settings.local.json.tpl" ]]; then
        settings_tpl="$domain_dir/settings.local.json.tpl"
    elif [[ -f "$TEMPLATES_DIR/settings.local.json.tpl" ]]; then
        settings_tpl="$TEMPLATES_DIR/settings.local.json.tpl"
    fi

    if [[ -z "$settings_tpl" ]]; then
        : # no template available
    elif [[ ! -f "$settings_file" ]]; then
        mkdir -p "$settings_dir"
        cp "$settings_tpl" "$settings_file"
        log_success "Created .claude/settings.local.json from template"
    else
        merge_settings_template "$settings_file" "$settings_tpl"
    fi
}

# Pre-create the workspace's .claude/skills/ so Claude Code's filesystem
# watcher (which only monitors directories that exist at session start)
# picks up skill symlinks live. Used by /workspace:new-project skill linking.
ensure_skills_dir() {
    local skills_dir="$WORKSPACE_ROOT_RESOLVED/.claude/skills"
    if [[ ! -d "$skills_dir" ]]; then
        mkdir -p "$skills_dir"
        log_success "Created .claude/skills/ (skill symlink dir)"
    fi
}

# ─── Init from Domain ────────────────────────────────────────────────────────

init_domain() {
    local domain_name_or_url="$1"

    if [[ -z "$domain_name_or_url" ]]; then
        log_info "Available domains:"
        echo
        list_available_domains
        echo
        log_info "Usage:"
        log_info "  setup.sh --workspace <path> init <domain-name>   Bundled or workspace domain"
        log_info "  setup.sh --workspace <path> init <url>           External domain git URL"
        log_info "  setup.sh --workspace <path> init <url#subdir>    Domain pack with multiple domains"
        exit 0
    fi

    # A fresh workspace may not exist yet — create it.
    mkdir -p "$WORKSPACE_ROOT_RESOLVED"

    local domain_name
    local source_type="bundled"
    local source_subdir=""

    if is_git_url "$domain_name_or_url"; then
        # External domain: fetch from git, then continue with the installed copy
        [[ "$domain_name_or_url" == *"#"* ]] && source_subdir="${domain_name_or_url##*#}"
        fetch_external_domain "$domain_name_or_url"
        domain_name="$FETCHED_DOMAIN_NAME"
        source_type="${domain_name_or_url%%#*}"   # store URL without fragment
    else
        domain_name="$domain_name_or_url"
    fi

    local domain_dir
    domain_dir="$(find_domain_dir "$domain_name")"
    if [[ -z "$domain_dir" ]]; then
        log_error "Domain '$domain_name' not found."
        log_info "Available domains:"
        list_available_domains
        exit 1
    fi

    if ! validate_domain "$domain_dir"; then
        exit 1
    fi

    if [[ -f "$DEV_ENV_YAML" ]]; then
        log_warn "dev-env.yaml already exists"
        read -rp "Overwrite? [y/N] " answer
        [[ "$answer" != "y" && "$answer" != "Y" ]] && { log_info "Aborted."; exit 0; }
    fi

    cp "$domain_dir/dev-env.yaml" "$DEV_ENV_YAML"
    log_success "Initialized dev-env.yaml from domain '$domain_name'"

    # Record where this domain came from so refresh-domain can re-fetch it
    record_domain_source "$DEV_ENV_YAML" "$domain_name" "$source_type" "" "$source_subdir"

    apply_settings_template "$domain_dir"

    echo
    log_info "Next steps:"
    log_info "  setup.sh --workspace $WORKSPACE_ROOT_RESOLVED clone    — Clone all repositories"
    log_info "  setup.sh --workspace $WORKSPACE_ROOT_RESOLVED status   — Check repo status"
}

# ─── Init Self (single-repo workspace) ───────────────────────────────────────
# The workspace root IS the wrapped repo checkout. No domain, nothing to
# clone; dev-env.yaml gets a self: block and an empty repos list.

init_self() {
    local name="" summary="" ignore_projects=false
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --summary) summary="$2"; shift 2 ;;
            --summary=*) summary="${1#*=}"; shift ;;
            --ignore-projects) ignore_projects=true; shift ;;
            *) name="$1"; shift ;;
        esac
    done

    if [[ ! -d "$WORKSPACE_ROOT_RESOLVED/.git" ]]; then
        log_error "Not a git repository root: $WORKSPACE_ROOT_RESOLVED"
        log_info "A self-workspace wraps an existing repo checkout."
        log_info "Run with --workspace pointing at the repo root."
        exit 1
    fi

    if [[ -f "$DEV_ENV_YAML" ]]; then
        log_error "dev-env.yaml already exists: $DEV_ENV_YAML"
        log_info "This workspace is already initialized. Edit the file directly,"
        log_info "or remove it and re-run 'init --self' to start over."
        exit 1
    fi

    [[ -z "$name" ]] && name="$(basename "$WORKSPACE_ROOT_RESOLVED")"

    # Safely substitute __SELF_NAME__ with the provided name, handling sed
    # metacharacters (/, &, \). Write to a temp file first to avoid truncating
    # dev-env.yaml on substitution failure.
    local tmp_dev_env name_escaped
    tmp_dev_env=$(mktemp) || { log_error "Cannot create temporary file"; return 1; }

    # Escape sed metacharacters in the name: \ -> \\, & -> \&, / -> \/
    # Process in order to avoid double-escaping.
    name_escaped="$name"
    name_escaped="${name_escaped//\\/\\\\}"
    name_escaped="${name_escaped//&/\\&}"
    name_escaped="${name_escaped//\//\\/}"

    if ! sed "s/__SELF_NAME__/$name_escaped/" "$TEMPLATES_DIR/dev-env-self.yaml.template" > "$tmp_dev_env"; then
        rm -f "$tmp_dev_env"
        log_error "Failed to substitute name in template"
        return 1
    fi

    if ! mv "$tmp_dev_env" "$DEV_ENV_YAML"; then
        rm -f "$tmp_dev_env"
        log_error "Failed to create dev-env.yaml"
        return 1
    fi

    if [[ -n "$summary" ]]; then
        python3 - "$DEV_ENV_YAML" "$summary" << 'PYEOF'
import json, sys
path, summary = sys.argv[1], sys.argv[2]
text = open(path).read().replace('summary: ""', 'summary: ' + json.dumps(summary), 1)
open(path, 'w').write(text)
PYEOF
    fi

    log_success "Initialized dev-env.yaml (self-workspace: $name)"

    mkdir -p "$WORKSPACE_ROOT_RESOLVED/projects"
    if [[ "$ignore_projects" == "true" ]]; then
        local gi="$WORKSPACE_ROOT_RESOLVED/.gitignore"
        if ! grep -qxF 'projects/' "$gi" 2>/dev/null; then
            [[ -s "$gi" && -n "$(tail -c1 "$gi")" ]] && echo >> "$gi"
            echo 'projects/' >> "$gi"
            log_success "Added projects/ to .gitignore"
        fi
    fi
    apply_settings_template ""

    echo
    log_info "Next steps:"
    [[ -z "$summary" ]] && log_info "  Fill in self.summary in dev-env.yaml"
    log_info "  /workspace:new-project — scaffold a task under projects/"
}

# ─── Detect ──────────────────────────────────────────────────────────────────
# Print {"mode": ..., "root": ...} for /workspace:setup-environment:
#   self    — dev-env.yaml with a self: block (already set up)
#   domain  — dev-env.yaml with a domain: block (offer refresh)
#   custom  — dev-env.yaml without either (e.g. from /workspace:create-domain)
#   repo    — no dev-env.yaml, inside a git repo (root = repo top level)
#   new     — none of the above (root = the candidate directory)

detect_mode() {
    local mode root="$WORKSPACE_ROOT_RESOLVED"
    if [[ -f "$DEV_ENV_YAML" ]]; then
        if has_self_block; then
            mode=self
        elif grep -q '^domain:' "$DEV_ENV_YAML"; then
            mode=domain
        else
            mode=custom
        fi
    elif root_git="$(git -C "$root" rev-parse --show-toplevel 2>/dev/null)" && [[ -n "$root_git" ]]; then
        mode=repo
        root="$root_git"
    else
        mode=new
    fi
    python3 -c 'import json,sys; print(json.dumps({"mode": sys.argv[1], "root": sys.argv[2]}))' "$mode" "$root"
}

# ─── Root CLAUDE.md ──────────────────────────────────────────────────────────
# Create $WS/CLAUDE.md from the template, or refresh only the repo table
# between the AUTO-GENERATED markers. Never runs in a self-workspace.

write_root_claude_md() {
    if has_self_block; then echo "skipped (self-workspace)"; return 0; fi
    local rows
    rows=$(parse_yaml_repos "$REPO_SOURCE" | while IFS='|' read -r url dir _b name cat summary; do
        [[ -z "$url" || -z "$dir" ]] && continue
        printf '| `%s` | %s | %s |\n' "${name:-$dir}" "$cat" "$summary"
    done)
    python3 - "$WORKSPACE_ROOT_RESOLVED/CLAUDE.md" "$TEMPLATES_DIR/workspace-CLAUDE.md.template" \
        "${ACTIVE_DOMAIN_NAME:-custom}" "$rows" << 'PYEOF'
import os, re, sys
target, template, domain, rows = sys.argv[1:5]
table = "| Name | Category | Summary |\n|------|----------|---------|\n" + rows
block = re.compile(r"(<!-- AUTO-GENERATED[^\n]*-->\n(?:<!--[^\n]*-->\n)*).*?(<!-- END AUTO-GENERATED -->)", re.S)
if os.path.isfile(target):
    text = open(target).read()
    if not block.search(text):
        print("unchanged (no AUTO-GENERATED markers)")
        sys.exit(0)
    action = "updated"
else:
    text = open(template).read().replace("__DOMAIN__", domain)
    action = "created"
text = block.sub(lambda m: m.group(1) + "\n" + table + "\n" + m.group(2), text, count=1)
open(target, "w").write(text)
print(action)
PYEOF
}

# Link every repo-shipped skill into $WS/.claude/skills/ (skills.py link-all).
link_repo_skills() {
    local out
    if ! out=$(WORKSPACE_ROOT="$WORKSPACE_ROOT_RESOLVED" python3 "$SCRIPT_DIR/skills.py" link-all 2>&1); then
        log_warn "Repo skill linking failed: $out"
        return 0
    fi
    python3 - "$out" << 'PYEOF' || log_warn "Repo skill linking: $out"
import json, sys
r = json.loads(sys.argv[1])
if r.get("status") != "ok":
    print(f"[WARN] Repo skills not linked: {r.get('error')}")
    sys.exit(0)
for s in r["linked"]:
    print(f"[OK] Linked skill /{s['name']} (from {s['repo']})")
for s in r["skipped"]:
    print(f"[WARN] Skill {s['name']} from {s['repo']} skipped: {s['reason']}")
for e in r["errors"]:
    print(f"[WARN] {e}")
PYEOF
}

# ─── Refresh Domain ──────────────────────────────────────────────────────────

refresh_domain() {
    if [[ ! -f "$DEV_ENV_YAML" ]]; then
        log_error "No dev-env.yaml found. Run 'init' first."
        exit 1
    fi

    if has_self_block; then
        log_error "This is a single-repo self-workspace — no domain to refresh."
        log_info "Self-workspaces have no domain; edit dev-env.yaml directly."
        exit 1
    fi

    local domain_info
    domain_info=$(parse_yaml_domain "$DEV_ENV_YAML")
    local domain_name source ref subdir
    IFS='|' read -r domain_name source ref subdir <<< "$domain_info"

    if [[ -z "$domain_name" ]]; then
        log_error "No domain recorded in dev-env.yaml."
        log_info "Re-run 'init <domain>' to initialize with source tracking."
        exit 1
    fi

    log_info "Refreshing domain '$domain_name' (source: $source)"

    if [[ "$source" == "bundled" ]]; then
        local domain_dir
        domain_dir="$(find_domain_dir "$domain_name")"
        if [[ -z "$domain_dir" ]]; then
            log_error "Bundled domain '$domain_name' not found."
            exit 1
        fi
        if ! validate_domain "$domain_dir"; then
            exit 1
        fi
    else
        # Re-fetch from git URL (source holds the URL, subdir holds the fragment path)
        local raw_url="$source"
        [[ -n "$subdir" ]] && raw_url="${source}#${subdir}"
        fetch_external_domain "$raw_url"
        domain_name="$FETCHED_DOMAIN_NAME"
    fi

    local domain_dir
    domain_dir="$(find_domain_dir "$domain_name")"

    log_warn "This will overwrite dev-env.yaml with the refreshed domain."
    read -rp "Continue? [y/N] " answer
    [[ "$answer" != "y" && "$answer" != "Y" ]] && { log_info "Aborted."; exit 0; }

    cp "$domain_dir/dev-env.yaml" "$DEV_ENV_YAML"
    record_domain_source "$DEV_ENV_YAML" "$domain_name" "$source" "$ref" "$subdir"

    log_success "Domain '$domain_name' refreshed."
    log_info "Run 'clone' to clone any newly added repositories."
}

# ─── Usage ────────────────────────────────────────────────────────────────────

usage() {
    echo "Workspace Setup Script (workspace plugin)"
    echo
    echo "Usage:"
    echo "  setup.sh [--workspace <path>] <command> [args]"
    echo
    echo "Commands:"
    echo "  init <name>           Initialize from a bundled/workspace domain"
    echo "  init <url>            Initialize from an external domain git URL"
    echo "  init <url#subdir>     External pack containing multiple domains"
    echo "  init --self [<name>] [--summary \"...\"] [--ignore-projects]"
    echo "                        Initialize a single-repo self-workspace"
    echo "                        (workspace root = the repo checkout; no domain)"
    echo "  detect                Print the setup mode as JSON"
    echo "  clone                 Clone all repos, link repo skills, write CLAUDE.md"
    echo "  clone <dir>           Clone a specific repo by directory name"
    echo "  update                Update all repos (git pull)"
    echo "  update <dir>          Update a specific repo by directory name"
    echo "  status                Show status of all repos"
    echo "  list                  List configured repos"
    echo "  refresh-domain        Re-fetch domain from its recorded source"
    echo "  help                  Show this help"
    echo
    echo "Workspace root:"
    echo "  --workspace <path> selects where dev-env.yaml, repos/, projects/,"
    echo "  and workspace-local domains/ live. If omitted, WORKSPACE_ROOT or"
    echo "  CLAUDE_PROJECT_DIR is used, else the nearest ancestor of \$PWD that"
    echo "  contains dev-env.yaml."
    echo
    echo "Domains:"
    echo "  Run 'init' (no args) to list bundled + workspace domains."
    echo "  External domains are fetched from git into the workspace's domains/<name>/."
    echo
    echo "Notes:"
    echo "  All repos are cloned with --filter=blob:none (blobless)."
    echo "  Full structure is visible, blobs fetched on-demand."
}

# ─── Main ─────────────────────────────────────────────────────────────────────

main() {
    # Parse --workspace anywhere on the command line, before the subcommand.
    local positionals=()
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --workspace)
                WS_FLAG="$2"; shift 2 ;;
            --workspace=*)
                WS_FLAG="${1#*=}"; shift ;;
            *)
                positionals+=("$1"); shift ;;
        esac
    done
    set -- "${positionals[@]}"

    local action="${1:-clone}"
    local target="${2:-}"

    case "$action" in
        init)
            resolve_workspace_root create
            if [[ "$target" == "--self" ]]; then
                shift 2
                init_self "$@"
            else
                init_domain "$target"
            fi
            ensure_skills_dir
            ;;
        detect)
            resolve_workspace_root create
            detect_mode
            ;;
        refresh-domain)
            resolve_workspace_root require
            refresh_domain
            ;;
        clone)
            resolve_workspace_root require
            ensure_skills_dir
            detect_repo_source
            if [[ -n "$target" ]]; then
                handle_specific_repo "clone" "$target"
            else
                clone_all
            fi
            apply_settings_template "$(find_domain_dir "$ACTIVE_DOMAIN_NAME")"
            link_repo_skills
            log_info "Root CLAUDE.md: $(write_root_claude_md)"
            ;;
        update)
            resolve_workspace_root require
            detect_repo_source
            if [[ -n "$target" ]]; then
                handle_specific_repo "update" "$target"
            else
                update_all
            fi
            ;;
        status)
            resolve_workspace_root require
            detect_repo_source
            show_status
            ;;
        list)
            resolve_workspace_root require
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
