# Law 05: Architecture Documentation

## Applicability

Repositories with multiple components or non-trivial system boundaries SHOULD have a `docs/architecture.md`.

## Content

`docs/architecture.md` SHOULD cover:

- **System overview** --- high-level description of what the system does and how components relate
- **Key design decisions** --- ADRs (Architecture Decision Records) or inline rationale for non-obvious choices
- **Component boundaries** --- what each component owns, what it delegates
- **Data flow** --- how data moves through the system, key interfaces
- **Dependencies** --- external services, libraries, APIs the system relies on
- **Constraints** --- performance budgets, compatibility requirements, deployment limitations
- **Anti-patterns** --- known bad approaches and why they were rejected

## Why This Matters

Without architecture documentation, AI agents optimize locally. They produce correct code that violates system-level invariants:

- Adding direct database access in a component that should go through an API layer
- Duplicating logic that belongs in a shared library
- Breaking event ordering guarantees
- Introducing circular dependencies

Architecture docs are guardrails. They prevent AI from making changes that are locally correct but globally wrong.

## Format

No prescribed format. ADR logs, C4 diagrams, or plain prose all work. The key requirement is that an AI agent reading the file can answer: "What constraints exist that I cannot infer from the code alone?"
