# Service proposals

This folder holds **design and adoption proposals for services** (internal tools, hosted platforms, shared infrastructure, or vendor products) before we depend on them as a team or organization.

Each proposal is a single Markdown file. Add a new file when you want to introduce or significantly change reliance on a service. Use the template so reviewers get comparable answers.

## How to add a proposal

1. Copy `TEMPLATE.md` to a new file named after the service or initiative, for example `payload-monitor-dashboard.md`.
2. Fill every section. Use `N/A` only when the question truly does not apply, with a one-line justification.
3. Open a pull request and request review from people who would own or operate the service.

## What each proposal must answer

| Topic | Question |
|--------|----------|
| **Availability** | What is the impact if this service becomes unavailable (to the team, to the org, to customers or downstream systems)? |
| **Recovery** | How do we recover when it goes down (runbooks, vendor support, failover, rebuild steps, RTO/RPO if known)? |
| **Cost** | Does it cost the team or organization money (licenses, usage-based billing, headcount, opportunity cost)? |
| **Maintenance** | What is the ongoing maintenance cost for the team (upgrades, on-call, security patches, integration work)? |

## Architecture

Include an **architecture diagram** so readers can see how the service fits into existing systems. Prefer one of:

- [Mermaid](https://mermaid.js.org/) in a fenced `mermaid` code block (renders in GitHub and many editors)
- A linked diagram in your team’s usual tool, with a short note in the proposal
- ASCII for very small sketches

If the diagram lives outside the repo, link it and summarize the boundaries and trust zones in prose.

## Template

The canonical structure lives in [`TEMPLATE.md`](TEMPLATE.md). New proposals should follow that file’s headings and section order. Examples: [`ec2-watchman.md`](ec2-watchman.md) (in-repo AWS tool), [`slack-bot-eddie.md`](slack-bot-eddie.md) (Slack app for webhooks).
