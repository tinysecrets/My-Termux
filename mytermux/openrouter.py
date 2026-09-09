"""OpenRouter free-model client (per integration playbook).

Implements streaming chat completions with ordered free-model fallback and
Retry-After / exponential backoff on transient errors. Safe mid-stream:
once tokens have started arriving we do NOT auto-fallback (would mix outputs).
"""
from __future__ import annotations

import json
import sys
import time
from typing import Callable, Iterable, List, Optional

from . import db
from .config import load_config

API_URL = "https://openrouter.ai/api/v1/chat/completions"


class ORException(Exception):
    def __init__(self, code, message, retry_after=None, partial: str = "", started: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after = retry_after
        self.partial = partial
        self.started = started


def _headers(api_key: str, title: str) -> dict:
    h = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/local/my-termux",
        "X-Title": title or "my-termux",
    }
    return h


def _parse_error(resp) -> tuple:
    retry_after = resp.headers.get("Retry-After") if hasattr(resp, "headers") else None
    try:
        # We are inside `client.stream(...)`, so the body is NOT buffered yet.
        # Without this read, .json()/.text raise ResponseNotRead and the real
        # OpenRouter message ("quota exceeded", "payment required", …) is
        # silently replaced with "unknown error" — which is useless on a phone.
        read = getattr(resp, "read", None)
        if callable(read):
            try:
                read()
            except Exception:
                pass
        body = resp.json()
        err = body.get("error", {})
        code = err.get("code", resp.status_code)
        msg = err.get("message", str(body))
    except Exception:
        code = getattr(resp, "status_code", 500)
        try:
            msg = resp.text or "unknown error"
        except Exception:
            msg = "unknown error"
    return code, msg, retry_after


def _sleep_backoff(retry_after, attempt: int) -> int:
    if retry_after:
        try:
            return max(1, int(float(retry_after)))
        except (ValueError, TypeError):
            pass
    return min(8, 2 ** attempt)


def _stream_one(client, api_key: str, title: str, model: str,
                messages: list, on_delta: Callable[[str], None]) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": 0.3,
    }
    with client.stream("POST", API_URL, headers=_headers(api_key, title), json=payload) as resp:
        if resp.status_code != 200:
            code, msg, retry_after = _parse_error(resp)
            raise ORException(code, msg, retry_after=retry_after)

        chunks: List[str] = []
        started = False
        for line in resp.iter_lines():
            if not line:
                continue
            if line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                return "".join(chunks)
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            if obj.get("error"):
                err = obj["error"]
                raise ORException(err.get("code", 500), err.get("message", "stream error"),
                                  partial="".join(chunks), started=started)
            choice = (obj.get("choices") or [{}])[0]
            if choice.get("finish_reason") == "error":
                err = obj.get("error", {})
                raise ORException(err.get("code", 500), err.get("message", "stream error"),
                                  partial="".join(chunks), started=started)
            delta = (choice.get("delta") or {}).get("content")
            if delta:
                started = True
                chunks.append(delta)
                on_delta(delta)
        return "".join(chunks)


def chat_stream(messages: list, on_delta: Optional[Callable[[str], None]] = None) -> tuple:
    """Run a streamed chat with free-model fallback. Returns (text, model_used).

    Raises RuntimeError if all models fail or no API key.
    """
    try:
        import httpx  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "httpx is not installed. Run `fix` or `pip install httpx`."
        ) from e

    cfg = load_config()
    api_key = (cfg.get("openrouter_api_key") or "").strip()
    if not api_key:
        raise RuntimeError(
            "No OpenRouter API key set. Run `menu` → Settings, or edit "
            "~/my-termux/config/config.yaml"
        )
    title = cfg.get("openrouter_title", "my-termux")
    model_order = cfg.get("model_order") or [
        "deepseek/deepseek-chat-v3.1:free",
        "google/gemini-2.0-flash-exp:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "openrouter/auto",
    ]

    if on_delta is None:
        def on_delta(s: str) -> None:  # default: write to stdout
            sys.stdout.write(s)
            sys.stdout.flush()

    last_err: Optional[Exception] = None
    with httpx.Client(timeout=90.0) as client:
        for model in model_order:
            for attempt in range(2):
                try:
                    text = _stream_one(client, api_key, title, model, messages, on_delta)
                    db.log("info", "openrouter", f"ok model={model} chars={len(text)}")
                    return text, model
                except ORException as e:
                    last_err = e
                    if e.code in (400, 401, 402, 403, 404, 413, 422):
                        db.log("error", "openrouter", f"fatal {e.code} on {model}: {e.message}")
                        raise RuntimeError(f"OpenRouter {e.code}: {e.message}") from e
                    if e.started and e.partial:
                        db.log("warn", "openrouter", f"partial mid-stream fail on {model}: {e.message}")
                        return e.partial, model
                    wait = _sleep_backoff(e.retry_after, attempt)
                    db.log("warn", "openrouter",
                           f"retry {model} in {wait}s ({e.code}: {e.message})")
                    time.sleep(wait)
                except Exception as e:  # network etc.
                    last_err = e
                    db.log("warn", "openrouter", f"network err {model}: {e}")
                    time.sleep(min(4, 2 ** attempt))
    raise RuntimeError(f"All free models failed: {last_err}")
