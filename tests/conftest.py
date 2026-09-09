"""Pytest configuration — isolates each test in a temporary MYTERMUX_HOME."""
import importlib
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.helpers import dispatch_as  # noqa: E402,F401  (re-exported)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Force every test to use its own MYTERMUX_HOME so nothing leaks."""
    home = tmp_path / "mytermux_home"
    monkeypatch.setenv("MYTERMUX_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    # no terminal width surprises: tests pin the width they want
    monkeypatch.delenv("COLUMNS", raising=False)
    # reimport paths to pick up new env
    import mytermux.paths as paths
    importlib.reload(paths)
    # also reload dependents that captured module-level constants
    for name in ("mytermux.db", "mytermux.config", "mytermux.notify",
                 "mytermux.export", "mytermux.heal", "mytermux.planner",
                 "mytermux.memory", "mytermux.openrouter", "mytermux.ui",
                 "mytermux.chat", "mytermux.menu", "mytermux.cli",
                 "mytermux.device", "mytermux.startup", "mytermux.media"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
    paths.ensure_dirs()
    from mytermux import db, device
    db.init_db()
    # device keeps a module-level cache; without this it leaks between tests
    device.clear_cache()
    yield home
