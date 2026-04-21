# Structured Outputs

[← AI Best Practices](../../README.md) · [Building AI Tools](README.md)

Structured outputs guarantee that Claude's responses conform to a specific JSON schema. Use them when downstream systems need to parse model output programmatically — no more `JSON.parse()` errors or retry loops for malformed responses.

## When to Use

- Tool responses that feed into scripts or pipelines
- Skills that produce structured reports consumed by other skills
- Any integration where output format consistency is critical
- Agent outputs that parent skills need to parse reliably

**Don't use structured outputs when:**

- The output is free-form text for humans to read
- You only need simple key-value pairs (prompt engineering is sufficient)
- The task requires creative or flexible formatting

## Two Capabilities

### JSON Output Mode

Controls Claude's response format at the API level:

```python
response = client.messages.create(
    model="claude-sonnet-4-6-20250514",
    max_tokens=1024,
    messages=[...],
    output_config={"format": {"type": "json", "schema": schema}}
)
```

**Guarantees:** Always valid JSON, guaranteed field types, required fields present.

### Strict Tool Use

Guarantees schema validation on tool inputs:

```python
tools = [{
    "name": "get_sprint_data",
    "description": "Fetch sprint metrics",
    "input_schema": {...},
    "strict": True
}]
```

These can be used independently or together.

## Best Practices

### Design schemas carefully

- Keep schemas focused — only include fields you actually need
- Use descriptive property names that guide the model
- Mark fields as `required` when they must always be present
- Set `additionalProperties: false` on all objects (required for strict mode)

### Handle edge cases

Structured outputs can still produce unexpected results in two cases:

1. **Safety refusals** — `stop_reason: "refusal"`. The model declined to generate content.
2. **Token limit** — `stop_reason: "max_tokens"`. The response was truncated.

Always check `stop_reason` before parsing the response.

### Know the limitations

| Constraint | Limit |
|-----------|-------|
| Max strict tools per request | 20 |
| Max optional parameters across all strict schemas | 24 |
| No recursive schemas | — |
| No external `$ref` references | — |
| Numerical/string constraints (`minimum`, `maxLength`) | Not enforced at API level — added to descriptions automatically by SDKs |

### Use SDK integrations

SDKs provide native schema support:

- **Python:** Pydantic models with `client.messages.parse()`
- **TypeScript:** Zod schemas with `zodOutputFormat()`

These are more ergonomic than hand-writing JSON Schema.

### Cache schemas

First request with a new schema has extra latency (grammar compilation). Compiled grammars are cached for 24 hours. Cache invalidates on schema structure changes but NOT on name/description changes.

## Relevance for Skill and Agent Builders

For Claude Code skills and agents, structured outputs are most useful when:

- An agent writes results to a JSON file that a parent skill must parse
- A skill produces data that feeds into a deterministic script
- You need consistent report formats across multiple runs

For simple cases (agent writes a JSON file), clear output format instructions in the agent prompt are usually sufficient. Reserve API-level structured outputs for cases where format violations would cause downstream failures.

## References

- [Structured outputs documentation](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
