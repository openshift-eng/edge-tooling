# Law 06: Upstream Documentation

## Applicability

Repositories that interact with upstream open-source projects SHOULD have a `docs/upstream.md`.

## Content

`docs/upstream.md` SHOULD cover:

- **Upstream project** --- name, repository URL, communication channels (mailing list, Slack, IRC)
- **Relationship** --- one of: consumer, contributor, maintainer, fork
- **Contribution process** --- how to submit patches upstream, CLA requirements, review expectations
- **Carry patches** --- list of patches carried downstream, rationale, upstream tracking issues
- **Sync cadence** --- how often the downstream syncs with upstream (e.g., every release, weekly rebase)
- **Style differences** --- where downstream conventions differ from upstream (naming, formatting, testing)

## Why This Matters

AI agents lack institutional knowledge about upstream relationships. Without this documentation, agents may:

- Submit PRs that conflict with upstream conventions
- Duplicate work already done upstream
- Modify carry patches without understanding why they exist
- Generate code in the wrong style for upstream contribution
- Miss required CLA or sign-off steps

## Format

Plain markdown. Keep it factual. Update when the relationship changes (e.g., fork becomes contributor, sync cadence changes).
