"""Voice I/O — STT + TTS bridge for Termux.

Wraps:
  termux-speech-to-text  -> text
  termux-tts-speak       -> speak text
  termux-microphone-record (fallback)
  termux-tts-engines     -> list engines

HARD RULE: never raises, never hangs forever. On laptop/CI without termux-api,
every function returns None/False and the caller falls back to text.

This is the ears and mouth of `hey` — the hands-free voice coding companion.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional, List, Dict

from . import companion as companion_mod


def _which(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def is_stt_available() -> bool:
    return _which("termux-speech-to-text")


def is_tts_available() -> bool:
    return _which("termux-tts-speak")


def is_mic_available() -> bool:
    return _which("termux-microphone-record")


def tts_engines() -> List[str]:
    if not _which("termux-tts-engines"):
        return []
    try:
        p = subprocess.run(["termux-tts-engines"], capture_output=True, text=True, timeout=4)
        if p.returncode != 0:
            return []
        # output is like: "engine: com.google.android.tts\n..."
        return [l.strip() for l in p.stdout.splitlines() if l.strip()]
    except Exception:
        return []


def stt(timeout: float = 15.0, language: str = "") -> Optional[str]:
    """Listen once via termux-speech-to-text. Returns transcript or None."""
    if not is_stt_available():
        return None
    cmd = ["termux-speech-to-text"]
    # some devices support -l language, but not all — best effort
    # we don't pass language by default to avoid breaking
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None
    if p.returncode != 0:
        return None
    text = (p.stdout or "").strip()
    # termux-speech-to-text sometimes returns multiple partials, one per line.
    # Take the last non-empty line as final.
    if not text:
        return None
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return None
    # Filter out obvious noise: if line is just "..." etc
    final = lines[-1]
    if len(final) < 1:
        return None
    return final


def tts(text: str, pitch: float = 1.0, rate: float = 1.0, language: str = "en",
        engine: str = "", stream: str = "MUSIC", persona: str | None = None) -> bool:
    """Speak text via termux-tts-speak. Returns True if attempted."""
    if not text or not text.strip():
        return False
    if not is_tts_available():
        return False

    # persona-aware tuning
    if persona:
        try:
            p = companion_mod.get_persona(persona)
            tts_cfg = p.get("tts", {})
            pitch = tts_cfg.get("pitch", pitch)
            rate = tts_cfg.get("rate", rate)
        except Exception:
            pass

    # Clean text for TTS: strip code blocks, tool tags, excessive symbols
    clean = _clean_for_tts(text)
    if not clean:
        return False

    # Split long text into sentences to avoid TTS engine choking (limit ~4000 chars but we chunk smaller)
    chunks = _chunk_for_tts(clean, max_len=350)
    ok = True
    for chunk in chunks:
        if not _tts_one(chunk, pitch=pitch, rate=rate, language=language, engine=engine, stream=stream):
            ok = False
            # don't keep trying if one fails, but don't crash
            break
        time.sleep(0.15)  # small gap between chunks
    return ok


def _clean_for_tts(text: str) -> str:
    # Remove <think>, <tool>, code fences, markdown heavy symbols
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<tool.*?</tool>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    # Remove excessive markdown
    text = text.replace("**", "").replace("__", "")
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    # Limit overall length for TTS (spoken answer should be concise)
    if len(text) > 1200:
        # Take first 2 sentences + last sentence that says what's next
        # Simple heuristic: split by .!? and keep first 800 chars
        text = text[:800].rsplit(" ", 1)[0] + "..."
    return text


def _chunk_for_tts(text: str, max_len: int = 350) -> List[str]:
    if len(text) <= max_len:
        return [text]
    # split by sentence boundaries
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: List[str] = []
    cur = ""
    for s in sentences:
        if len(cur) + len(s) + 1 <= max_len:
            cur = (cur + " " + s).strip() if cur else s
        else:
            if cur:
                chunks.append(cur)
            if len(s) > max_len:
                # hard split long sentence
                for i in range(0, len(s), max_len):
                    chunks.append(s[i:i+max_len])
                cur = ""
            else:
                cur = s
    if cur:
        chunks.append(cur)
    return chunks


def _tts_one(text: str, pitch: float, rate: float, language: str, engine: str, stream: str) -> bool:
    cmd = ["termux-tts-speak", "-r", str(rate), "-p", str(pitch), "-s", stream]
    if language:
        cmd.extend(["-l", language])
    if engine:
        cmd.extend(["-e", engine])
    # text as last arg, or via stdin if too weird
    try:
        # Use arg form for short, stdin for long/special chars
        if len(text) < 500 and '"' not in text and "'" not in text:
            p = subprocess.run([*cmd, text], capture_output=True, text=True, timeout=20)
        else:
            p = subprocess.run(cmd, input=text, capture_output=True, text=True, timeout=20)
        return p.returncode == 0
    except Exception:
        return False


def listen_once(prompt_text: str = "Listening...", timeout: float = 15.0,
                persona: str | None = None, speak_prompt: bool = True) -> Optional[str]:
    """High-level: optionally speak a prompt, then STT. Returns transcript or None."""
    if speak_prompt and prompt_text:
        # Don't TTS the prompt if we're in a tight loop — but for first time it's nice
        # We use a very short prompt to avoid talking over user
        tts(prompt_text, persona=persona)
    # small delay to let TTS finish and mic to be ready
    time.sleep(0.3)
    return stt(timeout=timeout)


def speak(text: str, persona: str | None = None, interrupt: bool = False) -> bool:
    """Convenience wrapper that picks persona if not given."""
    if persona is None:
        try:
            persona = companion_mod.current_persona_name()
        except Exception:
            persona = None
    return tts(text, persona=persona)


def vibrate_feedback(kind: str = "tap") -> None:
    """Haptic feedback for voice interactions."""
    try:
        from . import device as device_mod
        if kind == "tap":
            device_mod.vibrate(80)
        elif kind == "success":
            device_mod.vibrate(120)
        elif kind == "error":
            device_mod.vibrate(200)
        elif kind == "listen":
            device_mod.vibrate(50)
    except Exception:
        pass


# ---- clipboard helpers (used by `clip` command and voice flow) --------------

def clipboard_smart() -> Optional[str]:
    try:
        from . import device as device_mod
        return device_mod.clipboard_get()
    except Exception:
        return None


# ---- vision helper (camera as input) ----------------------------------------

def capture_photo(camera_id: str = "0", dest: Path | None = None) -> Optional[Path]:
    """Capture photo via termux-api, returns path or None."""
    if not _which("termux-camera-photo"):
        return None
    if dest is None:
        from . import paths
        import datetime
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = paths.HOME / "media" / "images" / f"hey_{ts}.jpg"
        dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        p = subprocess.run(["termux-camera-photo", "-c", camera_id, str(dest)],
                           capture_output=True, text=True, timeout=15)
        if p.returncode == 0 and dest.exists():
            return dest
    except Exception:
        pass
    return None
