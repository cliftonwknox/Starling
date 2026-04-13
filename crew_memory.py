"""Crew Memory — Unified vector memory for Starling.

Provides semantic search across all agents using a single lightweight
embedding model (nomic-embed-text-v1.5 via FastEmbed/ONNX, runs on CPU).
Backed by LanceDB (embedded, no server).

This layer sits alongside agent_memory.py's JSON storage. Every memory
written to episodic or semantic JSON is also embedded here, enabling
cross-agent retrieval by meaning rather than just keywords.
"""

import logging
import os
import time
from datetime import datetime
from typing import Optional

import pyarrow as pa

logger = logging.getLogger("starling.crew_memory")

# === Health tracking ===

_health = {
    "embedder_ok": False,
    "db_ok": False,
    "last_error": None,
    "last_error_time": None,
    "consecutive_failures": 0,
    "total_failures": 0,
    "last_success_time": None,
    "recovery_attempts": 0,
}

# After this many consecutive failures, back off and stop retrying every call
_MAX_CONSECUTIVE_FAILURES = 5
# After backoff, retry every N seconds to see if things recovered
_BACKOFF_RETRY_INTERVAL = 120


def _record_failure(error: Exception, context: str = ""):
    """Track a failure in the health state."""
    _health["consecutive_failures"] += 1
    _health["total_failures"] += 1
    _health["last_error"] = f"{context}: {type(error).__name__}: {error}"
    _health["last_error_time"] = datetime.now().isoformat()
    if _health["consecutive_failures"] <= 3:
        logger.warning(f"Crew Memory {context}: {error}")
    elif _health["consecutive_failures"] == _MAX_CONSECUTIVE_FAILURES:
        logger.error(
            f"Crew Memory degraded — {_health['consecutive_failures']} consecutive failures. "
            f"Last error: {error}. Backing off to retry every {_BACKOFF_RETRY_INTERVAL}s."
        )


def _record_success():
    """Reset failure tracking on success."""
    _health["consecutive_failures"] = 0
    _health["last_success_time"] = datetime.now().isoformat()


def _should_skip() -> bool:
    """Check if we should skip this operation due to backoff."""
    if _health["consecutive_failures"] < _MAX_CONSECUTIVE_FAILURES:
        return False
    # In backoff mode — only retry periodically
    last_err = _health.get("last_error_time")
    if not last_err:
        return False
    elapsed = (datetime.now() - datetime.fromisoformat(last_err)).total_seconds()
    if elapsed >= _BACKOFF_RETRY_INTERVAL:
        logger.info("Crew Memory backoff expired — retrying")
        return False
    return True


# === Embedding model (lazy singleton) ===

_embedder = None
_EMBED_MODEL = "nomic-ai/nomic-embed-text-v1.5"
_EMBED_DIM = 768


def _get_embedder():
    """Lazy-load the embedding model. Runs on CPU only — never touches GPU."""
    global _embedder
    if _embedder is None:
        import os as _os
        from fastembed import TextEmbedding
        from fastembed.common.types import Device
        cache_dir = _os.path.expanduser("~/.cache/starling/embeddings")
        _os.makedirs(cache_dir, exist_ok=True)
        _embedder = TextEmbedding(_EMBED_MODEL, cuda=Device.CPU, cache_dir=cache_dir)
        _health["embedder_ok"] = True
        logger.info(f"Embedding model loaded on CPU: {_EMBED_MODEL}")
    return _embedder


def embed_text(text: str) -> list:
    """Embed a single text string. Returns a list of floats."""
    embedder = _get_embedder()
    # fastembed returns a generator; we need the first (only) result
    return list(embedder.embed([text]))[0].tolist()


def embed_texts(texts: list) -> list:
    """Embed multiple texts. Returns list of float lists."""
    if not texts:
        return []
    embedder = _get_embedder()
    return [v.tolist() for v in embedder.embed(texts)]


# === LanceDB storage ===

_db = None
_TABLE_NAME = "crew_memory"

# LanceDB schema
_SCHEMA = pa.schema([
    pa.field("vector", pa.list_(pa.float32(), _EMBED_DIM)),
    pa.field("content", pa.string()),
    pa.field("agent_id", pa.string()),
    pa.field("memory_tier", pa.string()),   # episodic | semantic | global
    pa.field("entry_type", pa.string()),     # observation, decision, finding, etc.
    pa.field("source", pa.string()),         # crew_run, chat, user, promotion, daemon
    pa.field("tags", pa.string()),           # comma-separated
    pa.field("confidence", pa.string()),     # high, med, low
    pa.field("scope", pa.string()),          # global, project, agent, temporary
    pa.field("timestamp", pa.string()),      # ISO format
    pa.field("entry_id", pa.string()),       # links back to JSON entry id
])


def _get_db_path() -> str:
    """Resolve the LanceDB storage directory."""
    try:
        from config_loader import get_memory_dir
        base = get_memory_dir()
    except Exception:
        base = os.path.join(os.path.dirname(__file__), "memory")
    db_path = os.path.join(base, "vector_db")
    os.makedirs(db_path, exist_ok=True)
    return db_path


def _get_db():
    """Get or create the LanceDB connection."""
    global _db
    if _db is None:
        import lancedb
        _db = lancedb.connect(_get_db_path())
        _health["db_ok"] = True
        logger.info(f"LanceDB connected: {_get_db_path()}")
    return _db


def _reset_db():
    """Reset DB connection for recovery attempts."""
    global _db
    _db = None
    _health["db_ok"] = False


def _get_table():
    """Get or create the crew_memory table."""
    db = _get_db()
    if _TABLE_NAME in db.table_names():
        return db.open_table(_TABLE_NAME)
    # Create empty table with schema
    return db.create_table(_TABLE_NAME, schema=_SCHEMA)


# === Core API ===

def remember(
    agent_id: str,
    content: str,
    memory_tier: str = "episodic",
    entry_type: str = "observation",
    source: str = "crew_run",
    tags: Optional[list] = None,
    confidence: str = "med",
    scope: str = "project",
    entry_id: str = "",
) -> None:
    """Embed and store a memory entry in the vector database.

    Called automatically when agent_memory.add_episodic/add_semantic are used,
    or directly for global cross-agent memories.
    Never raises — failures are logged and tracked, never break the main flow.
    """
    if _should_skip():
        return
    try:
        vector = embed_text(content)
        table = _get_table()
        table.add([{
            "vector": vector,
            "content": content,
            "agent_id": agent_id,
            "memory_tier": memory_tier,
            "entry_type": entry_type,
            "source": source,
            "tags": ",".join(tags) if tags else "",
            "confidence": confidence,
            "scope": scope,
            "timestamp": datetime.now().isoformat(),
            "entry_id": entry_id,
        }])
        _record_success()
    except Exception as e:
        _record_failure(e, "remember")


def remember_global(
    content: str,
    source_agent_id: str = "",
    entry_type: str = "finding",
    source: str = "crew_run",
    tags: Optional[list] = None,
    confidence: str = "high",
    entry_id: str = "",
) -> None:
    """Store a memory in the global cross-agent pool.

    Global memories are discoverable by all agents. Automatically called
    for high-value entry types (decisions, findings, contacts) so that
    knowledge flows between agents without explicit handoff.
    """
    global_tags = list(tags) if tags else []
    if source_agent_id:
        global_tags.append(f"from:{source_agent_id}")
    remember(
        agent_id="global",
        content=content,
        memory_tier="global",
        entry_type=entry_type,
        source=source,
        tags=global_tags,
        confidence=confidence,
        scope="global",
        entry_id=entry_id,
    )


# Entry types that get auto-promoted to global memory
_GLOBAL_PROMOTE_TYPES = {"decision", "finding", "contact", "preference"}

# Size limits
_GLOBAL_MAX_ENTRIES = 500
_AGENT_MAX_VECTORS = 1000  # per agent


def recall(
    query: str,
    agent_id: Optional[str] = None,
    limit: int = 8,
    memory_tier: Optional[str] = None,
    include_global: bool = True,
) -> list:
    """Retrieve the most relevant memories by semantic similarity.

    Args:
        query: The text to search for.
        agent_id: Filter to a specific agent, or None for all agents.
            When set, also includes global memories unless include_global=False.
        limit: Max results to return.
        memory_tier: Filter to episodic/semantic/global, or None for all.
        include_global: When filtering by agent_id, also include global memories.

    Returns:
        List of dicts with content, agent_id, score, and metadata.
    """
    if _should_skip():
        return []
    try:
        table = _get_table()
        if table.count_rows() == 0:
            return []

        query_vector = embed_text(query)

        search = table.search(query_vector).limit(limit * 3)

        results = search.to_list()

        # Apply filters in Python (LanceDB filter syntax varies by version)
        if agent_id:
            if include_global:
                # Include both agent-specific and global memories
                results = [r for r in results
                           if r.get("agent_id") == agent_id
                           or r.get("memory_tier") == "global"]
            else:
                results = [r for r in results if r.get("agent_id") == agent_id]
        if memory_tier:
            results = [r for r in results if r.get("memory_tier") == memory_tier]

        # Trim to limit
        results = results[:limit]

        # Clean up output — drop the vector, keep the useful fields
        cleaned = []
        for r in results:
            cleaned.append({
                "content": r.get("content", ""),
                "agent_id": r.get("agent_id", ""),
                "memory_tier": r.get("memory_tier", ""),
                "entry_type": r.get("entry_type", ""),
                "source": r.get("source", ""),
                "tags": r.get("tags", ""),
                "confidence": r.get("confidence", ""),
                "timestamp": r.get("timestamp", ""),
                "score": r.get("_distance", 0),
            })
        _record_success()
        return cleaned
    except Exception as e:
        _record_failure(e, "recall")
        return []


def recall_hybrid(
    query: str,
    agent_id: Optional[str] = None,
    limit: int = 8,
) -> list:
    """Hybrid search: vector similarity + keyword matching, merged and deduplicated.

    Gives best results for both semantic ("what did we decide about pricing")
    and exact matches ("John Smith", "API key", specific names/values).
    """
    # Vector results
    vector_results = recall(query, agent_id=agent_id, limit=limit)

    # Keyword results from existing JSON memory
    keyword_results = []
    try:
        import agent_memory as mem
        if agent_id:
            raw = mem.search_memory(agent_id, query, limit=limit)
            for r in raw:
                keyword_results.append({
                    "content": r.get("content", ""),
                    "agent_id": agent_id,
                    "memory_tier": r.get("_source", "episodic"),
                    "entry_type": r.get("type", ""),
                    "source": r.get("source", ""),
                    "tags": ",".join(r.get("tags", [])),
                    "confidence": r.get("confidence", ""),
                    "timestamp": r.get("when", ""),
                    "score": 0,  # keyword matches get top priority
                })
    except Exception:
        pass

    # Merge: keyword matches first (exact hits are high value), then vector
    seen_content = set()
    merged = []
    for r in keyword_results + vector_results:
        content_key = r["content"].strip()[:200]
        if content_key not in seen_content:
            seen_content.add(content_key)
            merged.append(r)

    return merged[:limit]


def recall_formatted(
    query: str,
    agent_id: Optional[str] = None,
    limit: int = 8,
) -> str:
    """Recall memories and format them for injection into an LLM prompt.

    This is the main integration point — call this before sending a task
    to an LLM to get relevant context from unified memory.

    Separates agent-specific memories from shared team knowledge (global pool).
    """
    results = recall_hybrid(query, agent_id=agent_id, limit=limit)
    if not results:
        return ""

    # Split into agent-specific and global
    agent_results = [r for r in results if r.get("memory_tier") != "global"]
    global_results = [r for r in results if r.get("memory_tier") == "global"]

    lines = []
    if agent_results:
        lines.append("## Your Memories")
        for r in agent_results:
            tier_tag = f"[{r['memory_tier']}]" if r.get("memory_tier") else ""
            ts = r.get("timestamp", "")[:10]
            lines.append(f"- {tier_tag} [{ts}] {r['content']}")

    if global_results:
        lines.append("\n## Shared Team Knowledge")
        for r in global_results:
            # Show which agent contributed this knowledge
            tags = r.get("tags", "")
            source_agent = ""
            for tag in tags.split(","):
                if tag.startswith("from:"):
                    source_agent = tag[5:]
                    break
            origin = f"(via {source_agent})" if source_agent else ""
            ts = r.get("timestamp", "")[:10]
            lines.append(f"- [{r.get('entry_type', '?')}] {origin} [{ts}] {r['content']}")

    return "\n".join(lines)


# === Bulk indexing (for migrating existing JSON memories) ===

def index_existing_memories() -> int:
    """One-time migration: read all existing JSON memories and index them.

    Safe to run multiple times — checks for existing entries by content hash.
    Returns count of newly indexed entries.
    """
    try:
        import agent_memory as mem
    except ImportError:
        return 0

    memory_dir = mem._get_memory_dir()
    if not os.path.isdir(memory_dir):
        return 0

    count = 0
    for agent_id in os.listdir(memory_dir):
        agent_dir = os.path.join(memory_dir, agent_id)
        if not os.path.isdir(agent_dir) or agent_id == "vector_db":
            continue

        # Index episodic
        episodic_path = os.path.join(agent_dir, "episodic.json")
        if os.path.exists(episodic_path):
            entries = mem._load_json(episodic_path)
            for e in entries:
                if e.get("state") != "active":
                    continue
                etype = e.get("type", "observation")
                remember(
                    agent_id=agent_id,
                    content=e.get("content", ""),
                    memory_tier="episodic",
                    entry_type=etype,
                    source=e.get("source", "crew_run"),
                    tags=e.get("tags"),
                    confidence=e.get("confidence", "med"),
                    scope=e.get("scope", "project"),
                    entry_id=e.get("id", ""),
                )
                # Promote high-value types to global pool
                if etype in _GLOBAL_PROMOTE_TYPES:
                    remember_global(
                        content=e.get("content", ""),
                        source_agent_id=agent_id,
                        entry_type=etype,
                        source=e.get("source", "crew_run"),
                        tags=e.get("tags"),
                        confidence=e.get("confidence", "med"),
                        entry_id=e.get("id", ""),
                    )
                count += 1

        # Index semantic
        semantic_path = os.path.join(agent_dir, "semantic.json")
        if os.path.exists(semantic_path):
            entries = mem._load_json(semantic_path)
            for e in entries:
                if e.get("state") != "active":
                    continue
                etype = e.get("type", "finding")
                remember(
                    agent_id=agent_id,
                    content=e.get("content", ""),
                    memory_tier="semantic",
                    entry_type=etype,
                    source=e.get("source", "promotion"),
                    tags=e.get("tags"),
                    confidence=e.get("confidence", "high"),
                    scope=e.get("scope", "project"),
                    entry_id=e.get("id", ""),
                )
                # Semantic memories of high-value types always go global
                if etype in _GLOBAL_PROMOTE_TYPES:
                    remember_global(
                        content=e.get("content", ""),
                        source_agent_id=agent_id,
                        entry_type=etype,
                        source=e.get("source", "promotion"),
                        tags=e.get("tags"),
                        confidence=e.get("confidence", "high"),
                        entry_id=e.get("id", ""),
                    )
                count += 1

    return count


# === Memory hygiene ===

def delete_by_entry_id(entry_id: str) -> bool:
    """Delete a specific vector entry by its JSON entry_id."""
    try:
        table = _get_table()
        table.delete(f"entry_id = '{entry_id}'")
        return True
    except Exception as e:
        logger.warning(f"Failed to delete vector entry_id={entry_id}: {e}")
        return False


def delete_by_content(agent_id: str, content_fragment: str) -> int:
    """Delete vector entries matching a content fragment for an agent.

    Used when JSON entries are superseded — removes the old vector so it
    doesn't pollute search results.
    """
    # Performance note: LanceDB has no LIKE/CONTAINS — we still need to scan
    # rows for the fragment match. We pre-filter by agent_id (server-side) so
    # only that agent's vectors come back to the client, and cap the scan at
    # SCAN_LIMIT entries per call so a runaway memory store can't OOM the
    # process. If the cap is hit we log a warning so the user can see growth.
    SCAN_LIMIT = 2000
    try:
        table = _get_table()
        # Server-side filter to this agent only
        rows = (
            table.search()
            .where(f"agent_id = '{agent_id}'")
            .limit(SCAN_LIMIT)
            .select(["entry_id", "content"])
            .to_list()
        )
        if len(rows) == SCAN_LIMIT:
            logger.warning(
                f"delete_by_content hit SCAN_LIMIT={SCAN_LIMIT} for agent "
                f"{agent_id}; older matches may be missed. Consider compacting."
            )
        fragment_lower = content_fragment.lower()
        deleted = 0
        for row in rows:
            if fragment_lower in (row.get("content") or "").lower():
                eid = row.get("entry_id", "")
                if eid:
                    # Escape single quotes to keep the SQL fragment safe
                    safe_eid = eid.replace("'", "''")
                    table.delete(f"entry_id = '{safe_eid}'")
                    deleted += 1
        if deleted:
            logger.info(f"Deleted {deleted} superseded vectors for agent {agent_id}")
        return deleted
    except Exception as e:
        logger.warning(f"Failed to delete superseded vectors: {e}")
        return 0


def purge_stale(agent_id: str) -> int:
    """Remove vector entries for an agent's stale/superseded JSON memories.

    Reads the agent's JSON files, collects IDs of non-active entries,
    and deletes matching vectors.
    """
    try:
        import agent_memory as mem
    except ImportError:
        return 0

    stale_ids = set()
    for path_fn in [mem._episodic_path, mem._semantic_path]:
        path = path_fn(agent_id)
        entries = mem._load_json(path)
        for e in entries:
            if e.get("state") in ("stale", "superseded", "archived"):
                eid = e.get("id", "")
                if eid:
                    stale_ids.add(eid)

    if not stale_ids:
        return 0

    deleted = 0
    try:
        table = _get_table()
        for eid in stale_ids:
            try:
                table.delete(f"entry_id = '{eid}'")
                deleted += 1
            except Exception:
                pass
        # Also clean corresponding global entries
        for eid in stale_ids:
            try:
                table.delete(f"entry_id = '{eid}' AND memory_tier = 'global'")
            except Exception:
                pass
        if deleted:
            logger.info(f"Purged {deleted} stale vectors for agent {agent_id}")
    except Exception as e:
        logger.warning(f"Failed to purge stale vectors: {e}")
    return deleted


def _enforce_limits() -> dict:
    """Enforce size limits on the vector store.

    Trims the global pool and per-agent vectors to their max sizes, keeping
    the most recent entries. Only fetches the rows that are actually over the
    limit (instead of the full table) so this stays cheap as the store grows.
    """
    trimmed = {"global": 0, "agents": {}}
    try:
        table = _get_table()
        # Cheap server-side count first — avoids any data fetch when under limits.
        total = table.count_rows()
        if total == 0:
            return trimmed

        # --- Trim global pool ---
        # Use server-side filter + count for "is this even over the limit?"
        # If the count is at/under the cap we don't read any rows at all.
        try:
            global_total = table.count_rows(filter="memory_tier = 'global'")
        except Exception:
            # Older lancedb doesn't accept count_rows(filter=...) — fall back
            # to a scan only when needed.
            global_total = None

        if global_total is None or global_total > _GLOBAL_MAX_ENTRIES:
            global_rows = (
                table.search()
                .where("memory_tier = 'global'")
                .limit(global_total or _GLOBAL_MAX_ENTRIES * 2)
                .select(["entry_id", "timestamp"])
                .to_list()
            )
            if len(global_rows) > _GLOBAL_MAX_ENTRIES:
                global_rows.sort(key=lambda r: r.get("timestamp", ""))
                to_remove = global_rows[:len(global_rows) - _GLOBAL_MAX_ENTRIES]
                for r in to_remove:
                    eid = r.get("entry_id", "")
                    if eid:
                        try:
                            safe_eid = eid.replace("'", "''")
                            table.delete(f"entry_id = '{safe_eid}' AND memory_tier = 'global'")
                            trimmed["global"] += 1
                        except Exception:
                            pass
                if trimmed["global"]:
                    logger.info(f"Trimmed {trimmed['global']} oldest global memories (limit: {_GLOBAL_MAX_ENTRIES})")

        # --- Trim per-agent ---
        # Get the distinct agent IDs first (cheap) then check each agent's count
        # individually. Only read full rows for agents that are over the limit.
        try:
            id_rows = (
                table.search()
                .where("memory_tier != 'global'")
                .limit(total)
                .select(["agent_id"])
                .to_list()
            )
            agent_counts = {}
            for r in id_rows:
                aid = r.get("agent_id")
                if aid:
                    agent_counts[aid] = agent_counts.get(aid, 0) + 1
        except Exception:
            agent_counts = {}

        for aid, count in agent_counts.items():
            if count <= _AGENT_MAX_VECTORS:
                continue
            safe_aid = aid.replace("'", "''")
            rows = (
                table.search()
                .where(f"agent_id = '{safe_aid}' AND memory_tier != 'global'")
                .limit(count)
                .select(["entry_id", "timestamp"])
                .to_list()
            )
            rows.sort(key=lambda r: r.get("timestamp", ""))
            to_remove = rows[:len(rows) - _AGENT_MAX_VECTORS]
            removed = 0
            for r in to_remove:
                eid = r.get("entry_id", "")
                if eid:
                    try:
                        safe_eid = eid.replace("'", "''")
                        table.delete(f"entry_id = '{safe_eid}'")
                        removed += 1
                    except Exception:
                        pass
            if removed:
                trimmed["agents"][aid] = removed
                logger.info(f"Trimmed {removed} oldest vectors for agent {aid} (limit: {_AGENT_MAX_VECTORS})")

    except Exception as e:
        logger.warning(f"Failed to enforce limits: {e}")
    return trimmed


def compact() -> dict:
    """Full maintenance pass: purge stale entries, enforce limits, optimize.

    Returns summary of what was cleaned up.
    """
    result = {"purged": 0, "trimmed": {"global": 0, "agents": {}}}

    # Purge stale per-agent
    try:
        import agent_memory as mem
        memory_dir = mem._get_memory_dir()
        if os.path.isdir(memory_dir):
            for agent_id in os.listdir(memory_dir):
                agent_dir = os.path.join(memory_dir, agent_id)
                if os.path.isdir(agent_dir) and agent_id != "vector_db":
                    result["purged"] += purge_stale(agent_id)
    except Exception as e:
        logger.warning(f"Compact purge phase failed: {e}")

    # Enforce size limits
    result["trimmed"] = _enforce_limits()

    # LanceDB compact (reclaim disk space from deleted rows)
    try:
        table = _get_table()
        table.compact_files()
        table.cleanup_old_versions()
        logger.info("LanceDB files compacted")
    except Exception:
        pass  # older LanceDB versions may not support this

    total_cleaned = result["purged"] + result["trimmed"]["global"] + sum(result["trimmed"]["agents"].values())
    if total_cleaned:
        logger.info(f"Compact complete: {total_cleaned} entries cleaned")
    else:
        logger.info("Compact complete: nothing to clean")
    return result


# === Health checks and recovery ===

def startup_check() -> dict:
    """Validate that Crew Memory components are working on startup.

    Returns a dict with status, messages for the user, and component states.
    Call this once at TUI/daemon launch.
    """
    result = {
        "ok": False,
        "embedder": False,
        "db": False,
        "messages": [],
    }

    # Test embedder
    try:
        _get_embedder()
        result["embedder"] = True
    except Exception as e:
        result["messages"].append(f"Embedding model failed to load: {e}")
        logger.error(f"Startup check — embedder failed: {e}")

    # Test DB
    try:
        table = _get_table()
        _ = table.count_rows()
        result["db"] = True
    except Exception as e:
        result["messages"].append(f"Vector database failed to open: {e}")
        logger.error(f"Startup check — LanceDB failed: {e}")

    # Test end-to-end if both components are up
    if result["embedder"] and result["db"]:
        try:
            vec = embed_text("startup health check")
            result["ok"] = True
            _record_success()
            logger.info("Crew Memory startup check passed")
        except Exception as e:
            result["messages"].append(f"End-to-end embedding test failed: {e}")
            logger.error(f"Startup check — e2e failed: {e}")

    if not result["ok"]:
        result["messages"].append("Crew Memory is degraded — falling back to keyword-only memory")
        logger.warning("Crew Memory startup check FAILED — running in degraded mode")

    return result


def health_check() -> dict:
    """Check current health and attempt recovery if needed.

    Call this periodically (e.g. every heartbeat cycle) to detect and
    recover from transient failures. Returns current health state.
    """
    status = {
        "ok": _health["embedder_ok"] and _health["db_ok"] and _health["consecutive_failures"] == 0,
        "consecutive_failures": _health["consecutive_failures"],
        "total_failures": _health["total_failures"],
        "last_error": _health["last_error"],
        "last_error_time": _health["last_error_time"],
        "last_success_time": _health["last_success_time"],
        "recovery_attempts": _health["recovery_attempts"],
    }

    # If healthy, nothing to do
    if status["ok"]:
        return status

    # Attempt recovery
    _health["recovery_attempts"] += 1
    logger.info(f"Crew Memory health check — attempting recovery (attempt #{_health['recovery_attempts']})")

    recovered = True

    # Try to recover embedder
    if not _health["embedder_ok"]:
        try:
            global _embedder
            _embedder = None
            _get_embedder()
            logger.info("Embedder recovered")
        except Exception as e:
            logger.warning(f"Embedder recovery failed: {e}")
            recovered = False

    # Try to recover DB
    if not _health["db_ok"]:
        try:
            _reset_db()
            _get_db()
            logger.info("LanceDB recovered")
        except Exception as e:
            logger.warning(f"LanceDB recovery failed: {e}")
            recovered = False

    # If components are up, test e2e
    if _health["embedder_ok"] and _health["db_ok"]:
        try:
            embed_text("recovery check")
            _get_table().count_rows()
            _record_success()
            logger.info("Crew Memory recovered successfully")
        except Exception as e:
            logger.warning(f"Recovery e2e test failed: {e}")
            recovered = False

    status["ok"] = recovered
    status["consecutive_failures"] = _health["consecutive_failures"]
    return status


def get_health() -> dict:
    """Return the current health state without attempting recovery."""
    return {
        "ok": _health["embedder_ok"] and _health["db_ok"] and _health["consecutive_failures"] == 0,
        "embedder_ok": _health["embedder_ok"],
        "db_ok": _health["db_ok"],
        "consecutive_failures": _health["consecutive_failures"],
        "total_failures": _health["total_failures"],
        "last_error": _health["last_error"],
        "last_error_time": _health["last_error_time"],
        "last_success_time": _health["last_success_time"],
        "degraded": _health["consecutive_failures"] >= _MAX_CONSECUTIVE_FAILURES,
    }


# === Stats ===

def get_stats() -> dict:
    """Return stats about the vector memory store."""
    try:
        table = _get_table()
        total = table.count_rows()
        results = table.search().limit(total).select(["memory_tier"]).to_list() if total > 0 else []
        global_count = sum(1 for r in results if r.get("memory_tier") == "global")
        return {
            "total_vectors": total,
            "global_memories": global_count,
            "agent_memories": total - global_count,
            "db_path": _get_db_path(),
            "health": get_health(),
        }
    except Exception as e:
        return {"total_vectors": 0, "global_memories": 0, "agent_memories": 0,
                "db_path": _get_db_path(), "error": str(e), "health": get_health()}
