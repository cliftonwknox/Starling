"""Starling CLI — Entry point dispatcher."""

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
    elif cmd == "export":
        # Default bundles secrets (so restore is zero-setup).
        # Use --strip to produce a share-safe copy without API keys / tokens.
        rest = args[1:]
        with_secrets = True
        path_args = []
        for a in rest:
            if a in ("--strip", "--no-secrets"):
                with_secrets = False
            else:
                path_args.append(a)
        out_path = path_args[0] if path_args else ""
        from setup_wizard import export_backup
        export_backup(out_path, with_secrets=with_secrets)
    elif cmd == "models":
        sys.argv = ["starling-models"] + args[1:]
        from model_wizard import main as model_main
        model_main()
    elif cmd == "telegram":
        sys.argv = ["starling-telegram"] + args[1:]
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
            print("Usage: starling daemon <on|off|status>")
    elif cmd in ("-h", "--help", "help"):
        print_help()
    elif cmd in ("-v", "--version", "version"):
        from __version__ import __version__
        print(f"Starling {__version__}")
    else:
        print(f"Unknown command: {cmd}")
        print_help()
        sys.exit(1)


def _kill_stale_tui_processes():
    """Kill any orphaned Starling TUI processes (not attached to a terminal).

    Identification rule: command line must contain THIS install's directory
    (so we don't accidentally SIGKILL an unrelated user process whose name or
    args happen to contain the substring 'starling'), AND must NOT be the
    daemon (which is allowed to run detached).
    """
    import subprocess
    my_pid = os.getpid()
    base_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        result = subprocess.run(
            ["ps", "-o", "pid=,tty=,args=", "-u", str(os.getuid())],
            capture_output=True, text=True
        )
        for line in result.stdout.strip().split("\n"):
            parts = line.split(None, 2)  # pid, tty, full-args (preserve spaces)
            if len(parts) < 3:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            tty, args = parts[1], parts[2]
            if pid == my_pid or pid == os.getppid():
                continue
            # Must reference THIS install dir to be considered "ours"
            if base_dir not in args:
                continue
            # Daemon is allowed to run detached (tty=?)
            if "daemon.py" in args:
                continue
            if tty == "?":
                try:
                    os.kill(pid, 9)
                except ProcessLookupError:
                    pass
    except Exception:
        pass


def launch_tui():
    from config_loader import config_exists
    if not config_exists():
        print("No project_config.json found.")
        print("Run 'starling setup' to create your project.")
        sys.exit(1)
    _kill_stale_tui_processes()
    while True:
        from tui import StarlingApp
        app = StarlingApp()
        result = app.run()
        if app.return_code == 42:
            # Restart requested — exec a fresh process so all code reloads
            print("Restarting Starling...")
            os.execv(sys.executable, [sys.executable] + sys.argv)
        break


def print_help():
    print("""
Starling — Config-driven CrewAI Terminal UI

Usage: starling <command>

Commands:
  tui              Launch the TUI (default)
  setup            First-run setup wizard
  export [path]    Export current project as a .starling backup (includes secrets)
                   Add --strip to produce a share-safe copy (no keys/tokens)
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
