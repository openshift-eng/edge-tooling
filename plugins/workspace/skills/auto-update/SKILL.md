---
name: auto-update
description: Automatically save project notes within 50 minutes of any work, during idle, without interrupting the session
---

# Auto-Update Project Docs

Arm a one-shot `/workspace:update-project` cron job that fires 50 minutes
from now. Each time it fires and finds something to save, it re-arms
itself for another 50 minutes — so the loop continues without a
recurring cron job. It stops arming itself once there's nothing left to
save.

## Step 1: Check for Existing Loop

Run CronList. If any job's prompt contains `update-project`, tell the
user it's already running (show the job ID) and stop.

## Step 2: Schedule

Resolve the current project name (from conversation context or
`$ARGUMENTS`). Compute the exact date/time 50 minutes from now (`date`),
then call CronCreate with `recurring: false` and the cron string pinned
to that exact minute/hour/day-of-month/month — e.g. `"37 14 18 9 *"`,
not `"*/50 * * * *"` (cron reads that as minutes 0 and 50, not "50
minutes from now"). Prompt: `/workspace:update-project <project-name>
--auto`.

The trailing `--auto` token is a sentinel `/workspace:update-project`
uses to tell a cron-triggered run apart from a manual one — it's what
lets the loop re-arm itself after a cron-fired run without a manual
`/workspace:update-project` silently starting the loop as a side
effect. It's not name-shaped, so it can never collide with a project
that happens to be named `auto`.

If CronCreate succeeds, confirm: notes will be saved automatically in
about 50 minutes if there's anything new; cancel early with CronDelete
(show the job ID).

If CronCreate fails, report the error and stop — do not claim success.

Do NOT run `/workspace:update-project` immediately — the first fire
should happen after the idle window, when the user has had time to work.
