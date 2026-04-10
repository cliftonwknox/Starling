"""CrewTUI CLI — Entry point dispatcher."""

import os
import sys


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "tui"

    if cmd in ("tui", "run"):
        launch_tui()
    elif cmd == "setup":
        from setup_wizard import run_setup
        run_setup()
    elif cmd == "models":
        sys.argv = ["crewtui-models"] + args[1:]
        from model_wizard import main as model_main
        model_main()
    elif cmd == "telegram":
        sys.argv = ["crewtui-telegram"] + args[1:]
        from telegram_notify import main as tg_main
        tg_main()
    elif cmd == "daemon":
        sub = args[1] if len(args) > 1 else ""
        from daemon import start, stop, status
        if sub in ("on", "start"):
            start()
        elif sub in ("off", "stop"):
            stop()
        elif sub == "status":
            status()
        else:
            print("Usage: crewtui daemon <on|off|status>")
    elif cmd in ("-h", "--help", "help"):
        print_help()
    elif cmd in ("-v", "--version", "version"):
        print("CrewTUI 1.0.0")
    else:
        print(f"Unknown command: {cmd}")
        print_help()
        sys.exit(1)


def _kill_stale_tui_processes():
    """Kill any orphaned CrewTUI processes (not attached to a terminal)."""
    import subprocess
    my_pid = os.getpid()
    try:
        result = subprocess.run(
            ["ps", "-o", "pid=,tty=,args=", "-u", str(os.getuid())],
            capture_output=True, text=True
        )
        for line in result.stdout.strip().split("\n"):
            parts = line.split()
            if len(parts) < 3:
                continue
            pid, tty = int(parts[0]), parts[1]
            args = " ".join(parts[2:])
            if pid == my_pid or pid == os.getppid():
                continue
            # Kill detached crewtui processes (tty=? means no terminal)
            if tty == "?" and ("crewtui" in args or "tui.py" in args) and "daemon.py" not in args:
                os.kill(pid, 9)
    except Exception:
        pass


def launch_tui():
    from config_loader import config_exists
    if not config_exists():
        print("No project_config.json found.")
        print("Run 'crewtui setup' to create your project.")
        sys.exit(1)
    _kill_stale_tui_processes()
    while True:
        from tui import CrewTUIApp
        app = CrewTUIApp()
        result = app.run()
        if app.return_code == 42:
            # Restart requested — exec a fresh process so all code reloads
            print("Restarting CrewTUI...")
            os.execv(sys.executable, [sys.executable] + sys.argv)
        break


def print_help():
    print("""
CrewTUI — Config-driven CrewAI Terminal UI

Usage: crewtui <command>

Commands:
  tui              Launch the TUI (default)
  setup            First-run setup wizard
  models           Model preset manager (list/add/remove/test)
  telegram         Telegram notification setup
  daemon on        Start headless daemon (heartbeat + Telegram)
  daemon off       Stop the daemon
  daemon status    Check if daemon is running
  help             Show this help
  version          Show version
""")


if __name__ == "__main__":
    main()
