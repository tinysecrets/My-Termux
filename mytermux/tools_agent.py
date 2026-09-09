"""Agent tool registry for my-termux.

Each tool has:
  - name           : short id used in <tool name="…"> blocks
  - description    : shown to the model in the system prompt
  - args_schema    : dict of arg_name -> description
  - safety         : "safe" | "confirm" | "danger"
  - fn(args) -> str: returns an observation string given a dict of args

Tool executors are pure Python — no network unless the tool explicitly uses one.
Observations are truncated to keep the model's context small.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable, Dict, List

from . import db, scanner, tools, git_ops, notify, media


def _tool_run_script(args: Dict) -> str:
    path = Path(args.get("path", "")).expanduser()
    if not path.exists():
        return f"error: script not found: {path}"
    if not path.is_file():
        return f"error: not a file: {path}"
    cmd = args.get("cmd") or ""
    if cmd:
        shell_cmd = f"bash {path} {cmd}".strip()
    else:
        shell_cmd = f"bash {path}"
    cwd = args.get("cwd")
    rc, out, err = tools.run_shell(shell_cmd, cwd=Path(cwd).expanduser() if cwd else None,
                                    timeout=int(args.get("timeout", 120)))
    return _clip(f"exit_code: {rc}\nstdout:\n{out}\nstderr:\n{err}")


def _tool_install_package(args: Dict) -> str:
    pkg = (args.get("package") or "").strip()
    if not pkg:
        return "error: missing 'package'"
    cmd = f"pkg install -y {pkg}"
    rc, out, err = tools.run_shell(cmd, timeout=int(args.get("timeout", 120)))
    return _clip(f"exit_code: {rc}\nstdout:\n{out}\nstderr:\n{err}")


def _tool_copy_file(args: Dict) -> str:
    src = Path(args.get("src", "")).expanduser()
    dst = Path(args.get("dst", "")).expanduser()
    if not src.exists():
        return f"error: source not found: {src}"
    if not src.is_file():
        return f"error: source is not a file: {src}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return f"copied {src} -> {dst}"


def _tool_make_dir(args: Dict) -> str:
    path = Path(args.get("path", "")).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return f"created directory: {path}"


def _tool_remove_path(args: Dict) -> str:
    path = Path(args.get("path", "")).expanduser()
    if not path.exists():
        return f"error: not found: {path}"
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return f"removed: {path}"


MAX_OBSERVATION_CHARS = 4000


def _clip(s: str, n: int = MAX_OBSERVATION_CHARS) -> str:
    if len(s) <= n:
        return s
    return s[:n] + f"\n… [truncated {len(s) - n} chars]"


# ---------------- individual tools -----------------------------------------

def _tool_shell(args: Dict) -> str:
    cmd = args.get("cmd", "").strip()
    if not cmd:
        return "error: missing 'cmd'"
    cwd = args.get("cwd")
    rc, out, err = tools.run_shell(cmd, cwd=Path(cwd).expanduser() if cwd else None,
                                    timeout=int(args.get("timeout", 30)))
    return _clip(f"exit_code: {rc}\nstdout:\n{out}\nstderr:\n{err}")


def _tool_read_file(args: Dict) -> str:
    p = Path(args.get("path", "")).expanduser()
    max_bytes = int(args.get("max_bytes", 8000))
    if not p.exists():
        return f"error: not found: {p}"
    if p.is_dir():
        return f"error: '{p}' is a directory. Use list_dir."
    content = tools.read_file(p, max_bytes=max_bytes)
    return _clip(f"path: {p}\nbytes_read: {len(content)}\n---\n{content}")


def _tool_write_file(args: Dict) -> str:
    p = Path(args.get("path", "")).expanduser()
    content = args.get("content", "")
    if not p.parent.exists():
        return f"error: parent dir missing: {p.parent}"
    tools.write_file(p, content, backup=True)
    return f"wrote {len(content)} bytes to {p} (backup: {p}.bak if existed)"


def _tool_list_dir(args: Dict) -> str:
    p = Path(args.get("path", ".")).expanduser()
    if not p.exists():
        return f"error: not found: {p}"
    if not p.is_dir():
        return f"error: '{p}' is not a directory"
    entries = []
    for child in sorted(p.iterdir())[:200]:
        kind = "d" if child.is_dir() else "f"
        size = child.stat().st_size if child.is_file() else 0
        entries.append(f"  {kind} {size:>10} {child.name}")
    return _clip(f"path: {p}\nentries: {len(entries)}\n" + "\n".join(entries))


def _tool_scan_project(args: Dict) -> str:
    p = Path(args.get("path", ".")).expanduser()
    info = scanner.scan(p)
    return json.dumps(info, indent=2, default=str)


def _tool_git(args: Dict) -> str:
    action = args.get("action", "status")
    repo_arg = args.get("path", ".")
    if action == "clone":
        url = args.get("url", "").strip()
        dest = Path(args.get("dest", "")).expanduser()
        if not url:
            return "error: missing 'url' for clone"
        if not dest:
            return "error: missing 'dest' for clone"
        rc, out, err = git_ops.clone(url, dest)
        return _clip(out or err)
    repo = Path(repo_arg).expanduser().resolve()
    if not (repo / ".git").exists():
        return f"error: not a git repo: {repo}"
    if action == "status":
        return _clip(f"branch: {git_ops.current_branch(repo)}\n{git_ops.status(repo)}")
    if action == "log":
        rc, out, err = git_ops.run(["git", "log", "--oneline", "-20"], cwd=repo)
        return _clip(out or err)
    if action == "diff":
        rc, out, err = git_ops.run(["git", "diff", "--stat"], cwd=repo)
        return _clip(out or err)
    if action == "branch":
        rc, out, err = git_ops.run(["git", "branch", "-vv"], cwd=repo)
        return _clip(out or err)
    if action == "commit":
        message = args.get("message", "update")
        rc, out, err = git_ops.commit_all(repo, message)
        return _clip(out or err)
    if action == "push":
        remote = args.get("remote", "origin")
        branch = args.get("branch", "")
        rc, out, err = git_ops.push(repo, remote=remote, branch=branch)
        return _clip(out or err)
    if action == "pull":
        rc, out, err = git_ops.pull(repo)
        return _clip(out or err)
    if action == "sync":
        auto_push = bool(args.get("auto_push", False))
        info = git_ops.sync_repo(repo, auto_push=auto_push)
        return _clip(f"branch: {info['branch']}\n{info['status']}\nsummary: {info['summary']}")
    return f"error: unknown git action '{action}'. Allowed: status, log, diff, branch, commit, push, pull, clone, sync."


def _tool_media_list(args: Dict) -> str:
    kind = args.get("kind", "")
    rows = media.list_media(kind=kind, limit=20)
    if not rows:
        return "media vault is empty"
    lines = [f"#{r['id']} {r['kind']:<6} {r.get('original_name') or Path(r['path']).name}"
             + (" [cloud]" if r.get('cloud_public_id') else "")
             for r in rows]
    return "\n".join(lines)


def _tool_add_task(args: Dict) -> str:
    title = args.get("title", "").strip()
    if not title:
        return "error: missing 'title'"
    tid = db.add_task(title, project=args.get("project", ""),
                      priority=int(args.get("priority", 3)))
    return f"task #{tid} added: {title}"


def _tool_add_goal(args: Dict) -> str:
    title = args.get("title", "").strip()
    if not title:
        return "error: missing 'title'"
    gid = db.add_goal(title, project=args.get("project", ""))
    return f"goal #{gid} added: {title}"


def _tool_notify(args: Dict) -> str:
    ok = notify.notify(args.get("title", "my-termux"),
                       args.get("content", ""),
                       priority=args.get("priority", "default"))
    return "notification sent" if ok else "notification skipped (no termux-api or disabled)"


def _tool_web_search(args: Dict) -> str:
    """DuckDuckGo Instant Answer API — free, no key."""
    q = args.get("query", "").strip()
    if not q:
        return "error: missing 'query'"
    try:
        import httpx
    except ImportError:
        return "error: httpx not installed"
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as c:
            r = c.get("https://api.duckduckgo.com/",
                      params={"q": q, "format": "json", "no_html": 1, "skip_disambig": 1})
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return f"error: web_search failed: {e}"
    lines = []
    if data.get("AbstractText"):
        lines.append(f"summary: {data['AbstractText']}")
        if data.get("AbstractURL"):
            lines.append(f"source: {data['AbstractURL']}")
    for t in (data.get("RelatedTopics") or [])[:5]:
        if isinstance(t, dict) and t.get("Text"):
            lines.append(f"- {t['Text']}")
            if t.get("FirstURL"):
                lines.append(f"  {t['FirstURL']}")
    if not lines:
        return f"no direct answer for: {q}. Try more specific keywords."
    return _clip("\n".join(lines))


def _tool_exec_heal(args: Dict) -> str:
    """Self-healing runner: runs cmd, auto-installs missing deps, retries."""
    cmd = (args.get("cmd") or "").strip()
    if not cmd:
        return "error: missing 'cmd'"
    cwd = args.get("cwd")
    timeout = int(args.get("timeout", 30))
    attempts = int(args.get("attempts", 3))
    from . import runner as runner_mod
    from pathlib import Path
    cwd_p = Path(cwd).expanduser() if cwd else None
    result = runner_mod.run_with_heal(cmd, cwd=cwd_p, timeout=timeout, max_attempts=attempts)
    out = f"{result.output}\n---\nattempts: {result.attempts}\nhealed: {result.healed}\n"
    if result.fixes_applied:
        out += f"fixes: {', '.join(result.fixes_applied)}\n"
    out += f"summary: {result.summary()}"
    return _clip(out, n=6000)


def _tool_fix_file(args: Dict) -> str:
    p = Path(args.get("path", "")).expanduser()
    err = args.get("error", "") or args.get("error_output", "") or ""
    if not p.exists():
        return f"error: file not found: {p}"
    from . import runner as runner_mod
    ok, msg = runner_mod.fix_file_syntax(p, err)
    return msg


def _tool_clipboard(args: Dict) -> str:
    action = (args.get("action") or "get").lower()
    if action == "get":
        try:
            from . import device as dev_mod
            txt = dev_mod.clipboard_get()
            if not txt:
                return "clipboard empty or termux-api not available"
            return _clip(f"clipboard ({len(txt)} chars):\n{txt}")
        except Exception as e:
            return f"error reading clipboard: {e}"
    elif action == "set":
        txt = args.get("text", "")
        if not txt:
            return "error: missing 'text' for set"
        try:
            from . import device as dev_mod
            ok = dev_mod.clipboard_set(txt)
            return "clipboard set" if ok else "clipboard set failed (termux-api missing?)"
        except Exception as e:
            return f"error: {e}"
    return "error: action must be get|set"


def _tool_speak(args: Dict) -> str:
    text = (args.get("text") or "").strip()
    if not text:
        return "error: missing 'text'"
    try:
        from . import voice as voice_mod
        ok = voice_mod.tts(text)
        return "spoken" if ok else "tts not available (termux-api missing?)"
    except Exception as e:
        return f"tts error: {e}"


def _tool_see(args: Dict) -> str:
    """Capture photo and describe what to do with it."""
    camera = str(args.get("camera", "0"))
    try:
        from . import voice as voice_mod
        path = voice_mod.capture_photo(camera_id=camera)
        if not path:
            return "camera not available (needs termux-api + camera permission)"
        return f"photo captured: {path} — use media_list or attach it to a session"
    except Exception as e:
        return f"camera error: {e}"


def _tool_companion(args: Dict) -> str:
    action = (args.get("action") or "status").lower()
    try:
        from . import companion as comp_mod
        if action == "list":
            lines = [comp_mod.describe_persona(k) for k in comp_mod.list_personas()]
            return "\n".join(lines) + f"\n\ncurrent: {comp_mod.current_persona_name()}"
        if action == "set":
            name = (args.get("name") or "").strip().lower()
            if not name:
                return "error: missing 'name'"
            p = comp_mod.set_persona(name)
            return f"now {p['emoji']} {p['name']} — {p['tagline']}: {p['description']}"
        # status
        cur = comp_mod.get_persona()
        cur_name = comp_mod.current_persona_name()
        return f"{cur['emoji']} {cur['name']} ({cur_name}): {cur['tagline']} — {cur['description']}"
    except Exception as e:
        return f"companion error: {e}"


def _tool_finish(args: Dict) -> str:
    return "__FINISH__"


# ---------------- registry --------------------------------------------------

class Tool:
    def __init__(self, name: str, desc: str, schema: Dict[str, str],
                 fn: Callable[[Dict], str], safety: str = "safe"):
        self.name = name
        self.desc = desc
        self.schema = schema
        self.fn = fn
        self.safety = safety  # "safe" | "confirm" | "danger"


REGISTRY: Dict[str, Tool] = {
    "shell": Tool("shell",
                  "Run a shell command in Termux. Use for ls, cat, grep, python -c, etc. "
                  "Do NOT use for destructive ops (rm -rf, mkfs).",
                  {"cmd": "command string", "cwd": "optional working dir",
                   "timeout": "seconds (default 30)"},
                  _tool_shell, safety="confirm"),
    "run_script": Tool("run_script",
                       "Execute a shell script file, e.g. install.sh or a deployment payload.",
                       {"path": "script path", "cmd": "optional extra args", "cwd": "optional working dir",
                        "timeout": "seconds (default 120)"},
                       _tool_run_script, safety="confirm"),
    "install_package": Tool("install_package",
                             "Install a Termux package with pkg install -y.",
                             {"package": "package name", "timeout": "seconds (default 120)"},
                             _tool_install_package, safety="confirm"),
    "copy_file": Tool("copy_file",
                      "Copy a file from src to dst.",
                      {"src": "source file", "dst": "destination file"},
                      _tool_copy_file, safety="confirm"),
    "make_dir": Tool("make_dir",
                     "Create a directory (and parents) if missing.",
                     {"path": "directory path"},
                     _tool_make_dir, safety="safe"),
    "remove_path": Tool("remove_path",
                         "Remove a file or directory. Use carefully.",
                         {"path": "file or directory path"},
                         _tool_remove_path, safety="confirm"),
    "read_file": Tool("read_file",
                      "Read a file's contents (first max_bytes bytes).",
                      {"path": "file path", "max_bytes": "int, default 8000"},
                      _tool_read_file, safety="safe"),
    "write_file": Tool("write_file",
                       "Write content to a file. Creates a .bak of the previous version.",
                       {"path": "file path", "content": "full new content as string"},
                       _tool_write_file, safety="confirm"),
    "list_dir": Tool("list_dir",
                     "List entries in a directory (up to 200).",
                     {"path": "directory path"},
                     _tool_list_dir, safety="safe"),
    "scan_project": Tool("scan_project",
                         "Detect project kind (python/node/rust/…), git presence, file counts.",
                         {"path": "project root"},
                         _tool_scan_project, safety="safe"),
    "git": Tool("git",
                "Git helper for local repos. Actions: status, log, diff, branch, commit, push, pull, clone.",
                {"action": "status|log|diff|branch|commit|push|pull|clone|sync", "path": "repo path", "message": "commit message", "remote": "remote name", "branch": "branch name", "url": "clone URL", "dest": "clone destination", "auto_push": "bool"},
                _tool_git, safety="confirm"),
    "media_list": Tool("media_list",
                       "List entries in the local media vault.",
                       {"kind": "optional: image|video|audio|doc|other"},
                       _tool_media_list, safety="safe"),
    "add_task": Tool("add_task",
                     "Add a pending task to the user's task list.",
                     {"title": "short task title", "project": "optional",
                      "priority": "1-5 (1 = highest)"},
                     _tool_add_task, safety="safe"),
    "add_goal": Tool("add_goal",
                     "Add a higher-level goal.",
                     {"title": "goal title", "project": "optional"},
                     _tool_add_goal, safety="safe"),
    "notify": Tool("notify",
                   "Send a Termux notification to the user's phone.",
                   {"title": "notification title", "content": "body",
                    "priority": "low|default|high"},
                   _tool_notify, safety="safe"),
    "web_search": Tool("web_search",
                       "Search the web via DuckDuckGo Instant Answer (free, no key). "
                       "Best for definitions, docs, quick facts. Returns summaries + links.",
                       {"query": "search text"},
                       _tool_web_search, safety="safe"),
    "exec_heal": Tool("exec_heal",
                      "Run a shell command with AUTO-HEAL: if it fails due to missing pip/pkg deps, "
                      "installs them and retries. Use this INSTEAD of shell when running user code. "
                      "It fixes ModuleNotFoundError, command not found, etc automatically.",
                      {"cmd": "command string", "cwd": "optional working dir",
                       "timeout": "seconds (default 30)", "attempts": "max attempts (default 3)"},
                      _tool_exec_heal, safety="confirm"),
    "fix_file": Tool("fix_file",
                     "Try to auto-fix common syntax errors in a file (unclosed brackets, missing imports).",
                     {"path": "file path", "error": "error output to guide fix"},
                     _tool_fix_file, safety="confirm"),
    "clipboard": Tool("clipboard",
                      "Read or write Android clipboard via termux-api.",
                      {"action": "get|set", "text": "text to set (for set)"},
                      _tool_clipboard, safety="safe"),
    "speak": Tool("speak",
                  "Speak text aloud via termux-tts-speak (if termux-api available).",
                  {"text": "text to speak"},
                  _tool_speak, safety="safe"),
    "see": Tool("see",
                "Capture a photo via termux-camera-photo (if termux-api available).",
                {"camera": "camera id, 0=back 1=front"},
                _tool_see, safety="safe"),
    "companion": Tool("companion",
                      "Manage companion persona: list, set, status.",
                      {"action": "list|set|status", "name": "persona name for set"},
                      _tool_companion, safety="safe"),
    "finish": Tool("finish",
                   "Signal you are done and don't need another turn. Use when you have "
                   "given your final answer + next-step suggestions.",
                   {},
                   _tool_finish, safety="safe"),
}


def describe_tools() -> str:
    """Render the tool descriptions as a compact string for the system prompt."""
    lines = []
    for t in REGISTRY.values():
        schema = ", ".join(f"{k}={v}" for k, v in t.schema.items()) or "(no args)"
        lines.append(f"- {t.name}({schema}): {t.desc}")
    return "\n".join(lines)


def run_tool(name: str, args: Dict) -> str:
    t = REGISTRY.get(name)
    if t is None:
        return f"error: unknown tool '{name}'. Available: {', '.join(REGISTRY)}"
    try:
        return t.fn(args or {})
    except Exception as e:
        return f"error: tool '{name}' raised: {type(e).__name__}: {e}"


def is_dangerous(name: str, args: Dict) -> bool:
    """Return True if this tool call needs user confirmation."""
    t = REGISTRY.get(name)
    if t is None:
        return False
    if t.safety == "danger":
        return True
    if t.safety == "confirm":
        if name == "shell":
            return tools.is_dangerous(args.get("cmd", ""))
        if name in {"run_script", "install_package", "copy_file", "remove_path", "git"}:
            return True
        if name == "write_file":
            # writing to protected paths always confirms
            p = str(Path(args.get("path", "")).expanduser())
            protected_prefixes = ("/data/data/com.termux/files/usr/",)
            return p.startswith(protected_prefixes) or p.endswith(".bashrc") or p.endswith("/config.yaml")
    return False
