"""Tests for companion persona engine."""

def test_list_personas_has_nova():
    from mytermux import companion
    personas = companion.list_personas()
    assert "nova" in personas
    assert "bestie" in personas
    assert "partner" in personas
    assert "focus" in personas


def test_get_persona_defaults_to_nova():
    from mytermux import companion
    p = companion.get_persona(None)
    assert p["name"] == "Nova"
    assert "secret-admirer" in p["tagline"] or "secret" in p["description"].lower() or "Nova" in p["name"]


def test_get_persona_by_name():
    from mytermux import companion
    for name in ("nova", "bestie", "partner", "focus"):
        p = companion.get_persona(name)
        assert p["name"]
        assert p["system_addition"]


def test_get_persona_unknown_falls_back():
    from mytermux import companion
    p = companion.get_persona("unknown_xyz")
    assert p["name"] == "Nova"


def test_set_persona_and_current():
    from mytermux import companion
    companion.set_persona("bestie")
    assert companion.current_persona_name() == "bestie"
    companion.set_persona("nova")
    assert companion.current_persona_name() == "nova"


def test_set_persona_rejects_unknown():
    from mytermux import companion
    import pytest
    with pytest.raises(ValueError):
        companion.set_persona("nope")


def test_greetings_not_empty():
    from mytermux import companion
    for name in companion.list_personas():
        g = companion.companion_greeting(name)
        assert isinstance(g, str) and len(g) > 3
        a = companion.companion_ack(name)
        assert a
        s = companion.companion_success(name)
        assert s
        f = companion.companion_fixing(name)
        assert f
        c = companion.companion_closing(name)
        assert c


def test_persona_prompt_includes_competence():
    from mytermux import companion
    prompt = companion.get_persona_prompt("nova")
    # Nova must mention fixing errors, verifying
    assert "fix" in prompt.lower() or "verify" in prompt.lower() or "run" in prompt.lower()


def test_describe_persona():
    from mytermux import companion
    txt = companion.describe_persona("nova")
    assert "Nova" in txt
    assert "💫" in txt or "nova" in txt.lower()


def test_companion_cli_status(capsys):
    from mytermux import cli
    rc = cli.main(["companion", "status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "companion" in out.lower() or "Nova" in out


def test_companion_cli_list(capsys):
    from mytermux import cli
    rc = cli.main(["companion", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "nova" in out.lower()
