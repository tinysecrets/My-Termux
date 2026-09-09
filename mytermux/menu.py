"""Guided menu — arrow-free numeric picker so it works on any Termux keyboard."""
from __future__ import annotations

import sys

from . import chat, heal, scanner
from .config import load_config, save_config
from .ui import MENU_ITEMS, print_menu, prompt, dashboard
from . import export as export_mod
from . import git_ops


def _settings() -> None:
    cfg = load_config()
    print("\n-- settings --")
    print("1. Set OpenRouter API key")
    print("2. Set GitHub username")
    print("3. Set GitHub Personal Access Token")
    print("4. Toggle auto-dashboard on Termux launch (currently: %s)" % cfg.get("auto_dashboard"))
    print("5. Toggle notifications (currently: %s)" % cfg.get("notifications"))
    print("6. Set current project name")
    print("0. Back")
    ch = prompt("Choose")
    if ch == "1":
        v = prompt("Paste OpenRouter API key (leave blank to cancel)")
        if v:
            cfg["openrouter_api_key"] = v
    elif ch == "2":
        v = prompt("GitHub username", cfg.get("github_username", ""))
        cfg["github_username"] = v
    elif ch == "3":
        v = prompt("Paste GitHub PAT (leave blank to cancel)")
        if v:
            cfg["github_token"] = v
    elif ch == "4":
        cfg["auto_dashboard"] = not cfg.get("auto_dashboard", True)
    elif ch == "5":
        cfg["notifications"] = not cfg.get("notifications", True)
    elif ch == "6":
        v = prompt("Project name", cfg.get("current_project", ""))
        cfg["current_project"] = v
    else:
        return
    save_config(cfg)
    print("[saved]")


def run() -> int:
    while True:
        print_menu()
        ch = prompt("choose #")
        if not ch:
            continue
        try:
            idx = int(ch) - 1
        except ValueError:
            print("enter a number")
            continue
        if not (0 <= idx < len(MENU_ITEMS)):
            print("out of range")
            continue
        _, action = MENU_ITEMS[idx]
        if action == "exit":
            return 0
        if action == "hey":
            from . import cli
            import argparse
            # launch hey in text mode from menu (since menu itself is text)
            cli.cmd_hey(argparse.Namespace(persona=None, text=True, once=False, no_tts=False, prompt=[]))
        elif action == "flow":
            from . import cli
            import argparse
            cli.cmd_flow(argparse.Namespace(persona=None, no_tts=False, prompt=[]))
        elif action == "clip":
            from . import cli
            import argparse
            cli.cmd_clip(argparse.Namespace(text=[], instruction=""))
        elif action == "run":
            from . import cli
            q = prompt("command to run with auto-heal")
            if q:
                import argparse
                cli.cmd_run(argparse.Namespace(cmd_parts=[q], no_heal=False, attempts=3, cwd="."))
        elif action == "companion":
            from . import cli
            import argparse
            cli.cmd_companion(argparse.Namespace(action="status", name=""))
        elif action == "chat":
            chat.run()
        elif action == "resume":
            chat.run(resume=True)
        elif action == "ask":
            q = prompt("question")
            if q:
                from .memory import Conversation
                from . import agent
                conv = Conversation()
                try:
                    agent.run_turn(conv, q)
                finally:
                    conv.close("one-shot ask")
        elif action == "dev":
            from . import cli
            cli.cmd_dev(None)
        elif action == "scan":
            p = prompt("path to scan", ".")
            info = scanner.scan(p)  # type: ignore[arg-type]
            print(info)
        elif action == "sync":
            p = prompt("repo path", ".")
            print(git_ops.status(p))  # type: ignore[arg-type]
        elif action == "fix":
            report = heal.heal()
            fails = [c for c in report["after"] if not c["ok"] and "optional" not in c["name"]]
            print(f"repairs: {len(report['repairs'])}, remaining issues: {len(fails)}")
            for f in fails:
                print(f"  - {f['name']}: {f['detail']}")
        elif action == "export":
            what = prompt("what to export (session|config|project)", "session")
            path = export_mod.export(what)
            print(f"exported: {path}")
        elif action == "settings":
            _settings()
        elif action == "status":
            dashboard(show_banner=False)
    return 0
