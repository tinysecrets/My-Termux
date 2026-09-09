"""Single dispatcher entrypoint for all my-* commands.

Invoked as `python -m mytermux <command> [args...]` OR through the thin
shell wrappers installed in $PREFIX/bin.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import chat as chat_mod
from . import (db, device as device_mod, export as export_mod, git_ops,
               heal as heal_mod, menu, paths, scanner, startup, ui)
from .commands import cmd, describe
from .config import load_config, save_config


def _bootstrap() -> None:
    """Ensure folders + db exist. Runs before every command."""
    paths.ensure_dirs()
    db.init_db()
    load_config()  # creates default if missing
    # ensure media schema + folders too (idempotent)
    from . import media
    media.ensure_media_dirs()
    media._ensure_schema()


def _run_startup_heal(force: bool = False) -> dict:
    """Report health, but only actually probe when the cached verdict is stale.

    Opening a shell should feel instant; `heal()` can shell out to pip, so it
    runs at most once per heal window and is replayed from cache otherwise.
    """
    report = startup.heal_if_stale(force=force)
    issues = startup.outstanding_issues(report)
    fails = issues["required"]
    tag = "" if not report.get("from_cache") else f" (cached {report.get('age_hours', 0):g}h ago)"

    if fails:
        print(f"[my-termux] startup notice: {len(fails)} issue(s) remain — run `{cmd('fix')}`.")
        for item in fails:
            print(f"  - {item['name']}: {item['detail'] or 'missing'}")
    else:
        print(f"[my-termux] startup ready: required checks passed{tag}.")

    optional_missing = issues["optional"]
    if optional_missing and force:
        print("[my-termux] optional items still missing:")
        for item in optional_missing:
            print(f"  - {item['name']}: {item['detail'] or 'missing'}")
    return report


def cmd_dashboard(args) -> int:
    _bootstrap()
    quick = bool(getattr(args, "quick", False))
    if not quick:
        _run_startup_heal()
    ctx = ui.build_context()
    ui.dashboard(show_banner=True, ctx=ctx)
    startup.mark_opened()
    return 0


def cmd_start(args) -> int:
    """Full startup: heal (if stale) then dashboard."""
    _bootstrap()
    _run_startup_heal()
    ctx = ui.build_context()
    ui.dashboard(show_banner=True, ctx=ctx)
    startup.mark_opened()
    return 0


def cmd_now(args) -> int:
    """Instant status card. No heal, no pip probe, no network."""
    _bootstrap()
    ctx = ui.build_context()
    ui.dashboard(show_banner=False, ctx=ctx)
    return 0


def cmd_dev(args) -> int:
    """Show what the phone is reporting. Handy when the dashboard looks empty."""
    _bootstrap()
    device_mod.clear_cache()
    snap = device_mod.snapshot(use_cache=False)
    print("== device ==")
    print(f"  termux:        {'yes' if snap['is_termux'] else 'no'}")
    print(f"  termux-api:    {'yes' if snap['api'] else 'no (install the Termux:API app + `pkg install termux-api`)'}")
    batt = snap.get("battery") or {}
    if batt:
        print(f"  battery:       {device_mod.battery_line(snap)}")
        if batt.get("temperature") is not None:
            print(f"  temperature:   {batt['temperature']}°C")
        if batt.get("health"):
            print(f"  health:        {batt['health']}")
    else:
        print("  battery:       unavailable (termux-api not reachable)")
    sto = snap.get("storage") or {}
    if sto:
        print(f"  disk:          {device_mod.human_bytes(sto['free'])} free of "
              f"{device_mod.human_bytes(sto['total'])} ({sto['percent_free']}% free) at {sto['path']}")
    print(f"  screen width:  {snap['width']} cols")
    clip = device_mod.clipboard_get()
    print(f"  clipboard:     {repr(clip[:40] + '…') if clip and len(clip) > 40 else (repr(clip) if clip else '-')}")
    return 0


def cmd_ask(args) -> int:
    """One-shot question: run one agent turn and exit. Made for phone keyboards."""
    _bootstrap()
    question = " ".join(getattr(args, "question", None) or []).strip()
    if not question:
        print(f"[error] give me a question, e.g.  {cmd('ask')} \"what does this repo do?\"")
        return 1
    from .memory import Conversation
    conv = Conversation()
    try:
        from . import agent
        answer = agent.run_turn(conv, question)
    finally:
        conv.close("one-shot ask")
    # a non-zero exit lets `ask` be used in scripts and pipelines; the error
    # itself was already printed by the agent loop
    return 0 if (answer or "").strip() else 1


def cmd_chat(args) -> int:
    _bootstrap()
    return chat_mod.run(resume=False)


def cmd_menu(args) -> int:
    _bootstrap()
    return menu.run()


def cmd_status(args) -> int:
    _bootstrap()
    ctx = ui.build_context()
    ui.dashboard(show_banner=False, ctx=ctx)
    return 0


def cmd_help(args) -> int:
    print("my-termux — commands\n")
    print(describe())
    print("\nLegacy `my-` prefixed names still work: my-chat, my-menu, my-fix, ...")
    return 0


def cmd_scan(args) -> int:
    _bootstrap()
    path = args.path or "."
    info = scanner.scan(Path(path))
    for k, v in info.items():
        print(f"  {k}: {v}")
    # remember as current project
    if "path" in info:
        cfg = load_config()
        cfg["current_project"] = info["path"]
        save_config(cfg)
        print(f"[my-termux] current_project set to {info['path']}")
    return 0


def cmd_sync(args) -> int:
    _bootstrap()
    path = Path(args.path or ".").expanduser().resolve()
    if not (path / ".git").exists():
        print(f"[my-termux] {path} is not a git repo.")
        return 1

    info = git_ops.sync_repo(path, auto_push=bool(args.push))
    print(f"-- branch: {info['branch']}")
    print(info['status'])
    print(f"[sync] {info['summary']}")

    if args.pull:
        rc, out, err = git_ops.pull(path)
        print(out or err)
    if args.commit:
        rc, out, err = git_ops.commit_all(path, args.commit)
        print(out or err)
    if args.push:
        rc, out, err = git_ops.push(path)
        print(out or err)
    return 0


def cmd_fix(args) -> int:
    _bootstrap()
    report = heal_mod.heal()
    print("== self-heal report ==")
    for r in report["repairs"]:
        print(f"  {r['action']}: {'ok' if r['ok'] else 'fail'}")
    print("-- remaining checks --")
    for c in report["after"]:
        mark = "✓" if c["ok"] else ("~" if "optional" in c["name"] else "✗")
        print(f"  {mark} {c['name']:<32} {c['detail']}")
    if "log_path" in report:
        print(f"log: {report['log_path']}")
    return 0


def cmd_upgrade(args) -> int:
    _bootstrap()
    heal_mod.heal()
    path = Path(getattr(args, "path", ".") or ".").expanduser().resolve()
    info = git_ops.sync_repo(path, auto_push=False)
    print(f"[upgrade] branch: {info['branch']}")
    print(f"[upgrade] {info['summary']}")
    if info.get("status"):
        print(info["status"])
    return 0


def cmd_export(args) -> int:
    _bootstrap()
    what = args.what or "session"
    try:
        path = export_mod.export(what)
        print(f"[my-termux] exported {what}: {path}")
        return 0
    except RuntimeError as e:
        print(f"[error] {e}")
        return 1


def cmd_import(args) -> int:
    _bootstrap()
    what = args.what or "session"
    try:
        path = export_mod.import_export(what, args.path)
        print(f"[my-termux] imported {what}: {path}")
        return 0
    except (FileNotFoundError, RuntimeError, ValueError) as e:
        print(f"[error] {e}")
        return 1


def cmd_resume(args) -> int:
    _bootstrap()
    return chat_mod.run(resume=True)


# --------------------------------------------------------------------------
# media / cloud commands
# --------------------------------------------------------------------------

def _fmt_size(n: int) -> str:
    if not n:
        return "0"
    for unit in ("B", "K", "M", "G"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}T"


def cmd_media(args) -> int:
    _bootstrap()
    from . import media
    action = args.action

    if action == "add":
        src = Path(args.file).expanduser()
        # helpful pre-check: catch obvious mistakes with a friendly message
        if str(src).startswith("<") or "<" in str(src) or ">" in str(src):
            print(f"[error] '{src}' looks like a placeholder. "
                  f"Replace it with a real filename, e.g.\n"
                  f"        media add ~/storage/shared/DCIM/Camera/IMG_20240115_143022.jpg\n"
                  f"        (use Tab-completion: type the folder + start of name, then press Tab)")
            return 1
        if not src.exists():
            print(f"[error] file not found: {src}")
            print("  tip: run `ls ~/storage/shared/DCIM/Camera/` to see your photos,")
            print("       or make sure you ran `termux-setup-storage` at least once.")
            return 1
        if src.is_dir():
            print(f"[error] '{src}' is a directory, not a file.")
            print("  add one file at a time, e.g. media add <path>/photo.jpg")
            return 1
        try:
            row = media.add(src, kind=args.kind or "", tags=args.tags or "",
                            project=args.project or "", move=bool(args.move))
        except FileNotFoundError as e:
            print(f"[error] {e}")
            return 1
        except PermissionError as e:
            print(f"[error] permission denied: {e}")
            print("  tip: for files under ~/storage/shared/... run `termux-setup-storage` first.")
            return 1
        print(f"[media] added #{row['id']} kind={row['kind']} path={row['path']}")
        return 0

    if action == "list":
        rows = media.list_media(kind=args.kind or "", project=args.project or "",
                                limit=args.limit)
        if not rows:
            print("[media] (no items)  tip: `media add <path-to-file>` to import your first item")
            return 0
        print(f"{'ID':>4}  {'KIND':<6} {'SIZE':>7}  {'CLOUD':<6} NAME")
        for r in rows:
            cloud = "yes" if r.get("cloud_public_id") else "-"
            name = r.get("original_name") or Path(r["path"]).name
            print(f"{r['id']:>4}  {r['kind']:<6} {_fmt_size(r.get('size_bytes') or 0):>7}"
                  f"  {cloud:<6} {name}")
        return 0

    if action == "info":
        try:
            row = media.get(args.id)
        except KeyError as e:
            print(f"[error] {e}. Use `media list` to see valid IDs.")
            return 1
        for k, v in row.items():
            print(f"  {k}: {v}")
        return 0

    if action == "open":
        try:
            media.open_with_android(args.id)
        except KeyError as e:
            print(f"[error] {e}. Use `media list` to see valid IDs.")
            return 1
        return 0

    if action == "rm":
        try:
            row = media.remove(args.id, keep_file=bool(args.keep_file))
        except KeyError as e:
            print(f"[error] {e}. Use `media list` to see valid IDs.")
            return 1
        print(f"[media] removed #{row['id']}")
        return 0

    if action == "attach":
        try:
            row = media.attach(args.id, session_id=args.session,
                               project=args.project or "", tags=args.tags or "")
        except KeyError as e:
            print(f"[error] {e}. Use `media list` to see valid IDs.")
            return 1
        print(f"[media] attached #{row['id']} -> "
              f"session={row.get('session_id')} project={row.get('project')} tags={row.get('tags')}")
        return 0

    if action == "capture":
        try:
            row = media.capture_photo(args.camera or "0")
            print(f"[media] photo added #{row['id']} -> {row['path']}")
            return 0
        except RuntimeError as e:
            print(f"[error] {e}")
            return 1

    if action == "record":
        try:
            row = media.record_audio(args.seconds or 10)
            print(f"[media] audio added #{row['id']} -> {row['path']}")
            return 0
        except RuntimeError as e:
            print(f"[error] {e}")
            return 1

    print("[media] unknown action")
    return 2


def cmd_cloud(args) -> int:
    _bootstrap()
    from . import cloud, media
    action = args.action

    if action == "status":
        st = cloud.status()
        print(f"  provider:      {st['provider']}")
        print(f"  configured:    {st['configured']}")
        print(f"  cloud_name:    {st['cloud_name'] or '-'}")
        print(f"  folder_prefix: {st['folder_prefix']}")
        return 0

    if action == "setup":
        print("Cloudinary setup — get creds at https://cloudinary.com/console")
        cn = input("  cloud_name: ").strip()
        ak = input("  api_key:    ").strip()
        sk = input("  api_secret: ").strip()
        if not (cn and ak and sk):
            print("[error] all three values required")
            return 1
        cloud.setup(cn, ak, sk)
        print("[cloud] Cloudinary credentials saved.")
        return 0

    # everything below requires configured creds
    try:
        cloud._configured()  # raise if not set up
    except cloud.CloudNotConfigured as e:
        print(f"[error] {e}")
        return 1

    if action == "sync":
        report = cloud.sync_all()
        print(f"[cloud] uploaded {report['uploaded']} / pending {report['pending_before']}"
              f" (failed {report['failed']})")
        for err in report["errors"]:
            print(f"  ! {err}")
        return 0 if report["failed"] == 0 else 1

    if action == "up":
        try:
            row = cloud.upload(args.id, overwrite=bool(args.force))
            print(f"[cloud] uploaded #{row['id']} -> {row['cloud_url']}")
            return 0
        except Exception as e:
            print(f"[error] {e}")
            return 1

    if action == "pull":
        try:
            dest = cloud.download(args.id)
            print(f"[cloud] downloaded #{args.id} -> {dest}")
            return 0
        except Exception as e:
            print(f"[error] {e}")
            return 1

    if action == "rm":
        try:
            cloud.destroy_asset(args.id, also_local=bool(args.also_local))
            print(f"[cloud] destroyed cloud copy of #{args.id}"
                  + (" (also removed locally)" if args.also_local else ""))
            return 0
        except Exception as e:
            print(f"[error] {e}")
            return 1

    if action == "list":
        rows = cloud.list_remote(max_results=args.limit or 100)
        if not rows:
            print("[cloud] (no remote assets under my-termux/)")
            return 0
        print(f"{'RTYPE':<6} {'SIZE':>7}  PUBLIC_ID")
        for r in rows:
            print(f"{r['resource_type']:<6} {_fmt_size(r.get('bytes') or 0):>7}  {r['public_id']}")
        return 0

    print("[cloud] unknown action")
    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="termux",
        description="Termux AI workspace — your phone as an agent terminal.",
        epilog="Run `termux help` for the full list, or `menu` for a guided picker.",
    )
    sub = p.add_subparsers(dest="cmd")

    dash_p = sub.add_parser("dashboard", help="banner + status + next actions")
    dash_p.add_argument("--quick", action="store_true",
                        help="skip the self-heal probe (faster)")
    dash_p.set_defaults(func=cmd_dashboard)

    sub.add_parser("start", help="full startup: self-heal then dashboard").set_defaults(func=cmd_start)
    sub.add_parser("now", help="instant status card — no heal, no network").set_defaults(func=cmd_now)
    sub.add_parser("dev", help="what your phone reports: battery, storage, API").set_defaults(func=cmd_dev)
    sub.add_parser("chat", help="interactive agent chat").set_defaults(func=cmd_chat)

    ask_p = sub.add_parser("ask", help="one-shot question, prints the answer and exits")
    ask_p.add_argument("question", nargs="*",
                       help='e.g. ask "what does this repo do?"')
    ask_p.set_defaults(func=cmd_ask)

    sub.add_parser("menu", help="numeric guided menu").set_defaults(func=cmd_menu)
    sub.add_parser("status", help="status card without the banner").set_defaults(func=cmd_status)
    sub.add_parser("resume", help="resume the last chat session").set_defaults(func=cmd_resume)
    sub.add_parser("fix", help="diagnose + self-repair").set_defaults(func=cmd_fix)
    sub.add_parser("help", help="list all commands").set_defaults(func=cmd_help)

    upgrade_p = sub.add_parser("upgrade")
    upgrade_p.add_argument("path", nargs="?", default=".")
    upgrade_p.set_defaults(func=cmd_upgrade)

    scan_p = sub.add_parser("scan")
    scan_p.add_argument("path", nargs="?", default=".")
    scan_p.set_defaults(func=cmd_scan)

    sync_p = sub.add_parser("sync")
    sync_p.add_argument("path", nargs="?", default=".")
    sync_p.add_argument("--pull", action="store_true")
    sync_p.add_argument("--commit", help="commit message to make with -a")
    sync_p.add_argument("--push", action="store_true")
    sync_p.set_defaults(func=cmd_sync)

    exp_p = sub.add_parser("export")
    exp_p.add_argument("what", nargs="?", default="session",
                       choices=["session", "config", "project"])
    exp_p.set_defaults(func=cmd_export)

    imp_p = sub.add_parser("import")
    imp_p.add_argument("what", nargs="?", default="session",
                       choices=["session", "config", "project"])
    imp_p.add_argument("path")
    imp_p.set_defaults(func=cmd_import)

    # media
    m_p = sub.add_parser("media", help="local media vault")
    m_sub = m_p.add_subparsers(dest="action", required=True)
    m_add = m_sub.add_parser("add")
    m_add.add_argument("file")
    m_add.add_argument("--kind", choices=["image", "video", "audio", "doc", "other"])
    m_add.add_argument("--tags")
    m_add.add_argument("--project")
    m_add.add_argument("--move", action="store_true", help="move instead of copy")
    m_list = m_sub.add_parser("list")
    m_list.add_argument("--kind", choices=["image", "video", "audio", "doc", "other"])
    m_list.add_argument("--project")
    m_list.add_argument("--limit", type=int, default=50)
    m_info = m_sub.add_parser("info")
    m_info.add_argument("id", type=int)
    m_open = m_sub.add_parser("open")
    m_open.add_argument("id", type=int)
    m_rm = m_sub.add_parser("rm")
    m_rm.add_argument("id", type=int)
    m_rm.add_argument("--keep-file", action="store_true")
    m_att = m_sub.add_parser("attach")
    m_att.add_argument("id", type=int)
    m_att.add_argument("--session", type=int)
    m_att.add_argument("--project")
    m_att.add_argument("--tags")
    m_cap = m_sub.add_parser("capture", help="camera photo via termux-api")
    m_cap.add_argument("--camera", default="0", help='camera id ("0" back, "1" front)')
    m_rec = m_sub.add_parser("record", help="mic recording via termux-api")
    m_rec.add_argument("seconds", nargs="?", type=int, default=10)
    m_p.set_defaults(func=cmd_media)

    # cloud
    c_p = sub.add_parser("cloud", help="Cloudinary sync (optional)")
    c_sub = c_p.add_subparsers(dest="action", required=True)
    c_sub.add_parser("status")
    c_sub.add_parser("setup")
    c_sub.add_parser("sync")
    c_up = c_sub.add_parser("up")
    c_up.add_argument("id", type=int)
    c_up.add_argument("--force", action="store_true")
    c_pl = c_sub.add_parser("pull")
    c_pl.add_argument("id", type=int)
    c_rm = c_sub.add_parser("rm")
    c_rm.add_argument("id", type=int)
    c_rm.add_argument("--also-local", action="store_true")
    c_ls = c_sub.add_parser("list")
    c_ls.add_argument("--limit", type=int, default=100)
    c_p.set_defaults(func=cmd_cloud)

    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return cmd_dashboard(argparse.Namespace())
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
