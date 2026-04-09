# CrewTUI

Config-driven CrewAI Terminal UI with heartbeat task engine.

Define your own AI agents, assign models and tools, and run them from a rich terminal interface — no code changes needed.

## Features

- **Custom agents** — define 1-10 agents with roles, goals, backstories, and model assignments
- **3-tier tool system** — built-in tools, CrewAI ecosystem tools, and custom skills
- **Heartbeat engine** — persistent task queue with auto-routing, recurring tasks, dependencies, and output saving
- **Agent memory** — episodic + semantic memory per agent, persists across sessions
- **Skills tab** — install, assign, and manage tools from within the TUI
- **Model wizard** — 8 built-in presets (Grok, Claude, Kimi, Qwen, Gemini, etc.) plus custom model support
- **Telegram notifications** — crew completion/failure alerts
- **Copy/paste** — Ctrl+Y copy, Ctrl+V paste (requires xclip)

## Install

```bash
git clone https://github.com/cliftonwknox/crewtui.git
cd crewtui
uv sync
uv pip install -e .
```

## Setup

```bash
uv run crewtui setup
```

The wizard guides you through:
1. Project name and description
2. Working directory for output/memory/data
3. Agent creation (roles, goals, backstories, models, tools)
4. API key configuration and connection testing
5. Optional default tasks and heartbeat routing
6. Optional Telegram notifications
7. Desktop shortcut generation

## Usage

```bash
uv run crewtui          # Launch TUI (default)
uv run crewtui setup    # Run setup wizard
uv run crewtui models   # Model preset manager
uv run crewtui telegram # Telegram setup
```

## TUI Commands

| Command | Description |
|---------|-------------|
| `/crew <mission>` | Run a custom crew mission |
| `/config <agent> <preset>` | Change an agent's model |
| `/presets` | List available model presets |
| `/queue add <task>` | Add task to heartbeat queue |
| `/queue add --crew <task>` | Queue a full crew run |
| `/queue add --every 6h <task>` | Queue a recurring task |
| `/queue add @agent <task>` | Assign to specific agent |
| `/heartbeat on/off` | Start/stop the heartbeat engine |
| `/skills` | Open Skills tab |
| `/skills install <tool>` | Enable a tool |
| `/skills assign <tool> <agent>` | Give a tool to an agent |
| `/skills new` | Scaffold a custom skill |
| `/memory` | Show agent memory |
| `/help` | Full command reference |

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| F1 | Show all agents |
| F2-F6 | Focus on agent |
| F7 | Files tab |
| F8 | Run default crew |
| F9 | Config tab |
| Ctrl+Y | Copy panel to clipboard |
| Ctrl+V | Paste from clipboard |
| q | Quit |

## Custom Skills

Drop a Python file in your project's `skills/` directory:

```python
from crewai.tools import BaseTool

class MyTool(BaseTool):
    name: str = "My Tool"
    description: str = "What it does"

    def _run(self, query: str) -> str:
        return f"Result for: {query}"
```

Then `/skills refresh` in the TUI to pick it up.

## Requirements

- Python 3.12+
- uv
- xclip (for clipboard support)

## License

MIT
