"""CrewTUI Telegram Notifications — Send crew results to Telegram."""

import os
import json
import urllib.request
import urllib.parse

CONFIG_DIR = os.path.dirname(__file__)

DEFAULT_CONFIG = {
    "enabled": False,
    "bot_token": "",
    "chat_id": "",
    "notify_on": {
        "crew_complete": True,
        "crew_failed": True,
        "agent_error": False,
    },
    "include_summary": True,
    "max_message_length": 4000,
}


def _config_file() -> str:
    """Find telegram_config.json — prefer work dir, fall back to source dir."""
    try:
        from config_loader import get_data_file
        return get_data_file("telegram_config.json")
    except Exception:
        return os.path.join(CONFIG_DIR, "telegram_config.json")


def load_config() -> dict:
    config_file = _config_file()
    if os.path.exists(config_file):
        with open(config_file) as f:
            saved = json.load(f)
        return {**DEFAULT_CONFIG, **saved}
    return {**DEFAULT_CONFIG}


def save_config(config: dict):
    config_file = _config_file()
    with open(config_file, "w") as f:
        json.dump(config, f, indent=2)


def send_message(text: str, parse_mode: str = "Markdown") -> bool:
    """Send a message to the configured Telegram chat. Splits long messages."""
    config = load_config()
    if not config.get("enabled"):
        return False

    bot_token = config.get("bot_token")
    chat_id = config.get("chat_id")
    if not bot_token or not chat_id:
        return False

    # Split long messages into chunks (Telegram limit is 4096)
    chunk_size = 4000
    if len(text) <= chunk_size:
        return _send_single_message(bot_token, chat_id, text, parse_mode)

    # Split on paragraph breaks to keep readability
    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > chunk_size:
            if current:
                chunks.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line
    if current:
        chunks.append(current)

    success = True
    for i, chunk in enumerate(chunks):
        if len(chunks) > 1:
            header = f"_({i + 1}/{len(chunks)})_\n\n"
            chunk = header + chunk
        if not _send_single_message(bot_token, chat_id, chunk, parse_mode):
            success = False
        if i < len(chunks) - 1:
            import time
            time.sleep(0.5)  # rate limit between chunks
    return success


def _send_single_message(bot_token: str, chat_id: str, text: str, parse_mode: str = "Markdown") -> bool:
    """Send a single message to Telegram."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False


def send_document(file_path: str, caption: str = "") -> bool:
    """Send a file to the configured Telegram chat."""
    config = load_config()
    if not config.get("enabled"):
        return False

    bot_token = config.get("bot_token")
    chat_id = config.get("chat_id")
    if not bot_token or not chat_id:
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"

    # Build multipart form data
    boundary = "----CrewTUIBoundary"
    filename = os.path.basename(file_path)

    with open(file_path, "rb") as f:
        file_data = f.read()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
        f"{chat_id}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8") + file_data + f"\r\n".encode("utf-8")

    if caption:
        body += (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="caption"\r\n\r\n'
            f"{caption[:1024]}\r\n"
        ).encode("utf-8")

    body += f"--{boundary}--\r\n".encode("utf-8")

    try:
        req = urllib.request.Request(
            url, data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200
    except Exception:
        return False


def _get_brand():
    try:
        from config_loader import get_project_name
        return get_project_name() or "CrewTUI"
    except Exception:
        return "CrewTUI"


def notify_crew_complete(mission: str, duration: int, output_files: list = None):
    """Send crew completion notification."""
    config = load_config()
    if not config.get("notify_on", {}).get("crew_complete"):
        return

    brand = _get_brand()
    msg = f"*{brand} Crew Complete*\n\n"
    msg += f"*Mission:* {mission[:200]}\n"
    msg += f"*Duration:* {duration}s\n"

    if output_files and config.get("include_summary"):
        for fpath in output_files:
            if os.path.exists(fpath):
                fname = os.path.basename(fpath)
                try:
                    with open(fpath) as f:
                        content = f.read()
                    # Extract first few lines as summary
                    lines = content.strip().split("\n")
                    summary = "\n".join(lines[:10])
                    if len(lines) > 10:
                        summary += "\n..."
                    msg += f"\n📄 *{fname}*\n```\n{summary}\n```\n"
                except Exception:
                    msg += f"\n📄 {fname}\n"

    send_message(msg)

    # Also send the full files as documents
    if output_files:
        for fpath in output_files:
            if os.path.exists(fpath):
                send_document(fpath, caption=f"{brand}: {os.path.basename(fpath)}")


def notify_crew_failed(mission: str, error: str, duration: int):
    """Send crew failure notification."""
    config = load_config()
    if not config.get("notify_on", {}).get("crew_failed"):
        return

    brand = _get_brand()
    msg = f"*{brand} Crew Failed*\n\n"
    msg += f"*Mission:* {mission[:200]}\n"
    msg += f"*Duration:* {duration}s\n"
    msg += f"*Error:* `{error[:500]}`"

    send_message(msg)


# === CLI Wizard ===

def _prompt(text, default=""):
    if default:
        result = input(f"  {text} [{default}]: ").strip()
        return result if result else default
    return input(f"  {text}: ").strip()


def _prompt_yn(text, default=True):
    d = "Y/n" if default else "y/N"
    result = input(f"  {text} [{d}]: ").strip().lower()
    if not result:
        return default
    return result in ("y", "yes")


def cmd_show():
    """Show current Telegram config."""
    config = load_config()
    print("\n╔══════════════════════════════════════════════════════════════╗")
    print("║              CrewTUI — Telegram Config                   ║")
    print("╚══════════════════════════════════════════════════════════════╝\n")
    enabled = "✓ Enabled" if config.get("enabled") else "✗ Disabled"
    print(f"  Status:    {enabled}")
    print(f"  Bot token: {config.get('bot_token', 'not set')[:20]}...")
    print(f"  Chat ID:   {config.get('chat_id', 'not set')}")
    print(f"  Max length: {config.get('max_message_length', 4000)} chars")
    print(f"  Include summary: {config.get('include_summary', True)}")
    print(f"\n  Notify on:")
    for key, val in config.get("notify_on", {}).items():
        status = "✓" if val else "✗"
        print(f"    {status} {key}")
    print()


def cmd_setup():
    """Interactive setup wizard for Telegram notifications."""
    config = load_config()

    print("\n╔══════════════════════════════════════════════════════════════╗")
    print("║              Telegram Notification Setup                    ║")
    print("╚══════════════════════════════════════════════════════════════╝\n")

    config["enabled"] = _prompt_yn("Enable Telegram notifications?", config.get("enabled", True))

    if not config["enabled"]:
        save_config(config)
        print("\n  ✓ Notifications disabled.")
        return

    print("\n  Bot token — from BotFather. Leave blank to keep current.")
    token = _prompt("Bot token", config.get("bot_token", ""))
    if token:
        config["bot_token"] = token

    print("\n  Chat ID — your Telegram user/group ID for notifications.")
    print("  (Send /start to your bot, then check via API to find it)")
    chat_id = _prompt("Chat ID", config.get("chat_id", ""))
    if chat_id:
        config["chat_id"] = chat_id

    print("\n  What should trigger notifications?")
    notify = config.get("notify_on", {})
    notify["crew_complete"] = _prompt_yn("Notify on crew completion?", notify.get("crew_complete", True))
    notify["crew_failed"] = _prompt_yn("Notify on crew failure?", notify.get("crew_failed", True))
    notify["agent_error"] = _prompt_yn("Notify on individual agent errors?", notify.get("agent_error", False))
    config["notify_on"] = notify

    config["include_summary"] = _prompt_yn("Include report summary in message?", config.get("include_summary", True))

    max_len = _prompt("Max message length", str(config.get("max_message_length", 4000)))
    try:
        config["max_message_length"] = int(max_len)
    except ValueError:
        pass

    save_config(config)
    print("\n  ✓ Config saved!")

    if _prompt_yn("\n  Send a test message now?", True):
        cmd_test()


def cmd_test():
    """Send a test notification."""
    print("\n  Sending test message...")
    brand = _get_brand()
    ok = send_message(f"*{brand} Test*\n\nTelegram notifications are working!")
    if ok:
        print("  ✓ Test message sent! Check your Telegram.")
    else:
        config = load_config()
        if not config.get("enabled"):
            print("  ✗ Notifications are disabled. Run 'setup' first.")
        else:
            print("  ✗ Failed to send. Check bot token and chat ID.")


def cmd_disable():
    """Disable notifications."""
    config = load_config()
    config["enabled"] = False
    save_config(config)
    print("\n  ✓ Telegram notifications disabled.")


def cmd_enable():
    """Enable notifications."""
    config = load_config()
    config["enabled"] = True
    save_config(config)
    print("\n  ✓ Telegram notifications enabled.")


def cmd_remove():
    """Remove Telegram config entirely."""
    if os.path.exists(NOTIFY_CONFIG_FILE):
        os.remove(NOTIFY_CONFIG_FILE)
        print("\n  ✓ Telegram config removed. Will use defaults next time.")
    else:
        print("\n  No config file to remove.")


def main():
    import sys
    if len(sys.argv) < 2:
        print("\nCrewTUI Telegram Notifications")
        print("  Usage: python telegram_notify.py <command>")
        print()
        print("  Commands:")
        print("    show     — Show current config")
        print("    setup    — Interactive setup wizard")
        print("    test     — Send a test message")
        print("    enable   — Enable notifications")
        print("    disable  — Disable notifications")
        print("    remove   — Remove config file")
        print()
        return

    cmd = sys.argv[1].lower()
    cmds = {
        "show": cmd_show,
        "setup": cmd_setup,
        "test": cmd_test,
        "enable": cmd_enable,
        "disable": cmd_disable,
        "remove": cmd_remove,
    }
    fn = cmds.get(cmd)
    if fn:
        fn()
    else:
        print(f"  Unknown command: {cmd}. Use show, setup, test, enable, disable, or remove.")


if __name__ == "__main__":
    main()
