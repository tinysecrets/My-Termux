"""Canonical command registry — the single source of truth for CLI names.

WHY THIS FILE EXISTS
--------------------
Command names used to be hard-coded as strings in ~12 modules. When they were
renamed from ``my-chat`` to ``chat``, the planner, the dashboard, the chat help
and the .bashrc hook kept printing the old names — so every "next step" the
dashboard suggested was a command that did not exist on the phone.

Every place that shows a command to the user MUST go through :func:`cmd`, and
every place that installs a command MUST iterate :data:`COMMANDS`. ``cmd()``
raises on an unknown name, so a typo becomes a test failure instead of a
dead suggestion on someone's screen.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class Command:
    """One user-facing command."""

    name: str                  # canonical name symlinked into $PREFIX/bin
    sub: str                   # `python -m mytermux <sub>`
    help: str                  # one-liner, <= 52 chars
    args: Tuple[str, ...] = ()  # sub-actions, used for tab-completion
    hidden: bool = False       # omit from the dashboard hint line


COMMANDS: List[Command] = [
    Command("termux", "dashboard", "dashboard: banner, status, next actions",
            args=("--quick",)),
    Command("start", "start", "full startup: self-heal, then dashboard"),
    Command("now", "now", "instant status card — no heal, no network"),
    Command("chat", "chat", "interactive agent chat (thinking + tools)"),
    Command("ask", "ask", "one-shot question, prints the answer and exits",
            args=("QUESTION",)),
    Command("resume", "resume", "resume your last chat session"),
    Command("menu", "menu", "numeric guided menu"),
    Command("status", "status", "status card without the banner"),
    Command("dev", "dev", "what your phone reports: battery, storage, API"),
    Command("scan", "scan", "scan + register a project", args=("PATH",)),
    Command("sync", "sync", "git status / pull / commit / push",
            args=("--pull", "--commit", "--push")),
    Command("fix", "fix", "diagnose and self-repair the workspace"),
    Command("export", "export", "export session / config / project to /sdcard",
            args=("session", "config", "project")),
    Command("import", "import", "import a previous export",
            args=("session", "config", "project")),
    Command("media", "media", "local media vault",
            args=("add", "list", "info", "open", "rm", "attach", "capture", "record")),
    Command("cloud", "cloud", "optional Cloudinary sync",
            args=("setup", "status", "sync", "up", "pull", "rm", "list")),
    Command("upgrade", "upgrade", "check this app for updates", hidden=True),
]


BY_NAME: Dict[str, Command] = {c.name: c for c in COMMANDS}
BY_SUB: Dict[str, Command] = {c.sub: c for c in COMMANDS}

# Legacy alias prefix. `my-chat` still works so old muscle memory — and every
# screenshot, note and README out there — keeps working after the rename.
ALIAS_PREFIX = "my-"
# The name the .bashrc auto-launch hook calls.
STARTUP_ALIAS = "start-my-termux"


def get(name: str) -> Command:
    """Look a command up by canonical name. Raises KeyError if unknown."""
    try:
        return BY_NAME[name]
    except KeyError as exc:
        known = ", ".join(sorted(BY_NAME))
        raise KeyError(f"unknown my-termux command {name!r}. Known: {known}") from exc


def cmd(name: str, *extra: str) -> str:
    """Build a copy-pasteable command string.

    >>> cmd("scan", "~/projects/foo")
    'scan ~/projects/foo'

    Raises KeyError for an unknown command name, which keeps the dashboard
    honest: it can never print a command that was never installed.
    """
    c = get(name)
    parts = [c.name, *extra]
    return " ".join(parts)


def aliases(name: str) -> List[str]:
    """Legacy ``my-`` prefixed names for a canonical command."""
    get(name)  # validate
    return [ALIAS_PREFIX + name]


def installed_names() -> List[str]:
    """Every filename install.sh must place in $PREFIX/bin.

    Canonical names first, then the ``my-`` aliases, then the startup hook name.
    """
    names = [c.name for c in COMMANDS]
    names += [a for c in COMMANDS for a in aliases(c.name)]
    names.append(STARTUP_ALIAS)
    # de-duplicate, preserve order
    seen: set = set()
    out: List[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def visible_names() -> List[str]:
    """Canonical names worth showing in a hint line."""
    return [c.name for c in COMMANDS if not c.hidden]


def completion_words() -> List[str]:
    """Words bash tab-completion should offer."""
    words: List[str] = []
    for c in COMMANDS:
        words.append(c.name)
        words.extend(a for a in c.args if not a.isupper())
    return sorted(set(words))


def describe(width: int = 72) -> str:
    """A two-column help listing of every command."""
    rows = [(c.name, c.help) for c in COMMANDS if not c.hidden]
    name_w = max(len(n) for n, _ in rows)
    lines = []
    for n, h in rows:
        lines.append(f"  {n.ljust(name_w)}  {h}")
    return "\n".join(lines)


def hint_line(names: Tuple[str, ...] | None = None) -> str:
    """The short 'commands:' footer the dashboard prints."""
    chosen = list(names) if names else ["chat", "ask", "now", "resume", "menu", "fix"]
    return "  ".join(cmd(n) for n in chosen)
