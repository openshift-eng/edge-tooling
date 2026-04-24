---
name: lvms:z-stream-report
description: Generate z-stream release urgency report for all supported LVMS versions
user-invocable: true
allowed-tools: Bash, Read, Write, Glob, Grep, WebFetch
---

# lvms:z-stream-report

## Synopsis

```bash
/lvms:z-stream-report
```

## Description

Analyzes all currently supported LVMS versions and determines the urgency of releasing a new z-stream for each. Fetches data from Red Hat support policy, JIRA (OCPBUGS), and GitHub to produce a comprehensive urgency report with actionable recommendations.

## Prerequisites

Required environment variables:
- `JIRA_BASE_URL`: Base URL for the Jira Cloud instance (e.g., `https://redhat.atlassian.net`)
- `JIRA_EMAIL`: Email address for Jira authentication (e.g., `user@redhat.com`)
- `JIRA_API_TOKEN`: Atlassian Cloud API token for authentication

## Implementation

### Step 1: Validate Environment

Check that required environment variables are set:

```bash
echo "JIRA_BASE_URL=${JIRA_BASE_URL:-(not set)}"
echo "JIRA_EMAIL=${JIRA_EMAIL:-(not set)}"
echo "JIRA_API_TOKEN is $([ -n "$JIRA_API_TOKEN" ] && echo 'set' || echo 'NOT SET')"
```

If any are not set, display an error and stop:

```text
Error: Required environment variables are not set.
Please set JIRA_BASE_URL, JIRA_EMAIL, and JIRA_API_TOKEN before running this command.

Example:
  export JIRA_BASE_URL=https://redhat.atlassian.net
  export JIRA_EMAIL=user@redhat.com
  export JIRA_API_TOKEN=<your-atlassian-api-token>
```

### Step 2: Fetch Support Timeline

Fetch the LVMS support lifecycle data from Red Hat's official support policy page:

```text
URL: https://access.redhat.com/support/policy/updates/openshift_operators
```

Use WebFetch to retrieve the page and extract the **Logical Volume Manager Storage (LVMS)** support timeline. For each version, extract:
- Version number (e.g., 4.12, 4.14, 4.16, 4.17, 4.18, 4.19, 4.20, 4.21)
- Associated OpenShift version
- GA date
- Full Support end date
- Maintenance Support end date
- EUS Term 1/2/3 end dates (if applicable)

**IMPORTANT**: A version is still supported if ANY of its support phases are active (maintenance OR any EUS term). Even-numbered minor versions (4.12, 4.14, 4.16, 4.18, 4.20) are typically EUS-eligible. Include all versions that have at least one active support phase.

For urgency scoring, use the **latest active end date** across all support phases.

### Step 3: Fetch Release History from Red Hat Container Catalog

For each supported version, determine the latest z-stream release and its date using `skopeo` against `registry.redhat.io`.

**3.1: List all tags from the registry:**
```bash
skopeo list-tags docker://registry.redhat.io/lvms4/lvms-operator-bundle 2>/dev/null | jq -r '.Tags[]' | sort -V
```

Filter to only clean version tags matching `v{major}.{minor}.{patch}` (exclude `-source`, build suffix tags).

**3.2: For each supported version:**

1. Filter tags matching `v{version}.*` -- only clean semver tags
1. Take the highest z-stream tag as the latest release
1. Get the image creation date:

   ```bash
   DATE=$(skopeo inspect --override-arch amd64 --override-os linux \
     docker://registry.redhat.io/lvms4/lvms-operator-bundle:v{tag} 2>/dev/null | \
     jq -r '.Created' | cut -d'T' -f1)
   ```

1. Calculate days since last release
1. Count total number of z-stream releases

**Note**: `skopeo` must be installed and authenticated to `registry.redhat.io`. If unavailable, display install instructions and stop.

### Step 4: Discover JIRA Target Version Field

```bash
curl -s -u "$JIRA_EMAIL:$JIRA_API_TOKEN" \
  -H "Content-Type: application/json" \
  "$JIRA_BASE_URL/rest/api/3/field" | \
  jq -r '.[] | select(.name | test("target.*version"; "i")) | "\(.id) \(.name)"'
```

Save the field ID for subsequent queries. If no match, fall back to `fixVersions` and `versions` fields.

### Step 5: Query JIRA for Open Bugs

**IMPORTANT**: Use Basic auth (`-u "$JIRA_EMAIL:$JIRA_API_TOKEN"`) for Atlassian Cloud. Bearer token auth will fail to return Vulnerability issues.

```bash
curl -s -X POST \
  -u "$JIRA_EMAIL:$JIRA_API_TOKEN" \
  -H "Content-Type: application/json" \
  "$JIRA_BASE_URL/rest/api/3/search/jql" \
  -d '{
    "jql": "project = OCPBUGS AND component = \"Logical Volume Manager Storage\" AND type in (Bug, Vulnerability) AND status != Closed",
    "maxResults": 500,
    "fields": ["summary", "priority", "status", "fixVersions", "versions", "labels", "created", "issuetype", "<TARGET_VERSION_FIELD_ID>"]
  }'
```

**Handle pagination**: If `isLast` is `false`, fetch additional pages by incrementing `startAt`.

**Classify each bug:**
- **CVE**: `labels` contains `SecurityTracking` OR `summary` matches `CVE-\d{4}-\d+`
- **Regular bug**: Everything else

**Group by version** (priority order):
1. Target Version field -> extract minor version
1. `fixVersions` -> first entry's minor version
1. `versions` (Affects Version) -> first entry's minor version
1. If none available -> "Unassigned" group

**Skip** issues targeting unreleased versions.

**Track per version:**
- Total bug count (non-CVE)
- CVE count (total trackers and unique CVEs)
- CVE severity (Critical/Important, Moderate, Low)
- Count by priority: Blocker, Critical, Major, Normal, Minor/Trivial
- Critical issues count: Blocker/Critical bugs + Critical/Important CVEs

### Step 6: Calculate Urgency Score

For each supported version, calculate an urgency score (0-100):

| Factor | Weight | Scoring |
|--------|--------|---------|
| Days since last release | 30 pts max | <30d -> 0, 30-60d -> 10, 60-90d -> 20, >90d -> 30 |
| Open CVEs | 30 pts max | 15 per Critical/Important, 8 per Moderate, 3 per Low (capped at 30) |
| Blocker/Critical bugs | 20 pts max | 10 per Blocker, 5 per Critical (capped at 20) |
| Major bugs | 10 pts max | 2 per Major bug (capped at 10) |
| Support window proximity | 10 pts max | <3mo -> 10, 3-6mo -> 7, 6-12mo -> 3, >12mo -> 0 |

**Urgency levels:**
- **CRITICAL** (75-100): Immediate z-stream release recommended
- **HIGH** (50-74): Plan within 1-2 weeks
- **MEDIUM** (25-49): Schedule normally
- **LOW** (0-24): No urgent need

### Step 7: Generate Report

#### 7.1: Header and Overview Table

```markdown
# LVMS Z-Stream Release Urgency Report

**Generated**: <YYYY-MM-DD HH:MM UTC>
**Data Sources**: Red Hat Support Policy, JIRA (OCPBUGS), Red Hat Container Catalog

---

## Version Overview

| Version | Z-Streams | Latest Release | Days Since | Bugs | CVEs (unique) | Blockers/Crit | Support Phase | Ends | Score | Urgency |
|---------|-----------|----------------|------------|------|---------------|---------------|---------------|------|-------|---------|
```

Sort by urgency score descending.

#### 7.2: Detailed Bug Breakdown Per Version

For each version (ordered by urgency score):

```markdown
## LVMS 4.XX -- Urgency: LEVEL (Score: NN/100)

**Last Release**: vX.Y.Z (NN days ago)
**Support**: <phase> (ends YYYY-MM-DD)

### Score Breakdown
| Factor | Value | Points |
|--------|-------|--------|
| Days since release | NN days | X/30 |
| Open CVEs | N issues | X/30 |
| Blocker/Critical bugs | N issues | X/20 |
| Major bugs | N issues | X/10 |
| Support window | N months left | X/10 |
| **Total** | | **XX/100** |

### Open Issues
| Key | Summary | Type | Priority | Status | Age (days) |
|-----|---------|------|----------|--------|------------|
```

Sort: CVEs first, then by priority, then by age descending.

#### 7.3: Unassigned Bugs

```markdown
## Bugs Without Target Version
| Key | Summary | Priority | Status | Age (days) |
|-----|---------|----------|--------|------------|
```

#### 7.4: Recommendation

```markdown
## Recommendation

### Most Urgent: LVMS 4.XX (Score: NN/100 -- LEVEL)
**Why**: <urgency drivers>
**Action**: <recommended action>

### Other Priorities
| Priority | Version | Score | Recommended Action |
|----------|---------|-------|--------------------|

### Observations
- <trends, patterns, support window concerns>
```

### Step 8: Display Report

Display directly to the user. Do NOT save to file unless explicitly requested.

## Error Handling

| Error | Action |
|-------|--------|
| `JIRA_BASE_URL` not set | Display setup instructions, stop |
| `JIRA_EMAIL` not set | Display setup instructions, stop |
| `JIRA_API_TOKEN` not set | Display setup instructions, stop |
| JIRA returns 401/403 | Display auth error, suggest checking token/email |
| JIRA returns `"Failed to parse Connect Session Auth Token"` | Using Bearer instead of Basic auth -- use `-u "$JIRA_EMAIL:$JIRA_API_TOKEN"` |
| JIRA returns `"The requested API has been removed"` | Using v2 API -- switch to `/rest/api/3/search/jql` |
| `skopeo` not available | Display install instructions, stop |
| Support timeline fetch fails | Warn user, ask for versions manually |
| No bugs found | Report 0 bugs |
| Target Version field not found | Fall back to fixVersions/versions |

## Notes

- **JIRA Authentication**: Red Hat JIRA uses Atlassian Cloud (`redhat.atlassian.net`). Requires Basic auth with email + API token. Generate at https://id.atlassian.com/manage-profile/security/api-tokens. Do NOT use Bearer auth.
- **API Version**: Use v3 REST API (`/rest/api/3/`). The v2 API has been removed.
- **Vulnerability Visibility**: CVE/Vulnerability issues are security-restricted. Basic auth can access them; PAT-based Bearer auth cannot.
- **Urgency Score**: A guideline. A single Critical CVE may warrant immediate release regardless of score.
- **Component Name**: Uses `"Logical Volume Manager Storage"` as the JIRA component name.
- **Version Format**: Handles `4.18.z`, `4.18.0`, `4.18`, `v4.18.1`. All normalized to minor version.
- **Security Issues**: CVEs weighted heavily (30%) due to externally imposed fix deadlines.
