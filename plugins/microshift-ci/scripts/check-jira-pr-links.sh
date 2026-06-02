#!/usr/bin/bash
set -euo pipefail

# Check if a JIRA issue has a GitHub PR linked.
#
# Two-source check:
#   1. Parse JIRA changelog JSON (from jira_batch_get_changelogs) for
#      RemoteWorkItemLink entries referencing GitHub PRs. Accounts for
#      both additions and removals to determine current state.
#   2. If no JIRA link found, fall back to searching GitHub via gh CLI
#      for PRs whose title or branch contains the JIRA key.
#
# Usage:
#   check-jira-pr-links.sh <changelog.json> <jira-key>
#
# Exit codes:
#   0 — PR found (prints PR URL to stdout)
#   1 — no PR found
#   2 — usage error

usage() {
    echo "Usage: $(basename "$0") <changelog.json> <jira-key>" >&2
    exit 2
}

[[ ${#} -ne 2 ]] && usage

CHANGELOG_FILE="${1}"
JIRA_KEY="${2}"

[[ -f "${CHANGELOG_FILE}" ]] || { echo "Error: file not found: ${CHANGELOG_FILE}" >&2; exit 2; }

# --- Source 1: JIRA changelog ---
found=""
while IFS= read -r pr_url; do
    [[ -z "${pr_url}" ]] && continue
    gh_err=""
    pr_state=$(gh pr view "${pr_url}" --json state --jq '.state' 2>&1) || gh_err="$?"
    if [[ -n "${gh_err}" ]]; then
        echo "Error: gh pr view failed for ${pr_url} (exit ${gh_err}): ${pr_state}" >&2
        exit 1
    fi
    if [[ "${pr_state}" == "OPEN" || "${pr_state}" == "MERGED" ]]; then
        echo "${pr_url}"
        found=1
    fi
done < <(python3 - "${CHANGELOG_FILE}" <<'PYEOF' 2>/dev/null || true
import json
import re
import sys

changelog_file = sys.argv[1]

with open(changelog_file) as f:
    data = json.load(f)

changelogs = data[0].get("changelogs", []) if isinstance(data, list) and data else []

url_pattern = re.compile(r'github\.com/([^/]+)/([^/]+)/pull/(\d+)')
shorthand_pattern = re.compile(r'([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#(\d+)')
active_prs = {}

def extract_pr_url(text):
    m = url_pattern.search(text)
    if m:
        return f"https://github.com/{m.group(1)}/{m.group(2)}/pull/{m.group(3)}"
    m = shorthand_pattern.search(text)
    if m:
        return f"https://github.com/{m.group(1)}/pull/{m.group(2)}"
    return None

for entry in changelogs:
    for item in entry.get("items", []):
        if item.get("field") != "RemoteWorkItemLink":
            continue
        to_str = item.get("to_string", "") or ""
        from_str = item.get("from_string", "") or ""
        link_id = item.get("to_id") or item.get("from_id", "")

        if to_str:
            pr_url = extract_pr_url(to_str)
            if pr_url:
                active_prs[link_id] = pr_url

        if from_str and not to_str:
            rm_id = item.get("from_id", "")
            if rm_id in active_prs:
                del active_prs[rm_id]

for pr_url in active_prs.values():
    print(pr_url)
PYEOF
)
[[ -n "${found}" ]] && exit 0

# --- Source 2: GitHub fallback ---
gh_urls=$(gh pr list --repo openshift/microshift --search "${JIRA_KEY} in:title" --state open --json url --jq '.[].url' 2>/dev/null || true)
gh_urls+=$'\n'$(gh pr list --repo openshift/microshift --search "${JIRA_KEY} in:title" --state merged --json url --jq '.[].url' 2>/dev/null || true)
gh_urls=$(echo "${gh_urls}" | sed '/^$/d')
if [[ -n "${gh_urls}" ]]; then
    echo "${gh_urls}"
    exit 0
fi

exit 1
