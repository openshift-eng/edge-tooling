# markdownlint

PostToolUse hook that runs `markdownlint` on markdown files after Write or Edit operations, blocking changes that fail linting.

## Installation

```text
/plugin marketplace add openshift-eng/edge-tooling
/plugin install markdownlint
```

## Prerequisites

| Requirement | Install |
|-------------|---------|
| markdownlint-cli | `npm install -g markdownlint-cli` |

## Configuration

Place a `.markdownlint.json` in your repo root to customize rules. See [markdownlint rules](https://github.com/DavidAnson/markdownlint/blob/main/doc/Rules.md).

## Author

jeff-roche
