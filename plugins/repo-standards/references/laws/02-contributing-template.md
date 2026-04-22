# Law 02: CONTRIBUTING.md Template

Every repository MUST have a `CONTRIBUTING.md` with the sections below. Adapt content to your project; the section structure is mandatory.

## Required Sections

### Getting Started

MUST cover:
- Prerequisites (languages, tools, accounts)
- Repository setup steps
- First build / first test command

### Development Workflow

MUST describe the standard flow:
1. Branch from `main`
2. Make changes
3. Run tests locally
4. Open a pull request
5. Pass code review
6. Merge

### Branch Naming

SHOULD use the pattern: `type/short-description`

This convention is configurable per repository. Override via repository-specific branch protection rules or CONTRIBUTING.md customization.

Valid types: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`

### Commit Messages

MUST use conventional commits: `type(scope): description`

Examples:
- `feat(api): add batch endpoint`
- `fix(controller): prevent nil pointer in reconcile loop`
- `docs(readme): update deployment instructions`
- `refactor(store): extract cache layer`
- `test(e2e): add upgrade path coverage`
- `chore(deps): bump Go to 1.23`

Valid types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`

### Testing

MUST cover:
- Unit tests required for new code
- Integration tests required for API changes
- How to run tests (e.g., `make test`)
- Minimum coverage target: 80%

### Code Review

MUST state:
- Minimum 1 approval required
- CI MUST pass before merge

### Code Style

MUST reference:
- Language-specific formatters (e.g., `gofmt`, `black`, `prettier`)
- Format command (e.g., `make fmt`)

## Validation Checklist

Health checks validate CONTRIBUTING.md contains these section headers (case-insensitive):
- [ ] Getting Started
- [ ] Development Workflow
- [ ] Branch Naming
- [ ] Commit Messages
- [ ] Testing
- [ ] Code Review
- [ ] Code Style
