#!/usr/bin/bash
set -euo pipefail

# Validate the fork remote and push changes.
# Usage: pr-push.sh <branch> [commit-message] [--expected-files file1,file2,...]
# Exit codes: 0=success, 1=nothing to push, 3=error

die() {
    echo "Error: $1" >&2
    exit 3
}

escape_regex() {
    # Escape ERE metacharacters so dynamic input is treated literally.
    printf '%s' "$1" | sed -E 's/[][\\.^$*+?(){}|]/\\&/g'
}

check_dependencies() {
    command -v gh >/dev/null 2>&1 || die "gh CLI is not installed"
    command -v git >/dev/null 2>&1 || die "git is not installed"
    command -v jq >/dev/null 2>&1 || die "jq is not installed"
    gh auth status >/dev/null 2>&1 || die "gh CLI is not authenticated — run 'gh auth login'"
}

GH_USER=""

resolve_gh_user() {
    if [[ -z "${GH_USER}" ]]; then
        GH_USER=$(gh api user --jq '.login') || die "Failed to get GitHub username"
    fi
}

find_fork_remote() {
    resolve_gh_user
    local gh_user="${GH_USER}"

    # Determine expected repo name from PR URL or git directory name
    local expected_repo=""
    local pr_url="${PR_MONITOR_PR_URL:-}"
    if [[ -n "${pr_url}" ]]; then
        expected_repo=$(echo "${pr_url}" | cut -d'/' -f5)
    else
        expected_repo=$(basename "$(git rev-parse --show-toplevel 2>/dev/null)")
    fi

    local remote
    # Search for a push remote matching both the GitHub username and repo name (case-insensitive)
    if [[ -n "${expected_repo}" ]]; then
        local expected_repo_escaped
        expected_repo_escaped=$(escape_regex "${expected_repo}")
        remote=$(git remote -v \
            | grep '(push)' \
            | awk '{print $1, $2}' \
            | grep -i "${gh_user}" \
            | grep -iE "/${expected_repo_escaped}(\\.git)?$" \
            | head -1 \
            | awk '{print $1}')
    fi

    # Fallback: match by username only if repo-specific match failed
    if [[ -z "${remote:-}" ]]; then
        remote=$(git remote -v \
            | grep '(push)' \
            | awk '{print $1, $2}' \
            | grep -i "${gh_user}" \
            | head -1 \
            | awk '{print $1}')
    fi

    if [[ -z "${remote}" ]]; then
        # Fallback: first push remote that is not named "upstream"
        remote=$(git remote -v \
            | grep '(push)' \
            | awk '$1 != "upstream" {print $1}' \
            | head -1)
    fi

    if [[ -z "${remote}" ]]; then
        die "No fork remote found — add a remote pointing to your fork"
    fi

    echo "${remote}"
}

validate_not_upstream() {
    local remote="$1"
    local remote_url
    remote_url=$(git remote get-url "${remote}") || die "Failed to get URL for remote '${remote}'"

    local pr_url="${PR_MONITOR_PR_URL:-}"
    if [[ -z "${pr_url}" ]]; then
        return 0
    fi

    # Extract org/repo from PR URL (e.g., https://github.com/org/repo/pull/123)
    local upstream_org upstream_repo
    upstream_org=$(echo "${pr_url}" | cut -d'/' -f4)
    upstream_repo=$(echo "${pr_url}" | cut -d'/' -f5)
    local upstream_org_escaped upstream_repo_escaped
    upstream_org_escaped=$(escape_regex "${upstream_org}")
    upstream_repo_escaped=$(escape_regex "${upstream_repo}")

    resolve_gh_user
    local gh_user="${GH_USER}"

    # Match both HTTPS (org/repo) and SSH (org/repo.git) URL patterns
    if echo "${remote_url}" | grep -qiE "${upstream_org_escaped}/${upstream_repo_escaped}(\\.git)?$|:${upstream_org_escaped}/${upstream_repo_escaped}(\\.git)?$"; then
        if [[ "${upstream_org,,}" != "${gh_user,,}" ]]; then
            die "Remote '${remote}' points to upstream ${upstream_org}/${upstream_repo} — refusing to push directly to upstream"
        fi
    fi
}


check_blocked_patterns() {
    local staged_files
    staged_files=$(git diff --cached --name-only)
    [[ -z "${staged_files}" ]] && return 0

    # Tier 1: substring matches (case-insensitive, anywhere in path)
    local blocked_substrings=("rbac" "secret" "credential" "password" "passwd" "kubeconfig" "htpasswd" "token")

    # Tier 2: regex matches (extended regex against full path)
    local blocked_regexes=(
        '\.(key|pem|p12|pfx|jks|keystore)$'
        '(^|/)\.env(\..+)?$'
        '(^|/)id_(rsa|ed25519|ecdsa|dsa)'
        '(^|/)\.claude/.+\.lock$'
    )

    local file pattern
    while IFS= read -r file; do
        for pattern in "${blocked_substrings[@]}"; do
            if echo "${file}" | grep -qi "${pattern}"; then
                die "staged file '${file}' matches blocked security pattern '${pattern}'"
            fi
        done
        for pattern in "${blocked_regexes[@]}"; do
            if echo "${file}" | grep -qiE "${pattern}"; then
                die "staged file '${file}' matches blocked security pattern '${pattern}'"
            fi
        done
    done <<< "${staged_files}"
}

main() {
    [[ $# -lt 1 ]] && die "Usage: $(basename "$0") <branch> [commit-message] [--expected-files file1,file2,...]"

    local branch="" commit_message="" expected_files=""

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --expected-files)
                [[ $# -lt 2 ]] && die "--expected-files requires a value"
                expected_files="$2"
                shift 2
                ;;
            *)
                if [[ -z "${branch}" ]]; then
                    branch="$1"
                elif [[ -z "${commit_message}" ]]; then
                    commit_message="$1"
                fi
                shift
                ;;
        esac
    done

    [[ -z "${branch}" ]] && die "Usage: $(basename "$0") <branch> [commit-message] [--expected-files file1,file2,...]"

    check_dependencies

    # If a commit message is provided, stage and commit
    if [[ -n "${commit_message}" ]]; then
        local has_changes
        has_changes=$(git status --porcelain)
        if [[ -z "${has_changes}" ]]; then
            echo '{"pushed": false, "reason": "no changes to commit"}'
            exit 1
        fi

        if [[ -z "${expected_files}" ]]; then
            die "--expected-files is required when committing"
        fi

        git reset HEAD -- . >/dev/null 2>&1 || true

        local file
        IFS=',' read -ra expected_arr <<< "${expected_files}"
        for file in "${expected_arr[@]}"; do
            git add -- "${file}" || die "Failed to stage '${file}'"
        done

        check_blocked_patterns

        local unexpected_dirty
        unexpected_dirty=$(git diff --name-only)
        if [[ -n "${unexpected_dirty}" ]]; then
            echo "Warning: unstaged modified files not in expected list:" >&2
            while IFS= read -r line; do
                echo "  ${line}" >&2
            done <<< "${unexpected_dirty}"
        fi

        git commit -m "${commit_message}" \
            || die "git commit failed"
    fi

    local remote
    remote=$(find_fork_remote)
    validate_not_upstream "${remote}"

    git push "${remote}" "HEAD:${branch}" \
        || die "git push to ${remote} failed"

    local sha
    sha=$(git rev-parse --short HEAD)

    jq -n --arg remote "${remote}" --arg branch "${branch}" --arg sha "${sha}" \
        '{"pushed": true, "remote": $remote, "branch": $branch, "sha": $sha}'
}

main "$@"
