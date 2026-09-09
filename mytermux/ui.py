"""Terminal UI — dashboard, menus, banners. Uses `rich` if available, else plain.

The dashboard is the single most-seen surface in the app, so it is written to
be read at a glance on a phone:

  * width-aware — Termux in portrait is ~50 columns, so everything degrades
  * no network, no blocking probes (device facts come from a TTL cache)
  * every command it prints is guaranteed to exist (see commands.py)
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import paths, planner, startup
from . import device as device_mod
from .commands import cmd, hint_line
from .config import load_config


def _rich():
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text
        from rich.align import Align
        return Console, Panel, Table, Text, Align
    except Exception:
        return None


BANNER = r"""
                       __                                      
   ____ ___  __  __   / /____  _________ ___  __  ___  __      
  / __ `__ \/ / / /  / __/ _ \/ ___/ __ `__ \/ / / / |/_/      
 / / / / / / /_/ /  / /_/  __/ /  / / / / / / /_/ />  <        
/_/ /_/ /_/\__, /   \__/\___/_/  /_/ /_/ /_/\__,_/_/|_|        
          /____/   your phone. your agent. your workspace.     
"""

# Narrower banner for portrait phones — the wide one wraps badly under ~60 cols.
BANNER_NARROW = r"""
  ███╗ ███╗ ██╗  ██╗
  ████╗████║  ██╗ ██╔╝
  ██╔████╔██║   ████╔╝
  ██║╚██╔╝██║   ██╔██╗
  ╚═╝     ╚═╝   ╚═╝ ╚═╝  my-termux
"""


def read_banner(narrow: bool = False) -> str:
    try:
        if paths.BANNER_FILE.exists():
            text = paths.BANNER_FILE.read_text(encoding="utf-8")
            # only trust the custom banner when there is room to render it
            if not narrow:
                return text
            widest = max((len(l) for l in text.splitlines()), default=0)
            if widest <= 56:
                return text
    except Exception:
        pass
    return BANNER_NARROW if narrow else BANNER


def greeting(now: Optional[datetime] = None) -> str:
    h = (now or datetime.now()).hour
    if h < 5:
        return "late night"
    if h < 12:
        return "good morning"
    if h < 18:
        return "good afternoon"
    return "good evening"


# ---- the context bundle -----------------------------------------------------

def build_context(heal_report: Optional[Dict] = None,
                  use_device_cache: bool = True) -> Dict[str, Any]:
    """Gather everything the dashboard needs in one pass.

    Cheap by construction: one disk-cached device snapshot, a handful of
    indexed SQLite counts, and at most three git subprocesses.
    """
    cfg = load_config()
    state = planner.current_state()
    dev = device_mod.snapshot(use_cache=use_device_cache)
    dig = startup.digest()

    proj = state.get("current_project") or ""
    git: Dict[str, Any] = {"repo": False, "branch": "", "dirty": False,
                           "ahead": False, "behind": False}
    if proj and (Path(proj).expanduser() / ".git").exists():
        git = planner._project_git(proj)

    media_count = 0
    try:
        from . import media
        media._ensure_schema()
        media_count = len(media.list_media(limit=1000))
    except Exception:
        pass

    return {
        "cfg": cfg,
        "state": state,
        "device": dev,
        "digest": dig,
        "git": git,
        "project": proj,
        "media_count": media_count,
        "heal": heal_report,
    }


def _short_model(cfg: Dict[str, Any], limit: int = 18) -> str:
    """`deepseek/deepseek-chat-v3.1:free` -> `deepseek-chat-v3.1`.

    The provider prefix and the `:free` suffix carry no information on a
    status line, and the full string overflows a portrait-width column.
    """
    order = cfg.get("model_order") or []
    if not order:
        return "(none)"
    name = str(order[0]).split("/")[-1]
    if name.endswith(":free"):
        name = name[: -len(":free")]
    return name if len(name) <= limit else name[: limit - 1] + "\u2026"


def _status_rows(ctx: Dict[str, Any]) -> List[tuple]:
    cfg, state = ctx["cfg"], ctx["state"]
    dev = ctx["device"] or {}
    sto = dev.get("storage") or {}
    git = ctx["git"] or {}

    rows: List[tuple] = [
        ("OpenRouter API", "ok" if state["api_configured"] else "missing",
         _short_model(cfg)),
        ("GitHub token", "ok" if state["github_configured"] else "missing",
         cfg.get("github_username", "") or "-"),
    ]

    proj = ctx["project"]
    if proj:
        name = Path(proj).name
        if git.get("repo"):
            marks = []
            if git.get("dirty"):
                marks.append("dirty")
            if git.get("ahead"):
                marks.append("ahead")
            if git.get("behind"):
                marks.append("behind")
            extra = f"{git.get('branch') or '?'}"
            if marks:
                extra += " · " + "/".join(marks)
            rows.append(("Project", "git", f"{name}  ({extra})"))
        else:
            rows.append(("Project", "ok", name))
    else:
        rows.append(("Project", "-", "(none — try `scan .`)"))

    rows.append(("Sessions", "ok" if state["last_session_id"] else "-",
                 f"#{state['last_session_id']} of {ctx['digest']['totals'].get('sessions', 0)}"
                 if state["last_session_id"] else "(none)"))
    rows.append(("Pending tasks", "ok" if state["pending_tasks"] else "-",
                 str(ctx["digest"]["totals"].get("tasks_pending", 0))))
    rows.append(("Media vault", "ok" if ctx["media_count"] else "-",
                 f"{ctx['media_count']} item(s)"))
    rows.append(("Disk free", "ok" if sto else "-",
                 device_mod.human_bytes(sto.get("free")) if sto else "-"))
    return rows


def _header_line(ctx: Dict[str, Any], width: int) -> str:
    """`Tue 09 Sep 14:22 · 78% ▓▓▓▓▓▓▓░░░ usb · 41.2G free`, trimmed to fit.

    Least-important segments are dropped from the right until the line fits,
    so it never wraps onto a second line inside the panel.
    """
    now = datetime.now()
    stamp = now.strftime("%a %d %b %H:%M")
    dev = ctx["device"] or {}
    bar_w = 6 if width < 70 else 10
    batt = device_mod.battery_line(dev, width=bar_w)
    sto = dev.get("storage") or {}
    disk = (f"{device_mod.human_bytes(sto.get('free'))} free"
            if sto.get("free") is not None else "")
    parts = [stamp]
    if batt != "-":
        parts.append(batt)
    if disk:
        parts.append(disk)
    while len(parts) > 1 and len("  ·  ".join(parts)) > width:
        parts.pop()
    return "  ·  ".join(parts)


def _next_actions(ctx: Dict[str, Any]) -> List[Dict]:
    return planner.next_actions(device=ctx["device"])


# ---- plain-text rendering (no rich) -----------------------------------------

def _plain(ctx: Dict[str, Any], show_banner: bool, width: int) -> None:
    narrow = width < 62
    if show_banner:
        print(read_banner(narrow=narrow))
    print("=" * min(width, 62))
    print(f"  my-termux — {greeting()}")
    print(f"  {_header_line(ctx, min(width, 62) - 2)}")
    print("=" * min(width, 62))
    for name, status, extra in _status_rows(ctx):
        print(f"  {name:<15} {status:<8} {extra}")

    dl = startup.digest_line(ctx["digest"])
    if dl:
        print("-" * min(width, 62))
        print(f"  since last time: {dl}")
    snip = startup.snippet(ctx["digest"].get("last_reply"), width=max(20, width - 22))
    if snip:
        print(f"  last answer: {snip}")

    print("-" * min(width, 62))
    print("  next:")
    for i, a in enumerate(_next_actions(ctx), 1):
        print(f"   {i}. {a['cmd']}")
        print(f"      {a['action']}  ({a['why']})")
    print("=" * min(width, 62))
    print(f"  commands: {hint_line()}")
    print()


# ---- rich rendering ---------------------------------------------------------

def _rich_dashboard(ctx: Dict[str, Any], show_banner: bool, width: int) -> None:
    Console, Panel, Table, Text, Align = _rich()
    narrow = width < 62
    con = Console(width=width)

    if show_banner:
        con.print(Text(read_banner(narrow=narrow),
                       style="bold cyan", justify="center" if not narrow else "left"))

    # header strip
    header = Text()
    header.append(f"  {greeting()}  ", style="bold white")
    header.append("·  ", style="dim")
    header.append(_header_line(ctx, width - 6))
    con.print(Panel(header, border_style="cyan", padding=(0, 1)))

    # status table
    tbl = Table(show_header=False, expand=True, box=None, padding=(0, 1))
    tbl.add_column(style="bold", no_wrap=True, width=15)
    tbl.add_column(width=8, no_wrap=True)
    tbl.add_column(style="dim", overflow="fold")
    for name, status, extra in _status_rows(ctx):
        color = {"ok": "green", "git": "blue", "-": "yellow"}.get(status, "red")
        tbl.add_row(name, f"[{color}]{status}[/{color}]", str(extra))
    con.print(Panel(tbl, title="[bold cyan]status[/bold cyan]",
                    border_style="cyan", padding=(0, 1)))

    # what changed since you were last here
    dig = ctx["digest"]
    dl = startup.digest_line(dig)
    snip = startup.snippet(dig.get("last_reply"), width=max(24, width - 12))
    if dl or snip:
        body = Text()
        if dl:
            body.append(dl + "\n")
        if snip:
            body.append("last answer: ", style="dim")
            body.append(snip, style="italic")
        con.print(Panel(body, title="[bold yellow]since last time[/bold yellow]",
                        border_style="yellow", padding=(0, 1)))

    # next steps.
    # Deliberately a stacked two-column layout rather than a 4-column table:
    # a table sizes its columns from the longest cell, and one long command
    # (e.g. `ask "work on: ..."` ) squeezes every other column into a 6-char
    # ribbon. Stacked rows stay readable from 40 to 200 columns.
    actions = _next_actions(ctx)
    act = Table(show_header=False, box=None, expand=True, padding=(0, 1))
    act.add_column(style="dim", width=3, no_wrap=True)
    act.add_column(overflow="fold")
    for i, a in enumerate(actions, 1):
        body = Text()
        body.append(a["cmd"] + "\n", style="bold green")
        body.append(a["action"])
        body.append(f"  ({a['why']})", style="dim italic")
        act.add_row(f"{i}.", body)
    con.print(Panel(act, title="[bold magenta]next[/bold magenta]",
                    border_style="magenta", padding=(0, 1)))

    con.print(f"[dim]commands:[/dim] {hint_line()}  [dim]·[/dim] "
              f"[bold]{cmd('menu')}[/bold] [dim]for everything else[/dim]\n")


# ---- entry point ------------------------------------------------------------

def dashboard(show_banner: bool = True, ctx: Optional[Dict[str, Any]] = None) -> None:
    """Render the dashboard. Pass a prebuilt `ctx` to avoid recomputing."""
    width = device_mod.screen_width()
    if ctx is None:
        ctx = build_context()
    if _rich() is None:
        _plain(ctx, show_banner, width)
        return
    _rich_dashboard(ctx, show_banner, width)


MENU_ITEMS = [
    ("💫 Hey — voice coding companion (NEW)", "hey"),
    ("Flow — continuous talk-coding", "flow"),
    ("Chat with agent", "chat"),
    ("Resume last session", "resume"),
    ("Ask one question and exit", "ask"),
    ("Clipboard agent — act on copied text", "clip"),
    ("Run with auto-heal", "run"),
    ("Companion persona", "companion"),
    ("Scan a project", "scan"),
    ("GitHub sync (status)", "sync"),
    ("Run self-heal", "fix"),
    ("Show device info", "dev"),
    ("Export data", "export"),
    ("Settings", "settings"),
    ("Show status", "status"),
    ("Exit", "exit"),
]


def print_menu() -> None:
    r = _rich()
    if r is None:
        print("\n== my-termux menu ==")
        for i, (label, _) in enumerate(MENU_ITEMS, 1):
            print(f"  {i}. {label}")
        return
    Console, Panel, Table, Text, Align = r
    con = Console()
    tbl = Table(show_header=False, box=None, padding=(0, 2))
    tbl.add_column("#", style="bold cyan", width=3)
    tbl.add_column("action")
    for i, (label, _) in enumerate(MENU_ITEMS, 1):
        tbl.add_row(str(i), label)
    con.print(Panel(tbl, title="[bold cyan]my-termux menu[/bold cyan]",
                    border_style="cyan", padding=(1, 2)))


def prompt(msg: str, default: str = "") -> str:
    tail = f" [{default}]" if default else ""
    try:
        val = input(f"{msg}{tail}: ").strip()
    except EOFError:
        return default
    return val or default


def confirm(msg: str, default: bool = False) -> bool:
    d = "Y/n" if default else "y/N"
    v = prompt(f"{msg} ({d})").lower()
    if not v:
        return default
    return v in ("y", "yes")
