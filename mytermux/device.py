"""Termux / Android device bridge.

This is what makes the dashboard feel like it belongs on *your phone* instead
of a generic terminal: battery, charging state, temperature, free storage,
screen size — the things you actually glance at before you start typing.

HARD RULE: nothing in here may raise or hang. On a laptop, in CI, or on a
phone without the Termux:API app installed, every probe returns ``None`` and
the caller draws a dash. Each probe is also cheap-cached on disk so an
interactive shell startup never shells out more than it must.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from . import paths


# ---- capability detection ---------------------------------------------------

def is_termux() -> bool:
    """True when we look like we are running inside Termux on Android."""
    if os.environ.get("MYTERMUX_FORCE") == "1":
        return True
    if os.environ.get("PREFIX", "").startswith("/data/data/com.termux"):
        return True
    return Path("/data/data/com.termux/files/usr").is_dir()


def api_available() -> bool:
    """True when the Termux:API commands are reachable."""
    return shutil.which("termux-battery-status") is not None


def _run(cmd: list, timeout: float = 3.0) -> Optional[str]:
    """Run a helper, return stdout or None on any failure."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if p.returncode != 0:
        return None
    return p.stdout


# ---- individual probes ------------------------------------------------------

def battery(use_cache: bool = True, ttl: int = 60) -> Optional[Dict[str, Any]]:
    """Battery percent / charge state / temperature, or None if unavailable.

    ``termux-battery-status`` returns a JSON *list* with one entry per battery.
    """
    if not api_available():
        return None
    if use_cache:
        cached = _cache_read("battery", ttl)
        if cached is not None:
            return cached
    raw = _run(["termux-battery-status"], timeout=4.0)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    entry = data[0] if isinstance(data, list) and data else (
        data if isinstance(data, dict) else None)
    if not isinstance(entry, dict):
        return None
    out = {
        "percent": entry.get("percentage"),
        "plugged": (entry.get("plugged") or "UNPLUGGED").upper(),
        "status": (entry.get("status") or "").upper(),
        "temperature": entry.get("temperature"),
        "health": entry.get("health"),
    }
    _cache_write("battery", out)
    return out


def storage(path: str | Path | None = None) -> Optional[Dict[str, Any]]:
    """Free / total bytes for a filesystem. Uses only stdlib, so it works
    everywhere (no termux-api needed)."""
    target = str(path or os.environ.get("HOME", "."))
    try:
        du = shutil.disk_usage(target)
    except (OSError, ValueError):
        return None
    if du.total <= 0:
        return None
    return {
        "path": target,
        "free": du.free,
        "total": du.total,
        "percent_free": round(100.0 * du.free / du.total, 1),
    }


def screen_width(default: int = 80) -> int:
    """Terminal width in columns, clamped to something a phone can render."""
    try:
        cols = int(os.environ.get("COLUMNS", "0")) or shutil.get_terminal_size().columns
    except (ValueError, OSError):
        cols = default
    return max(40, min(cols, 200))


def clipboard_set(text: str) -> bool:
    """Copy text to the Android clipboard. Best-effort."""
    if shutil.which("termux-clipboard-set") is None:
        return False
    try:
        p = subprocess.run(["termux-clipboard-set"], input=text, text=True,
                           capture_output=True, timeout=4)
        return p.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def clipboard_get() -> Optional[str]:
    """Read the Android clipboard, or None."""
    if shutil.which("termux-clipboard-get") is None:
        return None
    out = _run(["termux-clipboard-get"], timeout=4.0)
    return out.rstrip("\n") if out is not None else None


def vibrate(ms: int = 200) -> bool:
    """Buzz the phone. Nice for 'long job finished' signals."""
    if shutil.which("termux-vibrate") is None:
        return False
    try:
        p = subprocess.run(["termux-vibrate", "-d", str(int(ms))],
                           capture_output=True, timeout=4)
        return p.returncode == 0
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return False


# ---- aggregate snapshot -----------------------------------------------------

def voice_capabilities() -> Dict[str, bool]:
    """Check which voice features are available (never raises)."""
    return {
        "stt": shutil.which("termux-speech-to-text") is not None,
        "tts": shutil.which("termux-tts-speak") is not None,
        "mic": shutil.which("termux-microphone-record") is not None,
        "camera": shutil.which("termux-camera-photo") is not None,
        "clipboard": shutil.which("termux-clipboard-get") is not None,
    }


def snapshot(use_cache: bool = True, ttl: int = 120) -> Dict[str, Any]:
    """One dict describing the device, safe to call on every startup."""
    if use_cache:
        cached = _cache_read("snapshot", ttl)
        if cached is not None:
            # copy: the marker must not be written back into the live cache
            out = dict(cached)
            out["cached"] = True
            return out
    snap: Dict[str, Any] = {
        "is_termux": is_termux(),
        "api": api_available(),
        "battery": battery(use_cache=use_cache),
        "storage": storage(),
        "width": screen_width(),
        "voice": voice_capabilities(),
    }
    _cache_write("snapshot", snap)
    return snap


# ---- presentation helpers ---------------------------------------------------

def human_bytes(n: float | int | None) -> str:
    """1536 -> '1.5K', big values get one decimal."""
    if n is None:
        return "-"
    n = float(n)
    for unit in ("B", "K", "M", "G"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}T"


def battery_bar(percent: Optional[float], width: int = 10) -> str:
    """'▓▓▓▓▓▓▓░░░' — a glanceable bar that renders in any Termux font."""
    if percent is None:
        return "-" * width
    try:
        p = max(0.0, min(100.0, float(percent)))
    except (TypeError, ValueError):
        return "-" * width
    filled = int(round(p / 100.0 * width))
    return "\u2593" * filled + "\u2591" * (width - filled)


def battery_style(percent: Optional[float], plugged: str = "") -> str:
    """rich colour name for a battery reading."""
    if percent is None:
        return "dim"
    if plugged and plugged != "UNPLUGGED":
        return "green"
    if percent <= 15:
        return "red"
    if percent <= 30:
        return "yellow"
    return "green"


def battery_line(snap: Dict[str, Any], width: int = 10) -> str:
    """One short line: '78% ▓▓▓▓▓▓▓░░░ USB' or '-' when unknown."""
    b = snap.get("battery") if snap else None
    if not b or b.get("percent") is None:
        return "-"
    pct = b["percent"]
    plugged = b.get("plugged") or "UNPLUGGED"
    suffix = ""
    if plugged != "UNPLUGGED":
        suffix = f" {plugged.lower()}"
    elif b.get("status") == "CHARGING":
        suffix = " charging"
    return f"{pct:.0f}% {battery_bar(pct, width)}{suffix}"


# ---- cache plumbing ---------------------------------------------------------

_CACHE: Dict[str, Any] = {}


def _cache_file() -> Path:
    return paths.DEVICE_CACHE


def _cache_load() -> Dict[str, Any]:
    global _CACHE
    if _CACHE:
        return _CACHE
    try:
        _CACHE = json.loads(_cache_file().read_text(encoding="utf-8"))
        if not isinstance(_CACHE, dict):
            _CACHE = {}
    except (OSError, json.JSONDecodeError, ValueError):
        _CACHE = {}
    return _CACHE


def _cache_read(key: str, ttl: int) -> Optional[Any]:
    """Return the cached value for `key` if younger than `ttl` seconds."""
    import time
    data = _cache_load()
    entry = data.get(key)
    if not isinstance(entry, dict):
        return None
    ts = entry.get("ts")
    if not isinstance(ts, (int, float)) or (time.time() - ts) > ttl:
        return None
    return entry.get("value")


def _cache_write(key: str, value: Any) -> None:
    import time
    data = _cache_load()
    data[key] = {"ts": time.time(), "value": value}
    try:
        _cache_file().parent.mkdir(parents=True, exist_ok=True)
        _cache_file().write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def clear_cache() -> None:
    """Drop the in-memory and on-disk device cache."""
    global _CACHE
    _CACHE = {}
    try:
        _cache_file().unlink()
    except OSError:
        pass
