"""CrewTUI — Config-driven CrewAI Terminal UI"""

import math
from textual.app import App, ComposeResult
from textual.widgets import (
    Header,
    Footer,
    RichLog,
    Static,
    Input,
    TabbedContent,
    TabPane,
    ListView,
    ListItem,
    Label,
    Button,
    Select,
    Rule,
)
from textual.containers import Vertical, Horizontal, VerticalScroll
from textual.binding import Binding
from textual.message import Message
import os
import json
import threading
from datetime import datetime

import logging

from model_wizard import load_presets as _load_model_presets
MODEL_PRESETS = _load_model_presets()

# Log to file so crew errors are visible
_log_path = None
try:
    from config_loader import get_data_file
    _log_path = get_data_file("crewtui.log")
except Exception:
    _log_path = os.path.join(os.path.dirname(__file__), "crewtui.log")
logging.basicConfig(
    filename=_log_path,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("crewtui")


def _output_dir():
    try:
        from config_loader import get_output_dir
        return get_output_dir()
    except Exception:
        return os.path.join(os.path.dirname(__file__), "output")


def _history_file():
    try:
        from config_loader import get_data_file
        return get_data_file("run_history.json")
    except Exception:
        return os.path.join(os.path.dirname(__file__), "run_history.json")


def get_agent_display(agent_cfg: dict) -> dict:
    """Get display info for an agent from config."""
    preset = MODEL_PRESETS.get(agent_cfg.get("preset", ""), {})
    return {
        "name": agent_cfg.get("name", agent_cfg["id"]),
        "model": preset.get("label", "?"),
        "provider": preset.get("provider", "?"),
        "color": agent_cfg.get("color", "white"),
    }


HELP_TEXT = """[bold]Commands:[/]
  [cyan]/crew[/] <mission>       — Run a custom crew mission
  [cyan]/config[/]               — Show current agent configuration
  [cyan]/config[/] <agent> <preset> — Change an agent's model
  [cyan]/presets[/]              — List available model presets
  [cyan]/open[/]                 — List output files
  [cyan]/open[/] <file>          — View a report file
  [cyan]/memory[/]               — Show current agent's memory
  [cyan]/memory[/] stats         — Memory stats for all agents
  [cyan]/memory[/] <keyword>     — Search agent's memory
  [cyan]/memory[/] promote       — Show promotion candidates
  [cyan]/memory[/] decay         — Mark old entries as stale
  [cyan]/memory[/] wipe          — Delete current agent's memory
  [cyan]/remember[/] <text>      — Save to long-term memory
  [cyan]/forget[/] <keyword>     — Remove matching memories
  [cyan]/queue[/] add <task>     — Add task (single-agent chat)
  [cyan]/queue[/] add --crew     — Add task (full crew run)
  [cyan]/queue[/] add --every 6h — Add recurring task (30m, 6h, 1d)
  [cyan]/queue[/] add --after ID — Add task that waits for another
  [cyan]/queue[/] add @agent     — Assign to specific agent
  [cyan]/queue[/] list           — Show all queued tasks
  [cyan]/queue[/] cancel <id>    — Cancel a pending task
  [cyan]/queue[/] clear          — Remove done/failed tasks
  [cyan]/heartbeat[/] on         — Start heartbeat (saves auto-start)
  [cyan]/heartbeat[/] off        — Stop heartbeat
  [cyan]/heartbeat[/] status     — Show heartbeat status
  [cyan]/heartbeat[/] interval N — Set check interval (seconds)
  [cyan]/skills[/]               — Show Skills tab
  [cyan]/skills[/] install <tool> — Enable a tool
  [cyan]/skills[/] assign <tool> <agent> — Give tool to agent
  [cyan]/skills[/] unassign <tool> <agent> — Remove tool from agent
  [cyan]/skills[/] new           — Scaffold custom skill template
  [cyan]/skills[/] refresh       — Rescan skills directory
  [cyan]/cron[/] add              — Add a scheduled job (wizard)
  [cyan]/cron[/] list             — Show all cron jobs
  [cyan]/cron[/] remove|delete <id> — Delete a cron job
  [cyan]/cron[/] on/off <id>     — Enable/disable a cron job
  [cyan]/cron[/] approve <id>    — Approve agent-proposed job
  [cyan]/cron[/] reject <id>     — Reject agent-proposed job
  [cyan]/view[/] <number|name>   — View an output file
  [cyan]/delete[/] <file>        — Delete an output file
  [cyan]/purge[/]                — Delete all output files
  [cyan]/docs[/] [section]       — Show documentation
  [cyan]/copy[/]                 — Copy current panel to clipboard (or Ctrl+Y)
  [cyan]/help[/]                 — Show this help
  [cyan]/clear[/]                — Clear current agent's log
  [cyan]/history[/]              — Show past crew runs
  [cyan]/status[/]               — Show all agent statuses

[bold]Keys:[/]
  [cyan]F1[/]  All agents    [cyan]F2+[/]  Focus agent
  [cyan]F7[/]  Files          [cyan]F8[/]     Run default crew
  [cyan]F9[/]  Config         [cyan]q[/]      Quit

[bold]Chat:[/] Just type a message to talk to the selected agent.
[bold]Memory:[/] Persists across model changes. Tied to agent role, not model.
[bold]Heartbeat:[/] Always-on task engine. Add tasks, start the heartbeat, it runs them."""


def load_history():
    path = _history_file()
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []


def save_history(entry):
    path = _history_file()
    history = load_history()
    history.append(entry)
    history = history[-50:]
    with open(path, "w") as f:
        json.dump(history, f, indent=2)


# === Messages ===

class AgentOutput(Message):
    def __init__(self, agent_id: str, text: str):
        super().__init__()
        self.agent_id = agent_id
        self.text = text


class AgentStatus(Message):
    def __init__(self, agent_id: str, status: str):
        super().__init__()
        self.agent_id = agent_id
        self.status = status


class CrewFinished(Message):
    def __init__(self, success: bool, error: str = "", mission: str = "",
                 duration: int = 0, output_files: list = None):
        super().__init__()
        self.success = success
        self.error = error
        self.mission = mission
        self.duration = duration
        self.output_files = output_files or []


class ChatResponse(Message):
    def __init__(self, agent_id: str, text: str):
        super().__init__()
        self.agent_id = agent_id
        self.text = text


class HeartbeatLog(Message):
    def __init__(self, text: str):
        super().__init__()
        self.text = text


class HeartbeatTaskDone(Message):
    def __init__(self, task: dict, result: str):
        super().__init__()
        self.task = task
        self.result = result


# === Agent Panel Widget ===

class AgentPanel(Vertical):
    def __init__(self, agent_id: str, display_info: dict, **kwargs):
        super().__init__(**kwargs)
        self.agent_id = agent_id
        self.agent_info = display_info
        self._buffer: list[str] = []
        self._raw_status: str = "idle"

    def compose(self) -> ComposeResult:
        color = self.agent_info["color"]
        name = self.agent_info["name"]
        model = self.agent_info["model"]
        provider = self.agent_info["provider"]
        yield Static(
            f"[bold {color}]{name}[/] [{color}]({model} via {provider})[/] — [dim]idle[/]",
            id=f"status-{self.agent_id}",
            classes="agent-status",
        )
        yield RichLog(
            id=f"log-{self.agent_id}",
            highlight=True,
            markup=True,
            wrap=True,
            classes="agent-log",
        )

    def set_status(self, status: str):
        # Extract raw keyword for status tracking
        s = status.lower()
        if "done" in s:
            self._raw_status = "done"
        elif "working" in s or "thinking" in s:
            self._raw_status = "working"
        elif "waiting" in s:
            self._raw_status = "waiting"
        elif "error" in s:
            self._raw_status = "error"
        else:
            self._raw_status = "idle"
        color = self.agent_info["color"]
        name = self.agent_info["name"]
        model = self.agent_info["model"]
        provider = self.agent_info["provider"]
        status_widget = self.query_one(f"#status-{self.agent_id}", Static)
        status_widget.update(
            f"[bold {color}]{name}[/] [{color}]({model} via {provider})[/] — {status}"
        )

    def update_info(self, display_info: dict):
        self.agent_info = display_info
        self.set_status("[dim]idle[/]")

    def write(self, text: str):
        log = self.query_one(f"#log-{self.agent_id}", RichLog)
        log.write(text)
        self._buffer.append(text)

    def clear(self):
        log = self.query_one(f"#log-{self.agent_id}", RichLog)
        log.clear()
        self._buffer.clear()

    def get_text(self) -> str:
        import re
        lines = []
        for line in self._buffer:
            plain = re.sub(r"\[/?[^\]]*\]", "", str(line))
            lines.append(plain)
        return "\n".join(lines)


# === Main App ===

class CrewTUIApp(App):
    CSS = """
    Screen { layout: vertical; }
    #main-area { height: 1fr; }
    #agent-grid {
        layout: grid;
        grid-gutter: 1;
        padding: 1;
        height: 1fr;
    }
    #agent-grid.focused {
        layout: vertical;
    }
    .agent-status {
        height: 1;
        padding: 0 1;
        background: $surface;
    }
    .agent-log {
        height: 1fr;
        border: solid $primary-background;
        padding: 0 1;
    }
    AgentPanel { height: 1fr; }
    #prompt-bar {
        dock: bottom;
        height: 5;
        padding: 0 1;
        background: $surface;
    }
    #prompt-bar.focused-mode {
        height: 7;
    }
    #prompt-input { width: 1fr; }
    #agent-select {
        width: 22;
        height: 1;
        color: $text;
        background: $primary-background;
        padding: 0 1;
    }
    #config-log {
        height: 1fr;
        padding: 1;
    }
    #file-area {
        height: 1fr;
    }
    #file-filter-bar {
        height: 3;
        padding: 0 1;
        background: $surface;
    }
    .file-filter {
        width: auto;
        padding: 0 2;
        margin: 0 1;
        height: 3;
        content-align: center middle;
    }
    .file-filter-active {
        background: $primary;
        color: $text;
    }
    #file-content-area {
        height: 1fr;
    }
    #file-list {
        width: 35;
        height: 1fr;
        border-right: solid $primary-background;
    }
    #file-list > ListItem {
        padding: 0 1;
        height: 1;
    }
    #file-viewer {
        width: 1fr;
        height: 1fr;
        padding: 0 1;
    }
    #no-config {
        width: 100%;
        height: 100%;
        content-align: center middle;
        text-align: center;
        padding: 4;
    }
    #status-log {
        height: 1fr;
        padding: 1;
    }
    #models-area {
        height: 1fr;
    }
    #models-list-area {
        width: 30;
        height: 1fr;
        border-right: solid $primary-background;
        padding: 0 1;
    }
    #models-header {
        height: 1;
        padding: 0 1;
    }
    #models-list {
        height: 1fr;
    }
    #models-list > ListItem {
        padding: 0 1;
        height: 1;
    }
    #agents-list {
        height: 1fr;
    }
    #agents-list > ListItem {
        padding: 0 1;
        height: 1;
    }
    #model-new-bar {
        height: 3;
        margin: 0 0 1 0;
    }
    #agent-new-bar {
        height: 3;
        margin: 0 0 1 0;
    }
    #agent-btn-bar {
        height: 3;
        margin: 1 0;
    }
    #agent-btn-bar Button {
        margin: 0 1;
    }
    #models-form-area {
        width: 1fr;
        height: 1fr;
        padding: 1 2;
    }
    #models-form-area Input {
        margin: 0 0 1 0;
    }
    #models-form-area Select {
        margin: 0 0 1 0;
    }
    #models-btn-bar {
        height: 3;
        margin: 1 0;
    }
    #models-btn-bar Button {
        margin: 0 1;
    }
    #model-result-log {
        height: auto;
        max-height: 10;
        padding: 0 1;
    }
    #docs-area {
        height: 1fr;
    }
    #docs-nav {
        height: 3;
        padding: 0 1;
        background: $surface;
    }
    .docs-section {
        width: auto;
        padding: 0 2;
        margin: 0 1;
        height: 3;
        content-align: center middle;
    }
    .docs-section-active {
        background: $primary;
        color: $text;
    }
    #docs-log {
        height: 1fr;
        padding: 1;
    }
    """

    BINDINGS = [
        Binding("f1", "show_all", "All Agents"),
        Binding("f7", "show_files", "Files"),
        Binding("f8", "run_default_crew", "Run Crew"),
        Binding("f9", "show_config", "Config"),
        Binding("ctrl+y", "copy_panel", "Copy"),
        Binding("ctrl+v", "paste_clipboard", "Paste"),
        Binding("ctrl+q", "quit", "Quit"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self):
        super().__init__()
        self._project_config = None
        self._agents_cfg = []
        self._agent_ids = []
        self._role_to_id = {}
        self.current_agent = ""
        self.crew_running = False
        self._crew_start_time = None
        self._crew_tasks = []  # list of {"desc": ..., "agent": ..., "done": bool}
        self._components = None
        self._heartbeat = None
        self._telegram_listener = None
        self._cron_wizard = None  # {"step": N, "data": {...}}
        self._file_filter = "chronological"  # chronological, heartbeat, report, decision

        # Load config
        from config_loader import config_exists, load_project_config
        if config_exists():
            self._project_config = load_project_config()
            self._agents_cfg = self._project_config.get("agents", [])
            self._agent_ids = [a["id"] for a in self._agents_cfg]
            self._role_to_id = {a["role"]: a["id"] for a in self._agents_cfg}
            if self._agent_ids:
                self.current_agent = self._agent_ids[0]

        # Dynamic title
        if self._project_config:
            name = self._project_config.get("project", {}).get("name", "CrewTUI")
            self.TITLE = f"{name} — Agent Command Center"
        else:
            self.TITLE = "CrewTUI — Setup Required"

    def compose(self) -> ComposeResult:
        yield Header()

        if not self._project_config or not self._agents_cfg:
            yield Static(
                "[bold red]No project configured.[/]\n\n"
                "Run [bold cyan]crewtui setup[/] to create your project.\n\n"
                "This will guide you through:\n"
                "  - Naming your project\n"
                "  - Creating agents with roles and backstories\n"
                "  - Configuring model presets and API keys\n"
                "  - Setting up tools and skills\n\n"
                "Press [bold]q[/] to quit.",
                id="no-config",
            )
            yield Footer()
            return

        out_dir = _output_dir()
        with TabbedContent(id="main-tabs"):
            with TabPane("Agents", id="tab-agents"):
                with Vertical(id="main-area"):
                    with Horizontal(id="agent-grid"):
                        for agent_cfg in self._agents_cfg:
                            info = get_agent_display(agent_cfg)
                            yield AgentPanel(agent_cfg["id"], info, id=f"panel-{agent_cfg['id']}")
            with TabPane("Models", id="tab-models"):
                with Horizontal(id="models-area"):
                    # Left: existing presets + agents list
                    with Vertical(id="models-list-area"):
                        yield Static("[bold]Model Presets[/]", id="models-header")
                        yield ListView(id="models-list")
                        yield Rule()
                        yield Static("[bold]Agents[/]")
                        yield ListView(id="agents-list")
                    # Right: forms
                    with VerticalScroll(id="models-form-area"):
                        # Model preset form
                        yield Static("[bold cyan]Model Preset[/]", id="models-form-header")
                        yield Horizontal(
                            Button("New Model", id="model-new-btn", variant="primary"),
                            Static("  [dim]or select a preset to edit:[/]"),
                            id="model-new-bar",
                        )
                        yield Select(
                            [(f"{k} — {v['label']}", k) for k, v in _load_model_presets().items()],
                            prompt="Load existing preset...",
                            id="model-preset-select",
                        )
                        yield Rule()
                        yield Static("Preset Name")
                        yield Input(placeholder="e.g., my-grok, deepseek", id="model-name-input")
                        yield Static("Display Label")
                        yield Input(placeholder="e.g., DeepSeek V3", id="model-label-input")
                        yield Static("Model ID")
                        yield Input(placeholder="e.g., openai/deepseek-chat", id="model-id-input")
                        yield Static("Base URL")
                        yield Input(placeholder="e.g., https://api.deepseek.com/v1", id="model-url-input")
                        yield Static("API Key Env Var [dim](blank for local models)[/]")
                        yield Input(placeholder="e.g., DEEPSEEK_API_KEY", id="model-key-input")
                        yield Static("Provider Name")
                        yield Input(placeholder="e.g., DeepSeek, Local, xAI", id="model-provider-input")
                        yield Static("API Compatibility")
                        yield Select(
                            [("OpenAI Compatible (most providers)", "openai"),
                             ("Anthropic (Claude direct API)", "anthropic")],
                            value="openai",
                            id="model-format-select",
                        )
                        yield Static("Assign to Agent [dim](optional)[/]")
                        yield Select(
                            [("None — save preset only", "")] +
                            [(f"{a['name']} ({a['id']})", a['id']) for a in self._agents_cfg],
                            prompt="Assign to agent...",
                            id="model-agent-select",
                        )
                        yield Horizontal(
                            Button("Test", id="model-test-btn", variant="default"),
                            Button("Save", id="model-save-btn", variant="success"),
                            Button("Delete", id="model-delete-btn", variant="error"),
                            id="models-btn-bar",
                        )
                        yield Rule()
                        # Agent form
                        yield Horizontal(
                            Static("[bold cyan]Agent[/]  "),
                            Button("New Agent", id="agent-new-btn", variant="primary"),
                            id="agent-new-bar",
                        )
                        yield Static("Agent ID")
                        yield Input(placeholder="e.g., researcher, writer (no spaces)", id="agent-id-input")
                        yield Static("Display Name")
                        yield Input(placeholder="e.g., Research Analyst", id="agent-name-input")
                        yield Static("Role")
                        yield Input(placeholder="e.g., Senior Research Analyst", id="agent-role-input")
                        yield Static("Goal")
                        yield Input(placeholder="e.g., Find and analyze information", id="agent-goal-input")
                        yield Static("Backstory")
                        yield Input(placeholder="e.g., Detail-oriented with 10 years experience", id="agent-backstory-input")
                        yield Static("Model Preset")
                        yield Select(
                            [(f"{k} — {v['label']}", k) for k, v in _load_model_presets().items()],
                            prompt="Select model preset...",
                            id="agent-preset-select",
                        )
                        yield Static("Tools [dim](comma-separated)[/]")
                        yield Input(placeholder="e.g., ddg_search, scrape_website, crewai:FileReadTool", id="agent-tools-input")
                        yield Static("[dim]Built-in: ddg_search, tavily_search, scrape_website, cron_tool[/]")
                        yield Static("[dim]CrewAI: crewai:FileReadTool, crewai:FileWriterTool, crewai:DirectoryReadTool[/]")
                        yield Static("Color")
                        yield Select(
                            [("Cyan", "cyan"), ("Green", "green"), ("Yellow", "yellow"),
                             ("Magenta", "magenta"), ("Blue", "blue"), ("Red", "red"), ("White", "white")],
                            prompt="Select color...",
                            id="agent-color-select",
                        )
                        yield Static("Routing Keywords [dim](comma-separated)[/]")
                        yield Input(placeholder="e.g., research, analyze, data, search", id="agent-keywords-input")
                        yield Horizontal(
                            Button("Save Agent", id="agent-save-btn", variant="success"),
                            Button("Delete Agent", id="agent-delete-btn", variant="error"),
                            id="agent-btn-bar",
                        )
                        yield RichLog(id="model-result-log", highlight=True, markup=True, wrap=True)
            with TabPane("Files", id="tab-files"):
                with Vertical(id="file-area"):
                    yield Horizontal(
                        Static("[bold cyan] Chronological [/]", id="file-filter-chronological", classes="file-filter file-filter-active"),
                        Static("[bold] Recurring [/]", id="file-filter-heartbeat", classes="file-filter"),
                        Static("[bold] Reports [/]", id="file-filter-report", classes="file-filter"),
                        Static("[bold] Decisions [/]", id="file-filter-decision", classes="file-filter"),
                        id="file-filter-bar",
                    )
                    with Horizontal(id="file-content-area"):
                        yield ListView(id="file-list")
                        yield RichLog(id="file-viewer", highlight=True, markup=True, wrap=True)
            with TabPane("History", id="tab-history"):
                yield RichLog(id="history-log", highlight=True, markup=True, wrap=True)
            with TabPane("Config", id="tab-config"):
                yield RichLog(id="config-log", highlight=True, markup=True, wrap=True)
            with TabPane("Queue", id="tab-queue"):
                yield RichLog(id="queue-log", highlight=True, markup=True, wrap=True)
            with TabPane("Skills", id="tab-skills"):
                yield RichLog(id="skills-log", highlight=True, markup=True, wrap=True)
            with TabPane("Cron", id="tab-cron"):
                yield RichLog(id="cron-log", highlight=True, markup=True, wrap=True)
            with TabPane("Status", id="tab-status"):
                yield RichLog(id="status-log", highlight=True, markup=True, wrap=True)
            with TabPane("Docs", id="tab-docs"):
                with Vertical(id="docs-area"):
                    yield Horizontal(
                        Static("[bold cyan] Overview [/]", id="docs-section-overview", classes="docs-section docs-section-active"),
                        Static("[bold] Install [/]", id="docs-section-install", classes="docs-section"),
                        Static("[bold] CLI [/]", id="docs-section-cli", classes="docs-section"),
                        Static("[bold] TUI [/]", id="docs-section-tui", classes="docs-section"),
                        Static("[bold] Telegram [/]", id="docs-section-telegram", classes="docs-section"),
                        Static("[bold] Agents [/]", id="docs-section-agents", classes="docs-section"),
                        Static("[bold] Tools [/]", id="docs-section-tools", classes="docs-section"),
                        Static("[bold] Cron [/]", id="docs-section-cron", classes="docs-section"),
                        Static("[bold] Troubleshoot [/]", id="docs-section-troubleshoot", classes="docs-section"),
                        id="docs-nav",
                    )
                    yield RichLog(id="docs-log", highlight=True, markup=True, wrap=True)

        first_agent = self._agents_cfg[0] if self._agents_cfg else None
        first_color = first_agent.get("color", "cyan") if first_agent else "cyan"
        first_name = first_agent.get("name", "?") if first_agent else "?"

        with Horizontal(id="prompt-bar"):
            yield Static(f"[bold {first_color}]{first_name}[/] > ", id="agent-select")
            yield Input(
                placeholder="/crew <mission> | /config | /help | or type to chat",
                id="prompt-input",
            )
        yield Footer()

    def on_mount(self) -> None:
        if not self._agents_cfg:
            return

        # Dynamic F-key bindings for agents (F2, F3, ...)
        for i, agent_cfg in enumerate(self._agents_cfg):
            fkey = f"f{i + 2}"
            if i + 2 > 6:  # F2-F6 max for agents
                break
            self.bind(fkey, f"focus_agent('{agent_cfg['id']}')", description=agent_cfg["name"])

        # Dynamic grid size
        n = len(self._agents_cfg)
        cols = max(1, math.ceil(math.sqrt(n)))
        rows = max(1, math.ceil(n / cols))
        grid = self.query_one("#agent-grid")
        grid.styles.grid_size_columns = cols
        grid.styles.grid_size_rows = rows

        # Init agent panels
        for agent_cfg in self._agents_cfg:
            info = get_agent_display(agent_cfg)
            panel = self.query_one(f"#panel-{agent_cfg['id']}", AgentPanel)
            panel.write(f"[dim]{info['name']} ready — {info['model']} via {info['provider']}[/]")

        self._load_history_view()
        self._load_config_view()
        self._load_queue_view()
        self._load_skills_view()
        self._load_cron_view()
        self._load_models_list()
        self._load_docs_section("overview")

        # Start daemon if not already running — it handles heartbeat + telegram
        from daemon import is_running as daemon_is_running, start as daemon_start
        if not daemon_is_running():
            daemon_start()  # uses subprocess.Popen, returns immediately
            if daemon_is_running():
                self.notify("Daemon started")
                logger.info("Daemon auto-started by TUI")
            else:
                logger.error("Daemon failed to start")
                # Fallback: run services locally
                import heartbeat as hb
                hb_config = hb.load_heartbeat_config()
                if hb_config.get("auto_start"):
                    heartbeat = self._init_heartbeat()
                    heartbeat.interval = hb_config.get("interval", 60)
                    heartbeat.start()
                    self.notify("Heartbeat started (local)")
                self._start_telegram_listener()
        else:
            self.notify("Daemon running")
            logger.info("Daemon already running")

        # Status tab — refresh every 2 seconds for live updates
        self._update_status_tab()
        self.set_interval(3, self._update_status_tab, name="status-refresh")
        self.set_interval(5, self._load_queue_view, name="queue-refresh")
        self.set_interval(5, self._refresh_file_list, name="files-refresh")
        self.set_interval(5, self._load_cron_view, name="cron-refresh")

    def _start_telegram_listener(self):
        """Start Telegram listener if bot is configured."""
        try:
            import telegram_notify as tg
            config = tg.load_config()
            if config.get("enabled") and config.get("bot_token") and config.get("chat_id"):
                from telegram_listener import TelegramListener, create_command_handler
                handler = create_command_handler(app=self)
                self._telegram_listener = TelegramListener(
                    bot_token=config["bot_token"],
                    chat_id=config["chat_id"],
                    on_command=handler,
                )
                self._telegram_listener.start()
                logger.info("Telegram listener started")
                self.notify("Telegram listener active")
        except Exception as e:
            logger.error(f"Telegram listener failed: {e}")

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        """Show/hide agent selector based on active tab."""
        try:
            select = self.query_one("#agent-select", Static)
            if event.pane.id == "tab-agents":
                select.display = True
            else:
                select.display = False
        except Exception:
            pass

    def _log_status(self, text: str):
        """Append a line to the Status tab log."""
        try:
            log = self.query_one("#status-log", RichLog)
            log.write(text)
        except Exception:
            pass

    def _show_activity(self, text: str):
        """Log activity to Status tab."""
        self._log_status(text)

    def _hide_activity(self):
        """Log idle state to Status tab."""
        pass

    def _update_status_tab(self):
        """Full refresh of the Status tab with current state."""
        try:
            log = self.query_one("#status-log", RichLog)
            log.clear()
        except Exception:
            return

        if self.crew_running:
            elapsed = int((datetime.now() - self._crew_start_time).total_seconds()) if self._crew_start_time else 0
            mins = elapsed // 60
            secs = elapsed % 60
            time_str = f"{mins}m {secs}s" if mins else f"{secs}s"

            # Progress bar based on tasks completed
            total = len(self._crew_tasks) or 1
            done_count = sum(1 for t in self._crew_tasks if t["done"])
            progress = done_count / total
            bar_width = 40
            filled = int(bar_width * progress)
            bar = "[bold yellow]" + "#" * filled + "[/][dim]" + "-" * (bar_width - filled) + "[/]"

            log.write(f"[bold yellow]CREW RUNNING[/]  {time_str} elapsed  ({done_count}/{total} tasks)\n")
            log.write(f"  [{bar}]\n")
            log.write("")

            # Task list
            log.write("[bold]Tasks:[/]")
            for i, t in enumerate(self._crew_tasks):
                if t["done"]:
                    indicator = "[green]OK[/]"
                    status = "[bold green]DONE[/]"
                elif i == done_count:
                    indicator = "[bold yellow]>>[/]"
                    status = "[bold yellow]RUNNING[/]"
                else:
                    indicator = "[dim]..[/]"
                    status = "[dim]PENDING[/]"
                log.write(f"  {indicator} {t['agent']:20s} {status}  [dim]{t['desc'][:50]}[/]")

        else:
            log.write("[dim]No crew running.[/]\n")
            log.write("")
            log.write("[bold]Agents:[/]")
            for a in self._agents_cfg:
                color = a.get("color", "white")
                preset = a.get("preset", "?")
                tools = len(a.get("tools", []))
                log.write(f"  [{color}]{a['name']:20s}[/] [dim]{preset} | {tools} tools[/]")

        # Always show service status
        log.write("")
        log.write("[bold]--- Services ---[/]")

        # Daemon
        from daemon import is_running as daemon_is_running
        if daemon_is_running():
            from daemon import _pid_file
            try:
                with open(_pid_file()) as f:
                    pid = f.read().strip()
                log.write(f"  [bold magenta]Daemon:[/]     [green]RUNNING[/] (PID {pid}) — heartbeat + telegram handled by daemon")
            except Exception:
                log.write(f"  [bold magenta]Daemon:[/]     [green]RUNNING[/]")
        else:
            log.write(f"  [bold magenta]Daemon:[/]     [dim]OFF[/]")

        # Heartbeat
        try:
            import heartbeat as hb
            if self._heartbeat and self._heartbeat.running:
                st = self._heartbeat.status()
                log.write(f"  [bold cyan]Heartbeat:[/]  [green]ACTIVE[/]  |  Interval: {st['interval']}s  |  Processed: {st['tasks_processed']}  |  Pending: {st['pending']}")
            elif daemon_is_running():
                log.write(f"  [bold cyan]Heartbeat:[/]  [green]via daemon[/]")
            else:
                log.write(f"  [bold cyan]Heartbeat:[/]  [dim]OFF[/]")
        except Exception:
            log.write(f"  [bold cyan]Heartbeat:[/]  [dim]OFF[/]")

        # Telegram
        tg_status = "[dim]OFF[/]"
        tg_listener = "[dim]OFF[/]"
        try:
            import telegram_notify as tg
            config = tg.load_config()
            if config.get("enabled"):
                tg_status = "[green]ON[/]"
            if self._telegram_listener and self._telegram_listener.running:
                tg_listener = "[green]LISTENING[/]"
            elif daemon_is_running():
                tg_listener = "[green]via daemon[/]"
        except Exception:
            pass
        log.write(f"  [bold cyan]Telegram:[/]   Notify: {tg_status}  |  Listener: {tg_listener}")

        # Show active heartbeat tasks
        try:
            import heartbeat as hb
            running_tasks = hb.list_tasks("running")
            pending_tasks = hb.list_tasks("pending")
            if running_tasks or pending_tasks:
                log.write("")
                log.write("[bold]--- Heartbeat Queue ---[/]")
                for t in running_tasks:
                    agent = t.get("agent") or "auto"
                    mode = "[magenta]CREW[/] " if t.get("crew") else ""
                    log.write(f"  [bold yellow]>>[/] {mode}[bold yellow]RUNNING[/]  {agent:15s}  {t['description'][:50]}")
                for t in pending_tasks[:5]:
                    agent = t.get("agent") or "auto"
                    mode = "[magenta]CREW[/] " if t.get("crew") else ""
                    log.write(f"  [dim]..[/] {mode}[dim]PENDING[/]  {agent:15s}  {t['description'][:50]}")
                if len(pending_tasks) > 5:
                    log.write(f"  [dim]   ... and {len(pending_tasks) - 5} more pending[/]")
        except Exception:
            pass

    def _ensure_components(self):
        if self._components is None:
            from crew import build_agents_from_config
            self._components = build_agents_from_config(self._project_config, MODEL_PRESETS)
        return self._components

    def _reload_components(self):
        self._components = None

    # === View loaders ===

    def _load_history_view(self):
        log = self.query_one("#history-log", RichLog)
        log.clear()
        history = load_history()
        if not history:
            log.write("[dim]No crew runs yet.[/]")
            return
        for entry in reversed(history):
            status = "[green]OK[/]" if entry.get("success") else "[red]FAIL[/]"
            log.write(
                f"{status} [{entry.get('timestamp', '?')}] "
                f"[bold]{entry.get('mission', 'default crew')[:60]}[/] "
                f"({entry.get('duration', '?')}s)"
            )

    def _load_config_view(self):
        log = self.query_one("#config-log", RichLog)
        log.clear()
        log.write("[bold]Agent Configuration[/]\n")
        log.write("Use [cyan]/config <agent> <preset>[/] to change models.\n")
        log.write("Use [cyan]/presets[/] to see available models.\n")
        log.write("")
        for agent_cfg in self._agents_cfg:
            info = get_agent_display(agent_cfg)
            color = info["color"]
            log.write(
                f"  [{color}]{info['name']:20s}[/] "
                f"preset=[bold]{agent_cfg.get('preset', '?')}[/] "
                f"-> {info['model']} via {info['provider']}"
            )
        log.write("")
        log.write("[dim]Changes take effect on next crew run or chat.[/]")

    def _load_queue_view(self):
        import heartbeat as hb
        log = self.query_one("#queue-log", RichLog)
        log.clear()
        if self._heartbeat:
            st = self._heartbeat.status()
            state = "[bold green]RUNNING[/]" if st["running"] else "[bold red]STOPPED[/]"
            log.write(f"[bold]Heartbeat:[/] {state}  |  Interval: {st['interval']}s  |  Processed: {st['tasks_processed']}")
            if st["started_at"]:
                log.write(f"  Started: {st['started_at'][:19]}")
        else:
            log.write("[bold]Heartbeat:[/] [dim]not initialized — use /heartbeat on[/]")
        log.write("")
        tasks = hb.list_tasks()
        if not tasks:
            log.write("[dim]No tasks in queue. Use /queue add <description> to add one.[/]")
            return
        log.write(f"[bold]Task Queue[/] ({len(tasks)} tasks)\n")
        status_colors = {
            "pending": "yellow", "running": "cyan", "done": "green",
            "failed": "red", "cancelled": "dim",
        }
        for t in tasks:
            color = status_colors.get(t["status"], "white")
            agent = t.get("agent") or "auto"
            pri = t["priority"]
            tid = t["id"][-6:]
            flags = []
            if t.get("crew"):
                flags.append("[magenta]CREW[/]")
            if t.get("every"):
                flags.append(f"[cyan]@{t['every']}[/]")
            if t.get("depends_on"):
                deps = ",".join(t["depends_on"])
                flags.append(f"[yellow]after #{deps}[/]")
            flag_str = (" " + " ".join(flags)) if flags else ""
            log.write(
                f"  [{color}]{t['status']:9s}[/] "
                f"[dim]#{tid}[/] "
                f"P{pri} -> [bold]{agent}[/]{flag_str}  "
                f"{t['description'][:60]}"
            )
            if t.get("next_run"):
                log.write(f"           [dim]Next run: {t['next_run'][:19]}[/]")
            if t.get("error"):
                log.write(f"           [red]Error: {t['error'][:80]}[/]")
            if t.get("result") and t["status"] == "done":
                log.write(f"           [green]Result: {t['result'][:80]}[/]")

    def _load_models_list(self):
        """Populate the models and agents lists."""
        # Models
        try:
            models_list = self.query_one("#models-list", ListView)
            models_list.clear()
            presets = _load_model_presets()
            for key, p in presets.items():
                label = p.get("label", key)
                api_key = p.get("api_key_env")
                key_status = ""
                if api_key:
                    if os.environ.get(api_key):
                        key_status = " [green]ok[/]"
                    else:
                        key_status = " [red]no key[/]"
                users = [a["name"] for a in self._agents_cfg if a.get("preset") == key]
                user_str = f" [dim]({', '.join(users)})[/]" if users else ""
                item = ListItem(
                    Label(f"[bold]{key}[/] {label}{key_status}{user_str}"),
                    name=key,
                )
                models_list.append(item)
        except Exception:
            pass

        # Agents
        try:
            agents_list = self.query_one("#agents-list", ListView)
            agents_list.clear()
            for a in self._agents_cfg:
                color = a.get("color", "white")
                preset = a.get("preset", "?")
                tool_count = len(a.get("tools", []))
                item = ListItem(
                    Label(f"[{color}]{a['name']}[/] [dim]{preset} | {tool_count} tools[/]"),
                    name=a["id"],
                )
                agents_list.append(item)
        except Exception:
            pass

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """When a preset or agent is highlighted, populate the form."""
        if not event.item or not event.item.name:
            return
        try:
            models_list = self.query_one("#models-list", ListView)
            if event.list_view is models_list:
                key = event.item.name
                presets = _load_model_presets()
                p = presets.get(key)
                if p:
                    self.query_one("#model-name-input", Input).value = key
                    self.query_one("#model-label-input", Input).value = p.get("label", "")
                    self.query_one("#model-id-input", Input).value = p.get("model", "")
                    self.query_one("#model-url-input", Input).value = p.get("base_url", "")
                    self.query_one("#model-key-input", Input).value = p.get("api_key_env", "") or ""
                    self.query_one("#model-provider-input", Input).value = p.get("provider", "")
                    self.query_one("#model-format-select", Select).value = p.get("api_format", "openai")
                    # Hint for local models
                    provider = p.get("provider", "").lower()
                    if provider in ("lm studio", "ollama"):
                        label = "LM Studio" if "lm studio" in provider else "Ollama"
                        self.notify(
                            f"Set Model ID to the model loaded in {label}, then Save",
                            severity="information",
                        )
                return
        except Exception:
            pass
        try:
            agents_list = self.query_one("#agents-list", ListView)
            if event.list_view is agents_list:
                aid = event.item.name
                agent = next((a for a in self._agents_cfg if a["id"] == aid), None)
                if agent:
                    self.query_one("#agent-id-input", Input).value = agent["id"]
                    self.query_one("#agent-name-input", Input).value = agent.get("name", "")
                    self.query_one("#agent-role-input", Input).value = agent.get("role", "")
                    self.query_one("#agent-goal-input", Input).value = agent.get("goal", "")
                    self.query_one("#agent-backstory-input", Input).value = agent.get("backstory", "")
                    self.query_one("#agent-tools-input", Input).value = ", ".join(agent.get("tools", []))
                    # Set preset select
                    preset = agent.get("preset", "")
                    try:
                        self.query_one("#agent-preset-select", Select).value = preset
                    except Exception:
                        pass
                    # Set color select
                    color = agent.get("color", "white")
                    try:
                        self.query_one("#agent-color-select", Select).value = color
                    except Exception:
                        pass
                    # Load keywords from routing config
                    self.query_one("#agent-keywords-input", Input).value = ""
                    routing = self._project_config.get("routing", {}).get("keywords", {})
                    if aid in routing:
                        self.query_one("#agent-keywords-input", Input).value = ", ".join(routing[aid])
                return
        except Exception:
            pass

    def on_select_changed(self, event: Select.Changed) -> None:
        """Handle preset select dropdown."""
        if event.select.id == "model-preset-select" and event.value != Select.NULL:
            key = str(event.value)
            presets = _load_model_presets()
            p = presets.get(key)
            if p:
                try:
                    self.query_one("#model-name-input", Input).value = key
                    self.query_one("#model-label-input", Input).value = p.get("label", "")
                    self.query_one("#model-id-input", Input).value = p.get("model", "")
                    self.query_one("#model-url-input", Input).value = p.get("base_url", "")
                    self.query_one("#model-key-input", Input).value = p.get("api_key_env", "") or ""
                    self.query_one("#model-provider-input", Input).value = p.get("provider", "")
                    self.query_one("#model-format-select", Select).value = p.get("api_format", "openai")
                    # Hint for local models
                    provider = p.get("provider", "").lower()
                    if provider in ("lm studio", "ollama"):
                        label = "LM Studio" if "lm studio" in provider else "Ollama"
                        self.notify(
                            f"Set Model ID to the model loaded in {label}, then Save",
                            severity="information",
                        )
                except Exception:
                    pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle form buttons."""
        if event.button.id == "model-test-btn":
            self._test_model_preset()
        elif event.button.id == "model-save-btn":
            self._save_model_preset()
        elif event.button.id == "model-delete-btn":
            self._delete_model_preset()
        elif event.button.id == "model-new-btn":
            self._clear_model_form()
        elif event.button.id == "agent-save-btn":
            self._save_agent()
        elif event.button.id == "agent-delete-btn":
            self._delete_agent()
        elif event.button.id == "agent-new-btn":
            self._clear_agent_form()

    def _get_model_form(self) -> dict:
        """Read current values from the model form."""
        try:
            fmt_select = self.query_one("#model-format-select", Select)
            api_format = str(fmt_select.value) if fmt_select.value != Select.NULL else "openai"
        except Exception:
            api_format = "openai"
        return {
            "name": self.query_one("#model-name-input", Input).value.strip(),
            "label": self.query_one("#model-label-input", Input).value.strip(),
            "model": self.query_one("#model-id-input", Input).value.strip(),
            "base_url": self.query_one("#model-url-input", Input).value.strip(),
            "api_key_env": self.query_one("#model-key-input", Input).value.strip() or None,
            "provider": self.query_one("#model-provider-input", Input).value.strip(),
            "api_format": api_format,
        }

    def _test_model_preset(self):
        """Test a model preset by sending a simple prompt."""
        log = self.query_one("#model-result-log", RichLog)
        log.clear()
        form = self._get_model_form()

        if not form["model"]:
            log.write("[red]Model ID is required.[/]")
            return
        if not form["base_url"] and form.get("api_format") != "anthropic":
            log.write("[red]Base URL is required (Anthropic format doesn't need one).[/]")
            return

        log.write("[yellow]Testing connection...[/]")

        def _do_test():
            try:
                from crew import build_llm_from_preset
                from model_wizard import _env_file
                from dotenv import load_dotenv
                load_dotenv(_env_file())

                preset = {
                    "label": form["label"],
                    "model": form["model"],
                    "base_url": form["base_url"],
                    "api_format": form.get("api_format", "openai"),
                    "api_key_env": form["api_key_env"],
                    "provider": form["provider"],
                    "extra": {},
                }
                # Check API key
                if form["api_key_env"]:
                    key_val = os.environ.get(form["api_key_env"])
                    if not key_val:
                        self.call_from_thread(
                            log.write,
                            f"[red]{form['api_key_env']} not set in .env[/]"
                        )
                        return

                presets = {form["name"]: preset}
                llm = build_llm_from_preset(form["name"], presets)
                response = llm.call(messages=[
                    {"role": "user", "content": "Say 'hello' in one word."}
                ])
                resp_text = str(response)[:200] if response else "No response"
                self.call_from_thread(
                    log.write,
                    f"[green]Connection successful![/]\nResponse: {resp_text}"
                )
            except Exception as e:
                self.call_from_thread(
                    log.write,
                    f"[red]Test failed:[/] {str(e)[:300]}"
                )

        threading.Thread(target=_do_test, daemon=True).start()

    def _clear_model_form(self):
        """Clear model form fields for a new model."""
        try:
            self.query_one("#model-name-input", Input).value = ""
            self.query_one("#model-label-input", Input).value = ""
            self.query_one("#model-id-input", Input).value = ""
            self.query_one("#model-url-input", Input).value = ""
            self.query_one("#model-key-input", Input).value = ""
            self.query_one("#model-provider-input", Input).value = ""
            self.query_one("#model-format-select", Select).value = "openai"
            log = self.query_one("#model-result-log", RichLog)
            log.clear()
            log.write("[dim]Fill in the fields below to add a new model preset.[/]")
        except Exception:
            pass

    def _clear_agent_form(self):
        """Clear agent form fields for a new agent."""
        try:
            self.query_one("#agent-id-input", Input).value = ""
            self.query_one("#agent-name-input", Input).value = ""
            self.query_one("#agent-role-input", Input).value = ""
            self.query_one("#agent-goal-input", Input).value = ""
            self.query_one("#agent-backstory-input", Input).value = ""
            self.query_one("#agent-tools-input", Input).value = ""
            self.query_one("#agent-keywords-input", Input).value = ""
            self.query_one("#agent-preset-select", Select).value = Select.NULL
            self.query_one("#agent-color-select", Select).value = Select.NULL
            log = self.query_one("#model-result-log", RichLog)
            log.clear()
            log.write("[dim]Fill in the fields below to add a new agent.[/]")
        except Exception:
            pass

    def _delete_model_preset(self):
        """Delete a custom model preset (built-ins cannot be deleted)."""
        log = self.query_one("#model-result-log", RichLog)
        log.clear()
        form = self._get_model_form()
        name = form["name"]

        if not name:
            log.write("[red]No preset name specified.[/]")
            return

        from model_wizard import BUILTIN_PRESETS
        if name in BUILTIN_PRESETS:
            log.write(f"[red]Cannot delete built-in preset '{name}'.[/]")
            return

        # Check if any agent uses this preset
        users = [a["name"] for a in self._agents_cfg if a.get("preset") == name]
        if users:
            log.write(f"[red]Cannot delete — used by: {', '.join(users)}[/]")
            log.write("[dim]Reassign those agents first.[/]")
            return

        from model_wizard import load_presets, save_custom_presets
        presets = load_presets()
        if name not in presets:
            log.write(f"[red]Preset '{name}' not found.[/]")
            return

        del presets[name]
        save_custom_presets(presets)
        self._clear_model_form()
        self._load_models_list()
        log.write(f"[yellow]Preset '{name}' deleted.[/]")

    def _save_model_preset(self):
        """Save the model preset and optionally assign to agent."""
        log = self.query_one("#model-result-log", RichLog)
        log.clear()
        form = self._get_model_form()

        if not form["name"]:
            log.write("[red]Preset name is required.[/]")
            return
        if not form["model"]:
            log.write("[red]Model ID is required.[/]")
            return
        if not form["base_url"] and form.get("api_format") != "anthropic":
            log.write("[red]Base URL is required (Anthropic format doesn't need one).[/]")
            return

        from model_wizard import load_presets, save_custom_presets, BUILTIN_PRESETS
        presets = load_presets()

        # Block overwriting built-in presets
        if form["name"] in BUILTIN_PRESETS:
            log.write(f"[red]Cannot overwrite built-in preset '{form['name']}'.[/]")
            log.write("[dim]Use a different name to create a custom version.[/]")
            return

        # Limit to 10 custom presets
        custom_count = sum(1 for k in presets if k not in BUILTIN_PRESETS)
        if form["name"] not in presets and custom_count >= 10:
            log.write("[red]Maximum 10 custom model presets reached. Delete one first.[/]")
            return
        presets[form["name"]] = {
            "label": form["label"] or form["name"],
            "model": form["model"],
            "base_url": form["base_url"],
            "api_format": form.get("api_format", "openai"),
            "api_key_env": form["api_key_env"],
            "provider": form["provider"] or "Custom",
            "extra": {},
        }
        save_custom_presets(presets)
        log.write(f"[green]Preset '{form['name']}' saved.[/]")

        # Assign to agent if selected
        try:
            agent_select = self.query_one("#model-agent-select", Select)
            agent_id = str(agent_select.value) if agent_select.value != Select.NULL else ""
        except Exception:
            agent_id = ""

        if agent_id:
            from config_loader import load_project_config, save_project_config
            config = load_project_config()
            for a in config.get("agents", []):
                if a["id"] == agent_id:
                    a["preset"] = form["name"]
                    break
            save_project_config(config)
            agent_name = next((a["name"] for a in self._agents_cfg if a["id"] == agent_id), agent_id)
            log.write(f"[green]Assigned to {agent_name}.[/]")
            log.write("[dim]Restart to apply the model change.[/]")

        self._load_models_list()

    def _save_agent(self):
        """Save an agent from the form fields."""
        log = self.query_one("#model-result-log", RichLog)
        log.clear()

        try:
            agent_id = self.query_one("#agent-id-input", Input).value.strip().lower().replace(" ", "_")
            name = self.query_one("#agent-name-input", Input).value.strip()
            role = self.query_one("#agent-role-input", Input).value.strip()
            goal = self.query_one("#agent-goal-input", Input).value.strip()
            backstory = self.query_one("#agent-backstory-input", Input).value.strip()
            tools_str = self.query_one("#agent-tools-input", Input).value.strip()
            keywords_str = self.query_one("#agent-keywords-input", Input).value.strip()
        except Exception as e:
            log.write(f"[red]Error reading form: {e}[/]")
            return

        if not agent_id or not name or not role:
            log.write("[red]Agent ID, Name, and Role are required.[/]")
            return

        # Check manager restriction
        if "manager" in agent_id or "manager" in role.lower():
            log.write("[red]Warning: 'manager' in ID or role will block tool access in CrewAI.[/]")
            log.write("[dim]Use 'coordinator', 'lead', or 'director' instead.[/]")
            return

        # Get preset
        try:
            preset_select = self.query_one("#agent-preset-select", Select)
            preset = str(preset_select.value) if preset_select.value != Select.NULL else ""
        except Exception:
            preset = ""

        if not preset:
            log.write("[red]Please select a model preset.[/]")
            return

        # Get color
        try:
            color_select = self.query_one("#agent-color-select", Select)
            color = str(color_select.value) if color_select.value != Select.NULL else "white"
        except Exception:
            color = "white"

        # Parse tools
        tools = [t.strip() for t in tools_str.split(",") if t.strip()] if tools_str else []

        # Parse keywords
        keywords = [k.strip().lower() for k in keywords_str.split(",") if k.strip()] if keywords_str else []

        # Build agent config
        agent_cfg = {
            "id": agent_id,
            "name": name,
            "role": role,
            "goal": goal,
            "backstory": backstory,
            "tools": tools,
            "preset": preset,
            "color": color,
            "allow_delegation": False,
        }

        # Save to project config
        from config_loader import load_project_config, save_project_config
        config = load_project_config()
        agents = config.get("agents", [])

        # Check max agents
        max_agents = config.get("max_agents", 10)
        existing = next((i for i, a in enumerate(agents) if a["id"] == agent_id), None)
        if existing is not None:
            agents[existing] = agent_cfg
            log.write(f"[green]Agent '{name}' updated.[/]")
        else:
            if len(agents) >= max_agents:
                log.write(f"[red]Max agents ({max_agents}) reached.[/]")
                return
            agents.append(agent_cfg)
            log.write(f"[green]Agent '{name}' created.[/]")

        config["agents"] = agents

        # Update routing keywords
        if "routing" not in config:
            config["routing"] = {"keywords": {}, "default_agent": agents[0]["id"]}
        if keywords:
            config["routing"]["keywords"][agent_id] = keywords
        else:
            config["routing"]["keywords"].pop(agent_id, None)

        save_project_config(config)
        self._project_config = config
        self._agents_cfg = config["agents"]
        self._agent_ids = [a["id"] for a in self._agents_cfg]
        self._load_models_list()
        log.write("[dim]Restart to activate the new agent.[/]")

    def _delete_agent(self):
        """Delete an agent."""
        log = self.query_one("#model-result-log", RichLog)
        log.clear()

        try:
            agent_id = self.query_one("#agent-id-input", Input).value.strip().lower()
        except Exception:
            log.write("[red]Enter the agent ID to delete.[/]")
            return

        if not agent_id:
            log.write("[red]Enter the agent ID to delete.[/]")
            return

        from config_loader import load_project_config, save_project_config
        config = load_project_config()
        agents = config.get("agents", [])
        before = len(agents)
        agents = [a for a in agents if a["id"] != agent_id]

        if len(agents) == before:
            log.write(f"[red]Agent '{agent_id}' not found.[/]")
            return

        if len(agents) == 0:
            log.write("[red]Cannot delete the last agent.[/]")
            return

        config["agents"] = agents

        # Remove from routing
        routing = config.get("routing", {}).get("keywords", {})
        routing.pop(agent_id, None)

        save_project_config(config)
        self._project_config = config
        self._agents_cfg = config["agents"]
        self._agent_ids = [a["id"] for a in self._agents_cfg]
        self._load_models_list()
        log.write(f"[yellow]Agent '{agent_id}' deleted.[/]")
        log.write("[dim]Restart to apply changes.[/]")

    def _load_docs_section(self, section: str = "overview"):
        """Load a section from DOCS.txt into the Docs tab."""
        try:
            log = self.query_one("#docs-log", RichLog)
        except Exception:
            return
        log.clear()

        docs_path = os.path.join(os.path.dirname(__file__), "DOCS.txt")
        if not os.path.exists(docs_path):
            log.write("[red]DOCS.txt not found.[/]")
            return

        with open(docs_path) as f:
            content = f.read()

        # Map section names to DOCS.txt headers
        section_map = {
            "overview": "OVERVIEW",
            "install": "INSTALLATION",
            "cli": "CLI COMMANDS",
            "tui": "TUI COMMANDS",
            "telegram": "TELEGRAM COMMANDS",
            "agents": "AGENTS AND MODELS",
            "tools": "TOOLS",
            "cron": "CRON SCHEDULING",
            "troubleshoot": "TROUBLESHOOTING",
        }

        header = section_map.get(section, "OVERVIEW")

        # Parse sections — they're separated by headers with underlines
        sections = {}
        current_name = None
        current_lines = []
        for line in content.split("\n"):
            if line.strip() and set(line.strip()) <= {"-", "="}:
                # This is an underline — previous line was a header
                if current_lines and current_lines[-1].strip():
                    if current_name:
                        # Remove the header line from previous section
                        sections[current_name] = "\n".join(current_lines[:-1])
                    current_name = current_lines[-1].strip()
                    current_lines = []
                continue
            current_lines.append(line)
        if current_name:
            sections[current_name] = "\n".join(current_lines)

        text = sections.get(header, f"Section '{section}' not found.")
        log.write(f"[bold cyan]{header}[/]\n")
        log.write(text.strip())

    def _load_cron_view(self):
        import cron_engine
        try:
            log = self.query_one("#cron-log", RichLog)
        except Exception:
            return
        log.clear()
        jobs = cron_engine.list_crons()
        if not jobs:
            log.write("[dim]No cron jobs. Use /cron add to create one.[/]\n")
            log.write("[dim]Agents can also create crons using the Cron Scheduler tool.[/]")
            return
        log.write(f"[bold]Scheduled Jobs[/] ({len(jobs)})\n")
        status_colors = {
            "active": "green", "disabled": "dim",
            "pending_approval": "yellow", "rejected": "red",
        }
        for j in jobs:
            color = status_colors.get(j["status"], "white")
            sid = j["id"][-6:]
            agent = j.get("agent") or ("CREW" if j.get("crew") else "auto")
            next_run = j.get("next_run", "")[:16] if j.get("next_run") else "—"
            last_run = j.get("last_run", "")[:16] if j.get("last_run") else "never"
            runs = j.get("run_count", 0)
            log.write(
                f"  [{color}]{j['status']:18s}[/] "
                f"[dim]#{sid}[/] "
                f"[bold]{j['name']}[/]"
            )
            log.write(
                f"           {j['schedule']:20s} -> [bold]{agent}[/]  "
                f"[dim]Runs: {runs} | Last: {last_run} | Next: {next_run}[/]"
            )
            log.write(f"           {j['description'][:70]}")
            if j.get("last_error"):
                log.write(f"           [red]Error: {j['last_error'][:60]}[/]")
            if j["status"] == "pending_approval":
                log.write(f"           [yellow]Awaiting approval: /cron approve {sid}[/]")
            log.write("")

    def _load_skills_view(self):
        from crew import list_available_tools
        from config_loader import load_project_config, save_project_config

        log = self.query_one("#skills-log", RichLog)
        log.clear()
        log.write("[bold]Skills & Tools[/]\n")

        config = load_project_config()
        agents = config.get("agents", [])
        available = list_available_tools()

        # Build assignment map
        assignments = {}  # tool_id -> [agent_names]
        for a in agents:
            for tid in a.get("tools", []):
                assignments.setdefault(tid, []).append(a["name"])

        # Installed (assigned to at least one agent)
        installed = {tid for a in agents for tid in a.get("tools", [])}

        if installed:
            log.write("[bold]Installed & Assigned:[/]")
            for tid in sorted(installed):
                info = available.get(tid, {})
                tier = info.get("tier", "?")
                assigned_to = ", ".join(assignments.get(tid, []))
                log.write(f"  [green]>[/] {tid} [dim]({tier})[/] -> {assigned_to}")
            log.write("")

        # Available but not installed
        log.write("[bold]Available Tools:[/]")
        for tier_name, tier_label in [("built-in", "Built-in"), ("crewai", "CrewAI Ecosystem"), ("custom", "Custom Skills")]:
            tier_tools = {tid: info for tid, info in available.items()
                         if info["tier"] == tier_name and tid not in installed}
            if tier_tools:
                log.write(f"\n  [bold]{tier_label}:[/]")
                for tid, info in sorted(tier_tools.items()):
                    log.write(f"    [dim]o[/] {tid} — {info['description'][:50]}")

        log.write(f"\n[dim]Commands: /skills install <tool>, /skills assign <tool> <agent>[/]")
        log.write(f"[dim]/skills unassign <tool> <agent>, /skills new, /skills refresh[/]")

    # === Heartbeat integration ===

    def _init_heartbeat(self):
        if self._heartbeat:
            return self._heartbeat
        import heartbeat as hb

        def on_tick():
            self.post_message(HeartbeatLog("[dim]@ heartbeat tick[/]"))

        def on_task_start(task):
            agent = task.get("agent", "crew" if task.get("crew") else "?")
            mode = "[magenta]CREW[/]" if task.get("crew") else ""
            self.post_message(HeartbeatLog(
                f"[bold yellow]> Starting task:[/] {mode} {task['description'][:60]} -> [bold]{agent}[/]"
            ))
            if not task.get("crew") and agent in self._agent_ids:
                self.post_message(AgentStatus(agent, "[bold yellow]working (heartbeat)...[/]"))

        def on_task_done(task, result):
            agent = task.get("agent", "?")
            recurring = f" [cyan](next in {task['every']})[/]" if task.get("every") else ""
            self.post_message(HeartbeatLog(
                f"[bold green]Done:[/] {task['description'][:60]}{recurring}"
            ))
            self.post_message(HeartbeatTaskDone(task, str(result)[:2000] if result else ""))
            if not task.get("crew") and agent in self._agent_ids:
                self.post_message(AgentStatus(agent, "[bold green]done[/]"))

        def on_task_fail(task, error):
            agent = task.get("agent", "?")
            self.post_message(HeartbeatLog(
                f"[bold red]Failed:[/] {task['description'][:60]} — {error}"
            ))
            if agent in self._agent_ids:
                self.post_message(AgentStatus(agent, "[bold red]error[/]"))

        def run_task(task):
            return self._run_heartbeat_task(task)

        def run_crew(task):
            return self._run_heartbeat_crew(task)

        self._heartbeat = hb.Heartbeat(
            on_tick=on_tick,
            on_task_start=on_task_start,
            on_task_done=on_task_done,
            on_task_fail=on_task_fail,
            run_task=run_task,
            run_crew=run_crew,
        )
        return self._heartbeat

    def _run_heartbeat_task(self, task):
        """Execute a single-agent task using CrewAI (with tools)."""
        from crewai import Crew, Task as CrewTask
        import agent_memory as mem
        components = self._ensure_components()
        agent_id = task.get("agent", self._agent_ids[0] if self._agent_ids else "")
        agent = components["agents"].get(agent_id)

        if not agent:
            raise ValueError(f"Unknown agent: {agent_id}")

        memory_context = mem.get_agent_context(agent_id)
        memory_section = f"\nAgent memory context:\n{memory_context}" if memory_context else ""

        self.post_message(AgentOutput(agent_id, f"[bold yellow]Heartbeat task:[/] {task['description']}"))

        crew_task = CrewTask(
            description=f"{task['description']}{memory_section}",
            expected_output="A thorough report with findings and recommendations.",
            agent=agent,
        )

        out_dir = _output_dir()
        os.makedirs(out_dir, exist_ok=True)
        original_cwd = os.getcwd()
        os.chdir(out_dir)
        try:
            crew = Crew(agents=[agent], tasks=[crew_task], verbose=True)
            result = crew.kickoff()
        finally:
            os.chdir(original_cwd)

        response_text = str(result) if result else "No response"
        display_text = response_text[:2000] + "\n[dim]... (truncated)[/]" if len(response_text) > 2000 else response_text
        self.post_message(AgentOutput(agent_id, f"[bold green]Result:[/]\n{display_text}"))

        mem.add_episodic(
            agent_id,
            f"Heartbeat task: {task['description'][:80]} -> {response_text[:150]}",
            source="heartbeat", entry_type="task", confidence="med", tags=["heartbeat"],
        )
        return response_text

    def _run_heartbeat_crew(self, task):
        from crew import build_crew_from_config
        mission = task["description"]

        self.post_message(HeartbeatLog(f"[bold magenta]Crew run:[/] {mission[:60]}"))
        for aid in self._agent_ids:
            self.post_message(AgentStatus(aid, "[bold yellow]crew (heartbeat)...[/]"))

        out_dir = _output_dir()
        os.makedirs(out_dir, exist_ok=True)

        crew, components = build_crew_from_config(self._project_config, MODEL_PRESETS, mission=mission)

        def step_callback(step_output):
            agent_role = getattr(step_output, 'agent', None)
            if agent_role:
                role_name = getattr(agent_role, 'role', str(agent_role))
                agent_id = self._role_to_id.get(role_name, self._agent_ids[0] if self._agent_ids else "")
            else:
                agent_id = self._agent_ids[0] if self._agent_ids else ""
            output_text = str(getattr(step_output, 'output', step_output))
            if len(output_text) > 500:
                output_text = output_text[:500] + "..."
            self.post_message(AgentOutput(agent_id, f"[dim]Step:[/] {output_text}"))

        def task_callback(task_output):
            agent_role = getattr(task_output, 'agent', None)
            if agent_role:
                role_name = getattr(agent_role, 'role', str(agent_role))
                agent_id = self._role_to_id.get(role_name, self._agent_ids[0] if self._agent_ids else "")
            else:
                agent_id = self._agent_ids[0] if self._agent_ids else ""
            output_text = str(getattr(task_output, 'raw', getattr(task_output, 'output', task_output)))
            if len(output_text) > 2000:
                output_text = output_text[:2000] + "\n[dim]... (truncated)[/]"
            self.post_message(AgentStatus(agent_id, "[bold green]done[/]"))
            self.post_message(AgentOutput(agent_id, f"[bold green]Task complete:[/]\n{output_text}"))

        crew.step_callback = step_callback
        crew.task_callback = task_callback

        original_cwd = os.getcwd()
        os.chdir(out_dir)
        try:
            result = crew.kickoff()
        finally:
            os.chdir(original_cwd)
        result_text = str(result) if result else "No output"

        save_history({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "mission": f"[heartbeat] {mission}",
            "success": True,
            "duration": 0,
        })
        return result_text

    def on_heartbeat_log(self, message: HeartbeatLog) -> None:
        try:
            log = self.query_one("#queue-log", RichLog)
            log.write(message.text)
        except Exception:
            pass

    def on_heartbeat_task_done(self, message: HeartbeatTaskDone) -> None:
        self._load_queue_view()
        threading.Thread(target=self._send_telegram_heartbeat, args=(message.task, message.result), daemon=True).start()

    def _send_telegram_heartbeat(self, task, result):
        try:
            import telegram_notify as tg
            from config_loader import get_project_name
            brand = get_project_name() or "CrewTUI"
            tg.send_message(
                f"*{brand} Heartbeat Task Complete*\n\n"
                f"*Task:* {task['description'][:100]}\n"
                f"*Agent:* {task.get('agent', '?')}\n"
                f"*Result:* {result[:200] if result else 'No output'}"
            )
        except Exception:
            pass

    # === File viewer ===

    def _cron_wizard_step(self, answer: str):
        """Process one step of the cron wizard."""
        import cron_engine
        panel = self.query_one(f"#panel-{self.current_agent}", AgentPanel)
        wiz = self._cron_wizard
        step = wiz["step"]
        data = wiz["data"]

        if step == 1:  # Name
            data["name"] = answer
            wiz["step"] = 2
            panel.write(f"  [green]→[/] {answer}\n")
            panel.write("[bold]Step 2/6:[/] How often should it run?")
            panel.write("[dim]  Formats: daily 08:00, weekly mon 9:00, every 6h, hourly, monthly 1 09:00[/]")

        elif step == 2:  # Schedule
            try:
                cron_engine.parse_schedule(answer)
            except ValueError as e:
                panel.write(f"  [red]{e}[/]")
                panel.write("[dim]  Try again: daily 08:00, weekly mon 9:00, every 6h, hourly[/]")
                return
            data["schedule"] = answer
            wiz["step"] = 3
            panel.write(f"  [green]→[/] {answer}\n")
            panel.write("[bold]Step 3/6:[/] Who should run it?\n")
            panel.write("  [bold cyan]0)[/] Full Crew [dim](all agents collaborate)[/]")
            for i, a in enumerate(self._agents_cfg, 1):
                color = a.get("color", "white")
                panel.write(f"  [bold cyan]{i})[/] [{color}]{a['name']}[/] [dim]({a['id']} — {a['role']})[/]")
            panel.write("\n[dim]  Enter a number:[/]")

        elif step == 3:  # Crew or agent
            choice = answer.strip()
            if choice == "0" or choice.lower() == "crew":
                data["crew"] = True
                data["agent"] = None
                panel.write(f"  [green]→[/] Full Crew\n")
            elif choice.isdigit() and 1 <= int(choice) <= len(self._agents_cfg):
                idx = int(choice) - 1
                a = self._agents_cfg[idx]
                data["crew"] = False
                data["agent"] = a["id"]
                panel.write(f"  [green]→[/] {a['name']} ({a['id']})\n")
            elif choice.lower() in self._agent_ids:
                data["crew"] = False
                data["agent"] = choice.lower()
                panel.write(f"  [green]→[/] @{choice.lower()}\n")
            else:
                panel.write(f"  [red]Invalid choice. Enter 0 for crew or 1-{len(self._agents_cfg)} for an agent.[/]")
                return
            wiz["step"] = 4
            panel.write("[bold]Step 4/6:[/] What's the mission/prompt?")
            panel.write("[dim]  e.g., research competitor pricing in our market[/]")

        elif step == 4:  # Mission
            data["mission"] = answer
            wiz["step"] = 5
            panel.write(f"  [green]→[/] {answer}\n")
            panel.write("[bold]Step 5/6:[/] Generate a report file when done?")
            panel.write("[dim]  yes = save output to Files tab  |  no = Telegram notification only[/]")

        elif step == 5:  # Report
            data["report"] = answer.lower() in ("yes", "y")
            wiz["step"] = 6
            report_str = "Yes" if data["report"] else "No"
            panel.write(f"  [green]→[/] {report_str}\n")
            # Summary
            mode = "Full Crew" if data.get("crew") else f"@{data['agent']}"
            for a in self._agents_cfg:
                if a["id"] == data.get("agent"):
                    mode = f"{a['name']} ({a['id']})"
                    break
            panel.write("[bold]Step 6/6:[/] Confirm and create?\n")
            panel.write(f"  [bold]Name:[/]     {data['name']}")
            panel.write(f"  [bold]Schedule:[/] {data['schedule']}")
            panel.write(f"  [bold]Run by:[/]   {mode}")
            panel.write(f"  [bold]Mission:[/]  {data['mission']}")
            panel.write(f"  [bold]Report:[/]   {report_str}")
            panel.write("\n  [dim]Type 'yes' to create, 'no' to cancel[/]")

        elif step == 6:  # Confirm
            if answer.lower() in ("yes", "y"):
                try:
                    job = cron_engine.add_cron(
                        name=data["name"],
                        description=data["mission"],
                        schedule=data["schedule"],
                        agent=data.get("agent"),
                        crew=data.get("crew", True),
                        report=data.get("report", True),
                    )
                    panel.write(f"\n[green]Cron job created![/]")
                    panel.write(f"  ID: #{job['id'][-6:]}  |  Next run: {job['next_run'][:16]}")
                    self._load_cron_view()
                except ValueError as e:
                    panel.write(f"\n[red]Error: {e}[/]")
            else:
                panel.write("[yellow]Cancelled.[/]")
            self._cron_wizard = None

    def _refresh_file_list(self):
        """Reload the file list with current filter."""
        try:
            file_list = self.query_one("#file-list", ListView)
        except Exception:
            return
        file_list.clear()
        out_dir = _output_dir()
        if not os.path.exists(out_dir):
            return

        files = sorted(
            [f for f in os.listdir(out_dir) if os.path.isfile(os.path.join(out_dir, f))],
            key=lambda f: os.path.getmtime(os.path.join(out_dir, f)),
            reverse=True,
        )

        # Apply filter — type tabs show only that type, chronological shows all
        if self._file_filter == "heartbeat":
            files = [f for f in files if f.startswith("heartbeat_")]
        elif self._file_filter == "report":
            files = [f for f in files if f.startswith("report_")]
        elif self._file_filter == "decision":
            files = [f for f in files if f.startswith("decision_")]

        self._filtered_files = files

        for f in files:
            if f.startswith("heartbeat_"):
                icon = "H"
                color = "cyan"
            elif f.startswith("report_"):
                icon = "R"
                color = "green"
            elif f.startswith("decision_"):
                icon = "D"
                color = "yellow"
            else:
                icon = "·"
                color = "white"
            display = f.replace(".md", "")
            if len(display) > 28:
                display = display[:28] + "…"
            item = ListItem(Label(f"[{color}]{icon}[/] {display}"), name=f)
            file_list.append(item)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle selection in list views (files, models)."""
        if not event.item or not event.item.name:
            return
        # Determine which list was clicked
        try:
            file_list = self.query_one("#file-list", ListView)
            if event.list_view is file_list:
                self._show_file(event.item.name)
                return
        except Exception:
            pass

    def _show_file(self, filename: str) -> None:
        """Display a file in the viewer panel."""
        viewer = self.query_one("#file-viewer", RichLog)
        viewer.clear()
        out_dir = _output_dir()
        path = os.path.join(out_dir, filename)
        try:
            with open(path) as f:
                content = f.read()
            viewer.write(f"[bold]{filename}[/]")
            viewer.write(f"[dim]{path}[/]\n")
            viewer.write(content)
        except Exception as e:
            viewer.write(f"[bold red]Error reading file:[/] {e}")

    def on_click(self, event) -> None:
        """Handle clicks on filter tabs and docs sections."""
        widget = event.widget if hasattr(event, 'widget') else None
        if not widget or not hasattr(widget, 'id') or not widget.id:
            return
        if widget.id.startswith("file-filter-"):
            filter_name = widget.id.replace("file-filter-", "")
            self._file_filter = filter_name
            for child in self.query(".file-filter"):
                child.remove_class("file-filter-active")
            widget.add_class("file-filter-active")
            self._refresh_file_list()
        elif widget.id.startswith("docs-section-"):
            section_name = widget.id.replace("docs-section-", "")
            for child in self.query(".docs-section"):
                child.remove_class("docs-section-active")
            widget.add_class("docs-section-active")
            self._load_docs_section(section_name)

    # === Message handlers ===

    def on_agent_output(self, message: AgentOutput) -> None:
        try:
            panel = self.query_one(f"#panel-{message.agent_id}", AgentPanel)
            panel.write(message.text)
        except Exception:
            pass

    def on_agent_status(self, message: AgentStatus) -> None:
        try:
            panel = self.query_one(f"#panel-{message.agent_id}", AgentPanel)
            panel.set_status(message.status)
        except Exception:
            pass

    def on_crew_finished(self, message: CrewFinished) -> None:
        self.crew_running = False
        self._hide_activity()
        if message.success:
            self.notify("Crew run completed!", severity="information")
            for aid in self._agent_ids:
                panel = self.query_one(f"#panel-{aid}", AgentPanel)
                panel.set_status("[bold green]done[/]")
            self._refresh_file_list()
            threading.Thread(target=self._send_telegram_complete,
                             args=(message.mission, message.duration, message.output_files), daemon=True).start()
        else:
            self.notify(f"Crew failed: {message.error[:80]}", severity="error")
            for aid in self._agent_ids:
                panel = self.query_one(f"#panel-{aid}", AgentPanel)
                panel.set_status("[bold red]error[/]")
            threading.Thread(target=self._send_telegram_failed,
                             args=(message.mission, message.error, message.duration), daemon=True).start()
        self._load_history_view()

    def _send_telegram_complete(self, mission, duration, output_files):
        try:
            import telegram_notify as tg
            tg.notify_crew_complete(mission, duration, output_files)
        except Exception:
            pass

    def _send_telegram_failed(self, mission, error, duration):
        try:
            import telegram_notify as tg
            tg.notify_crew_failed(mission, error, duration)
        except Exception:
            pass

    def on_chat_response(self, message: ChatResponse) -> None:
        agent_cfg = next((a for a in self._agents_cfg if a["id"] == message.agent_id), None)
        if agent_cfg:
            info = get_agent_display(agent_cfg)
            color = info["color"]
            panel = self.query_one(f"#panel-{message.agent_id}", AgentPanel)
            panel.write(f"[bold {color}]{info['name']}:[/] {message.text}")
            panel.set_status("[dim]idle[/]")

    # === Actions ===

    def action_paste_clipboard(self) -> None:
        try:
            import pyperclip
            text = pyperclip.paste()
            if text:
                inp = self.query_one("#prompt-input", Input)
                inp.value = inp.value + text
                inp.cursor_position = len(inp.value)
                inp.focus()
            else:
                self.notify("Clipboard is empty", severity="warning")
        except Exception as e:
            self.notify(f"Paste failed: {e}", severity="error")

    def action_copy_panel(self) -> None:
        tabs = self.query_one("#main-tabs", TabbedContent)
        active = tabs.active
        text = ""
        label = "unknown"

        if active == "tab-agents":
            panel = self.query_one(f"#panel-{self.current_agent}", AgentPanel)
            text = panel.get_text()
            agent_cfg = next((a for a in self._agents_cfg if a["id"] == self.current_agent), None)
            label = f"{agent_cfg['name']} panel" if agent_cfg else "panel"
        elif active == "tab-queue":
            import heartbeat as hb
            lines = [f"[{t['status']}] {t['description']} -> {t.get('agent', 'auto')}" for t in hb.list_tasks()]
            text = "\n".join(lines) if lines else "Queue empty"
            label = "queue"
        elif active == "tab-config":
            lines = []
            for a in self._agents_cfg:
                info = get_agent_display(a)
                lines.append(f"{info['name']:20s} preset={a.get('preset', '?')} -> {info['model']} via {info['provider']}")
            text = "\n".join(lines)
            label = "config"
        elif active == "tab-history":
            history = load_history()
            lines = []
            for entry in reversed(history):
                status = "OK" if entry.get("success") else "FAIL"
                lines.append(f"{status} [{entry.get('timestamp', '?')}] {entry.get('mission', 'default crew')[:60]}")
            text = "\n".join(lines) if lines else "No history"
            label = "history"

        if text:
            try:
                import pyperclip
                pyperclip.copy(text)
            except Exception:
                self.copy_to_clipboard(text)
            self.notify(f"Copied {label} to clipboard")
        else:
            self.notify("Nothing to copy", severity="warning")

    def action_show_all(self) -> None:
        if not self._agents_cfg:
            return
        self.query_one("#main-tabs", TabbedContent).active = "tab-agents"
        grid = self.query_one("#agent-grid")
        grid.remove_class("focused")
        for aid in self._agent_ids:
            self.query_one(f"#panel-{aid}").display = True
        self.query_one("#prompt-bar").remove_class("focused-mode")
        self.notify("Showing all agents")

    def action_focus_agent(self, agent_id: str) -> None:
        self.current_agent = agent_id
        agent_cfg = next((a for a in self._agents_cfg if a["id"] == agent_id), None)
        if not agent_cfg:
            return
        info = get_agent_display(agent_cfg)
        color = info["color"]
        self.query_one("#agent-select", Static).update(f"[bold {color}]{info['name']}[/] > ")
        self.query_one("#main-tabs", TabbedContent).active = "tab-agents"
        grid = self.query_one("#agent-grid")
        grid.add_class("focused")
        for aid in self._agent_ids:
            self.query_one(f"#panel-{aid}").display = (aid == agent_id)
        self.query_one("#prompt-bar").add_class("focused-mode")
        self.notify(f"Focused on {info['name']}")

    def action_show_files(self) -> None:
        self.query_one("#main-tabs", TabbedContent).active = "tab-files"

    def action_show_config(self) -> None:
        self._load_config_view()
        self.query_one("#main-tabs", TabbedContent).active = "tab-config"

    def action_run_default_crew(self) -> None:
        self._start_crew_run(mission=None)

    # === Crew run ===

    def _start_crew_run(self, mission: str = None):
        if self.crew_running:
            self.crew_running = False  # Reset stuck flag
            self.notify("Crew flag was stuck — reset. Try again.", severity="warning")
            return
        self.crew_running = True
        self._crew_start_time = datetime.now()
        label = mission[:50] + "..." if mission and len(mission) > 50 else (mission or "default tasks")
        self.notify(f"Starting crew: {label}")
        try:
            self.query_one("#main-tabs", TabbedContent).active = "tab-status"
        except Exception:
            pass
        for aid in self._agent_ids:
            panel = self.query_one(f"#panel-{aid}", AgentPanel)
            panel.clear()
            panel.set_status("[dim]waiting...[/]")
        thread = threading.Thread(target=self._run_crew_thread, args=(mission,), daemon=True)
        thread.start()

    def _run_crew_thread(self, mission: str = None):
        start_time = datetime.now()
        logger.info(f"Crew thread started: {mission or 'default'}")
        try:
            from crew import build_crew_from_config
            out_dir = _output_dir()
            os.makedirs(out_dir, exist_ok=True)

            logger.info("Building crew from config...")
            crew, components = build_crew_from_config(self._project_config, MODEL_PRESETS, mission=mission)
            self._components = components
            logger.info(f"Crew built: {len(crew.tasks)} tasks, {len(components['agents'])} agents")
            # Capture task list for Status tab
            self._crew_tasks = []
            for i, t in enumerate(crew.tasks):
                agent_role = getattr(t.agent, 'role', '?') if t.agent else '?'
                # Find agent name from config
                agent_name = agent_role
                for a in self._agents_cfg:
                    if a["role"] == agent_role:
                        agent_name = a["name"]
                        break
                self._crew_tasks.append({
                    "desc": t.description[:80],
                    "agent": agent_name,
                    "done": False,
                })
                logger.info(f"  Task {i}: agent={agent_role}, desc={t.description[:50]}")

            # Build reverse lookup: Agent object -> agent_id
            agent_obj_to_id = {}
            for aid, agent_obj in components["agents"].items():
                agent_obj_to_id[id(agent_obj)] = aid

            def _resolve_agent_id(agent_ref):
                """Resolve an agent reference from a callback to our agent_id."""
                if agent_ref is None:
                    logger.info("Callback agent_ref is None, defaulting to first agent")
                    return self._agent_ids[0] if self._agent_ids else ""
                # Try object identity first
                obj_id = id(agent_ref)
                if obj_id in agent_obj_to_id:
                    return agent_obj_to_id[obj_id]
                # Try role string match
                role_name = getattr(agent_ref, 'role', str(agent_ref))
                if role_name in self._role_to_id:
                    return self._role_to_id[role_name]
                # Try partial match
                for role, aid in self._role_to_id.items():
                    if role.lower() in role_name.lower() or role_name.lower() in role.lower():
                        return aid
                logger.info(f"Could not resolve agent: role='{role_name}', falling back to first agent")
                return self._agent_ids[0] if self._agent_ids else ""

            def step_callback(step_output):
                agent_ref = getattr(step_output, 'agent', None)
                agent_id = _resolve_agent_id(agent_ref)
                output_text = str(getattr(step_output, 'output', step_output))
                if len(output_text) > 500:
                    output_text = output_text[:500] + "..."
                self.post_message(AgentStatus(agent_id, "[bold yellow]working...[/]"))
                self.post_message(AgentOutput(agent_id, f"[dim]Step:[/] {output_text}"))

            _task_index = [0]  # mutable counter for sequential task tracking

            def task_callback(task_output):
                agent_ref = getattr(task_output, 'agent', None)
                agent_id = _resolve_agent_id(agent_ref)
                # Mark task done in our tracking list
                idx = _task_index[0]
                if idx < len(self._crew_tasks):
                    self._crew_tasks[idx]["done"] = True
                    _task_index[0] += 1
                output_text = str(getattr(task_output, 'raw', getattr(task_output, 'output', task_output)))
                if len(output_text) > 2000:
                    output_text = output_text[:2000] + "\n[dim]... (truncated)[/]"
                self.post_message(AgentStatus(agent_id, "[bold green]done[/]"))
                self.post_message(AgentOutput(agent_id, f"[bold green]Task complete:[/]\n{output_text}"))
                self.call_from_thread(self._update_status_tab)

            crew.step_callback = step_callback
            crew.task_callback = task_callback
            # chdir to output dir so CrewAI writes files there
            original_cwd = os.getcwd()
            os.chdir(out_dir)
            logger.info(f"Crew kickoff starting (cwd={out_dir})...")
            try:
                crew.kickoff()
            finally:
                os.chdir(original_cwd)
            logger.info("Crew kickoff completed")

            duration = int((datetime.now() - start_time).total_seconds())
            save_history({
                "timestamp": start_time.strftime("%Y-%m-%d %H:%M"),
                "mission": mission or "default crew",
                "success": True,
                "duration": duration,
            })

            output_files = []
            if os.path.exists(out_dir):
                for f in sorted(os.listdir(out_dir)):
                    fpath = os.path.join(out_dir, f)
                    if os.path.getmtime(fpath) >= start_time.timestamp():
                        output_files.append(fpath)

            self.post_message(CrewFinished(
                success=True, mission=mission or "default",
                duration=duration, output_files=output_files,
            ))

        except Exception as e:
            logger.error(f"Crew failed: {e}", exc_info=True)
            self.crew_running = False
            duration = int((datetime.now() - start_time).total_seconds())
            try:
                save_history({
                    "timestamp": start_time.strftime("%Y-%m-%d %H:%M"),
                    "mission": mission or "default crew",
                    "success": False,
                    "duration": duration,
                    "error": str(e)[:200],
                })
            except Exception:
                pass
            self.post_message(CrewFinished(success=False, error=str(e), mission=mission or "default", duration=duration))

    # === Input handling ===

    def on_input_submitted(self, event: Input.Submitted) -> None:
        message = event.value.strip()
        if not message:
            return
        event.input.value = ""

        # Handle active cron wizard
        if self._cron_wizard is not None:
            if message.lower() in ("/cancel", "cancel"):
                self._cron_wizard = None
                panel = self.query_one(f"#panel-{self.current_agent}", AgentPanel)
                panel.write("[yellow]Cron wizard cancelled.[/]")
            else:
                self._cron_wizard_step(message)
            return

        if message.startswith("/"):
            self._handle_command(message)
            return

        if not self.current_agent:
            self.notify("No agents configured", severity="warning")
            return

        agent_id = self.current_agent
        agent_cfg = next((a for a in self._agents_cfg if a["id"] == agent_id), None)
        if not agent_cfg:
            return
        info = get_agent_display(agent_cfg)
        panel = self.query_one(f"#panel-{agent_id}", AgentPanel)
        panel.write(f"[bold]You:[/] {message}")
        panel.set_status("[bold yellow]thinking...[/]")

        thread = threading.Thread(target=self._chat_thread, args=(agent_id, message), daemon=True)
        thread.start()

    def _chat_thread(self, agent_id: str, message: str):
        try:
            import agent_memory as mem
            components = self._ensure_components()
            agent = components["agents"][agent_id]
            llm = components["llms"][agent_id]

            if not llm:
                self.post_message(ChatResponse(agent_id, "[bold red]Error:[/] No LLM configured for this agent. Check API keys."))
                return

            memory_context = mem.get_agent_context(agent_id)
            memory_section = f"\n\n## Your Memory\n{memory_context}" if memory_context else ""

            system_prompt = (
                f"You are {agent.role}. {agent.backstory} "
                f"Respond concisely and helpfully."
                f"{memory_section}"
            )

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": message},
            ]

            response = llm.call(messages=messages)
            response_text = str(response) if response else "[dim]No response[/]"
            self.post_message(ChatResponse(agent_id, response_text))

            mem.add_episodic(
                agent_id, f"Chat Q: {message[:100]} -> A: {response_text[:150]}",
                source="chat", entry_type="observation", confidence="med", tags=["chat"],
            )
        except Exception as e:
            self.post_message(ChatResponse(agent_id, f"[bold red]Error:[/] {e}"))

    def _handle_command(self, command: str):
        parts = command.split(maxsplit=2)
        cmd = parts[0].lower()

        if cmd == "/crew":
            arg = command.split(maxsplit=1)[1] if len(parts) > 1 else ""
            if not arg:
                self.notify("Usage: /crew <mission description>", severity="warning")
                return
            self._start_crew_run(mission=arg)

        elif cmd == "/config":
            if len(parts) == 1:
                self.action_show_config()
                return
            if len(parts) < 3:
                self.notify("Usage: /config <agent> <preset>", severity="warning")
                return
            agent_key = parts[1].lower()
            preset_key = parts[2].lower()
            if agent_key not in self._agent_ids:
                self.notify(f"Unknown agent: {agent_key}. Use: {', '.join(self._agent_ids)}", severity="warning")
                return
            if preset_key not in MODEL_PRESETS:
                self.notify(f"Unknown preset: {preset_key}. Type /presets", severity="warning")
                return
            # Update config
            from config_loader import load_project_config, save_project_config
            config = load_project_config(force_reload=True)
            for a in config["agents"]:
                if a["id"] == agent_key:
                    a["preset"] = preset_key
                    break
            save_project_config(config)
            self._project_config = config
            self._agents_cfg = config.get("agents", [])
            self._reload_components()
            agent_cfg = next((a for a in self._agents_cfg if a["id"] == agent_key), None)
            if agent_cfg:
                info = get_agent_display(agent_cfg)
                panel = self.query_one(f"#panel-{agent_key}", AgentPanel)
                panel.update_info(info)
                panel.write(f"[bold green]Model changed to {info['model']} via {info['provider']}[/]")
            self._load_config_view()

        elif cmd == "/presets":
            panel = self.query_one(f"#panel-{self.current_agent}", AgentPanel)
            panel.write("[bold]Available model presets:[/]")
            for key, p in MODEL_PRESETS.items():
                panel.write(f"  [cyan]{key:18s}[/] {p['label']} via {p['provider']}")

        elif cmd == "/help":
            first_agent = self._agent_ids[0] if self._agent_ids else self.current_agent
            panel = self.query_one(f"#panel-{first_agent}", AgentPanel)
            panel.write(HELP_TEXT)
            agent_cfg = next((a for a in self._agents_cfg if a["id"] == first_agent), None)
            agent_name = agent_cfg["name"] if agent_cfg else first_agent
            self.notify(f"Help written to {agent_name}'s panel on the Agents tab", severity="information")

        elif cmd == "/clear":
            panel = self.query_one(f"#panel-{self.current_agent}", AgentPanel)
            panel.clear()

        elif cmd == "/history":
            self.query_one("#main-tabs", TabbedContent).active = "tab-history"
            self._load_history_view()

        elif cmd == "/open":
            out_dir = _output_dir()
            arg = parts[1] if len(parts) > 1 else ""
            if not arg:
                panel = self.query_one(f"#panel-{self.current_agent}", AgentPanel)
                if os.path.exists(out_dir):
                    files = sorted(os.listdir(out_dir))
                    if files:
                        panel.write("[bold]Output files:[/]")
                        for f in files:
                            size = os.path.getsize(os.path.join(out_dir, f))
                            panel.write(f"  [cyan]{f}[/] ({size:,} bytes)")
                    else:
                        panel.write("[dim]No output files yet.[/]")
                else:
                    panel.write("[dim]No output directory yet.[/]")
            else:
                filepath = os.path.join(out_dir, arg)
                if not os.path.exists(filepath):
                    matches = [f for f in os.listdir(out_dir) if arg.lower() in f.lower()] if os.path.exists(out_dir) else []
                    if len(matches) == 1:
                        filepath = os.path.join(out_dir, matches[0])
                    elif len(matches) > 1:
                        panel = self.query_one(f"#panel-{self.current_agent}", AgentPanel)
                        panel.write(f"[yellow]Multiple matches:[/] {', '.join(matches)}")
                        return
                    else:
                        self.notify(f"File not found: {arg}", severity="warning")
                        return
                self.query_one("#main-tabs", TabbedContent).active = "tab-files"
                viewer = self.query_one("#file-viewer", RichLog)
                viewer.clear()
                try:
                    with open(filepath) as f:
                        content = f.read()
                    viewer.write(f"[bold]{os.path.basename(filepath)}[/]")
                    viewer.write(f"[dim]{filepath}[/]\n")
                    viewer.write(content)
                except Exception as e:
                    viewer.write(f"[bold red]Error:[/] {e}")

        elif cmd == "/memory":
            import agent_memory as mem
            sub = parts[1] if len(parts) > 1 else "show"
            agent_id = self.current_agent
            panel = self.query_one(f"#panel-{agent_id}", AgentPanel)
            agent_cfg = next((a for a in self._agents_cfg if a["id"] == agent_id), None)
            agent_name = agent_cfg["name"] if agent_cfg else agent_id

            if sub == "show":
                context = mem.get_agent_context(agent_id)
                if context:
                    panel.write(f"[bold]Memory for {agent_name}:[/]")
                    panel.write(context)
                else:
                    panel.write("[dim]No memories stored yet.[/]")
            elif sub == "stats":
                panel.write("[bold]Memory Stats:[/]")
                for a in self._agents_cfg:
                    stats = mem.get_stats(a["id"])
                    panel.write(
                        f"  {a['name']}: {stats['semantic_active']} semantic, "
                        f"{stats['episodic_active']} episodic active, "
                        f"{stats['episodic_stale']} stale"
                    )
            elif sub == "decay":
                for aid in self._agent_ids:
                    mem.decay_episodic(aid)
                panel.write("[green]Decayed stale episodic entries for all agents.[/]")
            elif sub == "wipe":
                from config_loader import get_memory_dir
                agent_dir = os.path.join(get_memory_dir(), agent_id)
                if os.path.exists(agent_dir):
                    import shutil
                    shutil.rmtree(agent_dir)
                    panel.write(f"[bold red]Wiped all memory for {agent_name}.[/]")
                else:
                    panel.write("[dim]No memory to wipe.[/]")
            elif sub == "promote":
                candidates = mem.promote_candidates(agent_id)
                if candidates:
                    panel.write("[bold]Promotion candidates:[/]")
                    for c in candidates:
                        panel.write(f"  [{c.get('type', '?')}] {c['content'][:80]}")
                else:
                    panel.write("[dim]No promotion candidates.[/]")
            else:
                results = mem.search_memory(agent_id, sub)
                if results:
                    panel.write(f"[bold]Search '{sub}':[/]")
                    for r in results:
                        src = r.get("_source", "?")
                        panel.write(f"  [{src}] [{r.get('type', '?')}] {r['content'][:80]}")
                else:
                    panel.write(f"[dim]No memories matching '{sub}'.[/]")

        elif cmd == "/remember":
            import agent_memory as mem
            text = command.split(maxsplit=1)[1] if len(parts) > 1 else ""
            if not text:
                self.notify("Usage: /remember <fact to save>", severity="warning")
                return
            mem.add_semantic(self.current_agent, text, entry_type="decision", confidence="high", tags=["manual"])
            panel = self.query_one(f"#panel-{self.current_agent}", AgentPanel)
            panel.write("[green]Saved to long-term memory.[/]")

        elif cmd == "/forget":
            import agent_memory as mem
            text = parts[1] if len(parts) > 1 else ""
            if not text:
                self.notify("Usage: /forget <keyword>", severity="warning")
                return
            agent_id = self.current_agent
            removed = 0
            for path_fn in [mem._semantic_path, mem._episodic_path]:
                path = path_fn(agent_id)
                entries = mem._load_json(path)
                before = len(entries)
                entries = [e for e in entries if text.lower() not in e.get("content", "").lower()]
                removed += before - len(entries)
                mem._save_json(path, entries)
            panel = self.query_one(f"#panel-{self.current_agent}", AgentPanel)
            panel.write(f"[yellow]Removed {removed} matching memories.[/]")

        elif cmd == "/queue":
            import heartbeat as hb
            sub = parts[1].lower() if len(parts) > 1 else "list"
            panel = self.query_one(f"#panel-{self.current_agent}", AgentPanel)

            if sub == "add":
                desc = command.split(maxsplit=2)[2] if len(parts) > 2 else ""
                if not desc:
                    self.notify("Usage: /queue add [--crew] [--every 6h] [--after ID] [@agent] <task>", severity="warning")
                    return
                import re as _re
                crew = False
                every = None
                depends_on = []
                agent = None

                if "--crew" in desc:
                    crew = True
                    desc = desc.replace("--crew", "").strip()
                every_match = _re.search(r"--every\s+(\S+)", desc)
                if every_match:
                    every = every_match.group(1)
                    if not hb.parse_interval(every):
                        self.notify(f"Bad interval: {every}. Use e.g. 30m, 6h, 1d", severity="warning")
                        return
                    desc = desc[:every_match.start()] + desc[every_match.end():]
                    desc = desc.strip()
                after_match = _re.search(r"--after\s+(\S+)", desc)
                if after_match:
                    depends_on = [after_match.group(1)]
                    desc = desc[:after_match.start()] + desc[after_match.end():]
                    desc = desc.strip()
                if desc.startswith("@"):
                    agent_part, _, desc = desc.partition(" ")
                    agent = agent_part[1:]
                    if agent not in self._agent_ids:
                        self.notify(f"Unknown agent: {agent}. Use: {', '.join(self._agent_ids)}", severity="warning")
                        return
                if not desc:
                    self.notify("Task description is empty", severity="warning")
                    return

                task = hb.add_task(desc, agent=agent, crew=crew, every=every, depends_on=depends_on)
                labels = []
                if crew:
                    labels.append("[magenta]CREW[/]")
                labels.append(f"-> {agent or 'auto-route'}")
                if every:
                    labels.append(f"[cyan]every {every}[/]")
                if depends_on:
                    labels.append(f"[yellow]after #{depends_on[0]}[/]")
                panel.write(f"[green]Queued:[/] {desc[:50]} {' '.join(labels)} [dim](#{task['id'][-6:]})[/]")
                self._load_queue_view()

            elif sub == "list":
                self._load_queue_view()
                self.query_one("#main-tabs", TabbedContent).active = "tab-queue"

            elif sub == "cancel":
                if len(parts) < 3:
                    self.notify("Usage: /queue cancel <task-id-suffix>", severity="warning")
                    return
                tid_suffix = parts[2]
                tasks = hb.list_tasks()
                matches = [t for t in tasks if t["id"].endswith(tid_suffix)]
                if len(matches) == 1:
                    hb.cancel_task(matches[0]["id"])
                    self._load_queue_view()
                    try:
                        self.query_one("#main-tabs", TabbedContent).active = "tab-queue"
                    except Exception:
                        pass
                    self.notify(f"Task #{tid_suffix} cancelled", severity="information")
                elif len(matches) > 1:
                    panel.write(f"[yellow]Multiple matches for #{tid_suffix}. Be more specific.[/]")
                else:
                    panel.write(f"[red]No task matching #{tid_suffix}[/]")

            elif sub == "clear":
                removed = hb.clear_done()
                self._load_queue_view()
                try:
                    self.query_one("#main-tabs", TabbedContent).active = "tab-queue"
                except Exception:
                    pass
                self.notify(f"Cleared {removed} tasks", severity="information")

            elif sub == "remove":
                tid = parts[2] if len(parts) > 2 else ""
                if not tid:
                    panel.write("[dim]Usage: /queue remove <id>[/]")
                else:
                    tasks = hb.list_tasks()
                    matches = [t for t in tasks if t["id"].endswith(tid.lstrip("#"))]
                    if len(matches) == 1:
                        hb.update_task(matches[0]["id"], status="cancelled")
                        tasks_list = hb._load_queue()
                        tasks_list = [t for t in tasks_list if t["id"] != matches[0]["id"]]
                        hb._save_queue(tasks_list)
                        self._load_queue_view()
                        try:
                            self.query_one("#main-tabs", TabbedContent).active = "tab-queue"
                        except Exception:
                            pass
                        self.notify(f"Task #{tid} removed", severity="information")
                    elif len(matches) > 1:
                        panel.write(f"[yellow]Multiple matches for #{tid} — be more specific[/]")
                    else:
                        panel.write(f"[red]Task #{tid} not found[/]")

            elif sub == "priority" or sub == "pri":
                pri_parts = command.split()
                if len(pri_parts) < 4:
                    self.notify("Usage: /queue priority <id> <1-9>", severity="warning")
                    return
                tid_suffix = pri_parts[2]
                try:
                    pri = int(pri_parts[3])
                except ValueError:
                    self.notify("Priority must be a number 1-9", severity="warning")
                    return
                tasks = hb.list_tasks()
                matches = [t for t in tasks if t["id"].endswith(tid_suffix)]
                if len(matches) == 1:
                    hb.update_task(matches[0]["id"], priority=pri)
                    panel.write(f"[green]Task #{tid_suffix} priority -> {pri}[/]")
                    self._load_queue_view()
                else:
                    panel.write(f"[red]No unique match for #{tid_suffix}[/]")
            else:
                panel.write("[dim]Usage: /queue [add|list|cancel|clear|priority][/]")

        elif cmd == "/heartbeat":
            sub = parts[1].lower() if len(parts) > 1 else "status"
            panel = self.query_one(f"#panel-{self.current_agent}", AgentPanel)

            if sub in ("on", "start"):
                import heartbeat as hb_mod
                heartbeat = self._init_heartbeat()
                heartbeat.start()
                hb_config = hb_mod.load_heartbeat_config()
                hb_config["auto_start"] = True
                hb_config["interval"] = heartbeat.interval
                hb_mod.save_heartbeat_config(hb_config)
                panel.write("[bold green]Heartbeat started (auto-start saved).[/]")
                self.notify("Heartbeat started")
                self._load_queue_view()

            elif sub in ("off", "stop"):
                import heartbeat as hb_mod
                if self._heartbeat and self._heartbeat.running:
                    self._heartbeat.stop()
                    hb_config = hb_mod.load_heartbeat_config()
                    hb_config["auto_start"] = False
                    hb_mod.save_heartbeat_config(hb_config)
                    panel.write("[bold red]Heartbeat stopped (auto-start disabled).[/]")
                    self.notify("Heartbeat stopped")
                    self._load_queue_view()
                else:
                    panel.write("[dim]Heartbeat is not running.[/]")

            elif sub == "status":
                import heartbeat as hb_mod
                hb_config = hb_mod.load_heartbeat_config()
                auto = "[green]on[/]" if hb_config.get("auto_start") else "[red]off[/]"
                if self._heartbeat:
                    st = self._heartbeat.status()
                    state = "[bold green]RUNNING[/]" if st["running"] else "[bold red]STOPPED[/]"
                    panel.write(f"[bold]Heartbeat:[/] {state}  |  Auto-start: {auto}")
                    panel.write(f"  Interval: {st['interval']}s")
                    panel.write(f"  Processed: {st['tasks_processed']}")
                    panel.write(f"  Pending: {st['pending']}  Active: {st['active']}")
                else:
                    panel.write(f"[dim]Heartbeat not initialized. Auto-start: {auto}[/]")

            elif sub == "interval":
                import heartbeat as hb_mod
                if len(parts) < 3:
                    self.notify("Usage: /heartbeat interval <seconds>", severity="warning")
                    return
                try:
                    secs = max(10, int(parts[2]))
                    heartbeat = self._init_heartbeat()
                    heartbeat.interval = secs
                    hb_config = hb_mod.load_heartbeat_config()
                    hb_config["interval"] = secs
                    hb_mod.save_heartbeat_config(hb_config)
                    panel.write(f"[green]Heartbeat interval -> {secs}s (saved)[/]")
                except ValueError:
                    self.notify("Interval must be a number", severity="warning")
            else:
                panel.write("[dim]Usage: /heartbeat [on|off|status|interval N][/]")

        elif cmd == "/skills":
            sub = parts[1].lower() if len(parts) > 1 else "show"
            panel = self.query_one(f"#panel-{self.current_agent}", AgentPanel)

            if sub == "show" or sub == "list":
                self._load_skills_view()
                self.query_one("#main-tabs", TabbedContent).active = "tab-skills"

            elif sub == "install":
                if len(parts) < 3:
                    self.notify("Usage: /skills install <tool-id>", severity="warning")
                    return
                tool_id = parts[2]
                # Just verify it can be loaded
                from crew import list_available_tools
                available = list_available_tools()
                if tool_id not in available:
                    panel.write(f"[red]Unknown tool: {tool_id}[/]")
                    return
                panel.write(f"[green]Tool {tool_id} is available.[/] Use /skills assign {tool_id} <agent> to assign it.")
                self._load_skills_view()

            elif sub == "assign":
                if len(parts) < 3:
                    self.notify("Usage: /skills assign <tool> <agent>", severity="warning")
                    return
                # Parse: /skills assign tool_id agent_id
                assign_parts = command.split()
                if len(assign_parts) < 4:
                    self.notify("Usage: /skills assign <tool> <agent>", severity="warning")
                    return
                tool_id = assign_parts[2]
                agent_id = assign_parts[3]
                if agent_id not in self._agent_ids:
                    self.notify(f"Unknown agent: {agent_id}", severity="warning")
                    return
                from config_loader import load_project_config, save_project_config
                config = load_project_config(force_reload=True)
                for a in config["agents"]:
                    if a["id"] == agent_id:
                        if tool_id not in a.get("tools", []):
                            a.setdefault("tools", []).append(tool_id)
                        break
                save_project_config(config)
                self._project_config = config
                self._agents_cfg = config["agents"]
                self._reload_components()
                panel.write(f"[green]Assigned {tool_id} to {agent_id}[/]")
                self._load_skills_view()

            elif sub == "unassign":
                assign_parts = command.split()
                if len(assign_parts) < 4:
                    self.notify("Usage: /skills unassign <tool> <agent>", severity="warning")
                    return
                tool_id = assign_parts[2]
                agent_id = assign_parts[3]
                from config_loader import load_project_config, save_project_config
                config = load_project_config(force_reload=True)
                for a in config["agents"]:
                    if a["id"] == agent_id and tool_id in a.get("tools", []):
                        a["tools"].remove(tool_id)
                        break
                save_project_config(config)
                self._project_config = config
                self._agents_cfg = config["agents"]
                self._reload_components()
                self._load_skills_view()
                try:
                    self.query_one("#main-tabs", TabbedContent).active = "tab-skills"
                except Exception:
                    pass
                self.notify(f"Removed {tool_id} from {agent_id}", severity="information")

            elif sub == "new":
                from config_loader import get_skills_dir
                skills_dir = get_skills_dir()
                template_path = os.path.join(skills_dir, "my_custom_tool.py")
                if not os.path.exists(template_path):
                    with open(template_path, "w") as f:
                        f.write('"""Custom skill template — rename this file and class."""\n\n')
                        f.write('from crewai.tools import BaseTool\n\n\n')
                        f.write('class MyCustomTool(BaseTool):\n')
                        f.write('    name: str = "My Custom Tool"\n')
                        f.write('    description: str = "Describe what this tool does"\n\n')
                        f.write('    def _run(self, query: str) -> str:\n')
                        f.write('        # Your custom logic here\n')
                        f.write('        return f"Result for: {query}"\n')
                    panel.write(f"[green]Created template:[/] {template_path}")
                    panel.write("[dim]Edit the file, then use /skills refresh[/]")
                else:
                    panel.write(f"[yellow]Template already exists:[/] {template_path}")

            elif sub == "refresh":
                self._reload_components()
                self._load_skills_view()
                panel.write("[green]Skills refreshed.[/]")

            else:
                panel.write("[dim]Usage: /skills [show|install|assign|unassign|new|refresh][/]")

        elif cmd == "/cron":
            import cron_engine
            sub = parts[1].lower() if len(parts) > 1 else "list"
            panel = self.query_one(f"#panel-{self.current_agent}", AgentPanel)

            if sub == "add":
                # Quick one-liner: /cron add name | schedule | mission
                raw = command.split(maxsplit=2)[2] if len(command.split(maxsplit=2)) > 2 else ""
                if raw and "|" in raw:
                    fields = [f.strip() for f in raw.split("|")]
                    if len(fields) >= 3:
                        name, schedule, mission_raw = fields[0], fields[1], fields[2]
                        crew_mode = True
                        agent = None
                        if mission_raw.startswith("@"):
                            agent_part, _, mission_raw = mission_raw.partition(" ")
                            agent = agent_part[1:]
                            crew_mode = False
                        try:
                            job = cron_engine.add_cron(
                                name=name, description=mission_raw,
                                schedule=schedule, agent=agent, crew=crew_mode,
                            )
                            mode = "CREW" if crew_mode else f"@{agent}"
                            panel.write(f"[green]Cron added:[/] {name}")
                            panel.write(f"  Schedule: {schedule} | Mode: {mode}")
                            panel.write(f"  Next run: {job['next_run'][:16]}")
                            panel.write(f"  ID: #{job['id'][-6:]}")
                            self._load_cron_view()
                        except ValueError as e:
                            panel.write(f"[red]Error: {e}[/]")
                        return
                # Start interactive wizard
                self._cron_wizard = {"step": 1, "data": {}}
                panel.write("[bold cyan]━━━ Cron Job Wizard ━━━[/]")
                panel.write("[dim]Type 'cancel' at any step to abort.[/]\n")
                panel.write("[bold]Step 1/6:[/] What should this job be called?")
                panel.write("[dim]  e.g., Market Report, Daily SEO Check[/]")

            elif sub == "list":
                self._load_cron_view()
                try:
                    self.query_one("#main-tabs", TabbedContent).active = "tab-cron"
                except Exception:
                    pass

            elif sub in ("remove", "delete"):
                job_id = parts[2] if len(parts) > 2 else ""
                if not job_id:
                    self.notify("Usage: /cron remove <id>", severity="warning")
                elif cron_engine.remove_cron(job_id.lstrip("#")):
                    self._load_cron_view()
                    try:
                        self.query_one("#main-tabs", TabbedContent).active = "tab-cron"
                    except Exception:
                        pass
                    self.notify(f"Cron job #{job_id.lstrip('#')} removed", severity="information")
                else:
                    self.notify(f"Job not found: {job_id}", severity="error")

            elif sub == "on":
                job_id = parts[2] if len(parts) > 2 else ""
                if not job_id:
                    panel.write("[dim]Usage: /cron on <id>[/]")
                elif cron_engine.enable_cron(job_id.lstrip("#")):
                    self._load_cron_view()
                    self.notify("Cron job enabled", severity="information")
                else:
                    panel.write(f"[red]Job not found: {job_id}[/]")

            elif sub == "off":
                job_id = parts[2] if len(parts) > 2 else ""
                if not job_id:
                    panel.write("[dim]Usage: /cron off <id>[/]")
                elif cron_engine.disable_cron(job_id.lstrip("#")):
                    self._load_cron_view()
                    self.notify("Cron job disabled", severity="information")
                else:
                    panel.write(f"[red]Job not found: {job_id}[/]")

            elif sub == "approve":
                job_id = parts[2] if len(parts) > 2 else ""
                if not job_id:
                    panel.write("[dim]Usage: /cron approve <id>[/]")
                elif cron_engine.approve_cron(job_id.lstrip("#")):
                    panel.write("[green]Cron job approved and activated.[/]")
                    self._load_cron_view()
                else:
                    panel.write(f"[red]Job not found or not pending.[/]")

            elif sub == "reject":
                job_id = parts[2] if len(parts) > 2 else ""
                if not job_id:
                    panel.write("[dim]Usage: /cron reject <id>[/]")
                elif cron_engine.reject_cron(job_id.lstrip("#")):
                    panel.write("[yellow]Cron job rejected.[/]")
                    self._load_cron_view()
                else:
                    panel.write(f"[red]Job not found or not pending.[/]")

            elif sub == "run":
                job_id = parts[2] if len(parts) > 2 else ""
                if not job_id:
                    panel.write("[dim]Usage: /cron run <id>[/]")
                else:
                    import heartbeat as hb
                    job = cron_engine.run_now(job_id.lstrip("#"))
                    if job:
                        hb.add_task(
                            description=job["description"],
                            agent=job.get("agent"),
                            crew=job.get("crew", False),
                            tags=["cron", f"cron:{job['id']}",
                                  "report" if job.get("report", True) else "no-report"],
                        )
                        panel.write(f"[green]Cron triggered:[/] {job['name']}")
                        panel.write(f"  Queued for immediate execution.")
                        self._load_cron_view()
                    else:
                        panel.write(f"[red]Job not found: {job_id}[/]")

            else:
                panel.write("[dim]Usage: /cron [add|list|remove|delete|on|off|run|approve|reject][/]")

        elif cmd == "/daemon":
            sub = parts[1].lower() if len(parts) > 1 else "status"
            panel = self.query_one(f"#panel-{self.current_agent}", AgentPanel)
            from daemon import is_running, start, stop

            if sub in ("on", "start"):
                if is_running():
                    panel.write("[yellow]Daemon is already running.[/]")
                else:
                    start()  # uses subprocess, returns immediately
                    if is_running():
                        panel.write("[green]Daemon started. Heartbeat + Telegram running in background.[/]")
                    else:
                        panel.write("[red]Daemon failed to start. Check daemon log.[/]")
            elif sub in ("off", "stop"):
                if is_running():
                    stop()
                    panel.write("[yellow]Daemon stopped.[/]")
                else:
                    panel.write("[dim]Daemon is not running.[/]")
            elif sub == "status":
                if is_running():
                    from daemon import _pid_file
                    with open(_pid_file()) as f:
                        pid = f.read().strip()
                    panel.write(f"[green]Daemon is RUNNING[/] (PID {pid})")
                else:
                    panel.write("[dim]Daemon is not running.[/]")
                panel.write("[dim]Use /daemon on to start, /daemon off to stop[/]")
            else:
                panel.write("[dim]Usage: /daemon [on|off|status][/]")

        elif cmd == "/telegram":
            import telegram_notify as tg
            sub = parts[1] if len(parts) > 1 else "show"
            panel = self.query_one(f"#panel-{self.current_agent}", AgentPanel)
            if sub == "show":
                config = tg.load_config()
                enabled = "[green]enabled[/]" if config.get("enabled") else "[red]disabled[/]"
                listener = "[green]listening[/]" if self._telegram_listener and self._telegram_listener.running else "[dim]off[/]"
                panel.write(f"[bold]Telegram: {enabled}[/]  |  Listener: {listener}")
                panel.write(f"  Chat ID: {config.get('chat_id', 'not set')}")
                for key, val in config.get("notify_on", {}).items():
                    s = "[green]on[/]" if val else "[red]off[/]"
                    panel.write(f"  {key}: {s}")
            elif sub == "test":
                from config_loader import get_project_name
                brand = get_project_name() or "CrewTUI"
                ok = tg.send_message(f"*{brand} Test*\n\nTelegram notifications working!")
                panel.write("[green]Test sent![/]" if ok else "[red]Test failed. Check config.[/]")
            elif sub == "on":
                config = tg.load_config()
                config["enabled"] = True
                tg.save_config(config)
                panel.write("[green]Telegram notifications enabled.[/]")
                self._start_telegram_listener()
            elif sub == "off":
                config = tg.load_config()
                config["enabled"] = False
                tg.save_config(config)
                if self._telegram_listener and self._telegram_listener.running:
                    self._telegram_listener.stop()
                panel.write("[yellow]Telegram notifications disabled. Listener stopped.[/]")
            elif sub == "listen":
                if self._telegram_listener and self._telegram_listener.running:
                    panel.write("[green]Telegram listener already running.[/]")
                else:
                    self._start_telegram_listener()
                    if self._telegram_listener and self._telegram_listener.running:
                        panel.write("[green]Telegram listener started.[/]")
                    else:
                        panel.write("[red]Failed to start listener. Check /telegram show for config.[/]")
            else:
                panel.write("[dim]Usage: /telegram [show|test|on|off|listen][/]")

        elif cmd == "/restart":
            if self.crew_running:
                self.crew_running = False
            if self._heartbeat and self._heartbeat.running:
                self._heartbeat.stop()
            if self._telegram_listener and self._telegram_listener.running:
                self._telegram_listener.stop()
            # Stop daemon so it restarts fresh with new config
            from daemon import is_running as daemon_is_running, stop as daemon_stop
            if daemon_is_running():
                daemon_stop()
            self.exit(return_code=42)  # special code signals restart
            return

        elif cmd in ("/exit", "/quit", "/q"):
            # Stop local services but leave daemon running
            if self.crew_running:
                self.crew_running = False
                self._hide_activity()
            if self._heartbeat and self._heartbeat.running:
                self._heartbeat.stop()
            if self._telegram_listener and self._telegram_listener.running:
                self._telegram_listener.stop()
            # Daemon keeps running in background
            self.exit()
            return

        elif cmd == "/refresh":
            self._refresh_file_list()
            self.notify("Files refreshed")

        elif cmd == "/copy":
            self.action_copy_panel()

        elif cmd == "/delete":
            out_dir = _output_dir()
            arg = parts[1] if len(parts) > 1 else ""
            panel = self.query_one(f"#panel-{self.current_agent}", AgentPanel)
            if not arg:
                panel.write("[dim]Usage: /delete <filename> or /delete all[/]")
                return
            if arg.lower() == "all":
                if os.path.exists(out_dir):
                    count = 0
                    for f in os.listdir(out_dir):
                        os.remove(os.path.join(out_dir, f))
                        count += 1
                    self._refresh_file_list()
                    try:
                        self.query_one("#main-tabs", TabbedContent).active = "tab-files"
                    except Exception:
                        pass
                    self.notify(f"Deleted {count} files", severity="information")
                else:
                    panel.write("[dim]No output directory.[/]")
            else:
                filepath = os.path.join(out_dir, arg)
                if not os.path.exists(filepath):
                    # Try partial match
                    matches = [f for f in os.listdir(out_dir) if arg.lower() in f.lower()] if os.path.exists(out_dir) else []
                    if len(matches) == 1:
                        filepath = os.path.join(out_dir, matches[0])
                    elif len(matches) > 1:
                        panel.write(f"[yellow]Multiple matches:[/] {', '.join(matches)}")
                        return
                    else:
                        panel.write(f"[red]File not found: {arg}[/]")
                        return
                os.remove(filepath)
                self._refresh_file_list()
                try:
                    self.query_one("#main-tabs", TabbedContent).active = "tab-files"
                except Exception:
                    pass
                self.notify(f"Deleted {os.path.basename(filepath)}", severity="information")

        elif cmd == "/view":
            arg = parts[1] if len(parts) > 1 else ""
            if not arg:
                panel = self.query_one(f"#panel-{self.current_agent}", AgentPanel)
                panel.write("[dim]Usage: /view <number> or /view <filename>[/]")
            elif hasattr(self, '_filtered_files') and self._filtered_files:
                if arg.isdigit():
                    idx = int(arg) - 1
                    if 0 <= idx < len(self._filtered_files):
                        self._show_file(self._filtered_files[idx])
                        try:
                            self.query_one("#main-tabs", TabbedContent).active = "tab-files"
                        except Exception:
                            pass
                    else:
                        panel = self.query_one(f"#panel-{self.current_agent}", AgentPanel)
                        panel.write(f"[red]Invalid number. Range: 1-{len(self._filtered_files)}[/]")
                else:
                    # Match by partial name
                    matches = [f for f in self._filtered_files if arg.lower() in f.lower()]
                    if len(matches) == 1:
                        self._show_file(matches[0])
                        try:
                            self.query_one("#main-tabs", TabbedContent).active = "tab-files"
                        except Exception:
                            pass
                    elif len(matches) > 1:
                        panel = self.query_one(f"#panel-{self.current_agent}", AgentPanel)
                        panel.write(f"[yellow]Multiple matches: {', '.join(m[:30] for m in matches[:5])}[/]")
                    else:
                        panel = self.query_one(f"#panel-{self.current_agent}", AgentPanel)
                        panel.write(f"[red]No file matching: {arg}[/]")

        elif cmd == "/purge":
            out_dir = _output_dir()
            panel = self.query_one(f"#panel-{self.current_agent}", AgentPanel)
            if os.path.exists(out_dir):
                count = sum(1 for f in os.listdir(out_dir) if os.path.isfile(os.path.join(out_dir, f)))
                for f in os.listdir(out_dir):
                    fp = os.path.join(out_dir, f)
                    if os.path.isfile(fp):
                        os.remove(fp)
                self._refresh_file_list()
                try:
                    self.query_one("#main-tabs", TabbedContent).active = "tab-files"
                except Exception:
                    pass
                self.notify(f"Purged {count} files", severity="information")
            else:
                panel.write("[dim]No output directory.[/]")

        elif cmd == "/docs":
            section = parts[1].lower() if len(parts) > 1 else "overview"
            self._load_docs_section(section)
            try:
                self.query_one("#main-tabs", TabbedContent).active = "tab-docs"
            except Exception:
                pass

        elif cmd == "/status":
            panel = self.query_one(f"#panel-{self.current_agent}", AgentPanel)
            panel.write("[bold]Agent Status:[/]")
            for a in self._agents_cfg:
                info = get_agent_display(a)
                status = "running" if self.crew_running else "idle"
                panel.write(f"  [{info['color']}]{info['name']}[/] — {info['model']} via {info['provider']} — {status}")
            if self._heartbeat:
                st = self._heartbeat.status()
                hb_state = "[green]ON[/]" if st["running"] else "[red]OFF[/]"
                panel.write(f"\n[bold]Heartbeat:[/] {hb_state} | Pending: {st['pending']} | Processed: {st['tasks_processed']}")

        else:
            self.notify(f"Unknown command: {cmd}. Type /help", severity="warning")


if __name__ == "__main__":
    app = CrewTUIApp()
    app.run()
