# challenge

Adversarial hypothesis reviewer — systematically attacks theories and root cause analyses to find weaknesses before they find you.

## Installation

Install via Claude Code's plugin system:

```text
/plugin marketplace add openshift-eng/edge-tooling
/plugin install challenge
```

## Usage

During any conversation where you've formed a hypothesis or root cause theory:

```text
/challenge
```

Claude will:

1. Extract the hypothesis from conversation context
2. Break it into falsifiable claims
3. Actively search the codebase for counter-evidence
4. Present structured counter-arguments (CA-1, CA-2, ...) ranked by severity
5. Suggest experiments to confirm or deny each challenge

### When to use

- After forming a root cause theory during debugging
- After proposing an explanation for a CI failure or test regression
- After drawing conclusions from log analysis
- Any time you want your reasoning stress-tested before acting on it

### What you get

A structured adversarial review containing:

- **Counter-arguments** with severity/likelihood ratings
- **Evidence gaps** — claims that couldn't be verified
- **Assumptions inventory** — implicit assumptions that may not hold
- **Alternative explanations** with distinguishing tests
- **Next steps** ordered by information value
- **Investigation log** of all searches performed

## Requirements

- **Claude Code:** >= 1.0.0
- **Category:** debug

## Author

fonta-rh
