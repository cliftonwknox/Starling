"""Starling Heartbeat — Persistent task queue with auto-execution loop.

The always-on engine: checks for pending tasks, assigns to agents, kicks off
execution, sleeps, repeats. Tasks persist to disk so nothing is lost on restart.
"""

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Optional, Callable

logger = logging.getLogger("starling.heartbeat")

DEFAULT_INTERVAL = 60  # seconds between heartbeat checks


def _get_data_file(name: str) -> str:
    try:
        from config_loader import get_data_file
        return get_data_file(name)
    except Exception:
        return os.path.join(os.path.dirname(__file__), name)


def _get_output_dir() -> str:
    try:
        from config_loader import get_output_dir
        return get_output_dir()
    except Exception:
        d = os.path.join(os.path.dirname(__file__), "output")
        os.makedirs(d, exist_ok=True)
        return d


def _queue_file() -> str:
    return _get_data_file("task_queue.json")


def _heartbeat_config_file() -> str:
    return _get_data_file("heartbeat_config.json")


# === Task Queue (disk-backed) ===
#
# All queue I/O is serialized through `_queue_lock` because the TUI thread,
# the heartbeat background thread, the cron engine, and the Telegram listener
# all mutate task_queue.json. Without this lock, a concurrent write can produce
# a partial/empty JSON file and silently lose every queued task on disk.
# Atomic write (tmp + rename) guarantees the on-disk file is never partial
# even if the process is killed mid-save.
_queue_lock = threading.RLock()


def _load_queue() -> list:
    with _queue_lock:
        if os.path.exists(_queue_file()):
            with open(_queue_file()) as f:
                return json.load(f)
        return []


def _save_queue(tasks: list):
    qf = _queue_file()
    tmp = qf + ".tmp"
    with _queue_lock:
        with open(tmp, "w") as f:
            json.dump(tasks, f, indent=2)
        os.replace(tmp, qf)


def add_task(
    description: str,
    agent: Optional[str] = None,
    priority: int = 5,
    tags: Optional[list] = None,
    crew: bool = False,
    every: Optional[str] = None,
    depends_on: Optional[list] = None,
) -> dict:
    """Add a task to the queue.

    Args:
        crew: If True, run as full 5-agent crew instead of single-agent chat.
        every: Recurrence interval string, e.g. "6h", "30m", "1d".
        depends_on: List of task ID suffixes this task must wait for.
    """
    # The full read-modify-write must hold the queue lock so concurrent
    # add_task / update_task calls cannot interleave and clobber each other.
    with _queue_lock:
        tasks = _load_queue()
        task = {
            "id": datetime.now().strftime("%Y%m%d%H%M%S%f")[:18],
            "description": description,
            "agent": agent,  # None = CEO auto-delegates
            "priority": priority,
            "status": "pending",  # pending, running, done, failed, cancelled
            "crew": crew,
            "tags": tags or [],
            "created": datetime.now().isoformat(),
            "started": None,
            "completed": None,
            "result": None,
            "error": None,
            "retries": 0,
            "max_retries": 1,
            # Recurring
            "every": every,
            "next_run": None,
            # Dependencies
            "depends_on": depends_on or [],
        }
        tasks.append(task)
        _save_queue(tasks)
        return task


def get_task(task_id: str) -> Optional[dict]:
    for t in _load_queue():
        if t["id"] == task_id:
            return t
    return None


def list_tasks(status: Optional[str] = None) -> list:
    tasks = _load_queue()
    if status:
        tasks = [t for t in tasks if t["status"] == status]
    return tasks


def update_task(task_id: str, **updates) -> Optional[dict]:
    with _queue_lock:
        tasks = _load_queue()
        for t in tasks:
            if t["id"] == task_id:
                t.update(updates)
                _save_queue(tasks)
                return t
        return None


def cancel_task(task_id: str) -> bool:
    task = update_task(task_id, status="cancelled")
    return task is not None


def clear_done() -> int:
    """Remove completed/failed/cancelled tasks. Returns count removed."""
    with _queue_lock:
        tasks = _load_queue()
        before = len(tasks)
        tasks = [t for t in tasks if t["status"] in ("pending", "running")]
        _save_queue(tasks)
        return before - len(tasks)


def _recover_stale_tasks(max_age_minutes: int = 30):
    """Mark tasks stuck in 'running' for too long as failed."""
    with _queue_lock:
        tasks = _load_queue()
        changed = False
        now = datetime.now()
        for t in tasks:
            if t["status"] != "running":
                continue
            started = t.get("started")
            if not started:
                t["status"] = "failed"
                t["error"] = "No start time recorded; marked as stale"
                t["completed"] = now.isoformat()
                changed = True
                continue
            try:
                started_dt = datetime.fromisoformat(started)
                if (now - started_dt).total_seconds() > max_age_minutes * 60:
                    t["status"] = "failed"
                    t["error"] = f"Stale: running for over {max_age_minutes} minutes"
                    t["completed"] = now.isoformat()
                    changed = True
            except Exception:
                pass
        if changed:
            _save_queue(tasks)


def next_pending() -> Optional[dict]:
    """Get the highest-priority pending task whose dependencies are met."""
    all_tasks = _load_queue()
    done_ids = {t["id"] for t in all_tasks if t["status"] == "done"}
    pending = []
    for t in all_tasks:
        if t["status"] != "pending":
            continue
        # Check recurrence timing — parse both sides as datetime so this
        # works regardless of whether the stored string includes microseconds
        # (lexicographic ISO comparison silently mis-orders mismatched precision).
        nr = t.get("next_run")
        if nr:
            try:
                if datetime.now() < datetime.fromisoformat(nr):
                    continue  # not time yet
            except (ValueError, TypeError):
                # Bad timestamp on disk — treat as immediately due rather than
                # blocking the task forever.
                logger.warning(f"Task {t.get('id')}: unparseable next_run={nr!r}; running now")
        # Check dependencies
        deps = t.get("depends_on", [])
        if deps:
            # Match by suffix — deps are short IDs
            blocked = False
            for dep in deps:
                if not any(tid.endswith(dep) for tid in done_ids):
                    blocked = True
                    break
            if blocked:
                continue
        pending.append(t)
    if not pending:
        return None
    pending.sort(key=lambda t: (t["priority"], t["created"]))
    return pending[0]


def parse_interval(interval_str: str) -> Optional[timedelta]:
    """Parse interval string like '30m', '6h', '1d' into timedelta."""
    m = re.match(r"^(\d+)\s*(m|min|h|hr|hour|d|day)s?$", interval_str.strip().lower())
    if not m:
        return None
    val = int(m.group(1))
    unit = m.group(2)
    if unit in ("m", "min"):
        return timedelta(minutes=val)
    elif unit in ("h", "hr", "hour"):
        return timedelta(hours=val)
    elif unit in ("d", "day"):
        return timedelta(days=val)
    return None


def requeue_recurring(task: dict):
    """If a task has a recurrence interval, re-queue it for the next run.

    The new task's id is captured directly from add_task() so we can target
    it by id when stamping next_run — never assume tasks[-1] is "ours" since
    a concurrent add_task call could insert in between.
    """
    every = task.get("every")
    if not every:
        return
    delta = parse_interval(every)
    if not delta:
        return
    next_run = (datetime.now() + delta).isoformat()
    new_task = add_task(
        description=task["description"],
        agent=task.get("agent"),
        priority=task.get("priority", 5),
        tags=task.get("tags", []),
        crew=task.get("crew", False),
        every=every,
        depends_on=task.get("depends_on", []),
    )
    update_task(new_task["id"], next_run=next_run)


def save_task_output(task: dict, result: str):
    """Save heartbeat task result to output/ directory."""
    OUTPUT_DIR = _get_output_dir()
    agent = task.get("agent") or ("crew" if task.get("crew") else "unknown")
    mode = "crew" if task.get("crew") else "task"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"heartbeat_{mode}_{agent}_{ts}.md"
    filepath = os.path.join(OUTPUT_DIR, filename)
    with open(filepath, "w") as f:
        f.write(f"# Heartbeat {mode.title()} Result\n\n")
        f.write(f"**Agent:** {agent}\n")
        f.write(f"**Task:** {task['description']}\n")
        f.write(f"**Started:** {task.get('started', '?')}\n")
        f.write(f"**Completed:** {datetime.now().isoformat()}\n")
        if task.get("every"):
            f.write(f"**Recurring:** every {task['every']}\n")
        f.write(f"\n---\n\n{result}\n")
    return filepath


# === Agent routing ===

def _load_routing_keywords() -> dict:
    """Load routing keywords from project config."""
    try:
        from config_loader import get_routing_keywords
        return get_routing_keywords()
    except Exception:
        return {}


def _get_default_agent() -> str:
    try:
        from config_loader import get_default_agent, get_agent_ids
        default = get_default_agent()
        if default:
            return default
        ids = get_agent_ids()
        return ids[0] if ids else ""
    except Exception:
        return ""


def auto_route(description: str) -> tuple:
    """Pick the best agent for a task. Priority: keywords > semantic > default.

    Returns:
        (agent_id, route_method) where route_method is one of:
        "keyword", "semantic", or "default".
    """
    # 1. Try keyword matching (existing logic, unchanged)
    keywords = _load_routing_keywords()
    if keywords:
        desc_lower = description.lower()
        scores = {}
        for agent_id, kw_list in keywords.items():
            score = sum(1 for kw in kw_list if kw in desc_lower)
            if score > 0:
                scores[agent_id] = score
        if scores:
            return max(scores, key=scores.get), "keyword"

    # 2. Try semantic routing
    try:
        from semantic_router import semantic_route
        result = semantic_route(description)
        if result:
            return result, "semantic"
    except Exception:
        pass

    # 3. Fall back to default — log warning so user knows
    default = _get_default_agent()
    logger.warning(
        f"No semantic or keyword match for task '{description[:80]}', "
        f"falling back to default agent '{default}'"
    )
    return default, "default"


# === Heartbeat Config (auto-start, interval persistence) ===

def load_heartbeat_config() -> dict:
    if os.path.exists(_heartbeat_config_file()):
        with open(_heartbeat_config_file()) as f:
            return json.load(f)
    return {"auto_start": False, "interval": DEFAULT_INTERVAL}


def save_heartbeat_config(config: dict):
    with open(_heartbeat_config_file(), "w") as f:
        json.dump(config, f, indent=2)


# === Heartbeat Engine ===

class Heartbeat:
    """Background loop that processes the task queue."""

    def __init__(
        self,
        interval: int = DEFAULT_INTERVAL,
        on_task_start: Optional[Callable] = None,
        on_task_done: Optional[Callable] = None,
        on_task_fail: Optional[Callable] = None,
        on_tick: Optional[Callable] = None,
        run_task: Optional[Callable] = None,
        run_crew: Optional[Callable] = None,
    ):
        self.interval = interval
        self.on_task_start = on_task_start
        self.on_task_done = on_task_done
        self.on_task_fail = on_task_fail
        self.on_tick = on_tick
        self.run_task = run_task  # Callable(task) -> str result
        self.run_crew = run_crew  # Callable(task) -> str result (full crew run)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.tasks_processed = 0
        self.started_at: Optional[str] = None

    @property
    def running(self) -> bool:
        return self._running

    def start(self):
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self.started_at = datetime.now().isoformat()
        # Ensure semantic routing vectors are up to date
        try:
            from semantic_router import ensure_skill_vectors
            ensure_skill_vectors()
        except Exception as e:
            logger.warning(f"Semantic routing unavailable: {e}")
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self):
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as e:
                # Never crash the loop, but DO log so a systemic bug
                # (e.g. broken on_tick) is visible in starling_daemon.log
                # instead of silently swallowed forever.
                logger.exception(f"Heartbeat tick error: {e}")
            # Sleep in small increments so stop is responsive
            for _ in range(self.interval):
                if self._stop_event.is_set():
                    return
                time.sleep(1)

    def _tick(self):
        """One heartbeat cycle: find next task, run it."""
        if self.on_tick:
            self.on_tick()

        # Mark stale "running" tasks as failed (stuck > 30 min)
        _recover_stale_tasks()

        task = next_pending()
        if not task:
            return

        # Route if no agent assigned (skip for crew tasks)
        if not task.get("crew") and not task["agent"]:
            agent_id, route_method = auto_route(task["description"])
            task["agent"] = agent_id
            # Tag with routing method for visibility in Queue/History
            tags = task.get("tags", [])
            tags.append(f"routed:{route_method}")
            update_task(task["id"], agent=agent_id, tags=tags)
            # Cap retries for default-routed tasks (unfit agent, don't waste tokens)
            if route_method == "default":
                update_task(task["id"], max_retries=0)

        # Check for duplicate work before executing
        try:
            from semantic_router import check_duplicate
            dup = check_duplicate(task["description"])
            if dup:
                logger.warning(
                    f"Duplicate detected: task '{task['description'][:60]}' matches "
                    f"completed task {dup['task_id']} (distance={dup['distance']:.4f}). "
                    f"Tagging as duplicate."
                )
                tags = task.get("tags", [])
                tags.append("duplicate")
                tags.append(f"dup_of:{dup['task_id']}")
                update_task(task["id"], tags=tags)
        except Exception:
            pass

        # Mark running
        update_task(task["id"], status="running", started=datetime.now().isoformat())

        if self.on_task_start:
            self.on_task_start(task)

        # Execute
        try:
            if task.get("crew") and self.run_crew:
                result = self.run_crew(task)
            elif self.run_task:
                result = self.run_task(task)
            else:
                result = f"No executor configured. Task: {task['description']}"

            # Save output to file (skip if tagged no-report)
            result_str = str(result)[:5000] if result else "No output"
            output_file = None
            if "no-report" not in task.get("tags", []):
                output_file = save_task_output(task, result_str)

            update_task(
                task["id"],
                status="done",
                completed=datetime.now().isoformat(),
                result=result_str,
                output_file=output_file,
            )
            self.tasks_processed += 1

            # Measure how well the output addressed the goal
            try:
                from semantic_router import measure_progress
                progress = measure_progress(task["description"], result_str)
                update_task(task["id"], progress=progress)
                if progress["score"] < 30:
                    logger.warning(
                        f"Low progress score ({progress['score']}%) for task "
                        f"'{task['description'][:60]}' — output may not address the goal"
                    )
            except Exception:
                pass

            # Record for duplicate detection
            try:
                from semantic_router import record_completed_task
                record_completed_task(
                    task_id=task["id"],
                    description=task["description"],
                    agent_id=task.get("agent", ""),
                    completed=datetime.now().isoformat(),
                )
            except Exception:
                pass

            if self.on_task_done:
                self.on_task_done(task, result_str)

            # Re-queue if recurring
            requeue_recurring(task)

        except Exception as e:
            retries = task.get("retries", 0)
            max_retries = task.get("max_retries", 1)

            if retries < max_retries:
                update_task(
                    task["id"],
                    status="pending",
                    retries=retries + 1,
                    error=str(e)[:500],
                )
            else:
                update_task(
                    task["id"],
                    status="failed",
                    completed=datetime.now().isoformat(),
                    error=str(e)[:500],
                )

            if self.on_task_fail:
                self.on_task_fail(task, e)

    def status(self) -> dict:
        """Current heartbeat status."""
        pending = len(list_tasks("pending"))
        running = len(list_tasks("running"))
        return {
            "running": self._running,
            "interval": self.interval,
            "started_at": self.started_at,
            "tasks_processed": self.tasks_processed,
            "pending": pending,
            "active": running,
        }
