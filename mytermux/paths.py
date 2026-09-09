"""Central path management for my-termux.

Everything lives under ~/my-termux/ on the phone.
Shared/Android-visible exports go to ~/storage/shared/MyTermux/exports/.
"""
from __future__ import annotations

import os
from pathlib import Path

HOME = Path(os.environ.get("MYTERMUX_HOME", str(Path.home() / "my-termux")))

APP_DIR = HOME / "app"          # installed source
PROJECTS_DIR = HOME / "projects"
SESSIONS_DIR = HOME / "sessions"
LOGS_DIR = HOME / "logs"
CONFIG_DIR = HOME / "config"
BACKUPS_DIR = HOME / "backups"
CACHE_DIR = HOME / "cache"      # startup caches: device snapshot, heal marker

CONFIG_FILE = CONFIG_DIR / "config.yaml"
DB_FILE = HOME / "mytermux.db"
BANNER_FILE = APP_DIR / "assets" / "banner.txt"

# startup caches — cheap to rebuild, so never precious
DEVICE_CACHE = CACHE_DIR / "device.json"
HEAL_CACHE = CACHE_DIR / "heal.json"
LAST_OPEN_CACHE = CACHE_DIR / "last_open.json"

# Android-visible exports (created lazily; may not exist without termux-setup-storage)
SHARED_EXPORTS = Path.home() / "storage" / "shared" / "MyTermux" / "exports"

ALL_DIRS = [HOME, PROJECTS_DIR, SESSIONS_DIR, LOGS_DIR, CONFIG_DIR, BACKUPS_DIR, CACHE_DIR]


def ensure_dirs() -> None:
    for d in ALL_DIRS:
        d.mkdir(parents=True, exist_ok=True)


def try_ensure_shared_exports() -> bool:
    """Try to create ~/storage/shared/MyTermux/exports. Silently fail if not permitted."""
    try:
        SHARED_EXPORTS.mkdir(parents=True, exist_ok=True)
        return True
    except Exception:
        return False
