#!/usr/bin/bash
set -euo pipefail

# Fetch unresolved PR review comments, output structured JSON.
# Exit codes: 0=has new comments, 1=no new comments, 3=error

URL_PATTERN='^https://github\.com/[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+/pull/[0-9]+$'

die() {
    echo "Error: $1" >&2
    exit 3
}

check_dependencies() {
    command -v gh >/dev/null 2>&1 || die "gh CLI is not installed"
    command -v jq >/dev/null 2>&1 || die "jq is not installed"
    gh auth status >/dev/null 2>&1 || die "gh CLI is not authenticated — run 'gh auth login'"
}

validate_url() {
    local url="$1"
    if [[ ! "${url}" =~ ${URL_PATTERN} ]]; then
        die "Invalid PR URL: ${url}"
    fi
}

parse_url() {
    local url="$1"
    ORG=$(echo "${url}" | cut -d'/' -f4)
    REPO=$(echo "${url}" | cut -d'/' -f5)
    PR_NUMBER=$(echo "${url}" | cut -d'/' -f7)
}

fetch_review_comments() {
    local org="$1" repo="$2" pr_number="$3"
    local comments_json
    comments_json=$(gh api "repos/${org}/${repo}/pulls/${pr_number}/comments" --paginate \
        | jq -s 'add // []') \
        || die "Failed to fetch review comments for ${org}/${repo}#${pr_number}"
    echo "${comments_json}"
}

fetch_issue_comments() {
    local org="$1" repo="$2" pr_number="$3"
    local comments_json
    comments_json=$(gh api "repos/${org}/${repo}/issues/${pr_number}/comments" --paginate \
        | jq -s 'add // []') \
        || die "Failed to fetch issue comments for ${org}/${repo}#${pr_number}"
    echo "${comments_json}"
}

fetch_review_threads() {
    local org="$1" repo="$2" pr_number="$3"
    local review_json
    review_json=$(gh pr view "${pr_number}" --repo "${org}/${repo}" \
        --json reviewDecision,reviews) \
        || die "Failed to fetch review threads for ${org}/${repo}#${pr_number}"
    echo "${review_json}"
}

build_output() {
    local review_comments="$1" issue_comments="$2" review_threads="$3" addressed_ids="$4"

    local addressed_filter
    if [[ -n "${addressed_ids}" ]]; then
        addressed_filter=$(echo "${addressed_ids}" | jq -Rc 'split(",") | map(tonumber)')
    else
        addressed_filter="[]"
    fi

    local review_decision
    review_decision=$(echo "${review_threads}" | jq -r '.reviewDecision // "PENDING"')

    local inline_block
    inline_block=$(echo "${review_comments}" | jq -c \
        --argjson addressed "${addressed_filter}" \
        '[.[] | select(.id as $id | ($addressed | index($id)) | not) |
        {
            id: .id,
            author: .user.login,
            body: .body,
            file: .path,
            line: (.line // .original_line),
            diff_hunk: .diff_hunk,
            is_coderabbit: (.user.login == "coderabbitai"),
            created_at: .created_at,
            updated_at: .updated_at,
            in_reply_to_id: (.in_reply_to_id // null)
        }]')

    local pr_level_block
    pr_level_block=$(echo "${issue_comments}" | jq -c \
        --argjson addressed "${addressed_filter}" \
        '[.[] | select(.id as $id | ($addressed | index($id)) | not) |
        {
            id: .id,
            author: .user.login,
            body: .body,
            is_coderabbit: (.user.login == "coderabbitai"),
            created_at: .created_at
        }]')

    local inline_count pr_level_count total_count
    inline_count=$(echo "${inline_block}" | jq 'length')
    pr_level_count=$(echo "${pr_level_block}" | jq 'length')
    total_count=$((inline_count + pr_level_count))

    jq -nc \
        --argjson inline "${inline_block}" \
        --argjson pr_level "${pr_level_block}" \
        --arg decision "${review_decision}" \
        --argjson total "${total_count}" \
        --argjson inline_count "${inline_count}" \
        --argjson pr_level_count "${pr_level_count}" \
        '{
            inline_comments: $inline,
            pr_level_comments: $pr_level,
            review_decision: $decision,
            summary: {
                total_new: $total,
                inline: $inline_count,
                pr_level: $pr_level_count
            }
        }'
}

main() {
    [[ $# -lt 1 ]] && die "Usage: $(basename "$0") <github-pr-url> [addressed-comment-ids]"

    local pr_url="$1"
    local addressed_ids="${2:-}"

    check_dependencies
    validate_url "${pr_url}"
    parse_url "${pr_url}"

    local review_comments issue_comments review_threads
    review_comments=$(fetch_review_comments "${ORG}" "${REPO}" "${PR_NUMBER}")
    issue_comments=$(fetch_issue_comments "${ORG}" "${REPO}" "${PR_NUMBER}")
    review_threads=$(fetch_review_threads "${ORG}" "${REPO}" "${PR_NUMBER}")

    local output
    output=$(build_output "${review_comments}" "${issue_comments}" "${review_threads}" "${addressed_ids}")

    echo "${output}"

    local total_new
    total_new=$(echo "${output}" | jq -r '.summary.total_new')
    if [[ "${total_new}" -eq 0 ]]; then
        exit 1
    fi
}

main "$@"
