"""CrewTUI Telegram Listener — Receive commands from Telegram and execute them.

Polls for incoming messages via getUpdates API. Supports:
  /crew <mission>   — queue a full crew run
  /task <desc>      — queue a single-agent task
  /status           — current crew/heartbeat status
  /history          — recent crew run history
  /queue            — show task queue
  /agents           — list configured agents
  /help             — list commands
"""

import json
import os
import threading
import time
import urllib.request
import urllib.parse
import logging

logger = logging.getLogger("crewtui.telegram")

POLL_INTERVAL = 5  # seconds between polls


class TelegramListener:
    """Polls Telegram for incoming messages and routes commands."""

    def __init__(self, bot_token: str, chat_id: str, on_command=None):
        self.bot_token = bot_token
        self.chat_id = str(chat_id)
        self._running = False
        self._thread = None
        self._stop_event = threading.Event()
        self._last_update_id = 0
        self.on_command = on_command  # callback(command: str, args: str) -> str

    @property
    def running(self):
        return self._running

    def start(self):
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("Telegram listener started")

    def stop(self):
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None
        logger.info("Telegram listener stopped")

    def _poll_loop(self):
        while not self._stop_event.is_set():
            try:
                self._poll()
            except Exception as e:
                logger.error(f"Telegram poll error: {e}")
            for _ in range(POLL_INTERVAL):
                if self._stop_event.is_set():
                    return
                time.sleep(1)

    def _poll(self):
        """Fetch new messages via getUpdates."""
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        params = {
            "offset": self._last_update_id + 1,
            "timeout": 3,
            "allowed_updates": '["message"]',
        }
        query = urllib.parse.urlencode(params)
        try:
            req = urllib.request.Request(f"{url}?{query}")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
        except Exception:
            return

        if not data.get("ok") or not data.get("result"):
            return

        for update in data["result"]:
            update_id = update.get("update_id", 0)
            if update_id > self._last_update_id:
                self._last_update_id = update_id

            message = update.get("message", {})
            chat_id = str(message.get("chat", {}).get("id", ""))
            text = message.get("text", "").strip()

            # Only respond to our configured chat
            if chat_id != self.chat_id:
                continue

            if not text:
                continue

            logger.info(f"Telegram message: {text[:100]}")
            self._handle_message(text)

    def _handle_message(self, text: str):
        """Parse and route a Telegram command."""
        if not text.startswith("/"):
            # Not a command — could be a direct mission
            response = self._execute_command("crew", text)
            self._reply(response)
            return

        parts = text.split(maxsplit=1)
        command = parts[0].lower().lstrip("/")
        # Strip bot mention from command (e.g., /crew@Lovecraftllm_bot)
        if "@" in command:
            command = command.split("@")[0]
        args = parts[1] if len(parts) > 1 else ""

        response = self._execute_command(command, args)
        self._reply(response)

    def _execute_command(self, command: str, args: str) -> str:
        """Execute a command and return the response text."""
        if self.on_command:
            return self.on_command(command, args)
        return "No command handler configured."

    def _reply(self, text: str):
        """Send a reply to the configured chat (uses split-message logic)."""
        if not text:
            return
        try:
            import telegram_notify as tg
            tg.send_message(text)
        except Exception as e:
            logger.error(f"Telegram reply error: {e}")


def create_command_handler(app=None):
    """Create a command handler function that integrates with CrewTUI.

    Can work standalone (without TUI app) or integrated.
    """
    def handle_command(command: str, args: str) -> str:
        try:
            if command == "crew":
                return _cmd_crew(args, app)
            elif command == "task":
                return _cmd_task(args)
            elif command == "status":
                return _cmd_status(app)
            elif command == "history":
                return _cmd_history()
            elif command == "queue":
                return _cmd_queue()
            elif command == "agents":
                return _cmd_agents()
            elif command == "help" or command == "start":
                return _cmd_help()
            else:
                return f"Unknown command: /{command}\n\nType /help for available commands."
        except Exception as e:
            logger.error(f"Command error: {e}")
            return f"Error: {e}"

    return handle_command


def _cmd_crew(args: str, app=None) -> str:
    """Queue a full crew run."""
    if not args:
        return "Usage: /crew <mission description>"
    import heartbeat as hb
    task = hb.add_task(args, crew=True)
    # Start heartbeat if not running
    if app and app._heartbeat and not app._heartbeat.running:
        app._heartbeat.start()
    elif not app:
        # Standalone — just queue it
        pass
    return f"Crew mission queued: {args[:100]}\n\nTask ID: #{task['id'][-6:]}"


def _cmd_task(args: str) -> str:
    """Queue a single-agent task."""
    if not args:
        return "Usage: /task [@agent] <description>"
    import heartbeat as hb
    agent = None
    desc = args
    if args.startswith("@"):
        agent_part, _, desc = args.partition(" ")
        agent = agent_part[1:]
    task = hb.add_task(desc, agent=agent)
    agent_label = agent or "auto-route"
    return f"Task queued: {desc[:100]}\nAgent: {agent_label}\nID: #{task['id'][-6:]}"


def _cmd_status(app=None) -> str:
    """Get current status."""
    lines = []

    # Crew status
    if app and app.crew_running:
        from datetime import datetime
        elapsed = int((datetime.now() - app._crew_start_time).total_seconds()) if app._crew_start_time else 0
        total = len(app._crew_tasks)
        done = sum(1 for t in app._crew_tasks if t["done"])
        lines.append(f"*CREW RUNNING* ({done}/{total} tasks, {elapsed}s)")
        for t in app._crew_tasks:
            status = "DONE" if t["done"] else "pending"
            lines.append(f"  {status} — {t['agent']}: {t['desc'][:40]}")
    else:
        lines.append("No crew running.")

    # Heartbeat status
    import heartbeat as hb
    try:
        config = hb.load_heartbeat_config()
        if config.get("auto_start"):
            pending = len(hb.list_tasks("pending"))
            running = len(hb.list_tasks("running"))
            lines.append(f"\n*Heartbeat:* active | Pending: {pending} | Running: {running}")
        else:
            lines.append("\n*Heartbeat:* inactive")
    except Exception:
        lines.append("\n*Heartbeat:* unknown")

    return "\n".join(lines)


def _cmd_history() -> str:
    """Get recent crew run history."""
    try:
        from config_loader import get_data_file
        path = get_data_file("run_history.json")
        if os.path.exists(path):
            with open(path) as f:
                history = json.load(f)
            if not history:
                return "No crew runs yet."
            lines = ["*Recent Crew Runs:*\n"]
            for entry in reversed(history[-10:]):
                status = "OK" if entry.get("success") else "FAIL"
                lines.append(
                    f"{status} [{entry.get('timestamp', '?')}] "
                    f"{entry.get('mission', 'default')[:50]} "
                    f"({entry.get('duration', '?')}s)"
                )
            return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"
    return "No history available."


def _cmd_queue() -> str:
    """Show task queue."""
    import heartbeat as hb
    tasks = hb.list_tasks()
    if not tasks:
        return "Queue is empty."
    lines = ["*Task Queue:*\n"]
    for t in tasks:
        agent = t.get("agent") or "auto"
        flags = ""
        if t.get("crew"):
            flags += " [CREW]"
        if t.get("every"):
            flags += f" [every {t['every']}]"
        lines.append(f"{t['status']:9s} #{t['id'][-6:]} -> {agent}{flags}\n  {t['description'][:60]}")
    return "\n".join(lines)


def _cmd_agents() -> str:
    """List configured agents."""
    try:
        from config_loader import get_agents
        agents = get_agents()
        if not agents:
            return "No agents configured."
        lines = ["*Agents:*\n"]
        for a in agents:
            tools = len(a.get("tools", []))
            lines.append(f"*{a['name']}* ({a.get('preset', '?')}) — {a['role']}\n  {tools} tools")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


def _cmd_help() -> str:
    """Show help text."""
    return (
        "*CrewTUI Telegram Commands*\n\n"
        "/crew <mission> — Queue a full crew run\n"
        "/task <desc> — Queue a single-agent task\n"
        "/task @agent <desc> — Queue task for specific agent\n"
        "/status — Current crew/heartbeat status\n"
        "/history — Recent crew run history\n"
        "/queue — Show task queue\n"
        "/agents — List configured agents\n"
        "/help — This message\n\n"
        "_Or just send a message to queue it as a crew mission._"
    )
