"""Interactive chat mode — agent brain by default, plain LLM as fallback."""
from __future__ import annotations

import sys

from . import agent, db, notify, openrouter
from .memory import Conversation, resume_last
from .planner import next_actions


HELP = """\
commands inside chat:
  /help          show this help
  /new           start a fresh session (ends this one)
  /resume        resume the last session (if any)
  /project X     set the current project name
  /goal X        add a new goal
  /task X        add a new pending task
  /suggest       show proactive next actions
  /plain         toggle plain LLM mode (no tools, no thinking) — useful for pure Q&A
  /agent         toggle back to agent mode (default)
  /tools         list the tools the agent can use
  /quit or /q    exit chat

outside chat, from the shell:
  ask "..."      one-shot question — no REPL, prints the answer and exits
  now            instant status card
  dev            battery / storage / termux-api state
"""


def _print_suggestions() -> None:
    acts = next_actions()
    print("\nnext-step suggestions:")
    for i, a in enumerate(acts, 1):
        print(f"  {i}. {a['cmd']} — {a['action']}  (why: {a['why']})")
    print()


def _print_tools() -> None:
    from .tools_agent import REGISTRY
    print("\nagent tools:")
    for name, t in REGISTRY.items():
        print(f"  {name:<14} — {t.desc.splitlines()[0]}")
    print()


def _plain_reply(conv: Conversation, user_text: str) -> None:
    conv.record_user(user_text)
    messages = conv.build_messages(user_text)
    print("agent › ", end="", flush=True)
    try:
        text, model = openrouter.chat_stream(messages)
    except RuntimeError as e:
        print(f"\n[error] {e}\n")
        db.log("error", "chat", str(e))
        return
    print(f"\n[via {model}]")
    conv.record_assistant(text, model=model)


def run(resume: bool = False) -> int:
    conv = resume_last() if resume else None
    if conv is None:
        conv = Conversation()
        print(f"[my-termux] new chat session #{conv.session_id}. Type /help for commands.")
    else:
        print(f"[my-termux] resumed session #{conv.session_id}. Type /help for commands.")

    mode = "agent"  # "agent" | "plain"
    print(f"[my-termux] mode: {mode} (thinking + tools). Toggle with /plain or /agent.")

    while True:
        try:
            user = input("you › ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user:
            continue
        if user in ("/quit", "/q", "/exit"):
            break
        if user == "/help":
            print(HELP)
            continue
        if user == "/new":
            conv.close("ended via /new")
            conv = Conversation()
            print(f"[my-termux] new session #{conv.session_id}")
            continue
        if user == "/resume":
            new_conv = resume_last()
            if new_conv:
                conv = new_conv
                print(f"[my-termux] resumed session #{conv.session_id}")
            continue
        if user == "/suggest":
            _print_suggestions()
            continue
        if user == "/tools":
            _print_tools()
            continue
        if user == "/plain":
            mode = "plain"
            print("[my-termux] mode → plain (no tools, no thinking)")
            continue
        if user == "/agent":
            mode = "agent"
            print("[my-termux] mode → agent (thinking + tools)")
            continue
        if user.startswith("/project "):
            name = user.split(" ", 1)[1].strip()
            from .config import set_value
            set_value("current_project", name)
            print(f"[my-termux] current_project = {name}")
            continue
        if user.startswith("/goal "):
            title = user.split(" ", 1)[1].strip()
            gid = db.add_goal(title)
            print(f"[my-termux] goal #{gid} added.")
            continue
        if user.startswith("/task "):
            title = user.split(" ", 1)[1].strip()
            tid = db.add_task(title)
            print(f"[my-termux] task #{tid} added.")
            continue

        if mode == "agent":
            agent.run_turn(conv, user)
            notify.notify("my-termux", "agent replied")
        else:
            _plain_reply(conv, user)
            notify.notify("my-termux", "agent replied")

    conv.close("chat exited")
    print("[my-termux] session saved. bye.")
    return 0
