# Trivial Change Classification

This document governs classification of all proposed fixes — whether
they originate from CI failures or PR review comments. The scope
guards, categories, and non-trivial signals below apply uniformly.

For the comment track, upstream skills provide an initial classification
(coderabbit `auto_apply` = trivial, `review` = non-trivial; vet-review
`survived` = always non-trivial). When the yolo-agent proposes its own
fix for a review comment, use the criteria below to classify it.

## Trivial — auto-push WITHOUT confirmation

A change is trivial when it meets ALL three scope guards AND falls
into at least one category below.

**Scope guards** (all must hold):

- Diff touches 3 or fewer files
- Total added + removed lines across all files is under 50
- All changes are within a single Go package or directory tree

**Categories:**

1. **Formatting and style** — whitespace, indentation, line length,
   `gofmt`/`goimports` output, trailing newlines
2. **Linter fixes** — `golint`, `staticcheck`, `shellcheck`,
   `golangci-lint`, `yamllint` — where the fix is mechanical
   (e.g., exported function comment, unused variable removal, error
   string capitalization)
3. **Identifier renaming** — variable, function, or constant renames
   that are local to one file or unexported and used only within one
   package
4. **Missing imports** — adding an import that resolves a compile error
5. **Simple test fixes** — expected value mismatch, golden file updates,
   test data constant corrections where the test intent is unchanged
6. **Comment and string typos** — fixing typos in comments, string
   literals, log messages, or error messages where meaning is preserved
7. **Documentation-only changes** — edits to `*.md`, `*.txt`, `*.adoc`,
   `doc/`, `docs/`, `examples/` — no code files touched
8. **Go dependency tidying** — `go.sum` regeneration, `go.mod` tidy
   when the only `go.mod` change is the `go` directive version or
   indirect dependency ordering (NOT new direct dependencies)
9. **Generated code regeneration** — re-running code generators
   (`make generate`, `make manifests`, `controller-gen`, `deepcopy-gen`)
   when the only changes are in files with a generation header
   (`// Code generated ... DO NOT EDIT`)
10. **Build tag and constraint fixes** — adding or fixing `//go:build`
    lines to resolve CI platform-mismatch errors
11. **YAML/JSON config fixes** — fixing indentation, quoting, trailing
    commas, or key ordering in config files when the semantic content
    is unchanged

## Non-trivial — ALWAYS require confirmation

Any of these signals makes a change non-trivial regardless of category:

- **Logic or control flow** — any change to `if`, `for`, `switch`,
  `select`, `return`, `goto`, or boolean expressions
- **API surface** — changes to exported functions, struct fields,
  interfaces, or protobuf definitions
- **New files** — creating any file that did not exist before
- **Deletion** — removing functions, files, or test cases
- **Error handling** — adding, removing, or changing error returns,
  error wrapping, or panic/recover
- **Concurrency** — anything involving goroutines, channels, mutexes,
  or sync primitives
- **Multi-package scope** — changes spanning more than one Go package
  (or more than one directory tree for non-Go)
- **Dependency changes** — adding, removing, or upgrading direct
  dependencies in `go.mod`, `package.json`, `requirements.txt`,
  or `Dockerfile`
- **CI/workflow files** — any change to `.github/`, `Makefile`,
  `Dockerfile`, `.tekton/`, `Containerfile`, or CI pipeline configs
- **Security-adjacent** — changes to auth, TLS, certificate, RBAC,
  or permission logic even in files not blocked by security patterns
- **Scope guard exceeded** — more than 3 files, more than 100 lines
  changed, or cross-package changes

## Edge cases

| Situation | Classification |
|-----------|----------------|
| Mix of trivial + non-trivial changes in one fix | **Non-trivial** — the whole batch requires confirmation |
| Trivial category but scope guards exceeded | **Non-trivial** |
| Generated file changes mixed with hand-written code | **Non-trivial** |
| Test file changes that alter test logic (not just expected values) | **Non-trivial** |
| `go.mod` changes that add a new `require` line | **Non-trivial** |
| Vendor directory updates (`vendor/`) | **Non-trivial** |
| Renaming an exported symbol used across packages | **Non-trivial** |
| Comment change that alters a `//go:generate` directive | **Non-trivial** |
| Updating a `.gitignore` or `.dockerignore` | **Trivial** if only adding entries, **non-trivial** if removing |

When uncertain, classify as non-trivial.
