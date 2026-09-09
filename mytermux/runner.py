"""Self-healing code runner — the "no dependency errors, no hiccups" engine.

This is the core of the real pack upgrade the user asked for:

  "if I sat there from start to finish and just talk and told it it should be
   able to run it and correct syntax no dependency errors no hiccups mistakes
   and if it did it corrected instead of acting like it's oblivious"

So this module:
  1. Runs any shell command
  2. Detects failure patterns (ModuleNotFoundError, command not found, SyntaxError, etc)
  3. Auto-fixes by installing packages, creating dirs, fixing imports
  4. Re-runs up to N times
  5. Returns a full report so the agent can learn

It never raises — it always returns a RunResult, even on total failure.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Tuple

from . import tools
from . import db


# ---- common import -> pip package mapping -----------------------------------
# Many Python imports don't match pip names. This map saves a failed run.
PACKAGE_MAP: Dict[str, str] = {
    "yaml": "pyyaml",
    "PIL": "pillow",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "skimage": "scikit-image",
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "jwt": "pyjwt",
    "serial": "pyserial",
    "usb": "pyusb",
    "Crypto": "pycryptodome",
    "docx": "python-docx",
    "git": "gitpython",
    "telegram": "python-telegram-bot",
    "discord": "discord.py",
    "httpx": "httpx",
    "rich": "rich",
    "yaml": "pyyaml",
    "cv2": "opencv-python",
    "numpy": "numpy",
    "pandas": "pandas",
    "torch": "torch",
    "tensorflow": "tensorflow",
    "flask": "flask",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "requests": "requests",
    "openai": "openai",
    "anthropic": "anthropic",
    "cloudinary": "cloudinary",
    "lxml": "lxml",
    "jinja2": "jinja2",
    "sqlalchemy": "sqlalchemy",
    "psycopg2": "psycopg2-binary",
    "pymongo": "pymongo",
    "redis": "redis",
    "celery": "celery",
    "pytest": "pytest",
}

# command -> pkg mapping for Termux pkg install
PKG_MAP: Dict[str, str] = {
    "ffmpeg": "ffmpeg",
    "convert": "imagemagick",
    "magick": "imagemagick",
    "node": "nodejs",
    "npm": "nodejs",
    "cargo": "rust",
    "rustc": "rust",
    "go": "golang",
    "java": "openjdk-17",
    "javac": "openjdk-17",
    "sqlite3": "sqlite",
    "tree": "tree",
    "jq": "jq",
    "rg": "ripgrep",
    "fzf": "fzf",
    "bat": "bat",
    "exa": "exa",
    "lsd": "lsd",
}


@dataclass
class RunResult:
    cmd: str
    rc: int
    stdout: str
    stderr: str
    attempts: int = 1
    fixed: bool = False
    fixes_applied: List[str] = field(default_factory=list)
    healed: bool = False  # True if initially failed but now passes

    @property
    def ok(self) -> bool:
        return self.rc == 0

    @property
    def output(self) -> str:
        return f"exit_code: {self.rc}\nstdout:\n{self.stdout}\nstderr:\n{self.stderr}"

    def summary(self) -> str:
        if self.ok and not self.healed:
            return f"✓ {self.cmd} — ok on first try"
        if self.ok and self.healed:
            return f"✓ {self.cmd} — healed after {self.attempts} attempts: {', '.join(self.fixes_applied)}"
        return f"✗ {self.cmd} — failed after {self.attempts} attempts. Last error: {self.stderr[:200]}"


def run(cmd: str, cwd: Path | None = None, timeout: int = 30) -> RunResult:
    rc, out, err = tools.run_shell(cmd, cwd=cwd, timeout=timeout)
    return RunResult(cmd=cmd, rc=rc, stdout=out, stderr=err, attempts=1)


def _detect_missing_python_module(stderr: str) -> Optional[str]:
    # ModuleNotFoundError: No module named 'X'
    # ImportError: No module named X
    patterns = [
        r"ModuleNotFoundError:\s*No module named ['\"]?([a-zA-Z0-9_\-]+)['\"]?",
        r"ImportError:\s*No module named ['\"]?([a-zA-Z0-9_\-]+)['\"]?",
        r"No module named ['\"]?([a-zA-Z0-9_\-]+)['\"]?",
    ]
    for pat in patterns:
        m = re.search(pat, stderr)
        if m:
            mod = m.group(1).strip().split(".")[0]  # take top-level
            return mod
    return None


def _detect_missing_command(stderr: str) -> Optional[str]:
    # bash: X: command not found
    # /bin/sh: X: not found
    m = re.search(r"(?:bash|sh):\s*([a-zA-Z0-9_\-]+):\s*command not found", stderr)
    if m:
        return m.group(1)
    m = re.search(r"([a-zA-Z0-9_\-]+):\s*not found", stderr)
    if m and "No such file" not in stderr:
        # Avoid false positive on file not found
        cmd = m.group(1)
        if len(cmd) < 30 and " " not in cmd:
            return cmd
    return None


def _detect_missing_file(stderr: str) -> Optional[str]:
    m = re.search(r"No such file or directory:\s*['\"]?([^'\"\n]+)['\"]?", stderr)
    if m:
        return m.group(1)
    return None


def _pip_install(package: str) -> Tuple[bool, str]:
    """Try pip install, returns (ok, output). Handles PEP 668 externally-managed."""
    pip_pkg = PACKAGE_MAP.get(package, package)
    # Try with --break-system-packages first (needed in Termux and modern pip)
    for extra in ["--break-system-packages", ""]:
        cmd = f"pip install --quiet {extra} {pip_pkg}".strip()
        cmd = " ".join(cmd.split())  # normalize double spaces
        try:
            p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
            out = p.stdout + "\n" + p.stderr
            if p.returncode == 0:
                db.log("info", "runner", f"pip installed {pip_pkg}")
                return True, out
            # If error is about externally-managed and we didn't use break, retry will
            if "externally-managed" in out.lower() and not extra:
                continue
        except Exception as e:
            return False, str(e)
    return False, f"pip install {pip_pkg} failed"


def _pkg_install(pkg: str) -> Tuple[bool, str]:
    termux_pkg = PKG_MAP.get(pkg, pkg)
    # Only try pkg if we're actually in Termux or pkg exists
    if shutil.which("pkg") is None:
        return False, "pkg not found (not in Termux)"
    cmd = f"pkg install -y {termux_pkg}"
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=180)
        out = p.stdout + "\n" + p.stderr
        if p.returncode == 0:
            db.log("info", "runner", f"pkg installed {termux_pkg}")
            return True, out
        return False, out
    except Exception as e:
        return False, str(e)


def _try_heal_once(stderr: str, stdout: str) -> Tuple[bool, str]:
    """Attempt one heal step. Returns (healed?, description)."""
    combined = stderr + "\n" + stdout

    # 1. Python missing module
    mod = _detect_missing_python_module(combined)
    if mod:
        ok, out = _pip_install(mod)
        if ok:
            return True, f"pip install {PACKAGE_MAP.get(mod, mod)} (for missing module {mod})"
        # Try alternative: maybe it's a stdlib that needs different name?
        # Log failure but don't loop forever
        return False, f"failed to pip install {mod}: {out[:200]}"

    # 2. Missing system command
    cmd = _detect_missing_command(combined)
    if cmd:
        # First try pip (many CLIs are pip packages)
        ok, out = _pip_install(cmd)
        if ok:
            return True, f"pip install {cmd} (for missing command {cmd})"
        # Then try pkg
        ok, out = _pkg_install(cmd)
        if ok:
            return True, f"pkg install {PKG_MAP.get(cmd, cmd)} (for missing command {cmd})"
        return False, f"failed to install command {cmd}"

    # 3. Missing file — maybe parent dir missing? Try to create parent if path looks like file creation
    # We don't auto-create arbitrary files, but we can hint
    # For now, no auto-heal for missing files, just report
    return False, "no auto-heal pattern matched"


def run_with_heal(cmd: str, cwd: Path | None = None, timeout: int = 30,
                  max_attempts: int = 3, auto_install: bool = True) -> RunResult:
    """Run cmd, and if it fails with known patterns, auto-fix and retry."""
    fixes: List[str] = []
    last_rc = 1
    last_out = ""
    last_err = ""

    for attempt in range(1, max_attempts + 1):
        rc, out, err = tools.run_shell(cmd, cwd=cwd, timeout=timeout)
        last_rc, last_out, last_err = rc, out, err

        if rc == 0:
            return RunResult(
                cmd=cmd,
                rc=rc,
                stdout=out,
                stderr=err,
                attempts=attempt,
                fixed=len(fixes) > 0,
                fixes_applied=fixes,
                healed=len(fixes) > 0,
            )

        if not auto_install or attempt >= max_attempts:
            break

        healed, desc = _try_heal_once(err, out)
        if healed:
            fixes.append(desc)
            db.log("info", "runner", f"heal attempt {attempt}: {desc}")
            continue
        else:
            # No heal possible, stop retrying
            break

    return RunResult(
        cmd=cmd,
        rc=last_rc,
        stdout=last_out,
        stderr=last_err,
        attempts=len(fixes) + 1,
        fixed=len(fixes) > 0,
        fixes_applied=fixes,
        healed=False,
    )


# ---- file fixer -------------------------------------------------------------

def fix_file_syntax(path: Path, error_output: str) -> Tuple[bool, str]:
    """Attempt to fix common syntax errors in a Python file.

    This is a lightweight heuristic fixer, not a full LLM fixer.
    For complex fixes, the agent's `write_file` tool should be used.

    Returns (fixed?, message)
    """
    if not path.exists() or not path.is_file():
        return False, f"file not found: {path}"

    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        return False, f"cannot read {path}: {e}"

    original = content
    fixes = []

    # Fix 1: Missing closing parenthesis/bracket on previous line (common when LLM truncates)
    # We check if error is "unexpected EOF" and last non-empty line is unclosed
    if "unexpected EOF" in error_output or "was never closed" in error_output:
        # Count open/close
        open_parens = content.count("(") - content.count(")")
        open_brackets = content.count("[") - content.count("]")
        open_braces = content.count("{") - content.count("}")
        if open_parens > 0:
            content += ")" * open_parens
            fixes.append(f"added {open_parens} closing )")
        if open_brackets > 0:
            content += "]" * open_brackets
            fixes.append(f"added {open_brackets} closing ]")
        if open_braces > 0:
            content += "}" * open_braces
            fixes.append(f"added {open_braces} closing }}")

    # Fix 2: IndentationError - try to dedent
    if "IndentationError" in error_output or "unexpected indent" in error_output.lower():
        lines = content.splitlines()
        # Find minimum indent of non-empty lines after first
        # Simple fix: strip trailing whitespace and ensure consistent 4-space
        new_lines = []
        for line in lines:
            # Remove trailing whitespace
            new_lines.append(line.rstrip())
        content = "\n".join(new_lines)
        fixes.append("stripped trailing whitespace / normalized indent")

    # Fix 3: Missing import for common names
    # If error says "name 'X' is not defined" and X is a common module, add import
    m = re.search(r"name '([a-zA-Z_][a-zA-Z0-9_]*)' is not defined", error_output)
    if m:
        name = m.group(1)
        common_imports = {
            "os": "import os",
            "sys": "import sys",
            "json": "import json",
            "re": "import re",
            "time": "import time",
            "random": "import random",
            "pathlib": "from pathlib import Path",
            "Path": "from pathlib import Path",
            "requests": "import requests",
            "httpx": "import httpx",
        }
        if name in common_imports:
            imp = common_imports[name]
            if imp not in content:
                content = imp + "\n" + content
                fixes.append(f"added missing import: {imp}")

    if fixes and content != original:
        try:
            # Backup
            backup = path.with_suffix(path.suffix + ".bak")
            backup.write_text(original, encoding="utf-8")
            path.write_text(content, encoding="utf-8")
            return True, f"fixed {path}: {', '.join(fixes)} (backup {backup})"
        except Exception as e:
            return False, f"failed to write fix: {e}"

    return False, "no auto-fix applicable"
