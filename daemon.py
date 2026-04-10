"""CrewTUI Daemon — Headless heartbeat + Telegram listener.

Runs without the TUI so tasks process even when the terminal is closed.
Start: crewtui daemon on
Stop:  crewtui daemon off
Check: crewtui daemon status
"""

import os
import sys
import json
import signal
import time
import threading
import logging

BASE_DIR = os.path.dirname(__file__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("crewtui.daemon")


def _pid_file():
    try:
        from config_loader import get_data_file
        return get_data_file("crewtui_daemon.pid")
    except Exception:
        return os.path.join(BASE_DIR, "crewtui_daemon.pid")


def _log_file():
    try:
        from config_loader import get_data_file
        return get_data_file("crewtui_daemon.log")
    except Exception:
        return os.path.join(BASE_DIR, "crewtui_daemon.log")


def is_running() -> bool:
    """Check if the daemon is running."""
    pid_path = _pid_file()
    if not os.path.exists(pid_path):
        return False
    with open(pid_path) as f:
        pid = int(f.read().strip())
    try:
        os.kill(pid, 0)  # check if process exists
        return True
    except OSError:
        # Stale PID file
        os.remove(pid_path)
        return False


def start():
    """Start the daemon as a detached subprocess (safe from threads/TUI)."""
    if is_running():
        print("Daemon is already running.")
        return

    from config_loader import config_exists
    if not config_exists():
        print("No project_config.json found. Run 'crewtui setup' first.")
        return

    import subprocess
    log_path = _log_file()
    log_fd = open(log_path, "a")
    # Launch daemon.py directly as a detached process
    proc = subprocess.Popen(
        [sys.executable, os.path.join(BASE_DIR, "daemon.py")],
        stdout=log_fd,
        stderr=log_fd,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        cwd=BASE_DIR,
    )
    log_fd.close()

    # Write PID immediately
    with open(_pid_file(), "w") as f:
        f.write(str(proc.pid))

    time.sleep(1.5)
    if is_running():
        print(f"Daemon started (PID {proc.pid})")
    else:
        print("Daemon failed to start. Check daemon log.")


def stop():
    """Stop the daemon."""
    pid_path = _pid_file()
    if not os.path.exists(pid_path):
        print("Daemon is not running.")
        return

    with open(pid_path) as f:
        pid = int(f.read().strip())

    try:
        os.kill(pid, signal.SIGTERM)
        # Wait for it to stop
        stopped = False
        for _ in range(6):
            try:
                os.kill(pid, 0)
                time.sleep(0.5)
            except OSError:
                stopped = True
                break
        # Force kill if SIGTERM didn't work
        if not stopped:
            try:
                os.kill(pid, signal.SIGKILL)
                time.sleep(0.5)
            except OSError:
                pass
        print(f"Daemon stopped (PID {pid})")
    except OSError:
        print("Daemon was not running (stale PID).")

    try:
        os.remove(pid_path)
    except Exception:
        pass


def status():
    """Check daemon status."""
    if is_running():
        with open(_pid_file()) as f:
            pid = f.read().strip()
        print(f"Daemon is RUNNING (PID {pid})")

        # Show log tail
        log_path = _log_file()
        if os.path.exists(log_path):
            print(f"\nRecent log ({log_path}):")
            with open(log_path) as f:
                lines = f.readlines()
                for line in lines[-10:]:
                    print(f"  {line.rstrip()}")
    else:
        print("Daemon is NOT running.")


def _build_report_context(out_dir: str, max_reports: int = 3, max_chars: int = 3000) -> str:
    """Build context with recent report summaries injected directly.

    Includes actual report content so agents don't need file tools.
    """
    try:
        if not os.path.exists(out_dir):
            return ""
        report_files = sorted(
            [f for f in os.listdir(out_dir) if f.endswith(".md")],
            key=lambda f: os.path.getmtime(os.path.join(out_dir, f)),
            reverse=True,
        )[:max_reports]
        if not report_files:
            return ""

        sections = ["\n\n--- PREVIOUS REPORTS (for reference) ---"]
        chars_used = 0
        for fname in report_files:
            try:
                with open(os.path.join(out_dir, fname)) as f:
                    content = f.read()
                # Truncate individual reports to fit budget
                remaining = max_chars - chars_used
                if remaining <= 200:
                    break
                if len(content) > remaining:
                    content = content[:remaining] + "\n...(truncated)"
                sections.append(f"\n### {fname}\n{content}")
                chars_used += len(content)
            except Exception:
                continue
        sections.append("--- END PREVIOUS REPORTS ---")
        return "\n".join(sections)
    except Exception:
        return ""


def _run_daemon():
    """Main daemon loop — heartbeat + Telegram listener."""
    from dotenv import load_dotenv
    # Load .env from work dir first, then source dir as fallback
    try:
        from model_wizard import _env_file
        load_dotenv(_env_file())
    except Exception:
        load_dotenv(os.path.join(BASE_DIR, ".env"))

    from config_loader import load_project_config
    from model_wizard import load_presets
    import heartbeat as hb
    import agent_memory as mem

    config = load_project_config()
    presets = load_presets()
    project_name = config.get("project", {}).get("name", "CrewTUI")

    logger.info(f"Project: {project_name}")
    logger.info(f"Agents: {[a['id'] for a in config.get('agents', [])]}")

    # Build components for task execution
    components = None

    def ensure_components():
        nonlocal components
        if components is None:
            from crew import build_agents_from_config
            components = build_agents_from_config(config, presets)
        return components

    def run_task(task):
        """Execute a single-agent task using CrewAI (with tools)."""
        from crewai import Crew, Task as CrewTask
        from config_loader import get_output_dir

        comps = ensure_components()
        agent_id = task.get("agent", "")
        agent = comps["agents"].get(agent_id)

        if not agent:
            raise ValueError(f"Unknown agent: {agent_id}")

        memory_context = mem.get_agent_context(agent_id)
        memory_section = f"\nAgent memory context:\n{memory_context}" if memory_context else ""

        # List recent reports for context
        recent_reports = _build_report_context(out_dir)

        crew_task = CrewTask(
            description=f"{task['description']}{memory_section}{recent_reports}",
            expected_output="A thorough report with findings and recommendations.",
            agent=agent,
        )

        out_dir = get_output_dir()
        os.makedirs(out_dir, exist_ok=True)
        original_cwd = os.getcwd()
        os.chdir(out_dir)
        try:
            crew = Crew(agents=[agent], tasks=[crew_task], verbose=True)
            result = crew.kickoff()
        finally:
            os.chdir(original_cwd)

        result = str(result) if result else "No response"

        mem.add_episodic(
            agent_id,
            f"Daemon task: {task['description'][:80]} -> {result[:150]}",
            source="daemon", entry_type="task", confidence="med", tags=["daemon"],
        )

        logger.info(f"Task done: {task['description'][:40]}")
        return result

    def run_crew(task):
        """Execute a full crew run."""
        from crew import build_crew_from_config
        from config_loader import get_output_dir

        mission = task["description"]
        logger.info(f"Crew run: {mission[:60]}")

        out_dir = get_output_dir()
        os.makedirs(out_dir, exist_ok=True)

        # Append report context to mission
        mission += _build_report_context(out_dir)

        original_cwd = os.getcwd()
        os.chdir(out_dir)
        try:
            crew, comps = build_crew_from_config(config, presets, mission=mission)
            result = crew.kickoff()
        finally:
            os.chdir(original_cwd)

        result_text = str(result) if result else "No output"
        logger.info(f"Crew done: {mission[:40]}")
        return result_text

    def on_task_start(task):
        logger.info(f"Starting: {task['description'][:60]}")

    def on_task_done(task, result):
        logger.info(f"Done: {task['description'][:40]}")
        # Send full result to Telegram (split-message handles length)
        try:
            import telegram_notify as tg
            result_str = str(result) if result else "No output"
            tg.send_message(
                f"*{project_name} Task Complete*\n\n"
                f"*Task:* {task['description'][:100]}\n"
                f"*Agent:* {task.get('agent', '?')}\n\n"
                f"{result_str}"
            )
        except Exception:
            pass

    def on_task_fail(task, error):
        logger.error(f"Failed: {task['description'][:40]} — {error}")
        try:
            import telegram_notify as tg
            tg.send_message(
                f"*{project_name} Task Failed*\n\n"
                f"*Task:* {task['description'][:100]}\n"
                f"*Error:* {str(error)[:200]}"
            )
        except Exception:
            pass

    def on_tick():
        """Check for due cron jobs on each heartbeat cycle."""
        try:
            import cron_engine
            due_jobs = cron_engine.check_due_jobs()
            for job in due_jobs:
                hb.add_task(
                    description=job["description"],
                    agent=job.get("agent"),
                    crew=job.get("crew", False),
                    tags=["cron", f"cron:{job['id']}",
                          "report" if job.get("report", True) else "no-report"],
                )
                logger.info(f"Cron fired: {job['name']} -> queued")
                try:
                    import telegram_notify as tg
                    tg.send_message(
                        f"*{project_name} Cron Fired*\n\n"
                        f"*Job:* {job['name']}\n"
                        f"*Schedule:* {job['schedule']}\n"
                        f"*Next run:* {job.get('next_run', '?')[:16]}"
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"Cron check error: {e}")

    # Start heartbeat
    heartbeat = hb.Heartbeat(
        interval=hb.load_heartbeat_config().get("interval", 60),
        on_tick=on_tick,
        on_task_start=on_task_start,
        on_task_done=on_task_done,
        on_task_fail=on_task_fail,
        run_task=run_task,
        run_crew=run_crew,
    )
    heartbeat.start()
    logger.info("Heartbeat started")

    # Start Telegram listener
    telegram_listener = None
    try:
        import telegram_notify as tg
        tg_config = tg.load_config()
        if tg_config.get("enabled") and tg_config.get("bot_token") and tg_config.get("chat_id"):
            from telegram_listener import TelegramListener, create_command_handler
            handler = create_command_handler()
            telegram_listener = TelegramListener(
                bot_token=tg_config["bot_token"],
                chat_id=tg_config["chat_id"],
                on_command=handler,
            )
            telegram_listener.start()
            logger.info("Telegram listener started")

            # Announce
            tg.send_message(f"*{project_name} Daemon Started*\n\nHeartbeat + Telegram listener active.\nSend /help for commands.")
    except Exception as e:
        logger.error(f"Telegram listener failed: {e}")

    # Keep running — use Event so SIGTERM can interrupt immediately
    _shutdown_event = threading.Event()

    def _signal_shutdown(signum, frame):
        _shutdown_event.set()

    signal.signal(signal.SIGTERM, _signal_shutdown)
    signal.signal(signal.SIGINT, _signal_shutdown)

    _shutdown_event.wait()  # blocks until signal received

    heartbeat.stop()
    if telegram_listener:
        telegram_listener.stop()
    logger.info("Daemon stopped")


def main():
    if len(sys.argv) < 2:
        print("CrewTUI Daemon")
        print("  Usage: crewtui daemon <on|off|status>")
        return

    cmd = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    # Handle "crewtui daemon on" where sys.argv = ['crewtui', 'daemon', 'on']
    if cmd == "daemon" and len(sys.argv) > 2:
        cmd = sys.argv[2].lower()

    if cmd in ("on", "start"):
        start()
    elif cmd in ("off", "stop"):
        stop()
    elif cmd == "status":
        status()
    else:
        print(f"Unknown: {cmd}. Use on, off, or status.")


if __name__ == "__main__":
    if len(sys.argv) <= 1:
        # Launched as daemon subprocess — run directly
        try:
            _run_daemon()
        finally:
            try:
                os.remove(_pid_file())
            except Exception:
                pass
    else:
        main()
