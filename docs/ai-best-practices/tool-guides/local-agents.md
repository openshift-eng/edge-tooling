# Local Agents (Ollama and Local Models)

[← AI Best Practices](../README.md)

Local AI agents run models on your machine, providing offline AI assistance with no data leaving your workstation. This is relevant for working with sensitive code or in environments without reliable internet access.

## When to Use Local Models

- Working with confidential or sensitive code that cannot be shared with cloud services
- Offline or air-gapped environments
- Experimentation with models not yet approved for cloud use
- Personal learning and prototyping

## Setup and Configuration

### Ollama

[Ollama](https://ollama.com) is the recommended local model runtime. It manages model downloads, serves models locally, and provides an OpenAI-compatible API.

**Installation:**

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

**Pull a model:**

```bash
ollama pull granite3.1-dense:8b
```

### Model Selection

**Red Hat prefers models under genuine open-source licenses:**

| Model Family | License | Recommended |
|-------------|---------|-------------|
| IBM Granite | Apache-2.0 | Yes |
| Mistral (Apache-2.0 variants) | Apache-2.0 | Yes |
| Meta Llama | Llama License (restrictive) | Requires AIA review |

Even for local use, respect model licensing. Models with restrictive licenses (use limitations, user exclusions) do not meet Red Hat's open-source definition and should go through AIA review before use for Red Hat work.

### Integration with AI Tools

**With Claude Code:** Claude Code does not currently use local models directly, but local models can complement Claude Code workflows — use them for quick local queries while reserving Claude Code for complex agentic tasks.

**With Cursor:** Cursor can be configured to use local Ollama models. Set the model endpoint to `http://localhost:11434` in Cursor's model settings. Verify the specific model is approved for code assistant use.

**With VS Code extensions:** Several VS Code extensions (Continue, Cody) support local Ollama models. Verify extension approval status on the [AI Tools Source page] before use.

## Usage Guidelines

### Approval Requirements

Local models still require approval consideration:

- **Open source tools** (Ollama itself) that are not models and don't call external APIs don't need an AIA.
- **The models you run** are subject to licensing review. Apache-2.0 licensed models (Granite, some Mistral) are preferred.
- **Restrictive-license models** require AIA review even when run locally.
- **Use for Red Hat work** requires the use case to be covered by the AI Tools Source page or an approved AIA.

### Data Considerations

Local models are inherently more private — no data leaves your machine. However:

- **Model outputs are not confidential.** Don't assume local = secret. The model's responses could still be incorrect or contain training data patterns.
- **Local models have smaller context windows** than cloud models. Be more aggressive about context management.
- **Quality varies significantly** between local and cloud models. Verify outputs more carefully with smaller local models.

### Performance Expectations

Local models on typical developer hardware:

- **8B parameter models** — responsive for code completion and short queries
- **70B+ parameter models** — require significant GPU memory, may be slow without dedicated hardware
- **Code-specific models** (Granite Code, CodeLlama) — optimized for programming tasks and more efficient for code work

## Sandboxing for Experimentation

Red Hat provides sandbox environments for AI experimentation at no cost:

- **models.corp** — up to 14 days per request (renewable). Pre-populated with DataSciencePipelines.
- **MOSAIC Platform** — up to 48 hours per request (renewable up to 3 times). All storage is temporary.

Access both via Red Hat VPN. See the AI Tools FAQs on The Source for getting started guides.

For moving from experiment to production, the **AI Combinator program** provides a path to compute resources.

<!-- Link references -->
[AI Tools Source page]: https://source.redhat.com/departments/it/ai-tools
