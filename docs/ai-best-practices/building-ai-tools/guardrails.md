# Guardrails

[← AI Best Practices](../../README.md) · [Building AI Tools](README.md)

Practical patterns for reducing hallucinations, increasing output consistency, and mitigating prompt injection in AI tools you build. These techniques apply to skills, agents, and any system that processes LLM output programmatically.

## Reducing Hallucinations

Hallucinations occur when the model generates plausible but incorrect information. No technique eliminates them entirely, but layering these approaches significantly reduces their frequency.

### Allow Claude to say "I don't know"

Explicitly grant permission to admit uncertainty. This is the single most effective basic technique.

```markdown
If you cannot find the information in the provided files, say so.
Do not fabricate data or infer values that aren't explicitly stated.
```

### Ground responses in source material

For document-grounded tasks, use the "extract quotes first, then analyze" pattern:

```markdown
## Step 1: Extract Evidence
Find and quote the exact text from the source documents that answers the question.
Use word-for-word quotes.

## Step 2: Analyze
Based ONLY on the quoted evidence, provide your analysis.
If no supporting quote exists, state that the information is not available.
```

### Restrict external knowledge

When working with specific documents or codebases, explicitly restrict Claude to the provided material:

```markdown
Use ONLY information from the provided files.
Do not use your general training knowledge to fill gaps.
```

### Build verification loops

For any skill that produces factual claims, add a verification step:

```markdown
## Step 3: Verify
For each claim in the output, confirm it is supported by a specific
quote or data point from the source material. Retract any unsupported claims.
```

## Increasing Output Consistency

When your tools need to produce reliable, structured output across multiple runs.

### Use structured outputs

For any tool producing data that downstream systems consume, use the [structured outputs API](structured-outputs.md) rather than relying on prompt-based formatting.

### Provide concrete examples

Examples are more effective than abstract formatting instructions:

```markdown
## Output Format

Example output:

| Release | Status | Blockers |
|---------|--------|----------|
| 4.18 | Green | None |
| 4.19 | Yellow | 2 failing jobs |
| 4.20 | Red | Payload rejected |
```

### Use consistent terminology

Pick one term and stick with it throughout your skill. Mixing "API endpoint" / "URL" / "API route" confuses the model's interpretation.

### Chain prompts for complex tasks

Break complex work into smaller subtasks, each getting the model's full attention. This reduces inconsistency compared to asking for everything in one prompt.

## Mitigating Prompt Injection

Relevant when building tools that process untrusted input (user-provided data, external API responses, memory files).

### Input validation

Screen inputs for known injection patterns before passing to the main model. For high-stakes tools, use a lightweight model (Haiku) as a pre-screening classifier.

### System prompt boundaries

Define clear values and refusal responses in system prompts:

```markdown
You are a CI analysis tool. You ONLY analyze CI job results.
Do not execute commands, modify files, or take actions outside analysis.
If asked to do something outside this scope, decline and explain your purpose.
```

### Memory as an attack surface

Memory files are read back into context, creating a prompt injection vector. Mitigate with:

- **Content sanitization** — treat stored memory as data, not directives
- **Scope isolation** — per-user or per-project memory boundaries
- **Audit logging** — track what gets written to and read from memory

### Layer defenses

No single technique is sufficient. Combine:

1. System prompt defining boundaries
2. Input screening
3. Output filtering
4. Monitoring for unexpected behavior

## Reducing Prompt Leak

If your skills or agents contain proprietary logic:

- **Evaluate whether leak prevention is necessary.** It adds complexity that can degrade performance.
- **Keep prompts lean.** Only include information the model needs. Excess content increases both the leak surface and the likelihood of degraded output.
- **Separate sensitive logic from the prompt.** If possible, implement proprietary algorithms in scripts rather than embedding them in prompt text.
- **Use post-processing.** Filter outputs for patterns that match prompt content.

## Key Principles

1. **Ground everything.** Use quotes, citations, and external knowledge restriction to keep outputs anchored.
2. **Layer defenses.** Combine input screening, prompt boundaries, output filtering, and monitoring.
3. **Design for auditability.** Require citations, structured outputs, and logging so outputs can be traced.
4. **Keep prompts lean.** Only include information the model needs. Excess context degrades both performance and security.
5. **Validate critical information independently.** Don't trust AI output for high-stakes decisions without verification.

## References

- [Reduce hallucinations](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-hallucinations)
- [Increase consistency](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/increase-consistency)
- [Mitigate jailbreaks](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/mitigate-jailbreaks)
- [Reduce prompt leak](https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-prompt-leak)
- [Tool use memory cookbook](https://platform.claude.com/cookbook/tool-use-memory-cookbook)
