"""CrewTUI Agent Memory — Layered, contextual, human-like memory for crew agents.

Inspired by the elite-human-memory skill. Each agent has:
- Episodic memory: daily entries with full context (auto-decays)
- Semantic memory: promoted long-term facts (curated, durable)

Entries have metadata: when, source, confidence, scope, state, tags.
Old episodic entries decay. Only high-value items get promoted to semantic.
"""

import json
import os
from datetime import datetime, timedelta
from typing import Optional

def _get_memory_dir() -> str:
    try:
        from config_loader import get_memory_dir
        return get_memory_dir()
    except Exception:
        d = os.path.join(os.path.dirname(__file__), "memory")
        os.makedirs(d, exist_ok=True)
        return d

# How many days before episodic entries are considered stale
EPISODIC_STALE_DAYS = 14
# Max episodic entries per agent before pruning
EPISODIC_MAX_ENTRIES = 100
# Max semantic entries per agent
SEMANTIC_MAX_ENTRIES = 50


def _ensure_dirs(agent_id: str):
    agent_dir = os.path.join(_get_memory_dir(), agent_id)
    os.makedirs(agent_dir, exist_ok=True)
    return agent_dir


def _episodic_path(agent_id: str) -> str:
    return os.path.join(_ensure_dirs(agent_id), "episodic.json")


def _semantic_path(agent_id: str) -> str:
    return os.path.join(_ensure_dirs(agent_id), "semantic.json")


def _load_json(path: str) -> list:
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return []


def _save_json(path: str, data: list):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# === Entry creation ===

def create_entry(
    content: str,
    source: str = "crew_run",
    entry_type: str = "observation",
    confidence: str = "med",
    scope: str = "project",
    tags: Optional[list] = None,
    related: Optional[list] = None,
) -> dict:
    """Create a memory entry with full context metadata."""
    return {
        "id": datetime.now().strftime("%Y%m%d%H%M%S%f")[:18],
        "content": content,
        "type": entry_type,  # observation, decision, finding, preference, contact, task
        "source": source,    # crew_run, chat, user, promotion
        "when": datetime.now().isoformat(),
        "confidence": confidence,  # high, med, low
        "scope": scope,      # global, project, agent, temporary
        "state": "active",   # active, stale, superseded, archived
        "tags": tags or [],
        "related": related or [],
        "access_count": 0,
        "last_accessed": None,
        "created": datetime.now().isoformat(),
    }


# === Episodic memory (recent, auto-captured) ===

def add_episodic(agent_id: str, content: str, source: str = "crew_run",
                 entry_type: str = "observation", confidence: str = "med",
                 tags: Optional[list] = None):
    """Add an episodic memory entry for an agent."""
    entries = _load_json(_episodic_path(agent_id))
    entry = create_entry(content, source, entry_type, confidence, tags=tags)
    entries.append(entry)
    # Prune if over limit
    if len(entries) > EPISODIC_MAX_ENTRIES:
        entries = entries[-EPISODIC_MAX_ENTRIES:]
    _save_json(_episodic_path(agent_id), entries)
    return entry


def get_episodic(agent_id: str, limit: int = 20, tags: Optional[list] = None,
                 active_only: bool = True) -> list:
    """Retrieve recent episodic memories, optionally filtered by tags."""
    entries = _load_json(_episodic_path(agent_id))
    if active_only:
        entries = [e for e in entries if e.get("state") == "active"]
    if tags:
        entries = [e for e in entries if any(t in e.get("tags", []) for t in tags)]
    # Mark as accessed
    now = datetime.now().isoformat()
    for e in entries[-limit:]:
        e["access_count"] = e.get("access_count", 0) + 1
        e["last_accessed"] = now
    _save_json(_episodic_path(agent_id), _load_json(_episodic_path(agent_id)))
    return entries[-limit:]


# === Semantic memory (long-term, promoted) ===

def add_semantic(agent_id: str, content: str, entry_type: str = "finding",
                 confidence: str = "high", scope: str = "project",
                 tags: Optional[list] = None, supersedes: Optional[str] = None):
    """Add or promote a memory to semantic (long-term) storage."""
    entries = _load_json(_semantic_path(agent_id))
    # If superseding, mark old entry
    if supersedes:
        for e in entries:
            if supersedes.lower() in e.get("content", "").lower():
                e["state"] = "superseded"
    entry = create_entry(content, "promotion", entry_type, confidence, scope, tags=tags)
    entries.append(entry)
    if len(entries) > SEMANTIC_MAX_ENTRIES:
        # Remove oldest superseded/stale first
        active = [e for e in entries if e["state"] == "active"]
        inactive = [e for e in entries if e["state"] != "active"]
        entries = inactive[-(SEMANTIC_MAX_ENTRIES // 5):] + active[-SEMANTIC_MAX_ENTRIES:]
    _save_json(_semantic_path(agent_id), entries)
    return entry


def get_semantic(agent_id: str, limit: int = 20, tags: Optional[list] = None) -> list:
    """Retrieve semantic memories."""
    entries = _load_json(_semantic_path(agent_id))
    entries = [e for e in entries if e.get("state") == "active"]
    if tags:
        entries = [e for e in entries if any(t in e.get("tags", []) for t in tags)]
    return entries[-limit:]


def search_memory(agent_id: str, query: str, limit: int = 10) -> list:
    """Simple keyword search across both episodic and semantic memory."""
    query_lower = query.lower()
    results = []
    for entry in _load_json(_semantic_path(agent_id)):
        if query_lower in entry.get("content", "").lower():
            results.append({**entry, "_source": "semantic"})
    for entry in _load_json(_episodic_path(agent_id)):
        if query_lower in entry.get("content", "").lower():
            results.append({**entry, "_source": "episodic"})
    # Sort by recency
    results.sort(key=lambda e: e.get("when", ""), reverse=True)
    return results[:limit]


# === Maintenance ===

def decay_episodic(agent_id: str):
    """Mark old episodic entries as stale."""
    entries = _load_json(_episodic_path(agent_id))
    cutoff = (datetime.now() - timedelta(days=EPISODIC_STALE_DAYS)).isoformat()
    changed = False
    for e in entries:
        if e.get("state") == "active" and e.get("when", "") < cutoff:
            e["state"] = "stale"
            changed = True
    if changed:
        _save_json(_episodic_path(agent_id), entries)


def promote_candidates(agent_id: str) -> list:
    """Find episodic entries worth promoting to semantic memory.
    Returns candidates (doesn't auto-promote)."""
    entries = _load_json(_episodic_path(agent_id))
    candidates = []
    for e in entries:
        if e.get("state") != "active":
            continue
        # High confidence + accessed multiple times = promote candidate
        if e.get("confidence") == "high" and e.get("access_count", 0) >= 2:
            candidates.append(e)
        # Decisions and preferences always worth considering
        if e.get("type") in ("decision", "preference", "contact"):
            candidates.append(e)
    # Dedupe
    seen = set()
    unique = []
    for c in candidates:
        if c["id"] not in seen:
            seen.add(c["id"])
            unique.append(c)
    return unique


def get_agent_context(agent_id: str, max_entries: int = 15) -> str:
    """Build a context string for injecting into an agent's system prompt.
    Returns recent episodic + all active semantic as formatted text."""
    lines = []
    semantic = get_semantic(agent_id, limit=max_entries)
    if semantic:
        lines.append("## Long-term Memory")
        for e in semantic:
            tags = " ".join(e.get("tags", []))
            lines.append(f"- [{e.get('type','?')}] {e['content']} ({e.get('confidence','?')} confidence) {tags}")
    episodic = get_episodic(agent_id, limit=max_entries - len(semantic))
    if episodic:
        lines.append("\n## Recent Memory")
        for e in episodic:
            when = e.get("when", "")[:10]
            lines.append(f"- [{when}] {e['content']}")
    return "\n".join(lines) if lines else ""


def get_stats(agent_id: str) -> dict:
    """Get memory stats for an agent."""
    episodic = _load_json(_episodic_path(agent_id))
    semantic = _load_json(_semantic_path(agent_id))
    return {
        "episodic_total": len(episodic),
        "episodic_active": sum(1 for e in episodic if e.get("state") == "active"),
        "episodic_stale": sum(1 for e in episodic if e.get("state") == "stale"),
        "semantic_total": len(semantic),
        "semantic_active": sum(1 for e in semantic if e.get("state") == "active"),
    }
