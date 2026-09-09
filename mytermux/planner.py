"""Brain / proactive planner — figures out what you should do next.

Local signals only (no LLM calls) so it is instant, offline and free.

INVARIANT: every ``cmd`` returned here is built with :func:`commands.cmd`,
which raises on an unknown name. That is deliberate — the dashboard's entire
job is to hand you something to type, and it used to hand back six commands
that did not exist on the phone.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from . import db
from .commands import cmd
from .config import load_config


def current_state() -> Dict:
    cfg = load_config()
    last = db.get_last_session()
    pending = db.list_pending_tasks(limit=5)
    projects = db.list_projects()
    current_project = cfg.get("current_project", "")
    return {
        "api_configured": bool(cfg.get("openrouter_api_key")),
        "github_configured": bool(cfg.get("github_token")),
        "current_project": current_project,
        "last_session_id": last["id"] if last else None,
        "last_session_project": (last["project"] if last else "") or "",
        "pending_tasks": [dict(r) for r in pending],
        "projects_count": len(projects),
        "notifications_on": bool(cfg.get("notifications", True)),
    }


def _project_git(path_str: str) -> Dict:
    """Cheap git facts for the current project: branch, dirty, ahead, behind."""
    from . import git_ops

    empty = {"repo": False, "branch": "", "dirty": False, "ahead": False, "behind": False}
    if not path_str:
        return empty
    p = Path(path_str).expanduser()
    if not (p / ".git").exists():
        return empty
    branch = git_ops.current_branch(p)
    rc, out, _ = git_ops.run(["git", "status", "--porcelain"], cwd=p)
    dirty = bool((out or "").strip())
    ahead = behind = False
    rc, out, _ = git_ops.run(
        ["git", "rev-list", "--left-right", "--count", "@{upstream}...HEAD"], cwd=p)
    if rc == 0 and out:
        parts = out.split()
        if len(parts) >= 2:
            try:
                behind, ahead = int(parts[0]) > 0, int(parts[1]) > 0
            except ValueError:
                pass
    return {"repo": True, "branch": branch, "dirty": dirty,
            "ahead": ahead, "behind": behind}


def next_actions(device: Dict | None = None) -> List[Dict]:
    """Return a ranked list of proactive next actions.

    Each entry: ``cmd`` (copy-pasteable, guaranteed installed), ``action``
    (what it does), ``why`` (the signal that produced it), ``priority``.
    """
    st = current_state()
    device = device or {}
    actions: List[Dict] = []

    # ---- hard blockers first ------------------------------------------------
    if not st["api_configured"]:
        actions.append({
            "cmd": cmd("menu"),
            "why": "No OpenRouter API key set — chat cannot run yet.",
            "action": "Open Settings and paste your OpenRouter key.",
            "priority": 1,
        })

    # ---- device pressure: the phone is the environment, so say so -----------
    batt = device.get("battery") or {}
    pct = batt.get("percent")
    plugged = (batt.get("plugged") or "UNPLUGGED").upper()
    if isinstance(pct, (int, float)) and pct <= 15 and plugged == "UNPLUGGED":
        actions.append({
            "cmd": cmd("export", "session"),
            "why": f"Battery at {pct:.0f}% and unplugged.",
            "action": "Save your session to /sdcard before it dies.",
            "priority": 1,
        })

    sto = device.get("storage") or {}
    free_pct = sto.get("percent_free")
    if isinstance(free_pct, (int, float)) and free_pct < 10:
        actions.append({
            "cmd": cmd("fix"),
            "why": f"Only {free_pct:.0f}% of storage free.",
            "action": "Run repair to clear stale logs and backups.",
            "priority": 2,
        })

    # ---- resume where you left off -----------------------------------------
    if st["last_session_id"]:
        where = f" on {st['last_session_project']}" if st["last_session_project"] else ""
        actions.append({
            "cmd": cmd("resume"),
            "why": f"Open session #{st['last_session_id']}{where}.",
            "action": "Pick up your last conversation with full history.",
            "priority": 2,
        })

    # ---- current project ----------------------------------------------------
    proj = st["current_project"]
    git = _project_git(proj) if proj else {}
    if proj:
        name = Path(proj).name
        if git.get("repo") and git.get("dirty"):
            actions.append({
                "cmd": cmd("sync"),
                "why": f"{name} has uncommitted changes.",
                "action": "Review them, then commit and push.",
                "priority": 2,
            })
        if git.get("repo") and git.get("behind"):
            actions.append({
                "cmd": cmd("sync", "--pull"),
                "why": f"{name} is behind its upstream.",
                "action": "Pull the latest commits.",
                "priority": 2,
            })
        if proj and Path(proj).expanduser().exists():
            actions.append({
                "cmd": cmd("scan", proj),
                "why": f"Current project: {name}",
                "action": "Refresh its dependency and git state.",
                "priority": 4,
            })

    # ---- pending work -------------------------------------------------------
    if st["pending_tasks"]:
        top = st["pending_tasks"][0]
        n = len(st["pending_tasks"])
        # keep the command short enough to stay on one phone-width line
        title = top["title"]
        if len(title) > 32:
            title = title[:31].rstrip() + "\u2026"
        actions.append({
            "cmd": cmd("ask", f'"work on: {title}"'),
            "why": f"{n} pending task(s).",
            "action": f"Hand the next one to the agent: {top['title']}",
            "priority": 3,
        })

    # ---- onboarding / breadth ----------------------------------------------
    if not st["github_configured"]:
        actions.append({
            "cmd": cmd("menu"),
            "why": "No GitHub token configured.",
            "action": "Add a Personal Access Token to enable clone/pull/push.",
            "priority": 5,
        })

    if not st["projects_count"]:
        actions.append({
            "cmd": cmd("scan", "~"),
            "why": "No projects registered yet.",
            "action": "Scan a folder to register your first project.",
            "priority": 4,
        })

    # ---- flagship: hey voice companion --------------------------------------
    if st["api_configured"]:
        actions.append({
            "cmd": cmd("hey"),
            "why": "Hands-free voice coding — just talk, it builds and fixes itself.",
            "action": "Start Nova, your voice coding companion (auto-heals errors).",
            "priority": 1,
        })

    # ---- always-available escape hatches -----------------------------------
    if st["api_configured"]:
        actions.append({
            "cmd": cmd("ask", '"what should I do next in this project?"'),
            "why": "Fastest way in — one shot, no REPL.",
            "action": "Ask the agent a single question from the shell.",
            "priority": 6,
        })
        actions.append({
            "cmd": cmd("chat"),
            "why": "You have working context.",
            "action": "Open a full agent session with tools.",
            "priority": 6,
        })
        actions.append({
            "cmd": cmd("flow"),
            "why": "Continuous talk-coding session.",
            "action": "Flow mode — keep talking, it keeps building.",
            "priority": 5,
        })
        actions.append({
            "cmd": cmd("clip"),
            "why": "You have something in clipboard?",
            "action": "Clipboard agent — act on what you copied.",
            "priority": 6,
        })
    actions.append({
        "cmd": cmd("fix"),
        "why": "Keep the environment healthy.",
        "action": "Run diagnostics and safe self-repair.",
        "priority": 7,
    })

    actions.sort(key=lambda a: a["priority"])
    return _dedupe(actions)[:5]


def _dedupe(actions: List[Dict]) -> List[Dict]:
    """Drop repeated commands, keeping the highest-priority (earliest) one."""
    seen: set = set()
    out: List[Dict] = []
    for a in actions:
        if a["cmd"] in seen:
            continue
        seen.add(a["cmd"])
        out.append(a)
    return out
