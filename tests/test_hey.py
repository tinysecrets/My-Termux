"""Tests for hey / flow / clip / companion / run commands."""

def test_hey_command_registered():
    from mytermux.commands import BY_NAME, BY_SUB
    assert "hey" in BY_NAME
    assert "flow" in BY_NAME
    assert "clip" in BY_NAME
    assert "run" in BY_NAME
    assert "companion" in BY_NAME
    assert BY_NAME["hey"].sub == "hey"
    assert "hey" in BY_SUB


def test_hey_dispatch_resolves():
    from tests.helpers import dispatch_as
    for name in ("hey", "flow", "clip", "run", "companion"):
        rc, out, err = dispatch_as(name)
        assert rc == 0, f"{name} dispatch failed: {err}"
        assert out == name


def test_hey_legacy_aliases():
    from tests.helpers import dispatch_as
    for name in ("my-hey", "my-flow", "my-clip", "my-run", "my-companion"):
        rc, out, err = dispatch_as(name)
        assert rc == 0, f"{name} alias failed: {err}"


def test_hey_text_mode_once(capsys, monkeypatch):
    """hey --text --once should run one turn and exit, without real STT/TTS."""
    from mytermux import cli
    from mytermux import agent
    from mytermux.memory import Conversation

    # Mock agent to avoid needing OpenRouter key
    def fake_run_turn(conv, text):
        conv.record_user(text)
        conv.record_assistant("hey love, I built it and it works!")
        return "hey love, I built it and it works!"

    monkeypatch.setattr(agent, "run_turn", fake_run_turn)

    # Mock voice to avoid TTS attempts
    from mytermux import voice as voice_mod
    monkeypatch.setattr(voice_mod, "tts", lambda *a, **k: False)
    monkeypatch.setattr(voice_mod, "stt", lambda *a, **k: None)
    monkeypatch.setattr(voice_mod, "is_stt_available", lambda: False)
    monkeypatch.setattr(voice_mod, "is_tts_available", lambda: False)

    rc = cli.main(["hey", "--text", "--once", "build me a hello world"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Nova" in out or "hey" in out.lower()


def test_clip_empty_clipboard_shows_help(capsys, monkeypatch):
    from mytermux import cli
    from mytermux import device as dev_mod
    monkeypatch.setattr(dev_mod, "clipboard_get", lambda: None)
    rc = cli.main(["clip"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "clipboard empty" in out.lower() or "clip" in out.lower()


def test_clip_with_direct_text(monkeypatch, capsys):
    from mytermux import cli
    from mytermux import device as dev_mod
    from mytermux import agent
    monkeypatch.setattr(dev_mod, "clipboard_get", lambda: None)

    def fake_run_turn(conv, text):
        return "got it"

    monkeypatch.setattr(agent, "run_turn", fake_run_turn)
    rc = cli.main(["clip", "hello world"])
    assert rc == 0


def test_flow_is_alias_for_hey(monkeypatch, capsys):
    from mytermux import cli
    from mytermux import agent
    from mytermux import voice as voice_mod

    def fake_run_turn(conv, text):
        conv.record_user(text)
        conv.record_assistant("flow mode working")
        return "flow mode working"

    monkeypatch.setattr(agent, "run_turn", fake_run_turn)
    monkeypatch.setattr(voice_mod, "tts", lambda *a, **k: False)
    monkeypatch.setattr(voice_mod, "is_stt_available", lambda: False)
    monkeypatch.setattr(voice_mod, "is_tts_available", lambda: False)

    rc = cli.main(["flow", "--once", "test flow"])
    # flow internally calls hey, so should succeed
    # Note: our flow implementation forces text mode
    assert rc == 0


def test_device_snapshot_includes_voice():
    from mytermux import device
    device.clear_cache()
    snap = device.snapshot(use_cache=False)
    assert "voice" in snap
    assert isinstance(snap["voice"], dict)
    assert "stt" in snap["voice"]
    assert "tts" in snap["voice"]


def test_planner_suggests_hey_when_configured():
    from mytermux import planner
    from mytermux.config import set_value
    set_value("openrouter_api_key", "sk-test")
    actions = planner.next_actions()
    cmds = [a["cmd"].split()[0] for a in actions]
    assert "hey" in cmds, f"hey should be suggested when API configured, got {cmds}"


def test_tools_agent_has_new_tools():
    from mytermux.tools_agent import REGISTRY
    for name in ("exec_heal", "fix_file", "clipboard", "speak", "see", "companion"):
        assert name in REGISTRY, f"missing tool {name}"
