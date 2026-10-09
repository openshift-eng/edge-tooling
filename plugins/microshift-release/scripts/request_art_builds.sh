#!/usr/bin/env bash
#
# request_art_builds.sh - Phase 0 post-check actions for one MicroShift z-stream.
#
#   1. Create the ART request ticket from template ART-11857
#   2. Create the QE release testing ticket from template USHIFT-6945 (parent USHIFT-4531)
#   3. Label both tickets "microshift-X.Y.Z" and link them (QE ticket is blocked by ART ticket)
#   4. Post the build request to #forum-ocp-art
#
# Re-running for the same version is safe: existing tickets (found by label) are reused,
# an existing link is not duplicated, and the Slack message is only posted when a new
# ART ticket is created.
#
# Before creating a ticket, also checks for a similar-titled ticket missing the release
# label (e.g. one created by hand) and refuses to proceed if it finds one, to avoid
# creating a duplicate. Pass --force to create anyway once you've confirmed it's needed.
#
# Usage:
#   request_art_builds.sh <X.Y.Z> <ART_DUE_DATE> <RELEASE_DATE> [--dry-run] [--force]
#   Dates are YYYY-MM-DD. Example:
#   request_art_builds.sh 4.21.12 2026-10-09 2026-10-15 --dry-run
#
# Environment:
#   JIRA_USER_EMAIL, JIRA_API_TOKEN   required (also for --dry-run, which reads the templates)
#   SLACK_WEBHOOK_URL                 required unless --dry-run
#   JIRA_BASE_URL                     default https://redhat.atlassian.net
#   SLACK_ART_GROUP_ID                Slack user-group ID for @release-artists (S...)
#   SLACK_CC_USER_IDS                 space-separated Slack member IDs to cc (U...), added
#                                      on top of DEFAULT_CC_USER_IDS below (duplicates skipped)
#   LINK_TYPE                         default "Blocks" (use "Relates" if Blocks doesn't exist)
#
# Output: on success prints "<ART_KEY> <QE_KEY>" to stdout (logs go to stderr).

set -euo pipefail

DRY_RUN=false
FORCE=false
ARGS=()
for a in "$@"; do
  case "${a}" in
    --dry-run) DRY_RUN=true ;;
    --force) FORCE=true ;;
    -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
    *) ARGS+=("${a}") ;;
  esac
done

VERSION="${ARGS[0]:-}"
ART_DUE="${ARGS[1]:-}"
RELEASE_DATE="${ARGS[2]:-}"

die() { echo "ERROR: $*" >&2; exit 1; }
log() { echo "==> $*" >&2; }

[[ "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "version must look like X.Y.Z (got '${VERSION}')"
for d in "${ART_DUE}" "${RELEASE_DATE}"; do
  [[ "${d}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || die "dates must be YYYY-MM-DD (got '${d}')"
done
command -v jq >/dev/null || die "jq is required"
: "${JIRA_USER_EMAIL:?JIRA_USER_EMAIL is not set}"
: "${JIRA_API_TOKEN:?JIRA_API_TOKEN is not set}"
if ! ${DRY_RUN}; then
  : "${SLACK_WEBHOOK_URL:?SLACK_WEBHOOK_URL is not set}"
fi

JIRA_BASE_URL="${JIRA_BASE_URL:-https://redhat.atlassian.net}"
LINK_TYPE="${LINK_TYPE:-Blocks}"
ART_TEMPLATE="ART-11857"
QE_TEMPLATE="USHIFT-6945"
QE_PARENT="USHIFT-4531"
ART_LABEL="art:microshift:manual-zstream"
RELEASE_LABEL="microshift-${VERSION}"
# Pablo Acevedo, Rama Kasturi, Alejandro Gullón, Tami Love
DEFAULT_CC_USER_IDS="U026BG77CDD U028C6K6AH5 U01LDR1DY5V USCCU4Z0W"

# "Oct 15" style dates (GNU date); falls back to the ISO date on macOS/BSD.
human_date() { date -d "$1" '+%b %-d' 2>/dev/null || echo "$1"; }
ART_DUE_HUMAN="$(human_date "${ART_DUE}")"
RELEASE_HUMAN="$(human_date "${RELEASE_DATE}")"

# jira METHOD PATH [JSON_BODY] -> response body on stdout; exits on non-2xx
jira() {
  local method="$1" path="$2" body="${3:-}" out code
  out="$(mktemp)"
  local args=(-sS -o "${out}" -w '%{http_code}' -X "${method}"
              -u "${JIRA_USER_EMAIL}:${JIRA_API_TOKEN}"
              -H 'Accept: application/json' -H 'Content-Type: application/json')
  if [[ -n "${body}" ]]; then args+=(--data "${body}"); fi
  code="$(curl "${args[@]}" "${JIRA_BASE_URL}${path}")" || { rm -f "${out}"; die "curl failed: ${method} ${path}"; }
  if [[ "${code}" != 2* ]]; then
    echo "Jira ${method} ${path} failed (HTTP ${code}):" >&2
    cat "${out}" >&2; echo >&2
    rm -f "${out}"; exit 1
  fi
  cat "${out}"; rm -f "${out}"
}

# find_by_label PROJECT -> newest key in PROJECT with the release label, or empty
find_by_label() {
  local jql
  jql="project = $1 AND labels = \"${RELEASE_LABEL}\" ORDER BY created DESC"
  jira GET "/rest/api/3/search/jql?fields=key&maxResults=1&jql=$(jq -rn --arg q "${jql}" '$q|@uri')" \
    | jq -r '.issues[0].key // empty'
}

# find_by_summary PROJECT PHRASE -> "KEY: SUMMARY" lines for tickets whose summary
# contains PHRASE, even if they're missing the release label (catches hand-created
# duplicates). PHRASE should be a stable substring (e.g. "Create MicroShift zstream for
# 4.22.18") rather than just the bare version, so it doesn't match on loose number-token
# overlap with unrelated tickets (e.g. golang bump tickets mentioning "4.18").
find_by_summary() {
  local jql
  jql="project = $1 AND summary ~ \"$2\" ORDER BY created DESC"
  jira GET "/rest/api/3/search/jql?fields=key,summary&maxResults=5&jql=$(jq -rn --arg q "${jql}" '$q|@uri')" \
    | jq -r '.issues[] | "\(.key): \(.fields.summary)"'
}

# build_payload TEMPLATE_JSON PROJECT SUMMARY DUE_DATE LABELS_JSON [PARENT_KEY]
# Copies issue type, components and description from the template; replaces "X.Y.Z"
# in the description text with the real version.
build_payload() {
  jq -n --argjson tpl "$1" --arg project "$2" --arg summary "$3" --arg due "$4" \
        --argjson labels "$5" --arg parent "${6:-}" --arg version "${VERSION}" '
    ($tpl.fields) as $f
    | {fields: (
        {
          project:    {key: $project},
          issuetype:  {id: $f.issuetype.id},
          summary:    $summary,
          labels:     $labels,
          duedate:    $due,
          components: [($f.components // [])[] | {id}]
        }
        + (if $f.description then
             {description: ($f.description
               | walk(if type == "object" and .type == "text"
                      then .text |= gsub("X\\.Y\\.Z"; $version) else . end))}
           else {} end)
        + (if $parent != "" then {parent: {key: $parent}} else {} end)
      )}'
}

# create_from_template PROJECT TEMPLATE SUMMARY DUE_DATE LABELS_JSON SEARCH_PHRASE [PARENT_KEY]
# Sets globals NEW_KEY and CREATED (true/false).
create_from_template() {
  local project="$1" template="$2" summary="$3" due="$4" labels="$5" phrase="$6" parent="${7:-}" tpl payload
  CREATED=false
  NEW_KEY="$(find_by_label "${project}")"
  if [[ -n "${NEW_KEY}" ]]; then
    log "${project}: ticket for ${VERSION} already exists: ${NEW_KEY} (not creating another)"
    return
  fi

  local similar
  similar="$(find_by_summary "${project}" "${phrase}")"
  if [[ -n "${similar}" ]] && ! ${FORCE}; then
    log "${project}: no ticket labeled ${RELEASE_LABEL}, but found similar-titled ticket(s) for ${VERSION}:"
    echo "      ${similar//$'\n'/$'\n      '}" >&2
    die "refusing to create a possible duplicate; investigate the ticket(s) above, then re-run with --force if a new one is really needed"
  fi

  tpl="$(jira GET "/rest/api/3/issue/${template}?fields=description,issuetype,components")"
  payload="$(build_payload "${tpl}" "${project}" "${summary}" "${due}" "${labels}" "${parent}")"
  if ${DRY_RUN}; then
    log "[DRY RUN] would create ${project} ticket from ${template}:"
    echo "${payload}" | jq . >&2
    NEW_KEY="${project}-<new>"
    CREATED=true
  else
    NEW_KEY="$(jira POST /rest/api/3/issue "${payload}" | jq -r .key)"
    CREATED=true
    log "Created ${NEW_KEY}: ${JIRA_BASE_URL}/browse/${NEW_KEY}"
  fi
}

MODE=""; if ${DRY_RUN}; then MODE=" [DRY RUN]"; fi
log "MicroShift ${VERSION}: ART due ${ART_DUE}, release ${RELEASE_DATE}${MODE}"

# 1. ART request ticket (keeps ART's own label so their tooling still finds it)
ART_SUMMARY="Create MicroShift zstream for ${VERSION}"
create_from_template ART "${ART_TEMPLATE}" \
  "${ART_SUMMARY}" "${ART_DUE}" \
  "$(jq -cn --arg a "${ART_LABEL}" --arg b "${RELEASE_LABEL}" '[$a, $b]')" \
  "${ART_SUMMARY}"
ART_KEY="${NEW_KEY}"; ART_CREATED="${CREATED}"

# 2. QE release testing ticket under the release epic
# Search phrase omits the "[QE] " prefix and the parenthetical date so it still matches
# older tickets created without that prefix, or with a different due date, for this version.
create_from_template USHIFT "${QE_TEMPLATE}" \
  "[QE] ${VERSION} MicroShift Release Testing (${RELEASE_HUMAN})" "${RELEASE_DATE}" \
  "$(jq -cn --arg b "${RELEASE_LABEL}" '[$b]')" \
  "${VERSION} MicroShift Release Testing" "${QE_PARENT}"
QE_KEY="${NEW_KEY}"

# 3. Link: QE ticket "is blocked by" ART ticket.
# Jira's link API is easy to get backwards: for type "Blocks", inwardIssue is the ticket
# that "blocks" and outwardIssue is the one that "is blocked by". After the first real run,
# confirm the ART ticket shows "blocks USHIFT-..."; if it's reversed, swap the two keys.
LINK_PAYLOAD="$(jq -n --arg t "${LINK_TYPE}" --arg art "${ART_KEY}" --arg qe "${QE_KEY}" \
  '{type: {name: $t}, inwardIssue: {key: $art}, outwardIssue: {key: $qe}}')"
if ${DRY_RUN}; then
  log "[DRY RUN] would link ${QE_KEY} (is blocked by) ${ART_KEY}:"
  echo "${LINK_PAYLOAD}" | jq . >&2
else
  LINKED="$(jira GET "/rest/api/3/issue/${QE_KEY}?fields=issuelinks" \
    | jq -r --arg art "${ART_KEY}" \
        '[.fields.issuelinks[]? | (.inwardIssue.key, .outwardIssue.key) | select(. == $art)] | length')"
  if [[ "${LINKED}" -gt 0 ]]; then
    log "${QE_KEY} is already linked to ${ART_KEY}"
  else
    jira POST /rest/api/3/issueLink "${LINK_PAYLOAD}" >/dev/null
    log "Linked ${QE_KEY} (is blocked by) ${ART_KEY}"
  fi
fi

# 4. Slack request to #forum-ocp-art (only when the ART ticket is new, to avoid re-pinging)
if ${ART_CREATED}; then
  GROUP="${SLACK_ART_GROUP_ID:+<!subteam^${SLACK_ART_GROUP_ID}|@release-artists>}"
  GROUP="${GROUP:-@release-artists}"
  CC=""
  declare -A CC_SEEN=()
  for id in ${DEFAULT_CC_USER_IDS} ${SLACK_CC_USER_IDS:-}; do
    [[ -n "${CC_SEEN[${id}]:-}" ]] && continue
    CC_SEEN[${id}]=1
    CC+="<@${id}> "
  done
  MSG="Hello, ${GROUP} I've opened <${JIRA_BASE_URL}/browse/${ART_KEY}|${ART_KEY}> requesting MicroShift ${VERSION} builds (due date ${ART_DUE_HUMAN}). Please help create RPMs and bootc images, thanks!"
  if [[ -n "${CC}" ]]; then MSG+=$'\ncc '"${CC% }"; fi
  SLACK_PAYLOAD="$(jq -n --arg text "${MSG}" '{text: $text}')"

  if ${DRY_RUN}; then
    log "[DRY RUN] would post to Slack:"
    echo "${MSG}" >&2
  else
    curl -sS --fail -X POST -H 'Content-Type: application/json' \
      --data "${SLACK_PAYLOAD}" "${SLACK_WEBHOOK_URL}" >/dev/null || die "Slack post failed"
    log "Posted request to #forum-ocp-art"
  fi
else
  log "ART ticket already existed; skipping Slack message"
fi

echo "${ART_KEY} ${QE_KEY}"
