#!/usr/bin/bash
set -euo pipefail

# Validate the fork remote and push changes.
# Exit codes: 0=success, 1=nothing to push, 3=error

die() {
    echo "Error: $1" >&2
    exit 3
}

check_dependencies() {
    command -v gh >/dev/null 2>&1 || die "gh CLI is not installed"
    command -v git >/dev/null 2>&1 || die "git is not installed"
    gh auth status >/dev/null 2>&1 || die "gh CLI is not authenticated — run 'gh auth login'"
}

find_fork_remote() {
    local gh_user
    gh_user=$(gh api user --jq '.login') || die "Failed to get GitHub username"

    local remote
    # Search for a push remote matching the GitHub username (case-insensitive)
    remote=$(git remote -v \
        | grep '(push)' \
        | awk '{print $1, $2}' \
        | grep -i "${gh_user}" \
        | head -1 \
        | awk '{print $1}')

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

    local gh_user
    gh_user=$(gh api user --jq '.login') || die "Failed to get GitHub username"

    # If the remote URL contains the upstream org/repo AND the org doesn't match the gh user
    if echo "${remote_url}" | grep -qi "${upstream_org}/${upstream_repo}"; then
        if [[ "${upstream_org,,}" != "${gh_user,,}" ]]; then
            die "Remote '${remote}' points to upstream ${upstream_org}/${upstream_repo} — refusing to push directly to upstream"
        fi
    fi
}

main() {
    [[ $# -lt 1 ]] && die "Usage: $(basename "$0") <branch> [commit-message]"

    local branch="$1"
    local commit_message="${2:-}"

    check_dependencies

    # If a commit message is provided, stage and commit
    if [[ -n "${commit_message}" ]]; then
        local has_changes
        has_changes=$(git status --porcelain)
        if [[ -z "${has_changes}" ]]; then
            echo '{"pushed": false, "reason": "no changes to commit"}'
            exit 1
        fi
        git add -A
        git commit -m "${commit_message}"
    fi

    local remote
    remote=$(find_fork_remote)
    validate_not_upstream "${remote}"

    git push "${remote}" "HEAD:${branch}"

    local sha
    sha=$(git rev-parse --short HEAD)

    echo "{\"pushed\": true, \"remote\": \"${remote}\", \"branch\": \"${branch}\", \"sha\": \"${sha}\"}"
}

main "$@"
