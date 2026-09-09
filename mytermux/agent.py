"""Agent loop for my-termux — the *brain*.

Turns the raw OpenRouter chat wrapper into a genuine agent:
  THINK  → shows visible <think>…</think> reasoning
  ACT    → parses <tool name="…">{json}</tool> blocks and runs them
  OBSERVE → feeds tool output back
  Loops until model emits <tool name="finish"/> or plain final text

Free OpenRouter models sometimes wobble on strict function-calling, so we
use a robust text-protocol (XML wrappers with JSON args) that streams
naturally and is easy to parse and debug.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Callable, Dict, List, Optional, Tuple

from . import db, openrouter, planner
from .memory import Conversation
from .tools_agent import REGISTRY, describe_tools, is_dangerous, run_tool
from mytermux.ai_integration import AIClient, AIClientError
import asyncio


MAX_HOPS = int(os.environ.get("MYTERMUX_AGENT_MAX_HOPS", "6"))

TOOL_BLOCK_RE = re.compile(
    r"<tool\s+name=[\"']([a-z_]+)[\"']\s*>\s*(.*?)\s*</tool>",
    re.DOTALL | re.IGNORECASE,
)
THINK_BLOCK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)


def _now_state_snapshot() -> str:
    """A short local-state blurb we hand to the model each turn."""
    st = planner.current_state()
    parts = [
        f"api_configured={st['api_configured']}",
        f"github_configured={st['github_configured']}",
        f"current_project={st['current_project'] or '(none)'}",
        f"pending_tasks={len(st['pending_tasks'])}",
        f"projects_registered={st['projects_count']}",
        "termux_environment=available",
        "termux_notifications=enabled",
        "termux_media_capture=available",
        "termux_storage_access=available",
    ]
    return "; ".join(parts)


AGENT_SYSTEM_PROMPT = """You are my-termux, a proactive AI AGENT living inside the user's Termux CLI on their Android phone. You do NOT just chat — you THINK, PLAN, and USE TOOLS to actually accomplish tasks and take initiative.

Use a flexible approach when the user gives instructions. If they speak naturally, in plain language, or with loose steps, adapt rather than forcing them into a rigid pattern. You should still be helpful, structured, and action-oriented, but you do not need to mirror an exact variable/action diagram if the user is clearly telling you what they want done.

1) THINK — Wrap your private reasoning in <think>…</think> when it helps. Consider:
   • what is the user's real underlying goal?
   • what local context matters (current project, recent files, pending tasks, recent session history)?
   • what useful next move would actually advance the work on this Android/Termux device?
   • whether the user might want to use installed Termux capabilities such as notifications, media capture, storage access, scripts, or package installs.
   • whether the user might want a related follow-up, a different angle, or a broader plan rather than a narrow answer.
   • what tools would actually help.
   • what could go wrong or be destructive.

2) ACT (optional, repeat as needed) — If a tool would help, emit a tool call in the format below when it makes sense:
   <tool name="TOOL_NAME">
   {"arg": "value", "arg2": "value2"}
   </tool>
   If the user is giving plain-language instructions, you may respond with a short natural-language plan first and then use a tool when needed. The system will run the tool and reply with:
   <observation>…tool output…</observation>
   Read the observation, THINK again, then either call another tool or produce the FINAL answer.

3) FINAL — When you have enough information, give the user a concise, useful answer, THEN 2–4 concrete next-step suggestions each on its own line prefixed with "• ". Prefer suggestions that broaden, connect, or continue the work rather than repeating the same topic. End with <tool name="finish">{}</tool>.

TOOLS AVAILABLE:
{TOOLS}

HARD RULES:
• Never invent tool output. Wait for the <observation>.
• Args must be valid JSON when you use a tool block.
• Prefer safe, reversible actions. If a shell command is destructive, describe the risk BEFORE calling it — the user's terminal will still ask them to confirm.
• Never make up file contents, paths, or git output — read them with a tool.
• Every FINAL turn MUST include the 2–4 "• …" suggestions and end with <tool name="finish">{}</tool>.
• If the user asks a simple question, still offer one helpful next move or alternative angle.
• If the user is stuck, suggest a different tack, not just a rephrase of the same ask.
• Be flexible with natural language instructions and avoid being overly rigid about exact action patterns.

STYLE:
• Be concise. Terminal readers hate walls of text.
• Prefer bullet points and short code blocks.
• When you show a shell command as a suggestion, make it copy-pasteable.
• You are running on a phone — respect small screens.
• Be proactive: connect the user's task to nearby context, pending work, or the next best action.
"""


def _companion_prompt() -> str:
    try:
        from . import companion as comp_mod
        persona = comp_mod.get_persona()
        return f"\n\nCOMPANION PERSONA ({persona['name']}): {persona['system_addition']}\n"
    except Exception:
        return ""


def build_system() -> str:
    base = AGENT_SYSTEM_PROMPT.replace("{TOOLS}", describe_tools())
    # Inject companion vibe + self-heal mandate
    extra = _companion_prompt()
    extra += (
        "\n\nSELF-HEAL MANDATE: You MUST run code you write and verify it works. "
        "Use exec_heal tool instead of shell when running Python/Node/etc. "
        "If a run fails, fix it automatically — install missing packages, correct syntax, "
        "create missing dirs — and re-run until it passes. Never leave broken code. "
        "Never say 'it should work' without testing. "
        "If you fixed something, tell the user what you fixed in your final answer.\n"
    )
    return base + extra


def build_messages(conv: Conversation, user_text: str) -> List[Dict]:
    history = conv.load_history()
    trimmed = history[-conv.max_turns * 2:]
    sys_content = build_system() + "\n\nLOCAL STATE: " + _now_state_snapshot()
    return [{"role": "system", "content": sys_content}, *trimmed,
            {"role": "user", "content": user_text}]


# ---------------- parsing ---------------------------------------------------

def parse_response(text: str) -> Tuple[List[str], Optional[Tuple[str, Dict]], str]:
    """Return (thoughts, (tool_name, args) or None, visible_body)."""
    thoughts = [m.group(1).strip() for m in THINK_BLOCK_RE.finditer(text)]
    tool = None
    m = TOOL_BLOCK_RE.search(text)
    if m:
        name = m.group(1).lower()
        raw_args = m.group(2).strip()
        try:
            args = json.loads(raw_args) if raw_args else {}
            if not isinstance(args, dict):
                args = {"_raw": raw_args}
        except json.JSONDecodeError:
            args = {"_raw_json_error": raw_args}
        tool = (name, args)
    body = THINK_BLOCK_RE.sub("", text)
    body = TOOL_BLOCK_RE.sub("", body).strip()
    return thoughts, tool, body


# ---------------- UI helpers ------------------------------------------------

def _rich_console():
    try:
        from rich.console import Console
        return Console()
    except Exception:
        return None


def _print_thought(text: str, console) -> None:
    if not text.strip():
        return
    if console is not None:
        console.print(f"[dim italic]…thinking: {text.strip()}[/dim italic]")
    else:
        for line in text.strip().splitlines():
            print(f"  … {line}")


def _print_tool(name: str, args: Dict, console) -> None:
    args_short = json.dumps(args, ensure_ascii=False)
    if len(args_short) > 160:
        args_short = args_short[:157] + "..."
    if console is not None:
        console.print(f"[bold magenta]▶ tool[/bold magenta] [bold]{name}[/bold] "
                      f"[dim]{args_short}[/dim]")
    else:
        print(f"▶ tool {name} {args_short}")


def _print_observation(obs: str, console) -> None:
    truncated = obs if len(obs) < 1200 else obs[:1200] + f"\n… [+{len(obs)-1200} chars]"
    if console is not None:
        console.print(f"[green]◀ observation[/green]\n[dim]{truncated}[/dim]")
    else:
        print("◀ observation")
        print(truncated)


def _print_final(body: str, console) -> None:
    if console is not None:
        console.print(body)
    else:
        print(body)


def _confirm(msg: str) -> bool:
    try:
        v = input(f"\n[confirm] {msg} (y/N): ").strip().lower()
    except EOFError:
        return False
    return v in ("y", "yes")


# ---------------- main loop -------------------------------------------------

def _compose_followup_prompt(user_text: str, last_body: str) -> str:
    if not last_body:
        return user_text
    return (
        f"{user_text}\n\n"
        "Keep the conversation moving. If the user’s request is still narrow,"
        " suggest a related next step or a different angle instead of staying on the same topic."
        f"\nPrevious assistant answer:\n{last_body}"
    )


def run_turn(conv: Conversation, user_text: str,
             confirm_fn: Optional[Callable[[str], bool]] = None) -> str:
    """Run a single agent turn (may involve many hops). Returns the final body."""
    console = _rich_console()
    confirm_fn = confirm_fn or _confirm

    conv.record_user(user_text)
    recent = conv.load_history()
    last_assistant = ""
    if recent:
        for item in reversed(recent):
            if item.get("role") == "assistant" and item.get("content"):
                last_assistant = item["content"]
                break
    followup_user_text = _compose_followup_prompt(user_text, last_assistant)
    messages = build_messages(conv, followup_user_text)

    final_body = ""
    last_model = ""
    for hop in range(MAX_HOPS):
        if console is not None:
            console.print(f"\n[bold cyan]agent ›[/bold cyan] [dim](hop {hop+1}/{MAX_HOPS})[/dim]")
        else:
            print(f"\nagent › (hop {hop+1}/{MAX_HOPS})")

        # stream tokens to the user as they come
        buf: List[str] = []

        def on_delta(chunk: str) -> None:
            buf.append(chunk)
            sys.stdout.write(chunk)
            sys.stdout.flush()

        try:
            text, model = openrouter.chat_stream(messages, on_delta=on_delta)
        except RuntimeError as e:
            # Primary streaming failed; attempt a synchronous fallback via configured AI service.
            print(f"\n[error] {e}")
            db.log("error", "agent", str(e))
            try:
                client = AIClient()
                # run blocking send_request in a thread to avoid blocking event loop
                resp = asyncio.run(asyncio.to_thread(client.send_request, {"prompt": followup_user_text}))
                if isinstance(resp, dict):
                    text = resp.get("reply") or resp.get("result") or resp.get("text") or ""
                else:
                    text = str(resp)
                model = "ai-fallback"
            except AIClientError as ai_exc:
                print(f"[ai-fallback error] {ai_exc}")
                return ""
            except Exception as exc:
                print(f"[ai-fallback unexpected] {exc}")
                return ""
        sys.stdout.write("\n")
        sys.stdout.flush()
        last_model = model

        thoughts, tool_call, body = parse_response(text)
        for th in thoughts:
            _print_thought(th, console)

        if tool_call is None:
            # model produced a final answer without calling finish — accept it anyway
            final_body = body or text
            break

        name, args = tool_call
        _print_tool(name, args, console)

        if name == "finish":
            final_body = body
            break

        if is_dangerous(name, args):
            summary = json.dumps(args, ensure_ascii=False)[:200]
            if not confirm_fn(f"run tool `{name}` with {summary}?"):
                observation = "user_denied_tool_call"
            else:
                observation = run_tool(name, args)
        else:
            observation = run_tool(name, args)

        _print_observation(observation, console)

        # feed the model
        messages.append({"role": "assistant", "content": text})
        messages.append({"role": "user",
                         "content": f"<observation>\n{observation}\n</observation>"})
    else:
        final_body = "[agent] reached max hops without a final answer — try rephrasing."

    if final_body:
        _print_final(final_body, console)
    conv.record_assistant(final_body, model=last_model)
    return final_body
