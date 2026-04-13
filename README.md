# Starling

A config-driven terminal interface for managing multi-agent AI crews.
Built on CrewAI, designed for anyone who wants to run AI agent teams
without writing code.

**Current version: 1.3.1-alpha** — see [CHANGELOG.md](CHANGELOG.md) for what's new.

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

### Setup & configuration
- **Three-path setup wizard**: Quick start (1 agent, ~1 min), Team setup (multi-agent with Leader), or Advanced
- **"+ Build custom agent"** option in template picker for fully custom roles
- **Two-level model picker** used everywhere: configure providers once, pick from filtered models
- **Edit any preset** in-place: built-in or custom (overrides persist). Reset built-ins to defaults at any time.
- Full forward/back/skip navigation in every wizard step; quit suppressed inside sub-flows so you can't accidentally exit
- Relaunchable via `/setup` from inside the TUI

### Backup & restore
- **Full-fidelity `.starling` backup** including agents, custom presets, builtin overrides, cron jobs, custom skill files, API keys, and Telegram tokens — restore is zero-setup
- Export from CLI (`starling export`) or Config tab buttons; `--strip` for share-safe versions
- Numbered import picker shows every backup with date, size, and `[with secrets]` markers
- Backups stored outside the install directory (`~/starling-backups/`) — survive reinstall
- Backup directory is configurable via a one-question prompt and persisted in `~/.config/starling/preferences.json`

### Models & providers
- **21 built-in model presets** (OpenAI, Anthropic, xAI, DeepSeek, Mistral, Groq, Together, NVIDIA, Alibaba, Google, OpenRouter, Local)
- Custom model support for any OpenAI-compatible or Anthropic-compatible provider
- Custom presets persist in `~/.config/starling/model_presets.json` (survives reinstall)
- **Pre-downloaded embedding models** (CPU-only, persistent cache in `~/.cache/starling/embeddings/`)
- Models tab color-codes presets green (key configured) or red (key missing)

### Agents & crews
- **6 pre-built agent templates** (Researcher, Content Writer, Data Analyst, Project Planner, Software Engineer, Customer Support)
- **Agent tier system**: Specialist / Coordinator / Leader with cumulative permissions
- Crew Memory: unified vector memory across all agents (nomic-embed-text + LanceDB)
- Semantic task routing: automatic agent selection by meaning, not keywords
- Duplicate work detection and progress tracking

### Runtime
- Interactive TUI with agent panels, file browser, queue, history, status, and docs tabs
- Background daemon with concurrent-safe task queue (atomic writes, locking)
- Telegram bot integration (commands gated by chat-ID match; free-text auto-crew is opt-in)
- Cron scheduling with approval workflow (agent-proposed jobs require user confirmation)
- Custom skills (CSV, PowerPoint, charts, or build your own)
- Report generation with previous-report context injection
- In-app documentation with section navigation

### Security
- API keys and Telegram tokens written `chmod 600`
- Backup imports validate against env-var injection, path traversal, and unsafe filenames
- Cron jobs from imported backups are quarantined to `pending_approval` (never auto-fire)
- Manager keyword blocked at agent creation (CrewAI footgun protection)

## Documentation

Full documentation is available in DOCS.txt or the Docs tab inside the TUI.

## License

MIT
