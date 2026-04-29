#!/usr/bin/bash
set -euo pipefail

# Validate the fork remote and push changes.
# Usage: pr-push.sh <branch> [commit-message] [--expected-files file1,file2,...]
# Exit codes: 0=success, 1=nothing to push, 2=file mismatch, 3=error

die() {
    echo "Error: $1" >&2
    exit 3
}

check_dependencies() {
    command -v gh >/dev/null 2>&1 || die "gh CLI is not installed"
    command -v git >/dev/null 2>&1 || die "git is not installed"
    command -v jq >/dev/null 2>&1 || die "jq is not installed"
    gh auth status >/dev/null 2>&1 || die "gh CLI is not authenticated — run 'gh auth login'"
}

find_fork_remote() {
    local gh_user
    gh_user=$(gh api user --jq '.login') || die "Failed to get GitHub username"

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
        remote=$(git remote -v \
            | grep '(push)' \
            | awk '{print $1, $2}' \
            | grep -i "${gh_user}" \
            | grep -i "/${expected_repo}\\.git\|/${expected_repo}$" \
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

    local gh_user
    gh_user=$(gh api user --jq '.login') || die "Failed to get GitHub username"

    # Match both HTTPS (org/repo) and SSH (org/repo.git) URL patterns
    if echo "${remote_url}" | grep -qiE "${upstream_org}/${upstream_repo}(\.git)?$|:${upstream_org}/${upstream_repo}(\.git)?$"; then
        if [[ "${upstream_org,,}" != "${gh_user,,}" ]]; then
            die "Remote '${remote}' points to upstream ${upstream_org}/${upstream_repo} — refusing to push directly to upstream"
        fi
    fi
}

verify_staged_files() {
    local expected_csv="$1"

    local expected_sorted
    expected_sorted=$(echo "${expected_csv}" | tr ',' '\n' | sort)

    local staged_sorted
    staged_sorted=$(git diff --cached --name-only | sort)

    if [[ "${expected_sorted}" != "${staged_sorted}" ]]; then
        local only_expected only_staged
        only_expected=$(comm -23 <(echo "${expected_sorted}") <(echo "${staged_sorted}"))
        only_staged=$(comm -13 <(echo "${expected_sorted}") <(echo "${staged_sorted}"))

        echo "Error: staged files do not match expected files" >&2
        [[ -n "${only_expected}" ]] && echo "  Expected but not staged:" >&2 && echo "${only_expected}" | sed 's/^/    /' >&2
        [[ -n "${only_staged}" ]] && echo "  Staged but not expected:" >&2 && echo "${only_staged}" | sed 's/^/    /' >&2
        local staged_csv
        staged_csv=$(git diff --cached --name-only | tr '\n' ',' | sed 's/,$//')
        jq -n --arg expected "${expected_csv}" --arg staged "${staged_csv}" \
            '{"pushed": false, "reason": "file mismatch", "expected": $expected, "staged": $staged}'
        exit 2
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
        git add -A

        check_blocked_patterns

        if [[ -z "${expected_files}" ]]; then
            die "--expected-files is required when committing"
        fi
        verify_staged_files "${expected_files}"

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
