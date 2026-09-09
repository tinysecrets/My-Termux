"""Smoke + unit tests for the my-termux package."""
import argparse
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest


def test_paths_ensure_dirs():
    from mytermux import paths
    paths.ensure_dirs()
    for d in paths.ALL_DIRS:
        assert d.exists()


def test_config_default_and_roundtrip():
    from mytermux import config
    cfg = config.load_config()  # creates default
    assert cfg["model_order"], "default model_order must not be empty"
    assert cfg["openrouter_api_key"] == ""
    cfg2 = config.set_value("openrouter_api_key", "sk-test-123")
    assert cfg2["openrouter_api_key"] == "sk-test-123"
    # reload from disk
    assert config.load_config()["openrouter_api_key"] == "sk-test-123"


def test_config_survives_without_pyyaml(monkeypatch):
    from mytermux import config
    # simulate missing yaml by forcing _yaml() to return None
    monkeypatch.setattr(config, "_yaml", lambda: None)
    config.save_config({"openrouter_api_key": "abc", "model_order": ["m1", "m2"],
                        "auto_dashboard": True, "notifications": False})
    got = config.load_config()
    assert got["openrouter_api_key"] == "abc"
    assert got["model_order"] == ["m1", "m2"]
    assert got["auto_dashboard"] is True
    assert got["notifications"] is False


def test_db_schema_and_sessions():
    from mytermux import db
    db.init_db()
    sid = db.start_session("proj-x")
    db.add_message(sid, "user", "hello")
    db.add_message(sid, "assistant", "hi there", model="test-model")
    msgs = db.get_session_messages(sid)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["model"] == "test-model"
    db.end_session(sid, "done")
    last = db.get_last_session()
    assert last["id"] == sid
    assert last["summary"] == "done"


def test_db_tasks_and_goals():
    from mytermux import db
    db.init_db()
    gid = db.add_goal("ship v1", project="proj")
    t1 = db.add_task("write readme", project="proj", goal_id=gid, priority=1)
    db.add_task("later", project="proj", priority=5)
    pending = db.list_pending_tasks(project="proj")
    assert len(pending) == 2
    assert pending[0]["id"] == t1  # priority 1 first
    db.complete_task(t1)
    assert len(db.list_pending_tasks(project="proj")) == 1


def test_planner_next_actions_flags_missing_key():
    from mytermux import planner
    actions = planner.next_actions()
    reasons = " ".join(a["why"] for a in actions)
    assert "OpenRouter" in reasons, "must nag when API key missing"
    assert any(a["cmd"] == "fix" for a in actions)


def test_planner_every_suggested_command_is_actually_installed():
    """THE regression test for the dead-suggestion bug.

    The dashboard used to print six `my-*` commands that install.sh never
    created, so every "next step" it offered failed with `command not found`.
    Whatever the planner suggests must resolve to an installed command name.
    """
    from mytermux import planner
    from mytermux.commands import installed_names

    installed = set(installed_names())
    for a in planner.next_actions():
        first_word = a["cmd"].split()[0]
        assert first_word in installed, (
            f"planner suggested {a['cmd']!r} but {first_word!r} is not installed")


def test_planner_suggestions_are_deduplicated():
    from mytermux import planner
    cmds = [a["cmd"] for a in planner.next_actions()]
    assert len(cmds) == len(set(cmds)), f"duplicate suggestions: {cmds}"


def test_planner_low_battery_promotes_session_export():
    """With a working API key, a dying battery is the most urgent signal."""
    from mytermux import planner
    from mytermux.config import set_value

    set_value("openrouter_api_key", "sk-or-v1-test")
    device = {"battery": {"percent": 9, "plugged": "UNPLUGGED"},
              "storage": {"percent_free": 80}}
    actions = planner.next_actions(device=device)
    assert actions[0]["cmd"] == "export session"
    assert "9%" in actions[0]["why"]


def test_planner_low_battery_still_surfaces_when_unconfigured():
    """On a fresh install the API-key nag wins the top slot, but the battery
    warning must still be present."""
    from mytermux import planner
    device = {"battery": {"percent": 9, "plugged": "UNPLUGGED"},
              "storage": {"percent_free": 80}}
    cmds = [a["cmd"] for a in planner.next_actions(device=device)]
    assert "export session" in cmds


def test_planner_full_battery_does_not_nag():
    from mytermux import planner
    device = {"battery": {"percent": 98, "plugged": "AC"},
              "storage": {"percent_free": 80}}
    reasons = " ".join(a["why"] for a in planner.next_actions(device=device))
    assert "Battery" not in reasons


def test_planner_low_storage_promotes_fix():
    from mytermux import planner
    device = {"battery": {"percent": 90, "plugged": "UNPLUGGED"},
              "storage": {"percent_free": 4}}
    reasons = " ".join(a["why"] for a in planner.next_actions(device=device))
    assert "storage free" in reasons


def test_planner_missing_device_info_is_tolerated():
    """No termux-api (laptop/CI) must not break the planner."""
    from mytermux import planner
    assert planner.next_actions(device={})
    assert planner.next_actions(device=None)


def test_planner_detects_dirty_git_project(tmp_path):
    import subprocess
    from mytermux import planner
    from mytermux.config import set_value

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True)
    (repo / "a.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True, capture_output=True)

    set_value("current_project", str(repo))
    git = planner._project_git(str(repo))
    assert git["repo"] is True
    assert git["dirty"] is False
    assert git["branch"]

    (repo / "b.txt").write_text("y", encoding="utf-8")
    git = planner._project_git(str(repo))
    assert git["dirty"] is True
    cmds = [a["cmd"] for a in planner.next_actions()]
    assert "sync" in cmds, f"dirty repo should suggest sync, got {cmds}"


def test_planner_project_git_on_non_repo_is_inert():
    from mytermux import planner
    assert planner._project_git("") == {"repo": False, "branch": "", "dirty": False,
                                        "ahead": False, "behind": False}
    assert planner._project_git("/definitely/not/here")["repo"] is False


def test_cmd_dashboard_runs_startup_heal(monkeypatch):
    from mytermux import cli

    monkeypatch.setattr(cli, "_bootstrap", lambda: None)
    monkeypatch.setattr(cli.ui, "dashboard", lambda show_banner=True, ctx=None: None)
    monkeypatch.setattr(cli.ui, "build_context", lambda **kw: {})
    monkeypatch.setattr(cli.heal_mod, "heal", lambda: {"after": [], "repairs": []})

    assert cli.cmd_dashboard(argparse.Namespace(quick=False)) == 0


def test_cmd_dashboard_quick_skips_heal(monkeypatch):
    """--quick is what makes opening a shell feel instant."""
    from mytermux import cli

    healed = []
    monkeypatch.setattr(cli, "_bootstrap", lambda: None)
    monkeypatch.setattr(cli.ui, "dashboard", lambda show_banner=True, ctx=None: None)
    monkeypatch.setattr(cli.ui, "build_context", lambda **kw: {})
    monkeypatch.setattr(cli.heal_mod, "heal",
                        lambda: healed.append(1) or {"after": [], "repairs": []})

    assert cli.cmd_dashboard(argparse.Namespace(quick=True)) == 0
    assert healed == [], "--quick must not run self-heal"


def test_startup_heal_reports_required_and_optional_items(monkeypatch, capsys):
    from mytermux import cli

    monkeypatch.setattr(cli, "_bootstrap", lambda: None)
    monkeypatch.setattr(cli.ui, "dashboard", lambda show_banner=True, ctx=None: None)
    monkeypatch.setattr(cli.ui, "build_context", lambda **kw: {})
    monkeypatch.setattr(cli.heal_mod, "heal", lambda: {
        "after": [
            {"name": "config:openrouter_key", "ok": False, "detail": "missing"},
            {"name": "pip:httpx", "ok": False, "detail": "missing"},
            {"name": "pip:cloudinary (optional)", "ok": False, "detail": "missing (optional)"},
        ],
        "repairs": [],
    })

    cli.cmd_dashboard(argparse.Namespace(quick=False))
    out = capsys.readouterr().out
    assert "startup notice" in out
    assert "config:openrouter_key" in out


def test_startup_heal_names_the_canonical_fix_command(monkeypatch, capsys):
    """The nag must name a command that exists — `fix`, not the old `my-fix`."""
    from mytermux import cli

    monkeypatch.setattr(cli, "_bootstrap", lambda: None)
    monkeypatch.setattr(cli.ui, "dashboard", lambda show_banner=True, ctx=None: None)
    monkeypatch.setattr(cli.ui, "build_context", lambda **kw: {})
    monkeypatch.setattr(cli.heal_mod, "heal", lambda: {
        "after": [{"name": "pip:httpx", "ok": False, "detail": "missing"}],
        "repairs": [],
    })
    cli.cmd_dashboard(argparse.Namespace(quick=False))
    out = capsys.readouterr().out
    assert "`fix`" in out
    assert "my-fix" not in out


def test_cmd_upgrade_runs_heal_and_sync(monkeypatch):
    from mytermux import cli

    monkeypatch.setattr(cli, "_bootstrap", lambda: None)
    monkeypatch.setattr(cli.heal_mod, "heal", lambda: {"after": [], "repairs": []})
    monkeypatch.setattr(cli.git_ops, "sync_repo", lambda repo, auto_push=False: {"branch": "main", "summary": "up to date", "status": ""})

    assert cli.cmd_upgrade(argparse.Namespace(path=".")) == 0


def test_scanner_detects_python_project(tmp_path):
    from mytermux import scanner
    proj = tmp_path / "demo"
    (proj).mkdir()
    (proj / "requirements.txt").write_text("httpx\n", encoding="utf-8")
    (proj / "app.py").write_text("print('hi')\n", encoding="utf-8")
    info = scanner.scan(proj)
    assert "python" in info["kinds"]
    assert info["files"] >= 2


def test_scanner_detects_node_and_git(tmp_path):
    from mytermux import scanner
    proj = tmp_path / "node_demo"
    proj.mkdir()
    (proj / "package.json").write_text('{"name":"x"}', encoding="utf-8")
    (proj / ".git").mkdir()
    info = scanner.scan(proj)
    assert "node" in info["kinds"]
    assert info["git"] is True


def test_tools_is_dangerous():
    from mytermux import tools
    assert tools.is_dangerous("rm -rf /") is True
    assert tools.is_dangerous("rm -rf ~/") is True
    assert tools.is_dangerous("shutdown now") is True
    assert tools.is_dangerous("ls -la") is False
    assert tools.is_dangerous("") is False


def test_tools_run_shell(tmp_path):
    from mytermux import tools
    rc, out, err = tools.run_shell("echo hello", cwd=tmp_path)
    assert rc == 0
    assert "hello" in out


def test_tools_read_write_file(tmp_path):
    from mytermux import tools
    p = tmp_path / "a.txt"
    tools.write_file(p, "one")
    assert tools.read_file(p) == "one"
    # backup should happen on rewrite
    tools.write_file(p, "two", backup=True)
    assert tools.read_file(p) == "two"
    assert (tmp_path / "a.txt.bak").exists()


def test_heal_diagnose_and_repair():
    from mytermux import heal
    report = heal.heal()
    assert "before" in report and "after" in report
    names = {c["name"] for c in report["after"]}
    assert any(n.startswith("dir:") for n in names)
    assert any(n == "db:file" for n in names)
    # after heal, all required dirs must exist
    for c in report["after"]:
        if c["name"].startswith("dir:"):
            assert c["ok"], c


def test_git_ops_inject_pat_https():
    from mytermux.git_ops import _inject_pat
    url = "https://github.com/user/repo.git"
    out = _inject_pat(url, "tok", "alice")
    assert out == "https://alice:tok@github.com/user/repo.git"
    # ssh untouched
    assert _inject_pat("git@github.com:u/r.git", "tok") == "git@github.com:u/r.git"
    # no token → unchanged
    assert _inject_pat(url, "") == url


def test_git_ops_sync_repo_updates_when_needed(tmp_path):
    from mytermux import git_ops

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

    status = git_ops.sync_repo(repo)
    assert status["updated"] is False
    assert status["ahead"] is False
    assert status["behind"] is False

    (repo / "README.md").write_text("world\n", encoding="utf-8")
    status = git_ops.sync_repo(repo)
    assert status["updated"] is False
    assert status["dirty"] is True


def test_notify_no_termux_api(monkeypatch):
    from mytermux import notify
    monkeypatch.setattr(notify.shutil, "which", lambda _: None)
    assert notify.available() is False
    assert notify.notify("t", "c") is False


def test_export_session_writes_json(tmp_path):
    from mytermux import db, export as exp
    db.init_db()
    sid = db.start_session("p")
    db.add_message(sid, "user", "hi")
    db.add_message(sid, "assistant", "yo")
    out = exp.export("session")
    assert Path(out).exists()
    data = json.loads(Path(out).read_text())
    assert data["session"]["id"] == sid
    assert len(data["messages"]) == 2


def test_export_config(tmp_path):
    from mytermux import config, export as exp
    config.set_value("openrouter_api_key", "sk-x")
    out = exp.export("config")
    assert Path(out).exists()
    assert "sk-x" in Path(out).read_text()


def test_export_project_requires_current_project():
    from mytermux import export as exp
    with pytest.raises(RuntimeError):
        exp.export("project")


def test_export_project_creates_archive(tmp_path):
    from mytermux import config, export as exp
    proj = tmp_path / "myproj"
    proj.mkdir()
    (proj / "file.txt").write_text("x")
    config.set_value("current_project", str(proj))
    out = exp.export("project")
    assert Path(out).exists()
    assert out.endswith(".tar.gz")


def test_import_session_restores_messages(tmp_path):
    from mytermux import db, export as exp
    db.init_db()
    sid = db.start_session("p")
    db.add_message(sid, "user", "hi")
    db.add_message(sid, "assistant", "yo")
    out = exp.export("session")

    with db.connect() as conn:
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM sessions")

    restored = exp.import_export("session", out)
    assert Path(restored).exists()
    with db.connect() as conn:
        rows = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        msgs = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert rows == 1
    assert msgs == 2


def test_import_project_extracts_archive(tmp_path):
    from mytermux import config, export as exp
    proj = tmp_path / "myproj"
    proj.mkdir()
    (proj / "file.txt").write_text("x")
    config.set_value("current_project", str(proj))
    archive = exp.export("project")

    restored = exp.import_export("project", archive)
    assert Path(restored).exists()
    assert (Path(restored) / "file.txt").exists()


def test_openrouter_requires_key():
    from mytermux import openrouter
    with pytest.raises(RuntimeError) as ei:
        openrouter.chat_stream([{"role": "user", "content": "hi"}])
    assert "API key" in str(ei.value)


def test_openrouter_fallback_and_streaming(monkeypatch):
    """Simulate model #1 failing (500), model #2 streaming successfully."""
    from mytermux import config, openrouter

    config.set_value("openrouter_api_key", "sk-test")
    config.set_value("model_order", ["bad/model", "good/model"])

    class FakeResponse:
        def __init__(self, status_code, lines=None, err=None):
            self.status_code = status_code
            self._lines = lines or []
            self._err = err or {}
            self.headers = {}
            self.text = json.dumps({"error": err} if err else {})
        def json(self):
            return {"error": self._err} if self._err else {}
        def iter_lines(self):
            for l in self._lines:
                yield l
        def __enter__(self): return self
        def __exit__(self, *a): return False

    calls = {"i": 0}

    class FakeClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def stream(self, method, url, headers=None, json=None):
            calls["i"] += 1
            if json["model"] == "bad/model":
                return FakeResponse(500, err={"code": 500, "message": "boom"})
            # good/model → two SSE chunks + [DONE]
            lines = [
                'data: {"choices":[{"delta":{"content":"hel"}}]}',
                'data: {"choices":[{"delta":{"content":"lo"}}]}',
                "data: [DONE]",
            ]
            return FakeResponse(200, lines=lines)

    import mytermux.openrouter as ormod
    monkeypatch.setattr(ormod, "time", type("T", (), {"sleep": staticmethod(lambda *_: None)}))
    # inject fake httpx module
    fake_httpx = type("H", (), {"Client": FakeClient})
    monkeypatch.setitem(__import__("sys").modules, "httpx", fake_httpx)

    collected = []
    text, model = ormod.chat_stream(
        [{"role": "user", "content": "hi"}],
        on_delta=lambda s: collected.append(s),
    )
    assert text == "hello"
    assert model == "good/model"
    assert "".join(collected) == "hello"
    assert calls["i"] >= 2  # bad tried first, good used second


def test_openrouter_fatal_401_no_fallback(monkeypatch):
    from mytermux import config, openrouter

    config.set_value("openrouter_api_key", "sk-test")
    config.set_value("model_order", ["a/one", "a/two"])

    class FakeResponse:
        def __init__(self, code):
            self.status_code = code
            self.headers = {}
            self.text = ""
        def json(self):
            return {"error": {"code": 401, "message": "bad key"}}
        def iter_lines(self): return iter([])
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class FakeClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def stream(self, *a, **kw):
            return FakeResponse(401)

    monkeypatch.setitem(__import__("sys").modules, "httpx", type("H", (), {"Client": FakeClient}))
    with pytest.raises(RuntimeError) as ei:
        openrouter.chat_stream([{"role": "user", "content": "hi"}])
    assert "401" in str(ei.value)


def test_cli_dashboard_and_status(capsys):
    from mytermux import cli
    rc = cli.main(["status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "status" in out.lower() or "OpenRouter" in out


def test_cli_scan_registers_project(tmp_path, capsys):
    from mytermux import cli, db, config
    proj = tmp_path / "prx"
    proj.mkdir()
    (proj / "requirements.txt").write_text("x", encoding="utf-8")
    rc = cli.main(["scan", str(proj)])
    assert rc == 0
    assert any(p["name"] == "prx" for p in db.list_projects())
    assert config.load_config()["current_project"] == str(proj.resolve())


def test_cli_fix_runs_ok(capsys):
    from mytermux import cli
    rc = cli.main(["fix"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "self-heal report" in out


def test_cli_export_session_creates_file(capsys):
    from mytermux import cli, db
    db.init_db()
    sid = db.start_session("p")
    db.add_message(sid, "user", "hi")
    rc = cli.main(["export", "session"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "exported session" in out


def test_dispatch_script_name_mapping():
    """The dispatcher must resolve every legacy `my-*` alias to a subcommand.

    Exercised for real by running the script as each name, not by grepping it —
    grepping is exactly what let the rename drift through unnoticed.
    """
    from tests.helpers import dispatch_as

    expected = {
        "my-termux": "dashboard",
        "start-my-termux": "start",
        "my-chat": "chat",
        "my-menu": "menu",
        "my-status": "status",
        "my-scan": "scan",
        "my-sync": "sync",
        "my-fix": "fix",
        "my-export": "export",
        "my-resume": "resume",
    }
    for name, sub in expected.items():
        rc, out, err = dispatch_as(name)
        assert rc == 0, f"{name} exited {rc}: {out}"
        assert out == sub, f"{name} resolved to {out!r}, expected {sub!r}"


def test_dispatch_resolves_every_canonical_command():
    """Every name the registry declares must actually resolve."""
    from tests.helpers import dispatch_as
    from mytermux.commands import COMMANDS

    for c in COMMANDS:
        rc, out, err = dispatch_as(c.name)
        assert rc == 0, f"{c.name} exited {rc}: {out}"
        assert out == c.sub, f"{c.name} -> {out!r}, expected {c.sub!r}"


def test_dispatch_rejects_unknown_names_loudly():
    """An unknown command must error, not silently print a dashboard."""
    from tests.helpers import dispatch_as

    rc, out, err = dispatch_as("not-a-real-command")
    assert rc == 2
    assert "unknown command" in err


def test_termux_umbrella_forwards_a_subcommand():
    """`termux` is the umbrella name, so `termux help` must reach `help`.

    Regression: the dispatcher used to always map `termux` to `dashboard`, so
    `termux help` became `python -m mytermux dashboard help` and argparse
    rejected it with "unrecognized arguments: help".
    """
    from tests.helpers import dispatch_as

    for arg, expected in [("help", "help"), ("chat", "chat"), ("dev", "dev"),
                          ("media", "media"), ("dashboard", "dashboard")]:
        rc, out, err = dispatch_as("termux", arg)
        assert rc == 0, f"termux {arg} exited {rc}: {err}"
        assert out == expected, f"termux {arg} -> {out!r}, expected {expected!r}"


def test_termux_umbrella_keeps_flags_on_dashboard():
    """`termux --quick` is a dashboard flag, not a subcommand."""
    from tests.helpers import dispatch_as

    rc, out, err = dispatch_as("termux", "--quick")
    assert rc == 0, err
    assert out == "dashboard"


def test_termux_umbrella_passes_sub_arguments_through():
    """`termux media list` must keep `list` for the media subparser."""
    import os
    import subprocess
    import tempfile
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    script = root / "bin" / "mytermux-dispatch"
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "termux"
        target.write_text(script.read_text(encoding="utf-8"), encoding="utf-8")
        env = dict(os.environ, MYTERMUX_PYTHON="echo", MYTERMUX_DISPATCH_DRYRUN="0",
                   MYTERMUX_APP_DIR="/nonexistent")
        # MYTERMUX_PYTHON=echo makes the final exec print its own argv,
        # proving the dispatcher forwarded the right words
        p = subprocess.run(["bash", str(target), "media", "list"],
                           capture_output=True, text=True, env=env, timeout=30)
    assert p.stdout.split() == ["-m", "mytermux", "media", "list"], p.stdout


def test_install_script_has_expected_steps():
    root = Path(__file__).resolve().parents[1]
    install = (root / "install.sh").read_text()
    for needle in [
        "termux-setup-storage",
        "pkg install",
        "pip install",
        "auto-launch",
        "start-my-termux",
        "installed_names",
        "completions/mytermux.bash",
    ]:
        assert needle in install, f"install.sh missing: {needle}"


def test_install_script_installs_every_name_the_registry_declares():
    """install.sh must not keep its own copy of the command list.

    It asks the package for `installed_names()`. The fallback list is only a
    safety net, so assert it stays a superset of the registry too.
    """
    from mytermux.commands import installed_names

    root = Path(__file__).resolve().parents[1]
    install = (root / "install.sh").read_text()
    assert "installed_names" in install, "installer must query the registry"
    for name in installed_names():
        assert name in install, f"install.sh fallback list is missing {name!r}"


def test_install_bashrc_hook_calls_a_command_that_is_actually_installed():
    """The bug that killed the dashboard: .bashrc called `start-my-termux`,
    which install.sh never created, so every shell printed
    `command not found` and no dashboard."""
    import re
    from mytermux.commands import installed_names

    root = Path(__file__).resolve().parents[1]
    install = (root / "install.sh").read_text()
    installed = set(installed_names())

    # the invocation is emitted as an `echo '...'` line inside the generator
    called = re.findall(r"^\s*echo '\s*(\S+)[^']*\|\| true'", install, flags=re.MULTILINE)
    assert called, "no auto-launch invocation found in install.sh"
    for first in called:
        assert first in installed, f".bashrc calls {first!r}, which is not installed"


def test_generated_bashrc_block_is_valid_bash(tmp_path):
    """Extract the block install.sh appends and syntax-check it."""
    import re
    import subprocess

    root = Path(__file__).resolve().parents[1]
    install = (root / "install.sh").read_text()
    m = re.search(r"say \"adding prompt.*?\n\{\n(.*?)\n\} >> \"\$BASHRC\"",
                  install, flags=re.DOTALL)
    assert m, "could not locate the bashrc generation block"
    body = m.group(1)
    # the block is a list of `echo '...'` lines; render it exactly as bash would
    rendered = subprocess.run(["bash", "-c", body], capture_output=True, text=True)
    assert rendered.returncode == 0, rendered.stderr
    script = tmp_path / "rc.sh"
    script.write_text(rendered.stdout, encoding="utf-8")
    check = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert check.returncode == 0, f"generated .bashrc is not valid bash: {check.stderr}"
    assert "PS1=" in rendered.stdout
    assert "__mytermux_branch" in rendered.stdout
    assert "start-my-termux" in rendered.stdout


def test_uninstall_script_has_expected_steps():
    root = Path(__file__).resolve().parents[1]
    un = (root / "uninstall.sh").read_text()
    assert "my-termux auto-launch" in un
    assert "my-chat" in un
    assert "installed_names" in un, "uninstaller must query the registry"


def test_completion_script_covers_every_canonical_command():
    from mytermux.commands import visible_names

    root = Path(__file__).resolve().parents[1]
    comp = (root / "completions" / "mytermux.bash").read_text()
    for name in visible_names():
        assert name in comp, f"completion missing {name!r}"


def test_completion_script_is_valid_bash():
    import subprocess

    root = Path(__file__).resolve().parents[1]
    comp = root / "completions" / "mytermux.bash"
    r = subprocess.run(["bash", "-n", str(comp)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
