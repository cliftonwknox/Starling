"""CrewTUI Config Loader — Centralized project configuration.

All modules import from here instead of having their own file-path logic.
Searches for project_config.json in: $CREWUI_CONFIG, ./project_config.json,
~/.config/crewui/project_config.json.
"""

import json
import os
from typing import Optional

# Search order for project config
_SEARCH_PATHS = [
    os.environ.get("CREWUI_CONFIG", ""),
    os.path.join(os.path.dirname(__file__), "project_config.json"),
    os.path.expanduser("~/.config/crewui/project_config.json"),
]

_cached_config: Optional[dict] = None
_config_path: Optional[str] = None


def _find_config() -> Optional[str]:
    """Find the first existing project_config.json."""
    for path in _SEARCH_PATHS:
        if path and os.path.isfile(path):
            return path
    return None


def config_exists() -> bool:
    return _find_config() is not None


def get_config_path() -> Optional[str]:
    """Return the resolved path to the config file, or None."""
    return _find_config()


def load_project_config(force_reload: bool = False) -> dict:
    """Load and cache the project config. Returns empty structure if not found."""
    global _cached_config, _config_path
    if _cached_config is not None and not force_reload:
        return _cached_config

    path = _find_config()
    if not path:
        return _empty_config()

    with open(path) as f:
        _cached_config = json.load(f)
    _config_path = path
    return _cached_config


def save_project_config(config: dict, path: Optional[str] = None):
    """Write config to disk. Uses existing path or provided path."""
    global _cached_config, _config_path
    target = path or _config_path or _SEARCH_PATHS[1]
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    with open(target, "w") as f:
        json.dump(config, f, indent=2)
    _cached_config = config
    _config_path = target


def _empty_config() -> dict:
    return {
        "project": {"name": "", "description": "", "work_dir": ""},
        "agents": [],
        "max_agents": 10,
        "default_tasks": [],
        "routing": {"keywords": {}, "default_agent": ""},
    }


# === Convenience accessors ===

def get_project_name() -> str:
    config = load_project_config()
    return config.get("project", {}).get("name", "CrewTUI")


def get_project_description() -> str:
    config = load_project_config()
    return config.get("project", {}).get("description", "")


def get_agents() -> list:
    config = load_project_config()
    return config.get("agents", [])


def get_agent_by_id(agent_id: str) -> Optional[dict]:
    for agent in get_agents():
        if agent["id"] == agent_id:
            return agent
    return None


def get_agent_ids() -> list:
    return [a["id"] for a in get_agents()]


def get_routing_keywords() -> dict:
    config = load_project_config()
    return config.get("routing", {}).get("keywords", {})


def get_default_agent() -> str:
    config = load_project_config()
    return config.get("routing", {}).get("default_agent", "")


def get_default_tasks() -> list:
    config = load_project_config()
    return config.get("default_tasks", [])


def get_max_agents() -> int:
    config = load_project_config()
    return config.get("max_agents", 10)


def get_work_dir() -> str:
    """Resolve the working directory. Creates it if missing."""
    config = load_project_config()
    work_dir = config.get("project", {}).get("work_dir", "")
    if not work_dir:
        # Fallback: use directory containing the config file, or cwd
        if _config_path:
            work_dir = os.path.dirname(_config_path)
        else:
            work_dir = os.getcwd()
    work_dir = os.path.expanduser(work_dir)
    work_dir = os.path.abspath(work_dir)
    os.makedirs(work_dir, exist_ok=True)
    return work_dir


def get_output_dir() -> str:
    d = os.path.join(get_work_dir(), "output")
    os.makedirs(d, exist_ok=True)
    return d


def get_memory_dir() -> str:
    d = os.path.join(get_work_dir(), "memory")
    os.makedirs(d, exist_ok=True)
    return d


def get_skills_dir() -> str:
    d = os.path.join(get_work_dir(), "skills")
    os.makedirs(d, exist_ok=True)
    return d


def get_data_file(name: str) -> str:
    """Get path to a data file in the work directory (e.g. run_history.json)."""
    return os.path.join(get_work_dir(), name)
