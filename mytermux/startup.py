"""Fast, cached startup — the first thing you see when you open Termux.

Two jobs:

1. **Stay fast.** ``heal()`` walks the filesystem, probes pip packages and can
   shell out to ``pip install``. Running it on *every* interactive shell made
   opening Termux take seconds. :func:`heal_if_stale` runs it at most once per
   ``max_age_hours`` and otherwise replays the cached verdict in milliseconds.

2. **Say something worth reading.** :func:`digest` compares the database
   against the last time the dashboard was shown, so the panel can say
   "2 sessions and 14 messages since Tuesday" instead of a static table.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from . import db, heal as heal_mod, paths

DEFAULT_HEAL_AGE_HOURS = 12.0


# ---------------------------------------------------------------- heal cache --

def _read_json(path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _write_json(path, data: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def heal_cache_age_hours() -> Optional[float]:
    """Age in hours of the last cached heal, or None if there is no cache."""
    data = _read_json(paths.HEAL_CACHE)
    ts = data.get("ts")
    if not isinstance(ts, (int, float)):
        return None
    return max(0.0, (time.time() - ts) / 3600.0)


def heal_if_stale(max_age_hours: float = DEFAULT_HEAL_AGE_HOURS,
                  force: bool = False) -> Dict[str, Any]:
    """Run self-heal only when the cached verdict has gone stale.

    Returns a dict shaped like ``heal.heal()``'s report plus:
      ``from_cache``  bool   — True when nothing was re-run
      ``age_hours``   float  — how old the verdict is
    """
    if not force:
        age = heal_cache_age_hours()
        if age is not None and age <= max_age_hours:
            cached = _read_json(paths.HEAL_CACHE)
            report = cached.get("report") or {}
            if isinstance(report, dict) and report.get("after"):
                report["from_cache"] = True
                report["age_hours"] = round(age, 1)
                return report

    report = heal_mod.heal()
    report["from_cache"] = False
    report["age_hours"] = 0.0
    # store a slimmed copy — the full report can carry big pip output
    slim = {
        "started_at": report.get("started_at"),
        "after": [
            {"name": c.get("name"), "ok": c.get("ok"), "detail": c.get("detail", "")}
            for c in report.get("after", [])
        ],
        "repairs": report.get("repairs", []),
        "log_path": report.get("log_path"),
    }
    _write_json(paths.HEAL_CACHE, {"ts": time.time(), "report": slim})
    return report


def outstanding_issues(report: Dict[str, Any]) -> Dict[str, list]:
    """Split a heal report into required vs optional failures."""
    after = report.get("after", []) or []
    return {
        "required": [c for c in after if not c.get("ok") and "optional" not in (c.get("name") or "")],
        "optional": [c for c in after if not c.get("ok") and "optional" in (c.get("name") or "")],
    }


# -------------------------------------------------------------------- digest --

def last_opened_at() -> Optional[str]:
    data = _read_json(paths.LAST_OPEN_CACHE)
    ts = data.get("at")
    return ts if isinstance(ts, str) else None


def mark_opened() -> str:
    """Record that the dashboard was shown. Returns the ISO timestamp used."""
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    _write_json(paths.LAST_OPEN_CACHE, {"at": stamp})
    return stamp


def _human_delta(seconds: float) -> str:
    if seconds < 60:
        return "just now"
    minutes = seconds / 60.0
    if minutes < 60:
        return f"{int(minutes)}m ago"
    hours = minutes / 60.0
    if hours < 24:
        return f"{int(hours)}h ago"
    days = hours / 24.0
    if days < 7:
        return f"{int(days)}d ago"
    return f"{int(days / 7)}w ago"


def _parse_iso(ts: str) -> Optional[float]:
    try:
        return datetime.fromisoformat(ts).timestamp()
    except (ValueError, TypeError):
        return None


def digest() -> Dict[str, Any]:
    """What changed since the dashboard was last shown."""
    since = last_opened_at()
    now_epoch = time.time()

    out: Dict[str, Any] = {
        "since": since,
        "since_label": None,
        "sessions": 0,
        "messages": 0,
        "tasks_done": 0,
        "totals": db.totals(),
        "last_reply": None,
        "last_reply_at": None,
    }

    if since:
        since_epoch = _parse_iso(since)
        if since_epoch is not None:
            out["since_label"] = _human_delta(max(0.0, now_epoch - since_epoch))
        out["sessions"] = db.count_since("sessions", since)
        out["messages"] = db.count_since("messages", since)
        out["tasks_done"] = db.count_since("tasks", since, where="status='done'")

    row = db.last_message("assistant")
    if row is not None:
        text = (row["content"] or "").strip()
        out["last_reply"] = text
        out["last_reply_at"] = row["created_at"]
    return out


def digest_line(d: Dict[str, Any]) -> str:
    """Collapse a digest into one terminal-friendly sentence, or '' if empty."""
    if not d.get("since_label"):
        return ""
    bits = []
    if d.get("sessions"):
        bits.append(f"{d['sessions']} session" + ("s" if d["sessions"] != 1 else ""))
    if d.get("messages"):
        bits.append(f"{d['messages']} message" + ("s" if d["messages"] != 1 else ""))
    if d.get("tasks_done"):
        bits.append(f"{d['tasks_done']} task" + ("s" if d["tasks_done"] != 1 else "") + " done")
    if not bits:
        return f"nothing new since {d['since_label']}"
    return ", ".join(bits) + f" since {d['since_label']}"


def snippet(text: Optional[str], width: int = 60) -> str:
    """First meaningful line of an answer, collapsed to one line."""
    if not text:
        return ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("<tool", "<think", "```")):
            continue
        line = " ".join(line.split())
        if len(line) > width:
            return line[: width - 1] + "\u2026"
        return line
    return ""
