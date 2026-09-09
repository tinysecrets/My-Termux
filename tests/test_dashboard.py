"""Tests for the dashboard — the single most-seen surface in the app.

These render for real at several widths and assert on the output, because the
failure mode here is visual: a panel that renders as a 6-character ribbon is
still a "passing" function.
"""
import pytest


WIDTHS = [40, 48, 62, 78, 120]


def _seed(tmp_path):
    import subprocess
    from mytermux import config, db, scanner

    repo = tmp_path / "proj"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True)
    (repo / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (repo / "requirements.txt").write_text("flask\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True, capture_output=True)
    (repo / "app.py").write_text("print('hi')\nx = 1\n", encoding="utf-8")  # make it dirty

    info = scanner.scan(repo)
    cfg = config.load_config()
    cfg["openrouter_api_key"] = "sk-or-v1-test"
    cfg["github_token"] = "ghp_test"
    cfg["github_username"] = "tester"
    cfg["current_project"] = info["path"]
    config.save_config(cfg)

    db.add_task("finish the export command")
    sid = db.start_session("proj")
    db.add_message(sid, "user", "how do I parse json?")
    db.add_message(sid, "assistant", "Use json.loads on the string.\n\n• Validate keys")
    db.end_session(sid, "json help")
    return repo


# ---- context ---------------------------------------------------------------

def test_build_context_has_everything_the_panels_need(tmp_path):
    from mytermux import ui
    _seed(tmp_path)
    ctx = ui.build_context()
    for key in ("cfg", "state", "device", "digest", "git", "project", "media_count"):
        assert key in ctx
    assert ctx["git"]["repo"] is True
    assert ctx["git"]["dirty"] is True
    assert ctx["media_count"] == 0


def test_build_context_without_a_project(tmp_path):
    from mytermux import ui
    ctx = ui.build_context()
    assert ctx["project"] == ""
    assert ctx["git"]["repo"] is False


# ---- status rows ------------------------------------------------------------

def test_status_rows_show_git_state(tmp_path):
    from mytermux import ui
    _seed(tmp_path)
    rows = dict((n, e) for n, s, e in ui._status_rows(ui.build_context()))
    assert "dirty" in rows["Project"]
    assert "proj" in rows["Project"]


def test_status_rows_without_project_suggests_scan():
    from mytermux import ui
    rows = dict((n, e) for n, s, e in ui._status_rows(ui.build_context()))
    assert "scan ." in rows["Project"]


def test_status_rows_include_phone_signals(tmp_path):
    from mytermux import ui
    names = [n for n, s, e in ui._status_rows(ui.build_context())]
    assert "Disk free" in names
    assert "Media vault" in names
    assert "Pending tasks" in names


def test_short_model_strips_provider_and_free_suffix():
    from mytermux.ui import _short_model
    cfg = {"model_order": ["deepseek/deepseek-chat-v3.1:free"]}
    assert _short_model(cfg) == "deepseek-chat-v3.1"
    assert _short_model({}) == "(none)"
    assert _short_model({"model_order": []}) == "(none)"


def test_short_model_truncates_long_names():
    from mytermux.ui import _short_model
    cfg = {"model_order": ["vendor/some-absurdly-long-model-name-here"]}
    out = _short_model(cfg, limit=18)
    assert len(out) <= 18
    assert out.endswith("\u2026")


# ---- header line ------------------------------------------------------------

def test_header_line_never_exceeds_width(tmp_path):
    from mytermux import ui
    ctx = ui.build_context()
    for w in WIDTHS:
        assert len(ui._header_line(ctx, w)) <= w, f"header overflowed at {w} cols"


def test_header_line_drops_least_important_segments(monkeypatch):
    from mytermux import ui
    ctx = ui.build_context()
    ctx["device"] = {"battery": {"percent": 55, "plugged": "USB"},
                     "storage": {"free": 41 * 1024 ** 3}}
    wide = ui._header_line(ctx, 100)
    narrow = ui._header_line(ctx, 30)
    assert "free" in wide
    assert "free" not in narrow, "disk should be dropped before the line wraps"


# ---- rendering --------------------------------------------------------------

@pytest.mark.parametrize("width", WIDTHS)
def test_dashboard_renders_at_every_phone_width(tmp_path, capsys, monkeypatch, width):
    from mytermux import ui
    _seed(tmp_path)
    monkeypatch.setenv("COLUMNS", str(width))
    ui.dashboard(show_banner=True)
    out = capsys.readouterr().out
    assert out.strip(), "dashboard printed nothing"
    for line in out.splitlines():
        assert len(line) <= width + 2, (
            f"line {len(line)} chars at width {width}: {line!r}")
    # the point of the dashboard
    assert "resume" in out
    assert "sync" in out


@pytest.mark.parametrize("width", WIDTHS)
def test_dashboard_renders_without_rich(tmp_path, capsys, monkeypatch, width):
    from mytermux import ui
    _seed(tmp_path)
    monkeypatch.setenv("COLUMNS", str(width))
    monkeypatch.setattr(ui, "_rich", lambda: None)
    ui.dashboard(show_banner=False)
    out = capsys.readouterr().out
    assert "next:" in out
    assert "resume" in out


def test_dashboard_on_a_completely_fresh_install(capsys, monkeypatch):
    """No config, no project, no sessions — must still render usefully."""
    from mytermux import ui
    monkeypatch.setenv("COLUMNS", "70")
    ui.dashboard(show_banner=True)
    out = capsys.readouterr().out
    assert "menu" in out, "fresh install should point at the settings menu"


def test_dashboard_does_not_suggest_commands_that_are_not_installed(tmp_path, monkeypatch):
    """The regression this whole upgrade exists to prevent."""
    from mytermux import ui
    from mytermux.commands import installed_names
    _seed(tmp_path)
    monkeypatch.setenv("COLUMNS", "100")
    installed = set(installed_names())
    for action in ui._next_actions(ui.build_context()):
        assert action["cmd"].split()[0] in installed


def test_dashboard_narrow_banner_is_used_at_small_width(monkeypatch):
    from mytermux import ui
    monkeypatch.setenv("COLUMNS", "44")
    ctx = ui.build_context()
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ui.dashboard(show_banner=True, ctx=ctx)
    rendered = buf.getvalue()
    assert max(len(l) for l in rendered.splitlines()) <= 46


def test_read_banner_falls_back_when_custom_banner_is_too_wide(tmp_path, monkeypatch):
    from mytermux import paths, ui
    paths.BANNER_FILE.parent.mkdir(parents=True, exist_ok=True)
    paths.BANNER_FILE.write_text("X" * 120, encoding="utf-8")
    assert "X" not in ui.read_banner(narrow=True)
    assert "X" in ui.read_banner(narrow=False)


def test_greeting_varies_by_hour():
    from datetime import datetime
    from mytermux.ui import greeting
    assert greeting(datetime(2026, 9, 9, 2)) == "late night"
    assert greeting(datetime(2026, 9, 9, 9)) == "good morning"
    assert greeting(datetime(2026, 9, 9, 14)) == "good afternoon"
    assert greeting(datetime(2026, 9, 9, 21)) == "good evening"


# ---- menu -------------------------------------------------------------------

def test_menu_items_map_to_real_actions():
    from mytermux.ui import MENU_ITEMS
    valid = {"hey", "flow", "chat", "resume", "ask", "clip", "run", "companion",
             "scan", "sync", "fix", "dev", "export",
             "settings", "status", "exit"}
    for label, action in MENU_ITEMS:
        assert action in valid, f"menu item {label!r} has unknown action {action!r}"


def test_menu_run_handles_ask_and_dev(monkeypatch, capsys):
    from mytermux import menu
    from mytermux.ui import MENU_ITEMS

    # Find indices dynamically so test survives menu reordering
    def idx_of(action):
        for i, (_, a) in enumerate(MENU_ITEMS, 1):
            if a == action:
                return str(i)
        raise ValueError(f"action {action!r} not in MENU_ITEMS")

    answers = iter([idx_of("ask"), "", idx_of("dev"), idx_of("exit")])
    monkeypatch.setattr(menu, "prompt", lambda msg, default="": next(answers))
    ran = []
    monkeypatch.setattr(menu, "chat", type("C", (), {"run": staticmethod(lambda **k: ran.append("chat"))}))

    class FakeCli:
        @staticmethod
        def cmd_dev(args):
            ran.append("dev")
            return 0

    import mytermux.cli as cli_mod
    monkeypatch.setattr(cli_mod, "cmd_dev", FakeCli.cmd_dev)
    assert menu.run() == 0
    assert "dev" in ran
