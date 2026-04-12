# Starling

A config-driven terminal interface for managing multi-agent AI crews.
Built on CrewAI, designed for anyone who wants to run AI agent teams
without writing code.

## What it does

Starling lets you define AI agents, assign them roles and tools, and
run them as a coordinated crew — all from a terminal UI or your phone
via Telegram. A background daemon keeps your crew running even when
the terminal is closed.

## Quick start

```
git clone https://github.com/cliftonwknox/Starling.git
cd Starling
uv sync
uv pip install -e .
uv run starling setup
uv run starling
```

The setup wizard walks you through creating your first project:
naming your agents, picking AI models, and configuring tools.

## Requirements

- Python 3.12+
- uv (recommended) or pip
- At least one AI provider API key (xAI, NVIDIA, OpenRouter, etc.)
- Optional: LM Studio or Ollama for local models
- Optional: Telegram bot for remote control

## Features

- Interactive TUI with agent panels, file browser, queue, and cron scheduler
- Models & Agents tab for adding/editing models and agents without config files
- 21 built-in model presets (OpenAI, Anthropic, xAI, DeepSeek, Mistral, Groq, Together, and more)
- Custom model support for any OpenAI-compatible or Anthropic-compatible provider
- Crew Memory: unified vector memory across all agents (nomic-embed-text + LanceDB)
- Background daemon with Telegram bot integration
- Cron scheduling with approval workflow (agents can propose crons)
- Semantic task routing: automatic agent selection by meaning, not keywords
- 6 pre-built agent templates (Researcher, Content Writer, Data Analyst, etc.)
- Agent tier system: Specialist / Coordinator / Leader with cumulative permissions
- Duplicate work detection and progress tracking
- Task queue with heartbeat processing
- Agent memory (episodic + semantic) with global cross-agent knowledge sharing
- Custom skills (CSV, PowerPoint, charts, or build your own)
- Report generation with previous-report context injection
- In-app documentation with section navigation

## Documentation

Full documentation is available in DOCS.txt or the Docs tab inside the TUI.

## License

MIT
