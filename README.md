# Edge Tooling

## Dev Environment Configurations

### Claude Code Configuration

Red Hat gives access to Claude Code via Google Vertex. In order to configure the Google CLI and  Claude Code, you will need to follow the configuration [available on The Source](https://source.redhat.com/projects_and_programs/ai/ai_tools/claude_code)

### Cursor (Requires License)

Information for gaining  access to cursor is [available on The Source](https://source.redhat.com/projects_and_programs/ai/ai_tools/cursor)

### Visual Studio Code with AI Tools (No License Required)

If you would like utilize Claude Code in a similar manner to Cursor but without the additional IDE subscription, Visual Studio Code is a great solution.

For full cursor-like functionality you will need the following:

- [Visual Studio Code](https://code.visualstudio.com/)
- Claude Code for agentic development and repository context (no auto-complete)
- Continue.dev with Ollama for autocomplete functionality

#### Claude Code in Visual Studio Code

> **Note:** You will need to follow the configuration from [Claude Code Configuration](#claude-code-configuration) in order to configure Claude Code for use in Visual Studio Code.

Once Claude Code is configured, you can install the [Claude Code extension in Visual Studio Code](https://marketplace.visualstudio.com/items?itemName=anthropic.claude-code).

**Manually open Claude Code in the Side Bar**
To open the Claude Code chat in the sidebar, you can open the command pallette (ctrl/cmd + shift + p), type "Claude Code: Open in Side Bar", and hit Enter.

**Set Claude Code to use the Side Bar by default**
Set [Preferred Location](vscode://settings/claudeCode.preferredLocation)

Go to the gear icon > Settings > Extensions > Claude Code and set "Preferred Location" to "Side Bar"

#### Continue.dev configuration

Continue can be used for both chat and autocomplete functionality.

1. [Install the Continue extension](https://marketplace.visualstudio.com/items?itemName=Continue.continue) in Visual Studio Code

2. Configure Continue to use Claude in the `~/.continue/config.yaml` file

    ```yaml
    models:
    - name: Claude Opus 4.6
      provider: vertexai
      model: claude-opus-4-6@default
      env:
        projectId: <your_project_id>
        region: global
      roles:
        - chat
        - edit
        - apply
    ```

3. Install Ollama to run an autocomplete model

    ```bash
    $ curl -fsSL https://ollama.com/install.sh | sh
    ```

4. Configure Ollama to setup a recommended autocomplete model

    ```bash
    $ ollama pull qwen2.5-coder:1.5b
    ```

5. Configure Continue to use qwen for autocomplete in the `~/.continue/config.yaml` file
  
    ```yaml
    models:
    - name: Qwen2.5-Coder 1.5B
      provider: ollama
      model: qwen2.5-coder:1.5b-base
      roles:
        - autocomplete
    ```

**Optional** You can drag the Continue extension icon from the left bar to the Side Bar in VS Code to get the full height chatbar
