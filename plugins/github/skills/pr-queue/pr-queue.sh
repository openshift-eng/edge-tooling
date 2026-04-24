#!/usr/bin/bash
set -euo pipefail

REPOS=()
SHOW_ALL=false

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --all) SHOW_ALL=true ;;
            --*)
                echo "Error: Unknown flag '$1'" >&2
                echo "Usage: pr-queue.sh <owner/repo> [<owner/repo> ...] [--all]" >&2
                exit 1
                ;;
            *) REPOS+=("$1") ;;
        esac
        shift
    done
}

validate_args() {
    if [[ ${#REPOS[@]} -eq 0 ]]; then
        echo "Usage: pr-queue.sh <owner/repo> [<owner/repo> ...] [--all]" >&2
        echo "Example: pr-queue.sh openshift-eng/edge-tooling openshift-eng/edge-hiring --all" >&2
        exit 1
    fi
    for repo in "${REPOS[@]}"; do
        if [[ ! "$repo" =~ ^[^/]+/[^/]+$ ]]; then
            echo "Error: Invalid repository format '$repo'. Expected owner/repo (e.g., openshift-eng/edge-tooling)." >&2
            exit 1
        fi
    done
}

fetch_prs() {
    local repo="$1"
    RAW_PRS=$(gh pr list --repo "$repo" --state open --limit 200 \
        --json number,title,author,url,isDraft,labels,createdAt)
}

classify_prs() {
    CLASSIFIED=$(echo "$RAW_PRS" | jq '
        [.[] | {
            number,
            title,
            author: .author.login,
            url,
            createdAt: (.createdAt | split("T")[0]),
            exclusion: (
                [
                    (if .isDraft then "draft" else empty end),
                    (if (.title | test("\\bwip\\b"; "i")) then "WIP" else empty end),
                    (if ([.labels[].name] | index("do-not-merge/hold")) then "hold" else empty end),
                    (if ([.labels[].name] | index("do-not-merge/work-in-progress")) then "WIP" else empty end)
                ] | unique | join(", ")
            )
        }] | sort_by(.createdAt) | reverse
    ')
}

format_output() {
    local repo="$1"
    local total excluded actionable
    total=$(echo "$CLASSIFIED" | jq 'length')
    excluded=$(echo "$CLASSIFIED" | jq '[.[] | select(.exclusion != "")] | length')
    actionable=$((total - excluded))

    if [[ "$total" -eq 0 ]]; then
        echo "No open PRs in \`${repo}\`."
        return
    fi

    if [[ "$SHOW_ALL" == "true" ]]; then
        format_all_table "$repo" "$total" "$actionable" "$excluded"
    else
        format_actionable_table "$repo" "$actionable" "$excluded"
    fi
}

format_actionable_table() {
    local repo="$1" actionable="$2" excluded="$3"

    if [[ "$actionable" -eq 0 ]]; then
        echo "No actionable PRs in \`${repo}\`. ${excluded} open PRs are excluded (use \`--all\` to see them)."
        return
    fi

    echo "**${actionable} actionable PRs in \`${repo}\`:**"
    echo ""
    echo "| Created | PR | Author | Title |"
    echo "|---|---|---|---|"
    echo "$CLASSIFIED" | jq -r '
        .[] | select(.exclusion == "") |
        "| \(.createdAt) | [#\(.number)](\(.url)) | @\(.author) | \(.title | gsub("\\|"; "\\|")) |"
    '
}

format_all_table() {
    local repo="$1" total="$2" actionable="$3" excluded="$4"

    echo "**${total} open PRs in \`${repo}\`** (${actionable} actionable, ${excluded} excluded):"
    echo ""
    echo "| Created | PR | Author | Title | Exclusion |"
    echo "|---|---|---|---|---|"
    echo "$CLASSIFIED" | jq -r '
        .[] |
        "| \(.createdAt) | [#\(.number)](\(.url)) | @\(.author) | \(.title | gsub("\\|"; "\\|")) | \(.exclusion) |"
    '
}

main() {
    parse_args "$@"
    validate_args
    for repo in "${REPOS[@]}"; do
        fetch_prs "$repo"
        classify_prs
        format_output "$repo"
        echo ""
    done
}

main "$@"
