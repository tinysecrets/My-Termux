"""Self-heal / diagnostics — runs on startup and via `fix`."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict

from . import db, paths
from .config import load_config, save_config, DEFAULT_CONFIG


REQUIRED_PY_PACKAGES = ["httpx", "rich", "yaml"]
OPTIONAL_PY_PACKAGES = ["cloudinary"]


def _check(name: str, ok: bool, detail: str = "") -> Dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


def diagnose() -> List[Dict]:
    checks: List[Dict] = []
    # folders
    for d in paths.ALL_DIRS:
        checks.append(_check(f"dir:{d.name or d}", d.exists(), str(d)))
    # config
    cfg = load_config()
    checks.append(_check("config:file", paths.CONFIG_FILE.exists(), str(paths.CONFIG_FILE)))
    checks.append(_check("config:openrouter_key", bool(cfg.get("openrouter_api_key")),
                         "set" if cfg.get("openrouter_api_key") else "missing"))
    checks.append(_check("config:github_token (optional)", bool(cfg.get("github_token")),
                         "set" if cfg.get("github_token") else "missing (optional)"))
    # DB
    checks.append(_check("db:file", paths.DB_FILE.exists(), str(paths.DB_FILE)))
    # python deps
    for pkg in REQUIRED_PY_PACKAGES:
        mod = "yaml" if pkg == "yaml" else pkg
        try:
            __import__(mod)
            checks.append(_check(f"pip:{pkg}", True))
        except Exception as e:
            checks.append(_check(f"pip:{pkg}", False, str(e)))
    for pkg in OPTIONAL_PY_PACKAGES:
        try:
            __import__(pkg)
            checks.append(_check(f"pip:{pkg} (optional)", True))
        except Exception:
            checks.append(_check(f"pip:{pkg} (optional)", False, "missing (optional)"))
    # media dirs
    from . import media as media_mod
    for sub in media_mod.KIND_DIRS.values():
        d = media_mod.MEDIA_DIR / sub
        checks.append(_check(f"dir:media/{sub}", d.exists(), str(d)))
    # cloudinary configured?
    checks.append(_check(
        "config:cloudinary (optional)",
        bool(cfg.get("cloudinary_cloud_name") and cfg.get("cloudinary_api_key")
             and cfg.get("cloudinary_api_secret")),
        "set" if cfg.get("cloudinary_cloud_name") else "missing (optional)",
    ))
    # cli deps
    for tool in ["git", "python"]:
        checks.append(_check(f"bin:{tool}", shutil.which(tool) is not None,
                             shutil.which(tool) or "missing"))
    # optional
    checks.append(_check("bin:termux-notification (optional)",
                         shutil.which("termux-notification") is not None,
                         shutil.which("termux-notification") or "missing"))
    return checks


def _repair_dirs() -> bool:
    paths.ensure_dirs()
    paths.try_ensure_shared_exports()
    # media dirs
    from . import media as media_mod
    media_mod.ensure_media_dirs()
    return True


def _repair_config() -> bool:
    cfg = load_config()  # this creates default if missing
    # backup
    if paths.CONFIG_FILE.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        bak = paths.BACKUPS_DIR / f"config-{stamp}.yaml"
        try:
            bak.write_text(paths.CONFIG_FILE.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass
    # ensure required keys exist
    changed = False
    for k, v in DEFAULT_CONFIG.items():
        if k not in cfg:
            cfg[k] = v
            changed = True
    if changed:
        save_config(cfg)
    return True


def _repair_db() -> bool:
    db.init_db()
    from . import media as media_mod
    media_mod._ensure_schema()
    return True


def _repair_pip() -> bool:
    cfg = load_config()
    if not cfg.get("auto_pip_install", True):
        return True

    missing = []
    for pkg in REQUIRED_PY_PACKAGES:
        mod = "yaml" if pkg == "yaml" else pkg
        try:
            __import__(mod)
        except Exception:
            missing.append("pyyaml" if pkg == "yaml" else pkg)
    # optional packages — best-effort install, never block
    optional_missing = []
    for pkg in OPTIONAL_PY_PACKAGES:
        try:
            __import__(pkg)
        except Exception:
            optional_missing.append(pkg)
    to_install = missing + optional_missing
    if not to_install:
        return True
    try:
        # Captured on purpose: this runs during shell startup, so letting pip
        # write to the user's terminal means a wall of output every time an
        # optional package is missing. Failures are recorded in the repair log.
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "--upgrade", *to_install],
            check=False, timeout=240, capture_output=True, text=True,
        )
        # success is measured by required packages only — optional ones may
        # legitimately be unavailable (no compiler, no network, locked env)
        for pkg in REQUIRED_PY_PACKAGES:
            mod = "yaml" if pkg == "yaml" else pkg
            try:
                __import__(mod)
            except Exception:
                db.add_repair("install_missing_pip", "fail",
                              (proc.stderr or proc.stdout or "")[-500:])
                return False
        return True
    except Exception as e:
        db.add_repair("install_missing_pip", "fail", f"{type(e).__name__}: {e}")
        return False


def heal(auto: bool = True) -> Dict:
    """Run diagnosis and attempt safe repairs. Returns a report dict."""
    report = {"started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "before": diagnose(), "repairs": []}

    _repair_dirs();   report["repairs"].append({"action": "ensure_dirs", "ok": True})
    _repair_config(); report["repairs"].append({"action": "ensure_config", "ok": True})
    _repair_db();     report["repairs"].append({"action": "ensure_db", "ok": True})
    pip_ok = _repair_pip()
    report["repairs"].append({"action": "install_missing_pip", "ok": pip_ok})

    report["after"] = diagnose()
    # persist a repair record
    for r in report["repairs"]:
        db.add_repair(r["action"], "ok" if r["ok"] else "fail")

    # write a JSON log
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    log_path = paths.LOGS_DIR / f"repair-{stamp}.json"
    try:
        log_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        report["log_path"] = str(log_path)
    except Exception:
        pass
    return report
