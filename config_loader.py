"""Starling Config Loader — Centralized project configuration.

All modules import from here instead of having their own file-path logic.
Searches for project_config.json in: $STARLING_CONFIG, ./project_config.json,
~/.config/starling/project_config.json.
"""

import json
import os
import threading
from typing import Optional

# Search order for project config.
# CREWUI_CONFIG and ~/.config/crewui/ are legacy fallbacks from the pre-rename
# era (CrewTUI → Starling, 2026-04-11). They WILL be removed in v1.5.0 — users
# should set STARLING_CONFIG and migrate any config under ~/.config/crewui/ to
# ~/.config/starling/ before upgrading past v1.4.x.
_SEARCH_PATHS = [
    os.environ.get("STARLING_CONFIG", os.environ.get("CREWUI_CONFIG", "")),
    os.path.join(os.path.dirname(__file__), "project_config.json"),
    os.path.expanduser("~/.config/starling/project_config.json"),
    os.path.expanduser("~/.config/crewui/project_config.json"),  # legacy, removed in v1.5
]

_cached_config: Optional[dict] = None
_config_path: Optional[str] = None
_cache_lock = threading.Lock()  # guards _cached_config + _config_path


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
    """Load and cache the project config. Returns empty structure if not found
    or if the file is malformed (logs a warning in that case).

    Thread-safe: guards the module-level cache with a lock so concurrent
    first-loads from multiple threads don't double-migrate or race.
    """
    import logging
    logger = logging.getLogger("starling.config_loader")

    global _cached_config, _config_path

    with _cache_lock:
        if _cached_config is not None and not force_reload:
            return _cached_config

        path = _find_config()
        if not path:
            return _empty_config()

        try:
            with open(path) as f:
                _cached_config = json.load(f)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
            logger.error(
                f"Failed to load project_config.json at {path}: {e}. "
                f"Using empty config — run 'starling setup' to reconfigure."
            )
            return _empty_config()

        _config_path = path
        # Auto-migrate agents missing tier field (preserves backwards compat)
        _migrate_agent_tiers(_cached_config)
        return _cached_config


def _migrate_agent_tiers(config: dict) -> bool:
    """Assign sensible default tiers to agents that don't have one.

    Precedence rules (in order):
        1. Explicit `tier` field on an agent wins over all heuristics.
        2. If an agent already has `tier: "leader"`, no other agent can be
           auto-promoted to leader (one-Leader rule). The routing
           `default_agent` is silently demoted in this case.
        3. For untiered agents:
           - matches routing.default_agent (and no leader assigned yet) → leader
           - allow_delegation=True → coordinator
           - else → specialist

    Writes changes back to config in-place. Returns True if any changes made.
    Does NOT save to disk — caller saves when appropriate. Logs a warning if
    the default_agent is demoted due to an existing explicit leader.
    """
    import logging
    logger = logging.getLogger("starling.config_loader")

    # Tolerate "agents": null as well as missing key
    agents = config.get("agents") or []
    if not agents:
        return False

    # Valid tier values (kept local to avoid circular import from semantic_router)
    _VALID_TIERS = {"specialist", "coordinator", "leader"}

    default_agent_id = (config.get("routing") or {}).get("default_agent", "")
    changed = False
    leader_assigned = False

    # First pass: validate existing tier values; mark leader assignment
    for agent in agents:
        if "tier" in agent:
            tier = agent["tier"]
            if tier not in _VALID_TIERS:
                logger.warning(
                    f"Agent '{agent.get('id', '?')}' has invalid tier "
                    f"'{tier}' — correcting to 'specialist'. "
                    f"Valid tiers: {sorted(_VALID_TIERS)}."
                )
                agent["tier"] = "specialist"
                changed = True
            elif agent["tier"] == "leader":
                leader_assigned = True
                if default_agent_id and agent.get("id") != default_agent_id:
                    logger.warning(
                        f"Agent '{agent.get('id')}' has explicit tier='leader' but "
                        f"routing.default_agent='{default_agent_id}' does not match. "
                        f"Explicit tier takes precedence; default_agent will not be "
                        f"auto-promoted to leader."
                    )

    # Second pass: assign tiers to untiered agents
    for agent in agents:
        if "tier" in agent:
            continue
        if agent.get("id") == default_agent_id and not leader_assigned:
            agent["tier"] = "leader"
            leader_assigned = True
        elif agent.get("allow_delegation"):
            agent["tier"] = "coordinator"
        else:
            agent["tier"] = "specialist"
        changed = True

    return changed


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
    return config.get("project", {}).get("name", "Starling")


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
