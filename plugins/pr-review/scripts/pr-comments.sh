#!/usr/bin/bash
set -euo pipefail

# Fetch unresolved inline review comments, output structured JSON.
# Uses GitHub GraphQL API to filter out resolved review threads.
# Exit codes: 0=has unresolved comments, 1=no unresolved comments, 3=error

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


fetch_review_threads() {
    local org="$1" repo="$2" pr_number="$3"
    local review_json
    review_json=$(gh pr view "${pr_number}" --repo "${org}/${repo}" \
        --json reviewDecision,reviews) \
        || die "Failed to fetch review threads for ${org}/${repo}#${pr_number}"
    echo "${review_json}"
}

fetch_resolved_comment_ids() {
    local org="$1" repo="$2" pr_number="$3"
    gh api graphql -f query='
    query($owner: String!, $repo: String!, $number: Int!) {
      repository(owner: $owner, name: $repo) {
        pullRequest(number: $number) {
          reviewThreads(first: 100) {
            nodes {
              isResolved
              comments(first: 100) {
                nodes {
                  databaseId
                }
              }
            }
          }
        }
      }
    }' -f owner="${org}" -f repo="${repo}" -F number="${pr_number}" \
        | jq '[.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved) | .comments.nodes[].databaseId]' \
        || die "Failed to fetch resolved threads for ${org}/${repo}#${pr_number}"
}

build_output() {
    local review_comments="$1" review_threads="$2" addressed_ids="$3" resolved_ids="$4"

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
        --argjson resolved "${resolved_ids}" \
        '[.[] | select(
            (.id as $id | ($addressed | index($id)) | not) and
            (.id as $id | ($resolved | index($id)) | not)
        ) |
        {
            id: .id,
            author: .user.login,
            body: .body,
            file: .path,
            line: (.line // .original_line),
            diff_hunk: .diff_hunk,
            is_bot: (.user.type == "Bot" or (.user.login | test("\\[bot\\]$"))),
            created_at: .created_at,
            updated_at: .updated_at,
            in_reply_to_id: (.in_reply_to_id // null)
        }]')

    local inline_count
    inline_count=$(echo "${inline_block}" | jq 'length')

    jq -nc \
        --argjson inline "${inline_block}" \
        --arg decision "${review_decision}" \
        --argjson total "${inline_count}" \
        '{
            inline_comments: $inline,
            review_decision: $decision,
            summary: {
                total_new: $total
            }
        }'
}

main() {
    [[ $# -lt 1 ]] && die "Usage: $(basename "$0") <github-pr-url> [addressed-comment-ids] [--skip-users]"

    local pr_url="" addressed_ids="" skip_users=false

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --skip-users)
                skip_users=true
                shift
                ;;
            *)
                if [[ -z "${pr_url}" ]]; then
                    pr_url="$1"
                elif [[ -z "${addressed_ids}" ]]; then
                    addressed_ids="$1"
                fi
                shift
                ;;
        esac
    done

    [[ -z "${pr_url}" ]] && die "Usage: $(basename "$0") <github-pr-url> [addressed-comment-ids] [--skip-users]"

    check_dependencies
    validate_url "${pr_url}"
    parse_url "${pr_url}"

    local review_comments review_threads resolved_ids
    review_comments=$(fetch_review_comments "${ORG}" "${REPO}" "${PR_NUMBER}")
    review_threads=$(fetch_review_threads "${ORG}" "${REPO}" "${PR_NUMBER}")
    resolved_ids=$(fetch_resolved_comment_ids "${ORG}" "${REPO}" "${PR_NUMBER}")

    if [[ "${skip_users}" == "true" ]]; then
        review_comments=$(echo "${review_comments}" | jq '[.[] | select(.user.type == "Bot" or (.user.login | test("\\[bot\\]$")))]')
    fi

    local output
    output=$(build_output "${review_comments}" "${review_threads}" "${addressed_ids}" "${resolved_ids}")

    echo "${output}"

    local total_new
    total_new=$(echo "${output}" | jq -r '.summary.total_new')
    if [[ "${total_new}" -eq 0 ]]; then
        exit 1
    fi
}

main "$@"
