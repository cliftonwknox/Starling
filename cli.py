"""CrewTUI CLI — Entry point dispatcher."""

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
        # Pass remaining args to model_wizard
        sys.argv = ["crewtui-models"] + args[1:]
        from model_wizard import main as model_main
        model_main()
    elif cmd == "telegram":
        sys.argv = ["crewtui-telegram"] + args[1:]
        from telegram_notify import main as tg_main
        tg_main()
    elif cmd in ("-h", "--help", "help"):
        print_help()
    elif cmd in ("-v", "--version", "version"):
        print("CrewTUI 1.0.0")
    else:
        print(f"Unknown command: {cmd}")
        print_help()
        sys.exit(1)


def launch_tui():
    from config_loader import config_exists
    if not config_exists():
        print("No project_config.json found.")
        print("Run 'crewtui setup' to create your project.")
        sys.exit(1)
    from tui import CrewTUIApp
    app = CrewTUIApp()
    app.run()


def print_help():
    print("""
CrewTUI — Config-driven CrewAI Terminal UI

Usage: crewtui <command>

Commands:
  tui              Launch the TUI (default)
  setup            First-run setup wizard
  models           Model preset manager (list/add/remove/test)
  telegram         Telegram notification setup
  help             Show this help
  version          Show version
""")


if __name__ == "__main__":
    main()
