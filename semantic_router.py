"""Semantic Router — Skill-based task routing for Starling.

Embeds each agent's purpose (role + goal) as a skill vector in LanceDB.
When a task needs routing, embeds the description and finds the closest
agent by cosine similarity. Falls back gracefully if unavailable.

Uses its own dedicated embedding model (all-MiniLM-L6-v2 via FastEmbed/ONNX)
running natively in RAM on CPU. Fully independent of crew_memory and LM Studio.
Shares the same LanceDB instance but in a separate table.
"""

import hashlib
import json
import logging
import os
from collections import OrderedDict
from datetime import datetime
from typing import Optional

import pyarrow as pa

logger = logging.getLogger("starling.semantic_router")

# === Pre-built agent templates (from Grok conversation 2026-04-11) ===

AGENT_TEMPLATES = {
    "leader": {
        "name": "Leader / CEO",
        "role": "Chief Executive",
        "goal": "Set goals, delegate tasks to the right team members, monitor progress, and deliver a final report when the work is done",
        "backstory": "Strategic leader who breaks big objectives into actionable plans, "
                     "delegates to specialists, and keeps the crew focused on the goal. "
                     "Talks to the user, decides what needs doing, tracks progress, wraps up.",
        "primary_purpose": "Expert at setting goals, delegating work, and coordinating a team.",
        "secondary_purposes": [
            "Progress monitoring and reporting",
            "Strategic planning and prioritization",
        ],
        "tools": [
            "crewai:FileReadTool",
            "crewai:DirectoryReadTool",
            "crewai:DirectorySearchTool",
            "crewai:PDFSearchTool",
            "crewai:CSVSearchTool",
            "crewai:JSONSearchTool",
            "cron_tool",
        ],
        "color": "cyan",
        "tier": "leader",
    },
    "coordinator": {
        "name": "Team Coordinator",
        "role": "Team Coordinator",
        "goal": "Receive objectives from the Leader, break them into concrete tasks, schedule or delegate them to the right specialist, and report results back",
        "backstory": "Experienced coordinator who turns plans into scheduled work. "
                     "Owns a sub-team of specialists, knows their strengths, and routes "
                     "tasks accordingly. Schedules cron jobs for recurring work.",
        "primary_purpose": "Expert at breaking down tasks and delegating to the right team member.",
        "secondary_purposes": [
            "Task scheduling and cron management",
            "Team capacity tracking",
        ],
        "tools": [
            "crewai:FileReadTool",
            "crewai:FileWriterTool",
            "crewai:DirectoryReadTool",
            "cron_tool",
        ],
        "color": "blue",
        "tier": "coordinator",
    },
    "researcher": {
        "name": "Researcher",
        "role": "Senior Research Analyst",
        "goal": "Find accurate information and create clear, well-sourced summaries",
        "backstory": "Detail-oriented researcher with deep experience in web research, "
                     "document analysis, and source evaluation",
        "primary_purpose": "Expert at finding accurate information and creating clear summaries.",
        "secondary_purposes": [
            "Deep web searching",
            "Document analysis",
        ],
        "tools": [
            "ddg_search", "tavily_search", "scrape_website",
            "crewai:FileReadTool", "crewai:DirectoryReadTool",
            "crewai:PDFSearchTool", "crewai:DOCXSearchTool",
            "crewai:TXTSearchTool", "crewai:WebsiteSearchTool",
        ],
        "color": "green",
        "tier": "specialist",
    },
    "content_writer": {
        "name": "Content Writer",
        "role": "Senior Content Writer",
        "goal": "Create engaging, well-structured written content",
        "backstory": "Experienced writer skilled at adapting tone for different audiences "
                     "and synthesizing research into compelling narratives",
        "primary_purpose": "Expert at writing engaging, well-structured content.",
        "secondary_purposes": [
            "Editing and improving existing text",
            "Adapting tone for different audiences",
        ],
        "tools": [
            "crewai:FileReadTool", "crewai:FileWriterTool",
            "crewai:DirectoryReadTool",
        ],
        "color": "magenta",
        "tier": "specialist",
    },
    "data_analyst": {
        "name": "Data Analyst",
        "role": "Data Analyst",
        "goal": "Analyze data and turn numbers into actionable insights",
        "backstory": "Analytical thinker with strong skills in data processing, "
                     "statistical analysis, and trend detection",
        "primary_purpose": "Expert at analyzing data and turning numbers into actionable insights.",
        "secondary_purposes": [
            "Report writing",
            "Trend detection",
        ],
        "tools": [
            "crewai:FileReadTool", "crewai:FileWriterTool",
            "crewai:CSVSearchTool", "crewai:JSONSearchTool",
            "crewai:DirectoryReadTool",
        ],
        "color": "cyan",
        "tier": "specialist",
    },
    "project_planner": {
        "name": "Project Planner",
        "role": "Project Planner",
        "goal": "Break down complex goals into clear step-by-step plans",
        "backstory": "Strategic planner skilled at task decomposition, prioritization, "
                     "and timeline management",
        "primary_purpose": "Expert at breaking big goals into clear step-by-step plans.",
        "secondary_purposes": [
            "Risk assessment",
            "Timeline management",
        ],
        "tools": [
            "crewai:FileReadTool", "crewai:FileWriterTool",
            "crewai:DirectoryReadTool",
        ],
        "color": "blue",
        "tier": "specialist",
    },
    "software_engineer": {
        "name": "Software Engineer",
        "role": "Software Engineer",
        "goal": "Write clean, efficient code and fix bugs",
        "backstory": "Experienced developer with strong debugging skills and a focus "
                     "on clean, maintainable code",
        "primary_purpose": "Expert at writing clean code and fixing bugs.",
        "secondary_purposes": [
            "Code review",
            "Explaining technical concepts simply",
        ],
        "tools": [
            "crewai:FileReadTool", "crewai:FileWriterTool",
            "crewai:DirectoryReadTool", "crewai:DirectorySearchTool",
            "crewai:CodeInterpreterTool",
        ],
        "color": "yellow",
        "tier": "specialist",
    },
    "customer_support": {
        "name": "Customer Support",
        "role": "Customer Support Specialist",
        "goal": "Solve customer problems quickly and professionally",
        "backstory": "Patient and thorough support specialist with strong product knowledge "
                     "and communication skills",
        "primary_purpose": "Expert at solving customer problems quickly and professionally.",
        "secondary_purposes": [
            "Handling complaints",
            "Product knowledge",
        ],
        "tools": [
            "ddg_search", "crewai:FileReadTool",
            "crewai:DirectoryReadTool",
        ],
        "color": "red",
        "tier": "specialist",
    },
}


# === Skill tier system ===

TIER_ORDER = {"specialist": 1, "coordinator": 2, "leader": 3}
VALID_TIERS = {"specialist", "coordinator", "leader"}


def agent_has_tier(agent: dict, required_tier: str) -> bool:
    """Check if an agent's tier meets or exceeds a required tier.

    Example: a leader (tier 3) satisfies a coordinator requirement (tier 2).
    A specialist (tier 1) does not satisfy coordinator (tier 2).

    Logs a warning if the agent's tier or the required_tier is unknown,
    so typos/refactoring bugs surface instead of silently returning False.
    """
    agent_tier = agent.get("tier", "specialist")
    if agent_tier not in VALID_TIERS:
        logger.warning(
            f"Agent '{agent.get('id', '?')}' has unknown tier '{agent_tier}' — "
            f"treating as insufficient permission"
        )
        return False
    if required_tier not in VALID_TIERS:
        logger.warning(
            f"Unknown required_tier '{required_tier}' — valid tiers: "
            f"{sorted(VALID_TIERS)}"
        )
        return False
    return TIER_ORDER[agent_tier] >= TIER_ORDER[required_tier]


def get_template(template_id: str) -> Optional[dict]:
    """Get an agent template by ID. Returns None if not found."""
    return AGENT_TEMPLATES.get(template_id)


def list_templates() -> list:
    """List all available template IDs and names."""
    return [(tid, t["name"]) for tid, t in AGENT_TEMPLATES.items()]


# === Embedding model (lazy singleton, runs on CPU in RAM) ===

_ROUTING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
_EMBED_DIM = 384
_routing_embedder = None


def _get_embedder():
    """Lazy-load the routing embedding model. Runs on CPU only — never touches GPU."""
    global _routing_embedder
    if _routing_embedder is None:
        import os as _os
        from fastembed import TextEmbedding
        from fastembed.common.types import Device
        cache_dir = _os.path.expanduser("~/.cache/starling/embeddings")
        _os.makedirs(cache_dir, exist_ok=True)
        _routing_embedder = TextEmbedding(_ROUTING_MODEL, cuda=Device.CPU, cache_dir=cache_dir)
        logger.info(f"Routing embedding model loaded on CPU: {_ROUTING_MODEL}")
    return _routing_embedder


def _embed_text(text: str) -> list:
    """Embed a single text string. Returns a list of floats."""
    embedder = _get_embedder()
    return list(embedder.embed([text]))[0].tolist()


def _embed_texts(texts: list) -> list:
    """Embed multiple texts. Returns list of float lists."""
    if not texts:
        return []
    embedder = _get_embedder()
    return [v.tolist() for v in embedder.embed(texts)]


# === Route cache (LRU) ===
# Caches full routing results so recurring/cron tasks skip both embed + search.
# Invalidated on rebuild.

_ROUTE_CACHE_MAX = 128
_route_cache: OrderedDict = OrderedDict()  # key: description str, value: (agent_id | None)


def _cache_get(description: str) -> Optional[tuple]:
    """Get cached routing result. Returns (agent_id_or_none,) or None if miss."""
    if description in _route_cache:
        _route_cache.move_to_end(description)
        return (_route_cache[description],)
    return None


def _cache_put(description: str, agent_id: Optional[str]):
    """Store routing result in cache."""
    _route_cache[description] = agent_id
    _route_cache.move_to_end(description)
    if len(_route_cache) > _ROUTE_CACHE_MAX:
        _route_cache.popitem(last=False)


def _cache_clear():
    """Invalidate all cached routing results."""
    _route_cache.clear()


# === Schema ===

_TABLE_NAME = "agent_skills"

_ROUTING_SCHEMA = pa.schema([
    pa.field("vector", pa.list_(pa.float32(), _EMBED_DIM)),
    pa.field("agent_id", pa.string()),
    pa.field("skill_text", pa.string()),
    pa.field("skill_type", pa.string()),   # "primary" | "secondary"
    pa.field("timestamp", pa.string()),
])


# === Config hash ===

def _compute_agents_hash(agents: list) -> str:
    """Hash agent id+role+goal fields + model name. Changes trigger re-embed."""
    parts = [f"model:{_ROUTING_MODEL}"]
    for a in sorted(agents, key=lambda x: x["id"]):
        parts.append(f"{a['id']}:{a.get('role', '')}:{a.get('goal', '')}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


# === Metadata persistence ===

def _get_meta_path() -> str:
    """Resolve path to routing_meta.json."""
    try:
        from config_loader import get_memory_dir
        return os.path.join(get_memory_dir(), "routing_meta.json")
    except Exception:
        return os.path.join(os.path.dirname(__file__), "memory", "routing_meta.json")


def _load_meta() -> dict:
    """Load routing metadata, or empty dict if missing."""
    path = _get_meta_path()
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def _save_meta(data: dict):
    """Write routing metadata to disk."""
    path = _get_meta_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# === LanceDB access (lazy) ===

_db = None
_db_path = None  # Track the resolved path so we can reconnect if it changes


def _get_db():
    """Get a process-singleton LanceDB connection.

    Cached at module scope so repeated routing calls don't reopen the on-disk
    database every time (mirrors crew_memory._get_db). If the resolved path
    ever changes (e.g. work dir moved mid-process), we transparently reconnect.
    """
    global _db, _db_path
    import lancedb
    try:
        from config_loader import get_memory_dir
        db_path = os.path.join(get_memory_dir(), "vector_db")
    except Exception:
        db_path = os.path.join(os.path.dirname(__file__), "memory", "vector_db")
    os.makedirs(db_path, exist_ok=True)
    if _db is None or _db_path != db_path:
        _db = lancedb.connect(db_path)
        _db_path = db_path
    return _db


def _get_table():
    """Get or create the agent_skills table."""
    db = _get_db()
    if _TABLE_NAME in db.table_names():
        return db.open_table(_TABLE_NAME)
    return db.create_table(_TABLE_NAME, schema=_ROUTING_SCHEMA)


def _drop_table():
    """Drop the agent_skills table if it exists."""
    db = _get_db()
    if _TABLE_NAME in db.table_names():
        db.drop_table(_TABLE_NAME)


# === Public API ===

def ensure_skill_vectors(force: bool = False) -> bool:
    """Embed agent skills if config changed. Returns True if re-embedded.

    Called on startup (heartbeat/daemon init) and by /routing rebuild.
    """
    try:
        from config_loader import get_agents
        agents = get_agents()
    except Exception as e:
        logger.warning(f"Cannot load agents for routing: {e}")
        return False

    if not agents:
        logger.info("No agents configured — skipping skill vector embedding")
        return False

    current_hash = _compute_agents_hash(agents)
    meta = _load_meta()

    if not force and meta.get("agents_hash") == current_hash:
        logger.debug("Agent config unchanged — skill vectors up to date")
        return False

    # Build skill texts: primary (role.goal) + secondary (from template if set)
    now = datetime.now().isoformat()
    skill_entries = []  # list of (agent_id, skill_text, skill_type)

    for a in agents:
        # Primary skill from role + goal
        primary = f"{a.get('role', '')}. {a.get('goal', '')}"
        skill_entries.append((a["id"], primary, "primary"))

        # Secondary skills from template (if agent was created from one)
        tmpl_id = a.get("template")
        if tmpl_id and tmpl_id in AGENT_TEMPLATES:
            for sec in AGENT_TEMPLATES[tmpl_id].get("secondary_purposes", []):
                skill_entries.append((a["id"], sec, "secondary"))

    all_texts = [entry[1] for entry in skill_entries]

    try:
        vectors = _embed_texts(all_texts)
    except Exception as e:
        logger.error(f"Failed to embed skill vectors: {e}")
        return False

    # Rebuild table
    try:
        _drop_table()
        db = _get_db()
        rows = []
        for (agent_id, text, skill_type), vector in zip(skill_entries, vectors):
            rows.append({
                "vector": vector,
                "agent_id": agent_id,
                "skill_text": text,
                "skill_type": skill_type,
                "timestamp": now,
            })
        db.create_table(_TABLE_NAME, data=rows, schema=_ROUTING_SCHEMA)
    except Exception as e:
        logger.error(f"Failed to write skill vectors to LanceDB: {e}")
        return False

    # Save metadata
    _save_meta({
        "agents_hash": current_hash,
        "last_embed_time": now,
        "embedding_model": _ROUTING_MODEL,
        "agent_count": len(agents),
    })

    _cache_clear()
    logger.info(f"Embedded skill vectors for {len(agents)} agents")
    return True


def semantic_route(description: str, threshold: float = 0.65) -> Optional[str]:
    """Find the best agent for a task description by semantic similarity.

    Args:
        description: The task description text.
        threshold: Maximum cosine distance to accept (lower = more similar).
            Tuned for all-MiniLM-L6-v2 cosine distance.

    Returns agent_id of best match, or None if no agent scores below threshold.
    """
    # Check cache first (recurring tasks, crons hit this)
    cached = _cache_get(description)
    if cached is not None:
        logger.debug(f"Semantic route cache hit: '{description[:40]}' -> {cached[0]}")
        return cached[0]

    try:
        query_vector = _embed_text(description)
    except Exception as e:
        logger.debug(f"Semantic routing unavailable (embedding failed): {e}")
        return None

    try:
        table = _get_table()
        if table.count_rows() == 0:
            return None

        results = table.search(query_vector).metric("cosine").limit(1).to_list()
        if not results:
            return None

        best = results[0]
        distance = best.get("_distance", float("inf"))

        if distance <= threshold:
            agent_id = best.get("agent_id", "")
            logger.debug(
                f"Semantic route: '{description[:60]}' -> {agent_id} "
                f"(distance={distance:.4f})"
            )
            _cache_put(description, agent_id)
            return agent_id

        logger.debug(
            f"Semantic route: '{description[:60]}' no match — distance "
            f"{distance:.4f} exceeds threshold {threshold} "
            f"(best={best.get('agent_id')})"
        )
        _cache_put(description, None)
        return None

    except Exception as e:
        logger.debug(f"Semantic routing query failed: {e}")
        return None


def get_routing_info() -> dict:
    """Return current routing state for status displays."""
    meta = _load_meta()

    # Determine mode
    mode = "unavailable"
    if meta.get("agents_hash"):
        try:
            table = _get_table()
            if table.count_rows() > 0:
                mode = "semantic"
            else:
                mode = "keywords_only"
        except Exception:
            mode = "keywords_only"

    return {
        "mode": mode,
        "agent_count": meta.get("agent_count", 0),
        "last_embed_time": meta.get("last_embed_time"),
        "embedding_model": meta.get("embedding_model", ""),
        "cache_size": len(_route_cache),
        "cache_max": _ROUTE_CACHE_MAX,
    }


def rebuild():
    """Force re-embed all agent skills."""
    return ensure_skill_vectors(force=True)


# === Duplicate Work Detection (Roadmap Item 9) ===
#
# Tracks recent completed task descriptions as embeddings.
# Before a new task runs, checks if a near-duplicate was already completed.
# Returns the duplicate info so the caller can decide what to do.

_DEDUP_TABLE = "recent_tasks"
_DEDUP_MAX_ENTRIES = 200  # keep last N completed task embeddings

_DEDUP_SCHEMA = pa.schema([
    pa.field("vector", pa.list_(pa.float32(), _EMBED_DIM)),
    pa.field("task_id", pa.string()),
    pa.field("description", pa.string()),
    pa.field("agent_id", pa.string()),
    pa.field("completed", pa.string()),  # ISO timestamp
])


def _get_dedup_table():
    """Get or create the recent_tasks dedup table."""
    db = _get_db()
    if _DEDUP_TABLE in db.table_names():
        return db.open_table(_DEDUP_TABLE)
    return db.create_table(_DEDUP_TABLE, schema=_DEDUP_SCHEMA)


def record_completed_task(task_id: str, description: str, agent_id: str, completed: str):
    """Record a completed task's description embedding for future dedup checks.

    Called after a task finishes successfully.
    """
    try:
        vector = _embed_text(description)
        table = _get_dedup_table()
        table.add([{
            "vector": vector,
            "task_id": task_id,
            "description": description,
            "agent_id": agent_id,
            "completed": completed,
        }])
        # Enforce size limit
        if table.count_rows() > _DEDUP_MAX_ENTRIES:
            _trim_dedup_table(table)
        logger.debug(f"Recorded completed task for dedup: {task_id}")
    except Exception as e:
        logger.debug(f"Failed to record task for dedup: {e}")


def _trim_dedup_table(table):
    """Remove oldest entries to stay within size limit.

    Avoids `to_pandas()` (which would pull pandas as an implicit runtime
    dependency for what is otherwise a lightweight module). We delete the
    oldest entries by `completed` timestamp using LanceDB's native API.
    """
    try:
        # Use the cheap server-side count first.
        try:
            current = table.count_rows()
        except Exception:
            current = None
        if current is not None and current <= _DEDUP_MAX_ENTRIES:
            return

        # Pull only the columns needed for trimming.
        rows = (
            table.search()
            .limit(current or _DEDUP_MAX_ENTRIES * 4)
            .select(["task_id", "completed"])
            .to_list()
        )
        if len(rows) <= _DEDUP_MAX_ENTRIES:
            return
        rows.sort(key=lambda r: r.get("completed", ""))  # oldest first
        to_remove = rows[:len(rows) - _DEDUP_MAX_ENTRIES]
        for r in to_remove:
            tid = r.get("task_id", "")
            if not tid:
                continue
            safe_tid = tid.replace("'", "''")
            try:
                table.delete(f"task_id = '{safe_tid}'")
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"Failed to trim dedup table: {e}")


def check_duplicate(description: str, threshold: float = 0.15) -> Optional[dict]:
    """Check if a task description is near-duplicate of a recently completed task.

    Args:
        description: The new task description to check.
        threshold: Maximum cosine distance to consider a duplicate.
            0.15 is very tight — only catches near-identical descriptions.

    Returns:
        Dict with duplicate info if found: {task_id, description, agent_id, completed, distance}
        None if no duplicate detected.
    """
    try:
        table = _get_dedup_table()
        if table.count_rows() == 0:
            return None

        vector = _embed_text(description)
        results = table.search(vector).metric("cosine").limit(1).to_list()

        if not results:
            return None

        best = results[0]
        distance = best.get("_distance", float("inf"))

        if distance <= threshold:
            return {
                "task_id": best.get("task_id", ""),
                "description": best.get("description", ""),
                "agent_id": best.get("agent_id", ""),
                "completed": best.get("completed", ""),
                "distance": distance,
            }
        return None

    except Exception as e:
        logger.debug(f"Dedup check failed: {e}")
        return None


def get_dedup_stats() -> dict:
    """Return dedup table stats for status displays."""
    try:
        table = _get_dedup_table()
        count = table.count_rows()
    except Exception:
        count = 0
    return {
        "tracked_tasks": count,
        "max_entries": _DEDUP_MAX_ENTRIES,
    }


# === Progress Tracking (Roadmap Item 10) ===
#
# Measures how well a task's output addresses its original goal.
# Embeds both the goal (task description) and the output, then computes
# cosine similarity. Higher similarity = output is more relevant to the goal.
# Returns a 0-100 percentage score.

def measure_progress(goal: str, output: str) -> dict:
    """Measure how well an output addresses a goal using vector similarity.

    Args:
        goal: The original task description / mission goal.
        output: The task output text (truncated to first 1000 chars for embedding).

    Returns:
        {
            "score": int (0-100),       # percentage — higher is better
            "distance": float,          # raw cosine distance
            "assessment": str,          # "excellent" | "good" | "partial" | "weak"
        }
    """
    try:
        # Truncate output for embedding — first 1000 chars captures the gist
        output_trimmed = output[:1000] if output else ""
        if not output_trimmed.strip():
            return {"score": 0, "distance": 1.0, "assessment": "weak"}

        goal_vec = _embed_text(goal)
        output_vec = _embed_text(output_trimmed)

        # Cosine similarity = 1 - cosine_distance
        # We compute it manually since we already have both vectors
        import numpy as np
        g = np.array(goal_vec)
        o = np.array(output_vec)
        cosine_sim = float(np.dot(g, o) / (np.linalg.norm(g) * np.linalg.norm(o)))

        # Clamp to [0, 1] and convert to percentage
        score = max(0, min(100, int(cosine_sim * 100)))

        # Assess quality
        if score >= 70:
            assessment = "excellent"
        elif score >= 50:
            assessment = "good"
        elif score >= 30:
            assessment = "partial"
        else:
            assessment = "weak"

        distance = 1.0 - cosine_sim

        logger.debug(
            f"Progress: goal='{goal[:40]}' score={score}% "
            f"({assessment}, distance={distance:.4f})"
        )

        return {
            "score": score,
            "distance": round(distance, 4),
            "assessment": assessment,
        }

    except Exception as e:
        logger.debug(f"Progress measurement failed: {e}")
        return {"score": 0, "distance": 1.0, "assessment": "weak"}
