"""CrewTUI — Config-driven CrewAI Terminal UI"""

import math
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Header,
    Footer,
    RichLog,
    Static,
    Input,
    DirectoryTree,
    TabbedContent,
    TabPane,
)
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
  [cyan]/delete[/] <file>        — Delete an output file
  [cyan]/delete[/] all           — Delete all output files
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
    #file-tree {
        width: 30;
        height: 1fr;
        border-right: solid $primary-background;
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
    #activity-bar {
        dock: bottom;
        height: 1;
        background: $primary-background;
        padding: 0 1;
    }
    #activity-bar.idle {
        display: none;
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
        self._components = None
        self._heartbeat = None

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
            with TabPane("Files", id="tab-files"):
                with Horizontal(id="file-area"):
                    if os.path.exists(out_dir):
                        yield DirectoryTree(out_dir, id="file-tree")
                    else:
                        yield Static("[dim]No output directory yet.[/]", id="file-tree-empty")
                    yield RichLog(id="file-viewer", highlight=True, markup=True, wrap=True)
            with TabPane("History", id="tab-history"):
                yield RichLog(id="history-log", highlight=True, markup=True, wrap=True)
            with TabPane("Config", id="tab-config"):
                yield RichLog(id="config-log", highlight=True, markup=True, wrap=True)
            with TabPane("Queue", id="tab-queue"):
                yield RichLog(id="queue-log", highlight=True, markup=True, wrap=True)
            with TabPane("Skills", id="tab-skills"):
                yield RichLog(id="skills-log", highlight=True, markup=True, wrap=True)

        first_agent = self._agents_cfg[0] if self._agents_cfg else None
        first_color = first_agent.get("color", "cyan") if first_agent else "cyan"
        first_name = first_agent.get("name", "?") if first_agent else "?"

        yield Static("", id="activity-bar", classes="idle")
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

        # Auto-start heartbeat if configured
        import heartbeat as hb
        hb_config = hb.load_heartbeat_config()
        if hb_config.get("auto_start"):
            heartbeat = self._init_heartbeat()
            heartbeat.interval = hb_config.get("interval", 60)
            heartbeat.start()
            self.notify("Heartbeat auto-started")
            self._load_queue_view()

    def _show_activity(self, text: str):
        """Show the activity bar with a message."""
        bar = self.query_one("#activity-bar", Static)
        bar.update(text)
        bar.remove_class("idle")

    def _hide_activity(self):
        """Hide the activity bar."""
        bar = self.query_one("#activity-bar", Static)
        bar.add_class("idle")

    def _update_activity(self):
        """Update activity bar based on current state."""
        if self.crew_running:
            # Build status from agent panels
            working = []
            for a in self._agents_cfg:
                try:
                    panel = self.query_one(f"#panel-{a['id']}", AgentPanel)
                    status_widget = self.query_one(f"#status-{a['id']}", Static)
                    rendered = status_widget.render()
                    text = str(rendered)
                    if "working" in text or "thinking" in text or "waiting" in text:
                        working.append(a["name"])
                except Exception:
                    pass
            if working:
                dots = "." * (int(datetime.now().timestamp()) % 4)
                self._show_activity(f"[bold yellow]CREW RUNNING[/] | Active: {', '.join(working)}{dots}")
            else:
                dots = "." * (int(datetime.now().timestamp()) % 4)
                self._show_activity(f"[bold yellow]CREW RUNNING{dots}[/]")
        elif self._heartbeat and self._heartbeat.running:
            st = self._heartbeat.status()
            if st["active"] > 0:
                self._show_activity(f"[bold cyan]HEARTBEAT[/] | Processing task{' ' * (int(datetime.now().timestamp()) % 3)}")
            else:
                self._show_activity(f"[dim]HEARTBEAT IDLE[/] | Pending: {st['pending']}")
        else:
            self._hide_activity()

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
        import agent_memory as mem
        components = self._ensure_components()
        agent_id = task.get("agent", self._agent_ids[0] if self._agent_ids else "")
        agent = components["agents"].get(agent_id)
        llm = components["llms"].get(agent_id)

        if not agent or not llm:
            raise ValueError(f"Unknown agent: {agent_id}")

        memory_context = mem.get_agent_context(agent_id)
        memory_section = f"\n\n## Your Memory\n{memory_context}" if memory_context else ""

        system_prompt = (
            f"You are {agent.role}. {agent.backstory} "
            f"Complete the following task thoroughly and provide actionable results."
            f"{memory_section}"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task["description"]},
        ]

        self.post_message(AgentOutput(agent_id, f"[bold yellow]Heartbeat task:[/] {task['description']}"))

        response = llm.call(messages=messages)
        response_text = str(response) if response else "No response"

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

        result = crew.kickoff()
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

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        viewer = self.query_one("#file-viewer", RichLog)
        viewer.clear()
        path = str(event.path)
        try:
            with open(path) as f:
                content = f.read()
            viewer.write(f"[bold]{os.path.basename(path)}[/]")
            viewer.write(f"[dim]{path}[/]\n")
            viewer.write(content)
        except Exception as e:
            viewer.write(f"[bold red]Error reading file:[/] {e}")

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
        label = mission[:50] + "..." if mission and len(mission) > 50 else (mission or "default tasks")
        self.notify(f"Starting crew: {label}")
        self._show_activity(f"[bold yellow]CREW STARTING:[/] {label}")
        self.set_interval(2, self._update_activity, name="activity-pulse")
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
            for i, t in enumerate(crew.tasks):
                agent_role = getattr(t.agent, 'role', '?') if t.agent else '?'
                logger.info(f"  Task {i}: agent={agent_role}, desc={t.description[:50]}")

            # Build reverse lookup: Agent object -> agent_id
            agent_obj_to_id = {}
            for aid, agent_obj in components["agents"].items():
                agent_obj_to_id[id(agent_obj)] = aid

            def _resolve_agent_id(agent_ref):
                """Resolve an agent reference from a callback to our agent_id."""
                if agent_ref is None:
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
                logger.info(f"Could not resolve agent: role={role_name}")
                return self._agent_ids[0] if self._agent_ids else ""

            def step_callback(step_output):
                agent_ref = getattr(step_output, 'agent', None)
                agent_id = _resolve_agent_id(agent_ref)
                output_text = str(getattr(step_output, 'output', step_output))
                if len(output_text) > 500:
                    output_text = output_text[:500] + "..."
                self.post_message(AgentStatus(agent_id, "[bold yellow]working...[/]"))
                self.post_message(AgentOutput(agent_id, f"[dim]Step:[/] {output_text}"))

            def task_callback(task_output):
                agent_ref = getattr(task_output, 'agent', None)
                agent_id = _resolve_agent_id(agent_ref)
                output_text = str(getattr(task_output, 'raw', getattr(task_output, 'output', task_output)))
                if len(output_text) > 2000:
                    output_text = output_text[:2000] + "\n[dim]... (truncated)[/]"
                self.post_message(AgentStatus(agent_id, "[bold green]done[/]"))
                self.post_message(AgentOutput(agent_id, f"[bold green]Task complete:[/]\n{output_text}"))

            crew.step_callback = step_callback
            crew.task_callback = task_callback
            logger.info("Crew kickoff starting...")
            crew.kickoff()
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
            panel = self.query_one(f"#panel-{self.current_agent}", AgentPanel)
            panel.write(HELP_TEXT)

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
                    panel.write(f"[yellow]Cancelled task #{tid_suffix}[/]")
                    self._load_queue_view()
                elif len(matches) > 1:
                    panel.write(f"[yellow]Multiple matches for #{tid_suffix}. Be more specific.[/]")
                else:
                    panel.write(f"[red]No task matching #{tid_suffix}[/]")

            elif sub == "clear":
                removed = hb.clear_done()
                panel.write(f"[green]Cleared {removed} done/failed/cancelled tasks.[/]")
                self._load_queue_view()

            elif sub == "priority" or sub == "pri":
                if len(parts) < 4:
                    self.notify("Usage: /queue priority <id> <1-9>", severity="warning")
                    return
                tid_suffix = parts[2]
                try:
                    pri = int(parts[3])
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
                panel.write(f"[yellow]Removed {tool_id} from {agent_id}[/]")
                self._load_skills_view()

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

        elif cmd == "/telegram":
            import telegram_notify as tg
            sub = parts[1] if len(parts) > 1 else "show"
            panel = self.query_one(f"#panel-{self.current_agent}", AgentPanel)
            if sub == "show":
                config = tg.load_config()
                enabled = "[green]enabled[/]" if config.get("enabled") else "[red]disabled[/]"
                panel.write(f"[bold]Telegram: {enabled}[/]")
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
            elif sub == "off":
                config = tg.load_config()
                config["enabled"] = False
                tg.save_config(config)
                panel.write("[yellow]Telegram notifications disabled.[/]")
            else:
                panel.write("[dim]Usage: /telegram [show|test|on|off][/]")

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
                    panel.write(f"[yellow]Deleted {count} files from output.[/]")
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
                panel.write(f"[yellow]Deleted {os.path.basename(filepath)}[/]")

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
