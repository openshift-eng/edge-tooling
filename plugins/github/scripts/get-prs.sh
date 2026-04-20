#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<EOF
Usage: $(basename "$0") <org/repo> [org/repo ...]

Fetch open, non-draft PRs for one or more GitHub repositories.
Outputs JSON sorted oldest-to-newest with last-comment metadata.

Examples:
  $(basename "$0") openshift-eng/edge-tooling
  $(basename "$0") openshift-eng/edge-tooling openshift/microshift
EOF
}

if [[ $# -eq 0 ]]; then
    usage >&2
    exit 1
fi

if [[ "$1" == "-h" || "$1" == "--help" ]]; then
    usage
    exit 0
fi

now=$(date +%s)
three_days=$((3 * 24 * 60 * 60))
two_days=$((2 * 24 * 60 * 60))
one_day=$((24 * 60 * 60))

fetch_prs() {
    local repo="$1"
    if [[ ! "$repo" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]]; then
        echo "Invalid repository slug: $repo (expected org/repo)" >&2
        return 1
    fi
    local owner="${repo%/*}"
    local name="${repo#*/}"

    local pr_data
    pr_data=$(gh pr list --repo "$repo" --state open --limit 1000 \
        --json number,title,author,labels,createdAt,isDraft,url,assignees,reviewRequests \
        --jq '[.[] | select(.isDraft == false)]')

    local pr_numbers
    pr_numbers=$(echo "$pr_data" | jq -r '.[].number')

    if [[ -z "$pr_numbers" ]]; then
        echo "[]"
        return
    fi

    local numbers_list
    numbers_list=$(echo "$pr_numbers" | paste -sd ' ')

    local gql_nodes=""
    for num in $numbers_list; do
        gql_nodes+="
        pr${num}: pullRequest(number: ${num}) {
            number
            comments(last: 1) {
                nodes { createdAt author { login } }
            }
            reviews(last: 1) {
                nodes { createdAt author { login } }
            }
        }"
    done

    local gql_query="{ repository(owner: \"${owner}\", name: \"${name}\") { ${gql_nodes} } }"

    local comment_data
    comment_data=$(gh api graphql -f query="$gql_query" --jq '.data.repository')

    echo "$pr_data" | jq -c --argjson comments "$comment_data" --arg now "$now" --arg three_days "$three_days" --arg two_days "$two_days" --arg one_day "$one_day" '
        [.[] | . as $pr |
            ($comments["pr\($pr.number)"] // {}) as $c |
            ($c.comments.nodes[0] // null) as $lastComment |
            ($c.reviews.nodes[0] // null) as $lastReview |
            (
                if $lastComment != null and $lastReview != null then
                    if $lastComment.createdAt > $lastReview.createdAt then $lastComment
                    else $lastReview end
                elif $lastComment != null then $lastComment
                elif $lastReview != null then $lastReview
                else null end
            ) as $latest |
            ($pr.createdAt | fromdateiso8601) as $created_epoch |
            (($now | tonumber) - $created_epoch) as $age |
            {
                number: $pr.number,
                title: $pr.title,
                url: $pr.url,
                author: $pr.author.login,
                assignees: [($pr.assignees // [])[] | .login],
                reviewers: [($pr.reviewRequests // [])[] | .requestedReviewer.login // empty],
                labels: [($pr.labels // [])[] | .name],
                createdAt: $pr.createdAt,
                lastCommentAt: ($latest.createdAt // null),
                lastCommentBy: ($latest.author.login // null),
                openLongerThan3Days: ($age > ($three_days | tonumber)),
                ageCategory: (
                    if $age > ($three_days | tonumber) then "> 3 Days"
                    elif $age > ($two_days | tonumber) then "< 3 Days"
                    elif $age > ($one_day | tonumber) then "< 2 Days"
                    else "< 1 Day" end
                )
            }
        ] | sort_by(.createdAt)
    '
}

result=$(jq -n '{}')
for repo in "$@"; do
    repo_json=$(fetch_prs "$repo")
    result=$(echo "$result" | jq --arg key "$repo" --argjson val "$repo_json" '. + {($key): $val}')
done
echo "$result"
