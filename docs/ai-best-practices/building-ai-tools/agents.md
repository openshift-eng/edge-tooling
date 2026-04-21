# Agents

[← AI Best Practices](../../README.md) · [Building AI Tools](README.md)

Agents are focused sub-agents spawned by skills or users for isolated subtasks. They run in their own context window, execute a single well-defined task, and return results through files or return values. Use them for parallel execution, context isolation, and single-purpose analysis.

## When to Use

**Good fit:**

- Parallel independent subtasks (e.g., analyzing 12 CI jobs simultaneously)
- Context isolation -- keeping large exploration output out of the main conversation
- Long-running analysis that would bloat the parent context
- Tasks that need different tool sets than the parent skill

**Not a good fit:**

- Single tool calls -- just call the tool directly
- Tasks requiring user interaction -- agents cannot prompt the user
- Coordinated work where agents need to share intermediate state
- Trivial operations that add orchestration overhead without benefit

## Anatomy

Agent definitions live at `plugins/<name>/agents/<name>.md`. They are pure Markdown with no YAML frontmatter. Template variables like `{WORKDIR}` are substituted by the parent skill before spawning.

Agents can also be defined as skills (with YAML frontmatter and `user-invocable: false`) when they need `allowed-tools` constraints or MCP tool access. The parent skill reads the SKILL.md, substitutes variables, and spawns it as a sub-agent prompt.

### File Location

```text
plugins/
  my-plugin/
    agents/
      bug-analyzer.md
      log-collector.md
    skills/
      my-skill/
        SKILL.md           # orchestrator that spawns agents
```

## Structure

A well-structured agent has four sections:

```markdown
# Agent Name

Brief description of what this agent does.

## Inputs

- `{WORKDIR}` -- working directory for output files
- `{BUG_ID}` -- Jira issue key (e.g., `OCPBUGS-66217`)
- `{EC2_IP}` -- EC2 instance IP address

## Instructions

### 1. Fetch Data

Use the `jira_get_issue` MCP tool to fetch `{BUG_ID}`. Extract:
- `summary` -- issue title
- `status` -- current status
- `components` -- list of component names

### 2. Analyze

Classify the bug based on components and description.
Look for keywords: etcd, fencing, mco, networking.

### 3. Write Output

Write `{WORKDIR}/bug-analysis.json`:

## Output

Write results to `{WORKDIR}/analysis.json`:

    {
      "bug_id": "OCPBUGS-XXXXX",
      "summary": "...",
      "categories": ["etcd", "mco"],
      "topology": "arbiter|fencing|null",
      "confidence": "high|medium|low"
    }
```

Key structural elements:

- **Inputs** list all template variables with descriptions and example values
- **Instructions** use numbered steps with specific tool calls and concrete commands
- **Output** specifies the exact JSON schema the parent skill expects to parse

## Do's

**Design for a single, well-defined task.** Each agent should do one thing. The Bug Analyzer agent in `two-node` fetches a Jira bug, classifies it, and writes structured output. It does not deploy clusters or collect logs -- those are separate agents.

**Use `run_in_background: true` for parallel execution.** Launch all independent agents in a single message:

```text
Agent: subagent_type=general-purpose, prompt="Analyze job 1..."
  run_in_background: true

Agent: subagent_type=general-purpose, prompt="Analyze job 2..."
  run_in_background: true

Agent: subagent_type=general-purpose, prompt="Analyze job 3..."
  run_in_background: true
```

**Communicate via JSON files in `{WORKDIR}`.** Agents write results to predictable file paths. The parent skill reads these files after all agents complete:

```json
{
  "status": "success",
  "findings": ["..."],
  "summary": "one-line summary"
}
```

**Give enough context to work independently.** Agents have no context from the parent conversation. Include: what to do, why, relevant file paths, expected output format, and constraints. Brief agents like you would brief a new colleague who just joined the team.

**Specify the output format precisely.** Show the exact JSON structure with field names, types, and allowed values. Ambiguous output specs lead to parsing failures in the parent skill.

## Don'ts

**Don't make agents interactive.** Agents cannot use `AskUserQuestion` or prompt the user. If input is missing, the agent should write an error to its output file and stop:

```json
{"error": "Failed to fetch OCPBUGS-12345: issue not found"}
```

**Don't give agents unnecessary tools.** An analysis agent that reads files and writes JSON does not need `Bash`, `Glob`, or `Grep`. Constrain tool access to what the agent actually needs. For skill-based agents, use `allowed-tools` in the frontmatter.

**Don't use agents for trivial tasks.** If the work is a single tool call or a simple transformation, do it inline. Agents add orchestration overhead -- spawning, waiting, file I/O, error handling.

**Don't rely on shared state between agents.** Agents are isolated by design. They cannot read each other's in-progress output. If Agent B needs Agent A's results, run them sequentially or have the parent skill coordinate.

**Don't use agents for user-facing workflows.** Agents run in the background without user visibility. If the workflow needs user decisions, confirmations, or feedback, keep it in the main skill.

## Anti-Patterns

| Anti-Pattern | Problem | Fix |
|---|---|---|
| **Chatty agent** | Agent tries to report progress or ask questions | Design for autonomous execution with error output |
| **Over-tooled** | Agent has access to 15 tools but uses 2 | Restrict `allowed-tools` to what's needed |
| **Trivial agent** | Agent wraps a single `jira_search` call | Call the tool directly in the parent skill |
| **Missing output spec** | Parent skill guesses at agent output format | Define exact JSON schema in the agent definition |
| **Context-dependent** | Agent assumes knowledge from the parent conversation | Include all necessary context in the agent prompt |

## Examples from This Repo

### Bug Analyzer (`two-node`)

Location: `plugins/two-node/agents/bug-analyzer.md`

The Bug Analyzer fetches a Jira bug, detects the target topology (arbiter vs. fencing), classifies the bug category, extracts manifests and reproduction steps, and writes a structured JSON output.

Key patterns:

- **12 analysis steps** covering topology detection, version extraction, manifest parsing, and reproduction timing
- **Confidence levels** (`high`, `medium`, `low`) for ambiguous classifications
- **Error output** for failed fetches: `{"error": "Failed to fetch ..."}`
- **Structured JSON output** with 25+ fields at `{WORKDIR}/bug-analysis.json`

### CI Job Analyzer (`microshift-ci`)

The `microshift-ci:doctor` skill spawns one agent per failed CI job. Each agent runs `/microshift-ci:prow-job` on pre-downloaded artifacts and writes the analysis report to a file.

Agent prompt (from the doctor skill):

```text
Agent: subagent_type=general-purpose, prompt="Analyze this Prow job and save the report:
1. Run /microshift-ci:prow-job /tmp/microshift-ci-claude-workdir.260421/artifacts/1984108354347208704
2. After the analysis completes, save the FULL report output to:
   /tmp/microshift-ci-claude-workdir.260421/analyze-ci-release-4.22-job-3-1984108354347208704.txt
   Use the Write tool to save the file."
```

Key patterns:

- **All agents launched in a single message** with `run_in_background: true`
- **Predictable file naming**: `analyze-ci-release-<VERSION>-job-<N>-<JOB_ID>.txt`
- **Pre-downloaded artifacts** so agents use local paths (no redundant downloads)
- **No coordination between agents** -- each is fully independent

### Release Health Analysis (`edge-scrum`)

Location: `plugins/edge-scrum/skills/release-health-analysis/SKILL.md`

A skill-based agent (non-user-invocable) spawned by `release-health` during Phase 4. The parent skill fetches all data inline using MCP tools, transforms it with scripts, and writes four JSON files. The analysis agent reads those files, assesses risks, and writes `analysis.md`.

Key patterns:

- **Skill-based agent** with `user-invocable: false` and constrained `allowed-tools`
- **Template variables** (`{WORKDIR}`, `{VERSION}`, `{TODAY}`) substituted by parent
- **Read-only Jira access** -- agent can query but not modify data
- **Structured output sections** with sentinel markers (`===ANALYSIS_META===`, `===SECTION:*===`) for the parent to parse

## Orchestration Example

The `microshift-ci:doctor` skill demonstrates the full orchestration pattern:

```text
microshift-ci:doctor (skill orchestrator)
│
├── Step 1: Prepare (deterministic)
│   └── bash doctor.sh prepare --workdir $WORKDIR $RELEASES --rebase
│       → downloads artifacts, writes job JSON files
│
├── Step 1b: Graphs (deterministic)
│   └── bash doctor.sh graphs --workdir $WORKDIR
│       → generates PCP performance graphs from archives
│
├── Step 2: Analyze (parallel agents)
│   ├── Agent: prow-job analysis for release 4.19 job 1 → job-1.txt
│   ├── Agent: prow-job analysis for release 4.19 job 2 → job-2.txt
│   ├── Agent: prow-job analysis for release 4.20 job 1 → job-3.txt
│   ├── Agent: prow-job analysis for release 4.20 job 2 → job-4.txt
│   ├── ...
│   └── Agent: prow-job analysis for PR rebase job N  → pr-job-N.txt
│   (ALL launched in one message with run_in_background: true)
│
├── Step 3: Bug correlation (parallel agents)
│   ├── Agent: create-bugs 4.19 (dry-run) → bugs-4.19.json
│   ├── Agent: create-bugs 4.20 (dry-run) → bugs-4.20.json
│   └── Agent: create-bugs rebase-release-4.22 → bugs-rebase.json
│   (ALL launched in one message with run_in_background: true)
│
└── Step 4: Finalize (deterministic)
    └── bash doctor.sh finalize --workdir $WORKDIR $RELEASES
        → aggregates results, generates HTML report
```

Key principles:

1. **Deterministic scripts** handle data collection, artifact download, aggregation, and report generation
2. **LLM agents** handle only root cause analysis and Jira bug correlation -- tasks that require reasoning
3. **All agents in a wave launch simultaneously** -- no per-release sequential execution
4. **Communication through files** -- agents write to `{WORKDIR}` with prescribed filenames
5. **The orchestrator never reads raw agent work** -- it reads only the final output files

## Sub-agent Usage Recommendations

Sub-agents are the primary mechanism for maintaining context efficiency in long-running sessions. Key recommendations from [Effective Context Engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents):

**Delegate exploration, not decisions.** Sub-agents should explore, analyze, and summarize. The main agent should synthesize results and make decisions. A sub-agent that searches 50 files and returns "the retry logic is in `client.go:142` with a 3-second backoff" is valuable. A sub-agent that decides "we should refactor the retry logic" is overstepping.

**Each sub-agent returns a condensed summary.** A sub-agent might explore tens of thousands of tokens (reading files, running searches, analyzing logs), but returns only 1,000–2,000 tokens to the parent. This is the core value proposition -- context isolation with information preservation. The parent never sees the raw exploration.

**Use sub-agent types intentionally:**

| Sub-agent Type | When to Use |
|---|---|
| `Explore` | Codebase search, file discovery, tracing dependencies, reading multiple files for understanding |
| `general-purpose` | Research, web searches, multi-step reasoning, data analysis, running skills |
| `Plan` | Design implementation strategies, evaluate trade-offs, break down complex tasks |

**Parallelize aggressively.** If sub-tasks are independent, spawn them in a single message so they run concurrently. Sequential execution wastes time when tasks don't depend on each other. The `microshift-ci:doctor` skill launches 12+ agents in one message rather than analyzing jobs one at a time.

**Brief sub-agents like new colleagues.** They have no context from the parent conversation. Include: what to do, why, relevant file paths, expected output format, and any constraints. A prompt like "analyze the CI failure" is too vague. A prompt like "Run /microshift-ci:prow-job on artifacts at /tmp/workdir/artifacts/12345, save the full report to /tmp/workdir/job-1.txt" gives the agent everything it needs.

## References

- [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) -- context isolation and sub-agent architectures
- [Skill best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices) -- agent design within skills
- [Context Management](../tool-guides/context-management.md#sub-agent-architectures) -- sub-agent context costs and patterns
