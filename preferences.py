"""User preferences — small JSON file outside the install dir so it survives
reinstalls. Currently just stores backup_dir; extend as needed.
"""

import json
import os

PREFS_DIR = os.path.expanduser("~/.config/starling")
PREFS_FILE = os.path.join(PREFS_DIR, "preferences.json")
DEFAULT_BACKUP_DIR = os.path.expanduser("~/starling-backups")


def load_prefs() -> dict:
    if not os.path.exists(PREFS_FILE):
        return {}
    try:
        with open(PREFS_FILE) as f:
            return json.load(f) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_prefs(prefs: dict) -> None:
    os.makedirs(PREFS_DIR, exist_ok=True)
    with open(PREFS_FILE, "w") as f:
        json.dump(prefs, f, indent=2)


def get_backup_dir() -> str:
    prefs = load_prefs()
    return os.path.expanduser(prefs.get("backup_dir") or DEFAULT_BACKUP_DIR)


def set_backup_dir(path: str) -> None:
    prefs = load_prefs()
    prefs["backup_dir"] = path
    save_prefs(prefs)
