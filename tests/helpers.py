"""Shared test helpers."""
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def dispatch_as(name: str, *args: str) -> tuple:
    """Invoke bin/mytermux-dispatch *as if* it were installed under `name`.

    Every command is a symlink to one dispatcher script, so the only thing that
    decides what runs is $0. This resolves the subcommand for real (via the
    script's own MYTERMUX_DISPATCH_DRYRUN hook) instead of grepping the script
    for a string — which is how the name drift went unnoticed in the first
    place.

    Returns (returncode, stdout_stripped, stderr_stripped).
    """
    script = ROOT / "bin" / "mytermux-dispatch"
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / name
        target.write_text(script.read_text(encoding="utf-8"), encoding="utf-8")
        target.chmod(0o755)
        env = dict(os.environ, MYTERMUX_DISPATCH_DRYRUN="1")
        # the shebang points at Termux's env(1); run through bash so the script
        # is exercisable on any host
        p = subprocess.run(["bash", str(target), *args], capture_output=True,
                           text=True, env=env, timeout=30)
    return p.returncode, p.stdout.strip(), p.stderr.strip()
