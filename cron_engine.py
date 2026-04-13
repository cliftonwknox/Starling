"""Starling Cron Engine — Scheduled job management.

Stores cron jobs in cron_config.json in the project work dir.
The daemon checks for due jobs on each heartbeat tick.
"""

import calendar
import os
import json
import re
from datetime import datetime, timedelta
from typing import Optional

import logging
logger = logging.getLogger("starling.cron")

WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _cron_file() -> str:
    try:
        from config_loader import get_data_file
        return get_data_file("cron_config.json")
    except Exception:
        return os.path.join(os.path.dirname(__file__), "cron_config.json")


def _load_crons() -> list:
    path = _cron_file()
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


def _save_crons(jobs: list):
    path = _cron_file()
    with open(path, "w") as f:
        json.dump(jobs, f, indent=2)


def parse_schedule(schedule_str: str) -> dict:
    """Parse human-readable schedule into structured dict.

    Formats:
        "hourly"              -> interval 60m
        "every 6h"            -> interval 360m
        "every 30m"           -> interval 30m
        "daily 08:00"         -> daily at 08:00
        "daily"               -> daily at 00:00
        "weekly mon 9:00"     -> weekly on Monday at 9:00
        "monthly 1 09:00"     -> monthly on 1st at 09:00
    """
    s = schedule_str.strip().lower()

    if s == "hourly":
        return {"type": "interval", "interval_minutes": 60}

    # every Xh or every Xm
    m = re.match(r"every\s+(\d+)\s*(h|hr|hrs|hour|hours|m|min|mins|minute|minutes)", s)
    if m:
        val = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("h"):
            return {"type": "interval", "interval_minutes": val * 60}
        return {"type": "interval", "interval_minutes": val}

    # daily HH:MM
    m = re.match(r"daily(?:\s+(\d{1,2}):(\d{2}))?", s)
    if m:
        hour = int(m.group(1)) if m.group(1) else 0
        minute = int(m.group(2)) if m.group(2) else 0
        return {"type": "daily", "hour": hour, "minute": minute}

    # weekly DAY HH:MM
    m = re.match(r"weekly\s+(\w+)(?:\s+(\d{1,2}):(\d{2}))?", s)
    if m:
        day = m.group(1)[:3]
        if day not in WEEKDAYS:
            raise ValueError(f"Unknown weekday: {m.group(1)}")
        hour = int(m.group(2)) if m.group(2) else 0
        minute = int(m.group(3)) if m.group(3) else 0
        return {"type": "weekly", "weekday": WEEKDAYS[day], "hour": hour, "minute": minute}

    # monthly DAY HH:MM
    m = re.match(r"monthly\s+(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?", s)
    if m:
        day = int(m.group(1))
        hour = int(m.group(2)) if m.group(2) else 0
        minute = int(m.group(3)) if m.group(3) else 0
        return {"type": "monthly", "day": day, "hour": hour, "minute": minute}

    raise ValueError(f"Cannot parse schedule: '{schedule_str}'")


def compute_next_run(parsed: dict, after: datetime = None) -> datetime:
    """Compute the next run time from a parsed schedule."""
    now = after or datetime.now()

    if parsed["type"] == "interval":
        return now + timedelta(minutes=parsed["interval_minutes"])

    if parsed["type"] == "daily":
        target = now.replace(hour=parsed["hour"], minute=parsed["minute"], second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target

    if parsed["type"] == "weekly":
        target = now.replace(hour=parsed["hour"], minute=parsed["minute"], second=0, microsecond=0)
        days_ahead = parsed["weekday"] - now.weekday()
        if days_ahead < 0 or (days_ahead == 0 and target <= now):
            days_ahead += 7
        target += timedelta(days=days_ahead)
        return target

    if parsed["type"] == "monthly":
        # Clamp day to the last valid day of the month (e.g. day=31 in Feb -> 28/29)
        day = min(parsed["day"], calendar.monthrange(now.year, now.month)[1])
        target = now.replace(day=day, hour=parsed["hour"],
                             minute=parsed["minute"], second=0, microsecond=0)
        if target <= now:
            # Next month
            if now.month == 12:
                next_year, next_month = now.year + 1, 1
            else:
                next_year, next_month = now.year, now.month + 1
            day = min(parsed["day"], calendar.monthrange(next_year, next_month)[1])
            target = target.replace(year=next_year, month=next_month, day=day)
        return target

    raise ValueError(f"Unknown schedule type: {parsed['type']}")


def add_cron(name: str, description: str, schedule: str,
             agent: str = None, crew: bool = False,
             created_by: str = "user", require_approval: bool = False,
             report: bool = True) -> dict:
    """Add a new cron job. Returns the job dict."""
    parsed = parse_schedule(schedule)
    next_run = compute_next_run(parsed)

    job = {
        "id": f"cron_{datetime.now().strftime('%Y%m%d%H%M%S%f')[:18]}",
        "name": name,
        "description": description,
        "schedule": schedule,
        "schedule_parsed": parsed,
        "agent": agent,
        "crew": crew,
        "status": "pending_approval" if require_approval else "active",
        "report": report,
        "created_by": created_by,
        "created": datetime.now().isoformat(),
        "last_run": None,
        "next_run": next_run.isoformat(),
        "run_count": 0,
        "error_count": 0,
        "last_error": None,
    }

    jobs = _load_crons()
    jobs.append(job)
    _save_crons(jobs)
    logger.info(f"Cron added: {name} ({schedule})")
    return job


def remove_cron(job_id: str) -> bool:
    """Remove a cron job by ID or suffix.

    Guard: an empty `job_id` would match every cron job because every string
    ends with "" — refuse to delete-all by accident.
    """
    if not job_id or not job_id.strip():
        logger.warning("remove_cron called with empty job_id; refusing to delete all jobs")
        return False
    jobs = _load_crons()
    before = len(jobs)
    jobs = [j for j in jobs if not j["id"].endswith(job_id)]
    if len(jobs) == before:
        return False
    _save_crons(jobs)
    return True


def enable_cron(job_id: str) -> bool:
    """Enable a cron job."""
    return _update_status(job_id, "active", recompute=True)


def disable_cron(job_id: str) -> bool:
    """Disable a cron job."""
    return _update_status(job_id, "disabled")


def approve_cron(job_id: str) -> bool:
    """Approve a pending cron job."""
    jobs = _load_crons()
    for j in jobs:
        if j["id"].endswith(job_id) and j["status"] == "pending_approval":
            j["status"] = "active"
            parsed = j.get("schedule_parsed") or parse_schedule(j["schedule"])
            j["next_run"] = compute_next_run(parsed).isoformat()
            _save_crons(jobs)
            logger.info(f"Cron approved: {j['name']}")
            return True
    return False


def reject_cron(job_id: str) -> bool:
    """Reject a pending cron job."""
    return _update_status(job_id, "rejected")


def list_crons(status: str = None) -> list:
    """List cron jobs, optionally filtered by status."""
    jobs = _load_crons()
    if status:
        jobs = [j for j in jobs if j["status"] == status]
    return jobs


def get_cron(job_id: str) -> Optional[dict]:
    """Get a cron job by ID or suffix."""
    for j in _load_crons():
        if j["id"].endswith(job_id):
            return j
    return None


def update_cron(job_id: str, **updates) -> Optional[dict]:
    """Update fields on a cron job."""
    jobs = _load_crons()
    for j in jobs:
        if j["id"].endswith(job_id):
            for k, v in updates.items():
                j[k] = v
            # Recompute next_run if schedule changed
            if "schedule" in updates:
                parsed = parse_schedule(updates["schedule"])
                j["schedule_parsed"] = parsed
                j["next_run"] = compute_next_run(parsed).isoformat()
            _save_crons(jobs)
            return j
    return None


def check_due_jobs() -> list:
    """Check for jobs that are due to run. Updates last_run and next_run."""
    jobs = _load_crons()
    now = datetime.now()
    due = []

    for j in jobs:
        if j["status"] != "active":
            continue
        if not j.get("next_run"):
            continue
        try:
            next_run = datetime.fromisoformat(j["next_run"])
        except Exception:
            continue
        if next_run <= now:
            j["last_run"] = now.isoformat()
            j["run_count"] = j.get("run_count", 0) + 1
            # Compute next from the scheduled time to prevent drift
            parsed = j.get("schedule_parsed") or parse_schedule(j["schedule"])
            j["next_run"] = compute_next_run(parsed, after=now).isoformat()
            due.append(dict(j))  # copy

    if due:
        _save_crons(jobs)
    return due


def run_now(job_id: str) -> Optional[dict]:
    """Manually trigger a cron job immediately. Returns the job if found."""
    jobs = _load_crons()
    now = datetime.now()
    for j in jobs:
        if j["id"].endswith(job_id):
            j["last_run"] = now.isoformat()
            j["run_count"] = j.get("run_count", 0) + 1
            parsed = j.get("schedule_parsed") or parse_schedule(j["schedule"])
            j["next_run"] = compute_next_run(parsed, after=now).isoformat()
            _save_crons(jobs)
            logger.info(f"Cron manually triggered: {j['name']}")
            return dict(j)
    return None


def _update_status(job_id: str, new_status: str, recompute: bool = False) -> bool:
    jobs = _load_crons()
    for j in jobs:
        if j["id"].endswith(job_id):
            j["status"] = new_status
            if recompute and new_status == "active":
                parsed = j.get("schedule_parsed") or parse_schedule(j["schedule"])
                j["next_run"] = compute_next_run(parsed).isoformat()
            _save_crons(jobs)
            return True
    return False
