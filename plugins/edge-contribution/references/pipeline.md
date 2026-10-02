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
else
  # Missing files or --refresh set, collect now
  python3 "$PLUGIN_ROOT/bin/collect_jira.py"   --members-file "$WORKDIR/roster.json" \
    <PERIOD-FLAGS> --output "$WORKDIR/jira_activity.json"
  python3 "$PLUGIN_ROOT/bin/collect_github.py" --members-file "$WORKDIR/roster.json" \
    <PERIOD-FLAGS> --output "$WORKDIR/github_activity.json"
fi
```

Collection takes minutes, and a manager will typically run all three skills over
the same period back to back. Reusing the cache saves ~5 minutes per skill after
the first.

## What comes next

Each skill runs its own report or export command here. See the individual skill
docs for step 5.
