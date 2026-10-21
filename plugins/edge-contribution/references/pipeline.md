# Manager Skill Pipeline — Shared Setup

All three manager skills (`heatmap`, `summary`, `export`) run the same four-step
setup: period resolution, workdir, roster load, and activity collection. Those
steps live here so the skills cannot drift apart.

`PLUGIN_ROOT` is `${CLAUDE_PLUGIN_ROOT}` (this plugin's directory).

## 1. Resolve the period

`PERIOD` = the `--quarter` value or `<from>_<to>` (used for labels and the
workdir name). `<PERIOD-FLAGS>` = `--quarter $QUARTER` or `--from $FROM --to $TO`,
passed to both collectors.

If the skill accepts `--refresh`, read that flag too — you'll need it in step 4.

## 2. Set up the workdir

```bash
WORKDIR="/tmp/edge-contribution-$PERIOD"
mkdir -p "$WORKDIR"
```

## 3. Load the full Eng/QE roster

Fetched from GitHub via `gh`, no checkout needed:

```bash
python3 "$PLUGIN_ROOT/bin/load_context.py" --output "$WORKDIR/roster.json"
```

## 4. Collect activity (Jira + GitHub) for the whole roster

**Before you run the collectors**, check if both activity files already exist and
the user did NOT pass `--refresh`:

```bash
if [[ -f "$WORKDIR/jira_activity.json" && -f "$WORKDIR/github_activity.json" && -z "$REFRESH" ]]; then
  # Files exist and no --refresh, reuse them
  JIRA_AGE=$(( $(date +%s) - $(stat -f %m "$WORKDIR/jira_activity.json" 2>/dev/null || stat -c %Y "$WORKDIR/jira_activity.json") ))
  GITHUB_AGE=$(( $(date +%s) - $(stat -f %m "$WORKDIR/github_activity.json" 2>/dev/null || stat -c %Y "$WORKDIR/github_activity.json") ))
  echo "Reusing cached activity files (Jira: ${JIRA_AGE}s old, GitHub: ${GITHUB_AGE}s old)"
fi
```

If files exist and `--refresh` was not set, **skip to step 5** (the next skill step).

Otherwise, collect both sources:

### 4a. Collect Jira activity via MCP

Jira activity is collected through the `mcp__mcp-atlassian__jira_search` MCP tool,
then attributed locally via Python. This replaces direct REST API calls with
Claude-native MCP tools.

**Extract roster members:**

```bash
MEMBERS=$(python3 -c "
import json
roster = json.load(open('$WORKDIR/roster.json'))
print(' '.join([m['jira_username'] for m in roster]))
")
```

**Resolve period dates** from `<PERIOD-FLAGS>`:

```bash
if [[ -n "$QUARTER" ]]; then
  PERIOD_START=$(python3 -c "from _common import resolve_window; w = resolve_window('$QUARTER', None, None); print(w.start.isoformat())")
  PERIOD_END=$(python3 -c "from _common import resolve_window; w = resolve_window('$QUARTER', None, None); print(w.end.isoformat())")
else
  PERIOD_START="$FROM"
  PERIOD_END="$TO"
fi
```

**Query Jira for each member** (3 queries per member):

For each member in `$MEMBERS`, execute these three MCP queries **in parallel**:

1. **Assignee activity:**
   - JQL: `project in (OCPEDGE, USHIFT, OCPBUGS) AND assignee = "{member}" AND updated >= "{PERIOD_START}" AND updated <= "{PERIOD_END}"`
   - Fields: `key,components,updated,summary,parent`
   - Tag each result with: `member={member}`, `kind=assignee`

2. **QA Contact activity (cf[10470]):**
   - JQL: `project in (OCPEDGE, USHIFT, OCPBUGS) AND cf[10470] = "{member}" AND updated >= "{PERIOD_START}" AND updated <= "{PERIOD_END}"`
   - Fields: `key,components,updated,summary,parent`
   - Tag each result with: `member={member}`, `kind=qa`

3. **OCPSTRAT SME/Assignee activity (cf[10475]):**
   - JQL: `project = OCPSTRAT AND (cf[10475] = "{member}" OR assignee = "{member}") AND updated >= "{PERIOD_START}" AND updated <= "{PERIOD_END}"`
   - Fields: `key,components,updated,summary,parent`
   - Tag each result with: `member={member}`, `kind=ocpstrat_role`

**Pagination:** Each MCP query may return a `next_page_token`. If present, repeat
the query with `page_token` set to continue fetching. Loop until no token is
returned.

**Accumulate all results** into a list of issue records:

```json
[
  {
    "member": "jcope@redhat.com",
    "kind": "assignee",
    "fields": {
      "key": "OCPEDGE-1234",
      "components": [...],
      "updated": "2026-08-15T10:30:00Z",
      "summary": "...",
      "parent": {"key": "OCPEDGE-999"}
    }
  },
  ...
]
```

**Extract unique parent keys:**

```bash
PARENT_KEYS=$(python3 -c "
import json
issues = json.load(open('$WORKDIR/raw_issues_temp.json'))
parent_keys = list(set([
    issue['fields']['parent']['key']
    for issue in issues
    if issue.get('fields', {}).get('parent', {}).get('key')
]))
print(','.join(parent_keys))
")
```

**Resolve parent components** (batched, 100 keys per query):

Split `$PARENT_KEYS` into batches of 100. For each batch:

- JQL: `key in (OCPEDGE-1234,OCPEDGE-1235,...)`
- Fields: `key,components`

For each returned parent issue, map its key to the **first component** that
resolves to a workstream (via `component_to_workstream()` from `workstream_map.py`).
If no component maps, store `null` for that parent key.

Build a map: `{parent_key: workstream_or_null}`

**Write intermediate JSON:**

```bash
python3 -c "
import json
data = {
  'issues': json.load(open('$WORKDIR/raw_issues_temp.json')),
  'parent_workstreams': json.load(open('$WORKDIR/parent_map_temp.json')),
  'base_url': 'https://redhat.atlassian.net'
}
json.dump(data, open('$WORKDIR/raw_jira_issues.json', 'w'), indent=2)
"
```

**Apply attribution:**

```bash
python3 "$PLUGIN_ROOT/bin/attribute_jira_activities.py" \
  --raw-issues "$WORKDIR/raw_jira_issues.json" \
  --output "$WORKDIR/jira_activity.json"
```

This produces `jira_activity.json` in the same format as the legacy REST collector.

**Error handling:**

- **Auth error (401/403):** Display auth error, suggest checking `JIRA_USERNAME` and `JIRA_API_TOKEN`, stop.
- **Transient error (429/500/503):** Retry once, then skip that member with a warning.
- **Member with no results:** Valid — all 3 queries can return empty. Continue.

### 4b. Collect GitHub activity via Python script

GitHub activity is collected via the existing Python script (uses GitHub GraphQL API):

```bash
python3 "$PLUGIN_ROOT/bin/collect_github.py" --members-file "$WORKDIR/roster.json" \
  <PERIOD-FLAGS> --output "$WORKDIR/github_activity.json"
```

**Note:** GitHub collection is unchanged and continues to use the Python REST client.

---

Collection takes minutes, and a manager will typically run all three skills over
the same period back to back. Reusing the cache saves ~5 minutes per skill after
the first.

## What comes next

Each skill runs its own report or export command here. See the individual skill
docs for step 5.
