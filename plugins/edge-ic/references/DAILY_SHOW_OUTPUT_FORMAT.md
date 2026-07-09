# Daily Show Output Format

Reference template for the `edge-ic:daily-show` command output.

Render as plain Markdown — **not** inside a fenced code block. A heading marks the date, each non-empty TODO section becomes its own subheading, and items render as bullets with a status marker. This mirrors `edge-ic:morning`'s idea of one visually distinct block per section, without box-drawing: a code fence would suppress `**bold**` and force fragile manual column alignment, so don't use one here.

## Template

### Section icons to be used

- Priority: `⚠`
- Completed: `✓`
- All other sections use `»`

### Status Icons to be used

- Any completed items should use `✓`
- Any in progress items (or items waiting on review) should use `➲`
- All other items use `⭘`

### Markdown Template

```markdown
# TODO — {date}

## {Section Icon} {Section Name}
- {Status Icon} **{ticket-or-PR-ref}**: {not started item description}
  - {Type}: {value}
```

## Rules

- One `##` subheading per non-empty section, in file order. Omit the heading entirely for empty sections.
- Status marker is the Status Icon above (`✓`/`➲`/`⭘`), chosen by section/state — not configurable. Not inside a code fence, so the icons render fine here — no column-alignment concern.
- Bold any Jira ticket key or `owner/repo#N` PR reference at the start of an item's description (e.g., `**OCPEDGE-2510**`, `**edge-context#34**`). This works because the output isn't fenced.
- `Key Issues Tracked` entries have no checkbox — render as plain `-` bullets.
- Sub-lines are nested bullets indented under their parent item, keeping the `Type: value` text exactly as written in the file (`PR: ...`, `Jira: ...`, `Note: ...`) — never reword or drop one.
- Let Markdown handle wrapping and line length naturally — no manual padding, truncation, or alignment of any kind.
- If every section being displayed is empty, print "Nothing tracked for {date}" as plain text instead of any heading.
- If a `--limit` cutoff occurred, print "... N more item(s) not shown" as a plain line after the last section.
