#!/usr/bin/env bash
#
# request_art_builds.sh - MicroShift z-stream build & QE release coordinator.
#
# Usage:
#   request_art_builds.sh <X.Y.Z> <ART_DUE_DATE> <RELEASE_DATE> --reason <TEXT> [FLAGS]
#
# Flags:
#   --reason <TEXT>    Mandatory explanation (CVE, cadence, etc.)
#   --dry-run          Run preflight and print redacted previews without writing
#   --force            Bypass similarity duplicate warning
#   --resend-slack     Send Slack notification even if ART ticket was reused
#   --skip-slack       Create/link tickets but never send the Slack notification
#   -h, --help         Show usage

set -euo pipefail

# --- Config & Defaults ---
JIRA_BASE_URL="${JIRA_BASE_URL:-https://redhat.atlassian.net}"
JIRA_BASE_URL="${JIRA_BASE_URL%/}"
LINK_TYPE_NAME="Blocks"
ART_TEMPLATE="ART-11857"
QE_TEMPLATE="USHIFT-6945"
QE_PARENT="USHIFT-4531"
ART_LABEL="art:microshift:manual-zstream"
DEFAULT_CC_USER_IDS="U026BG77CDD U028C6K6AH5 U01LDR1DY5V USCCU4Z0W"

DRY_RUN=false
FORCE=false
RESEND_SLACK=false
SKIP_SLACK=false
REASON=""
ARGS=()

# --- Helpers ---
# Print a safe error message and exit.
die() { echo "ERROR: $*" >&2; exit 1; }
# Print operator progress without contaminating standard output.
log() { echo "==> $*" >&2; }

# Bash 3.2 regex patterns (must be unquoted in [[ =~ $pat ]])
RE_SEMVER='^[0-9]+\.[0-9]+\.[0-9]+$'
RE_ISODATE='^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
RE_JIRAKEY='^[A-Z0-9]+-[0-9]+$'
RE_HTTPS_BASE_URL='^https://[^/@[:space:]]+(:[0-9]+)?(/[^[:space:]]*)?$'

[[ "${JIRA_BASE_URL}" =~ ${RE_HTTPS_BASE_URL} ]] \
  || die "JIRA_BASE_URL must be an HTTPS URL without embedded credentials"

# --- Argument Parsing ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    --force) FORCE=true; shift ;;
    --resend-slack) RESEND_SLACK=true; shift ;;
    --skip-slack) SKIP_SLACK=true; shift ;;
    --reason)
      [[ $# -ge 2 && "$2" != -* ]] || die "--reason requires a non-option argument"
      REASON="$2"; shift 2 ;;
    --reason=*) REASON="${1#*=}"; shift ;;
    -h|--help) sed -n '3,14p' "$0"; exit 0 ;;
    -*) die "Unknown option '$1'" ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

${SKIP_SLACK} && ${RESEND_SLACK} && die "--skip-slack and --resend-slack are mutually exclusive"

[[ ${#ARGS[@]} -eq 3 ]] || die "Expected 3 args: <VERSION> <ART_DUE_DATE> <RELEASE_DATE>"
VERSION="${ARGS[0]}"
ART_DUE="${ARGS[1]}"
RELEASE_DATE="${ARGS[2]}"
RELEASE_LABEL="microshift-${VERSION}"

# Validate reason & formats
REASON="$(echo "${REASON}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
[[ -n "${REASON}" ]] || die "--reason must not be empty or whitespace-only"
[[ "${VERSION}" =~ ${RE_SEMVER} ]] || die "Version must be X.Y.Z (got '${VERSION}')"
[[ "${ART_DUE}" =~ ${RE_ISODATE} ]] || die "ART_DUE must be YYYY-MM-DD (got '${ART_DUE}')"
[[ "${RELEASE_DATE}" =~ ${RE_ISODATE} ]] || die "RELEASE_DATE must be YYYY-MM-DD (got '${RELEASE_DATE}')"

# Date round-trip & chronological validation (GNU & BSD date portable)
# Validate an ISO date using GNU or BSD date.
valid_calendar_date() {
  local in="$1" out=""
  out="$(date -d "${in}" '+%Y-%m-%d' 2>/dev/null || date -j -f "%Y-%m-%d" "${in}" '+%Y-%m-%d' 2>/dev/null || true)"
  [[ "${out}" == "${in}" ]]
}
valid_calendar_date "${ART_DUE}" || die "ART_DUE is not a valid calendar date: ${ART_DUE}"
valid_calendar_date "${RELEASE_DATE}" || die "RELEASE_DATE is not a valid calendar date: ${RELEASE_DATE}"
[[ "${ART_DUE}" > "${RELEASE_DATE}" ]] && die "ART_DUE (${ART_DUE}) cannot be after RELEASE_DATE (${RELEASE_DATE})"

# Render an ISO date for the operator-facing notification.
human_date() { date -d "$1" '+%b %-d' 2>/dev/null || date -j -f "%Y-%m-%d" "$1" '+%b %e' 2>/dev/null | tr -s ' ' || echo "$1"; }
ART_DUE_HUMAN="$(human_date "${ART_DUE}")"
RELEASE_HUMAN="$(human_date "${RELEASE_DATE}")"

# Environment checks
command -v jq >/dev/null || die "jq is required"
command -v curl >/dev/null || die "curl is required"
: "${JIRA_USER_EMAIL:?JIRA_USER_EMAIL is required}"
: "${JIRA_API_TOKEN:?JIRA_API_TOKEN is required}"
${DRY_RUN} || ${SKIP_SLACK} || : "${SLACK_WEBHOOK_URL:?SLACK_WEBHOOK_URL is required unless running with --dry-run or --skip-slack}"

# Atomic directory lock (trap installed only after successful acquisition)
LOCKDIR="${TMPDIR:-/tmp}/request_art_builds_${VERSION}.lock"
mkdir "${LOCKDIR}" 2>/dev/null || die "Cannot acquire lock: ${LOCKDIR} (another run active or stale lock)"
# Remove the lock owned by this process.
cleanup_lock() { rmdir "${LOCKDIR}" 2>/dev/null || true; }
trap cleanup_lock EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# --- Jira API Client ---
# Call Jira while keeping credentials, response bodies, and hosts out of logs.
jira() {
  local method="$1" path="$2" body="${3:-}" out code safe_path
  safe_path="${path%%\?*}"
  out="$(mktemp "${TMPDIR:-/tmp}/jira_res.XXXXXX")"
  local args=(-sS -o "${out}" -w '%{http_code}' -X "${method}"
    --connect-timeout 10 --max-time 30
    -u "${JIRA_USER_EMAIL}:${JIRA_API_TOKEN}"
    -H 'Accept: application/json' -H 'Content-Type: application/json')
  [[ -n "${body}" ]] && args+=(--data "${body}")

  code="$(curl "${args[@]}" "${JIRA_BASE_URL}${path}" 2>/dev/null)" || {
    rm -f "${out}"
    if [[ "${method}" == "POST" || "${method}" == "PUT" ]]; then
      die "Network failure during ${method} ${safe_path}; mutation outcome unknown. Reconcile manually before retrying."
    fi
    die "Network failure during ${method} ${safe_path}"
  }

  if [[ "${code}" != 2* ]]; then
    echo "Jira API request failed (HTTP ${code}): ${method} ${safe_path}" >&2
    rm -f "${out}"; return 1
  fi
  cat "${out}"; rm -f "${out}"
}

# Paginated JQL search with strict completion checks
# Fetch every JQL page or fail before any mutation.
paginate_jql() {
  local jql="$1" fields="$2" max_pages="${3:-5}" page=0 token="" all_issues="[]"
  while [[ ${page} -lt ${max_pages} ]]; do
    local url
    url="/rest/api/3/search/jql?jql=$(jq -rn --arg q "${jql}" '$q|@uri')&fields=${fields}&maxResults=50"
    [[ -n "${token}" ]] && url+="&nextPageToken=$(jq -rn --arg t "${token}" '$t|@uri')"
    local res; res="$(jira GET "${url}")" || return 1
    printf '%s\n' "${res}" | jq -e '.issues | type == "array"' >/dev/null || die "Search response missing issues array"
    all_issues="$(jq -n --argjson acc "${all_issues}" --argjson cur "${res}" '$acc + $cur.issues')" || return 1
    local is_last; is_last="$(printf '%s\n' "${res}" | jq -r 'if (.isLast | type) == "boolean" then .isLast else error("missing isLast") end')" || die "Invalid search response: missing or non-boolean isLast"
    [[ "${is_last}" == "true" ]] && { echo "${all_issues}"; return 0; }
    token="$(printf '%s\n' "${res}" | jq -r '.nextPageToken // empty')"
    [[ -n "${token}" ]] || die "Search incomplete: isLast=false but nextPageToken is missing"
    page=$((page + 1))
  done
  die "Search incomplete: page limit (${max_pages} pages) reached"
}

# --- Payload Builder ---
# Build a create-issue payload from a validated template.
build_payload() {
  local tpl_json="$1" proj="$2" summary="$3" due="$4" labels_json="$5" parent_key="${6:-}"
  jq -n --argjson tpl "${tpl_json}" --arg proj "${proj}" --arg summary "${summary}" \
        --arg due "${due}" --argjson labels "${labels_json}" --arg parent "${parent_key}" \
        --arg version "${VERSION}" --arg reason "${REASON}" '
    ($tpl.fields) as $f
    | ($f.description // {type: "doc", version: 1, content: []}) as $desc
    | ($desc | walk(if type == "object" and .type == "text" then .text |= gsub("X\\.Y\\.Z"; $version; "i") else . end)) as $updatedDesc
    | ($updatedDesc.content + [{
        type: "paragraph",
        content: [{type: "text", text: ("Reason: " + $reason), marks: [{type: "strong"}]}]
      }]) as $finalContent
    | {
        fields: (
          {
            project: {key: $proj},
            issuetype: {id: $f.issuetype.id},
            summary: $summary,
            labels: $labels,
            duedate: $due,
            components: [($f.components // [])[] | {id}]
          }
          + (if $f.priority then {priority: {id: $f.priority.id}} else {} end)
          + {description: {type: "doc", version: 1, content: $finalContent}}
          + (if $parent != "" then {parent: {key: $parent}} else {} end)
        )
      }'
}

# ==============================================================================
# PHASE 1: PREFLIGHT VALIDATION (READ-ONLY)
# ==============================================================================
log "Starting Phase 1: Preflight checks for MicroShift ${VERSION}..."

# 1. Validate Link Type semantics
LINK_TYPES="$(jira GET "/rest/api/3/issueLinkType")" || exit 1
LINK_TYPE_ID="$(echo "${LINK_TYPES}" | jq -er --arg n "${LINK_TYPE_NAME}" '
  .issueLinkTypes[] | select(.name == $n and .inward == "is blocked by" and .outward == "blocks") | .id
')" || die "Link type '${LINK_TYPE_NAME}' (blocks / is blocked by) not found"

# 2. Validate Templates & Parent Epic
ART_TPL_JSON="$(jira GET "/rest/api/3/issue/${ART_TEMPLATE}?fields=description,issuetype,components,priority,project")" || exit 1
QE_TPL_JSON="$(jira GET "/rest/api/3/issue/${QE_TEMPLATE}?fields=description,issuetype,components,priority,project")" || exit 1
QE_PARENT_JSON="$(jira GET "/rest/api/3/issue/${QE_PARENT}?fields=issuetype,project")" || exit 1

[[ "$(echo "${ART_TPL_JSON}" | jq -r '.fields.project.key // empty')" == "ART" ]] || die "${ART_TEMPLATE} is not in project ART"
[[ "$(echo "${QE_TPL_JSON}" | jq -r '.fields.project.key // empty')" == "USHIFT" ]] || die "${QE_TEMPLATE} is not in project USHIFT"
[[ "$(echo "${QE_PARENT_JSON}" | jq -r '.fields.project.key // empty')" == "USHIFT" && \
   "$(echo "${QE_PARENT_JSON}" | jq -r '.fields.issuetype.name // empty')" == "Epic" ]] || die "${QE_PARENT} must be an Epic in project USHIFT"

ART_TPL_TYPE_ID="$(echo "${ART_TPL_JSON}" | jq -r '.fields.issuetype.id // empty')"
QE_TPL_TYPE_ID="$(echo "${QE_TPL_JSON}" | jq -r '.fields.issuetype.id // empty')"
[[ -n "$ART_TPL_TYPE_ID" && -n "$QE_TPL_TYPE_ID" ]] || die "Template response missing issue-type ID"

# 3. Label duplicate checks
ART_LABEL_ISSUES="$(paginate_jql "project = ART AND labels = \"${RELEASE_LABEL}\"" "key,summary,issuetype" 3)" || exit 1
QE_LABEL_ISSUES="$(paginate_jql "project = USHIFT AND labels = \"${RELEASE_LABEL}\"" "key,summary,issuetype,parent" 3)" || exit 1

[[ $(echo "${ART_LABEL_ISSUES}" | jq 'length') -gt 1 ]] && die "Multiple ART tickets found with label ${RELEASE_LABEL}"
[[ $(echo "${QE_LABEL_ISSUES}" | jq 'length') -gt 1 ]] && die "Multiple USHIFT tickets found with label ${RELEASE_LABEL}"

EXISTING_ART_KEY="$(echo "${ART_LABEL_ISSUES}" | jq -r '.[0].key // empty')"
EXISTING_QE_KEY="$(echo "${QE_LABEL_ISSUES}" | jq -r '.[0].key // empty')"

# Validate reused tickets if matched by label
ART_SUMMARY="Create MicroShift zstream for ${VERSION}"
QE_SUMMARY="[QE] ${VERSION} MicroShift Release Testing (${RELEASE_HUMAN})"

if [[ -n "${EXISTING_ART_KEY}" ]]; then
  echo "${ART_LABEL_ISSUES}" | jq -e --arg tid "${ART_TPL_TYPE_ID}" --arg s "${ART_SUMMARY}" \
    '.[0] | select(.fields.issuetype.id == $tid and .fields.summary == $s)' >/dev/null \
    || die "Existing ART ticket ${EXISTING_ART_KEY} does not match expected issue type or summary"
fi

if [[ -n "${EXISTING_QE_KEY}" ]]; then
  echo "${QE_LABEL_ISSUES}" | jq -e --arg tid "${QE_TPL_TYPE_ID}" --arg p "${QE_PARENT}" --arg ver "${VERSION}" '
    ($ver | split(".") | join("\\.")) as $v
    | .[0]
    | select(
        .fields.issuetype.id == $tid
        and .fields.parent.key == $p
        and (.fields.summary | test("(^|[^0-9.])" + $v + "([^0-9.]|$)"))
        and (.fields.summary | test("(?i)microshift release testing"))
      )
  ' >/dev/null || die "Existing QE ticket ${EXISTING_QE_KEY} does not match expected type, parent, or release purpose"
fi

# 4. Text similarity search if no label match
if [[ -z "${EXISTING_ART_KEY}" ]] && ! ${FORCE}; then
  SIMILAR_ART="$(paginate_jql "project = ART AND summary ~ \"${VERSION}\"" "key,summary" 5)" || exit 1
  ART_MATCHES="$(echo "${SIMILAR_ART}" | jq -r --arg s "${ART_SUMMARY}" '.[]? | select(.fields.summary == $s) | "\(.key): \(.fields.summary)"')"
  [[ -n "${ART_MATCHES}" ]] && die "Found existing ART ticket(s) missing label '${RELEASE_LABEL}':\n${ART_MATCHES}\nInspect or use --force"
fi

if [[ -z "${EXISTING_QE_KEY}" ]] && ! ${FORCE}; then
  SIMILAR_QE="$(paginate_jql "project = USHIFT AND summary ~ \"${VERSION}\"" "key,summary" 5)" || exit 1
  QE_MATCHES="$(echo "${SIMILAR_QE}" | jq -r --arg ver "${VERSION}" '
    ($ver | split(".") | join("\\.")) as $v
    | .[]?
    | select(
        (.fields.summary | test("(^|[^0-9.])" + $v + "([^0-9.]|$)"))
        and (.fields.summary | test("(?i)microshift release testing"))
      )
    | "\(.key): \(.fields.summary)"
  ')"
  [[ -n "${QE_MATCHES}" ]] && die "Found existing QE ticket(s) missing label '${RELEASE_LABEL}':\n${QE_MATCHES}\nInspect or use --force"
fi

# 5. Preflight reverse-link check if both exist
if [[ -n "${EXISTING_ART_KEY}" && -n "${EXISTING_QE_KEY}" ]]; then
  QE_LINKS="$(jira GET "/rest/api/3/issue/${EXISTING_QE_KEY}?fields=issuelinks")" || exit 1
  REV_LINK="$(echo "${QE_LINKS}" | jq -r --arg art "${EXISTING_ART_KEY}" --arg lt "${LINK_TYPE_NAME}" '
    [.fields.issuelinks[]? | select(.type.name == $lt and .inwardIssue.key == $art)] | length
  ')"
  [[ "${REV_LINK}" -gt 0 ]] && die "Conflicting reverse link detected: ${EXISTING_QE_KEY} already blocks ${EXISTING_ART_KEY}"
fi

# 6. Prebuild payloads in preflight
ART_PAYLOAD=""
QE_PAYLOAD=""
if [[ -z "${EXISTING_ART_KEY}" ]]; then
  ART_LABELS="$(jq -cn --arg a "${ART_LABEL}" --arg b "${RELEASE_LABEL}" '[$a, $b]')"
  ART_PAYLOAD="$(build_payload "${ART_TPL_JSON}" "ART" "${ART_SUMMARY}" "${ART_DUE}" "${ART_LABELS}")"
fi
if [[ -z "${EXISTING_QE_KEY}" ]]; then
  QE_LABELS="$(jq -cn --arg b "${RELEASE_LABEL}" '[$b]')"
  QE_PAYLOAD="$(build_payload "${QE_TPL_JSON}" "USHIFT" "${QE_SUMMARY}" "${RELEASE_DATE}" "${QE_LABELS}" "${QE_PARENT}")"
fi

log "Preflight checks passed."

# ==============================================================================
# PHASE 2: EXECUTION (WRITES)
# ==============================================================================
ART_CREATED=false
if [[ -n "${EXISTING_ART_KEY}" ]]; then
  ART_KEY="${EXISTING_ART_KEY}"
  log "ART: Reusing existing ticket ${ART_KEY}"
else
  if ${DRY_RUN}; then
    log "[DRY RUN] Would create ART ticket for ${VERSION} (description and reason redacted)."
    ART_KEY="ART-<dryrun>"
    ART_CREATED=true
  else
    CREATE_RES="$(jira POST "/rest/api/3/issue" "${ART_PAYLOAD}")" || exit 1
    ART_KEY="$(echo "${CREATE_RES}" | jq -r '.key // empty')"
    [[ "${ART_KEY}" =~ ${RE_JIRAKEY} ]] || die "Unexpected create response from ART: missing/invalid issue key; creation outcome unknown. Reconcile before retrying."
    ART_CREATED=true
    log "Created ART ticket: ${ART_KEY}"
  fi
fi

if [[ -n "${EXISTING_QE_KEY}" ]]; then
  QE_KEY="${EXISTING_QE_KEY}"
  log "USHIFT: Reusing existing ticket ${QE_KEY}"
else
  if ${DRY_RUN}; then
    log "[DRY RUN] Would create USHIFT QE ticket for ${VERSION} (description and reason redacted)."
    QE_KEY="USHIFT-<dryrun>"
  else
    CREATE_RES="$(jira POST "/rest/api/3/issue" "${QE_PAYLOAD}")" || exit 1
    QE_KEY="$(echo "${CREATE_RES}" | jq -r '.key // empty')"
    [[ "${QE_KEY}" =~ ${RE_JIRAKEY} ]] || die "Unexpected create response from USHIFT: missing/invalid issue key; creation outcome unknown. Reconcile before retrying."
    log "Created USHIFT ticket: ${QE_KEY}"
  fi
fi

# Issue Linking: QE is blocked by ART
if ${DRY_RUN}; then
  log "[DRY RUN] Would link: ${QE_KEY} (is blocked by) -> ${ART_KEY} (blocks)"
else
  QE_LINKS="$(jira GET "/rest/api/3/issue/${QE_KEY}?fields=issuelinks")" || exit 1
  ALREADY_LINKED="$(echo "${QE_LINKS}" | jq -r --arg art "${ART_KEY}" --arg lt "${LINK_TYPE_NAME}" '
    [.fields.issuelinks[]? | select(.type.name == $lt and .outwardIssue.key == $art)] | length
  ')"
  if [[ "${ALREADY_LINKED}" -gt 0 ]]; then
    log "Link verified: ${QE_KEY} is already blocked by ${ART_KEY}"
  else
    LINK_PAYLOAD="$(jq -n --arg id "${LINK_TYPE_ID}" --arg art "${ART_KEY}" --arg qe "${QE_KEY}" '{
      type: {id: $id},
      inwardIssue: {key: $qe},
      outwardIssue: {key: $art}
    }')"
    jira POST "/rest/api/3/issueLink" "${LINK_PAYLOAD}" >/dev/null || exit 1
    VERIFY_LINKS="$(jira GET "/rest/api/3/issue/${QE_KEY}?fields=issuelinks")" || exit 1
    VERIFIED="$(echo "${VERIFY_LINKS}" | jq -r --arg art "${ART_KEY}" --arg lt "${LINK_TYPE_NAME}" '
      [.fields.issuelinks[]? | select(.type.name == $lt and .outwardIssue.key == $art)] | length
    ')"
    [[ "${VERIFIED}" -gt 0 ]] || die "Link created reported success, but readback verification failed"
    log "Successfully linked: ${QE_KEY} is blocked by ${ART_KEY}"
  fi
fi

# Slack Notification
if ${SKIP_SLACK} || ${ART_CREATED} || ${RESEND_SLACK}; then
  GROUP="${SLACK_ART_GROUP_ID:+<!subteam^${SLACK_ART_GROUP_ID}|@release-artists>}"
  GROUP="${GROUP:-@release-artists}"
  ALL_USERS="$(echo "${DEFAULT_CC_USER_IDS} ${SLACK_CC_USER_IDS:-}" | tr ' ' '\n' | grep -v '^$' | sort -u)"
  CC_STR=""
  while IFS= read -r uid; do
    [[ -n "${uid}" ]] || continue
    CC_STR+="<@${uid}> "
  done <<< "${ALL_USERS}"

  MSG="Hello, ${GROUP} I've opened <${JIRA_BASE_URL}/browse/${ART_KEY}|${ART_KEY}> requesting MicroShift ${VERSION} builds (due date ${ART_DUE_HUMAN}). Please help create RPMs and bootc images, thanks!"
  MSG+=$'\nReason: '"${REASON}"
  [[ -n "${CC_STR}" ]] && MSG+=$'\ncc '"${CC_STR% }"

  if ${SKIP_SLACK}; then
    # Slack's <url|text>/<@id>/<!subteam> markup only renders when delivered via
    # the API/webhook. For manual copy-paste, emit plain text: a bare URL (Slack
    # auto-links it) and a literal @release-artists the operator types to mention.
    MSG_HUMAN="Hello, @release-artists I've opened ${ART_KEY} (${JIRA_BASE_URL}/browse/${ART_KEY}) requesting MicroShift ${VERSION} builds (due date ${ART_DUE_HUMAN}). Please help create RPMs and bootc images, thanks!"
    MSG_HUMAN+=$'\nReason: '"${REASON}"
    log "Slack notification skipped (--skip-slack). Paste this into Slack:"
    printf '%s\n' "----- 8< ----- Slack message ----- 8< -----" "${MSG_HUMAN}" "----- 8< ----------------------- 8< -----" >&2
    [[ -n "${ALL_USERS}" ]] && log "Then @-mention these people (IDs can't be pasted as mentions): $(echo "${ALL_USERS}" | tr '\n' ' ')"
  elif ${DRY_RUN}; then
    log "[DRY RUN] Would send the notification for ${ART_KEY} (message, reason, and URL redacted)."
  else
    log "Posting notification to Slack..."
    curl -sS --fail --connect-timeout 10 --max-time 15 -X POST \
      -H 'Content-Type: application/json' \
      --data "$(jq -n --arg text "${MSG}" '{text: $text}')" "${SLACK_WEBHOOK_URL}" >/dev/null 2>&1 \
      || die "Slack delivery failed: outcome unknown. Verify delivery before using --resend-slack."
    log "Slack notification posted successfully."
  fi
else
  log "ART ticket reused and --resend-slack not set; skipping Slack notification."
fi

# Standard output: issue keys
echo "${ART_KEY} ${QE_KEY}"
