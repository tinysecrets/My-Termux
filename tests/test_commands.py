"""Tests for the canonical command registry.

The registry exists so command names can never again drift between what is
installed and what the UI tells you to type.
"""
import pytest


def test_every_command_has_required_fields():
    from mytermux.commands import COMMANDS
    assert COMMANDS, "registry must not be empty"
    for c in COMMANDS:
        assert c.name and c.sub and c.help, f"{c!r} is missing a field"
        assert len(c.help) <= 60, f"{c.name} help is too long for a phone: {c.help!r}"


def test_command_names_are_unique():
    from mytermux.commands import COMMANDS
    names = [c.name for c in COMMANDS]
    assert len(names) == len(set(names)), f"duplicate command names: {names}"
    subs = [c.sub for c in COMMANDS]
    assert len(subs) == len(set(subs)), f"duplicate subcommands: {subs}"


def test_cmd_builds_copy_pasteable_strings():
    from mytermux.commands import cmd
    assert cmd("chat") == "chat"
    assert cmd("scan", "~/projects/foo") == "scan ~/projects/foo"
    assert cmd("export", "session") == "export session"


def test_cmd_rejects_unknown_names():
    """The whole point: a typo must fail loudly, not print a dead suggestion."""
    from mytermux.commands import cmd
    with pytest.raises(KeyError):
        cmd("nope")
    with pytest.raises(KeyError):
        cmd("my-chat")  # prefixed names are aliases, not canonical


def test_get_returns_the_command():
    from mytermux.commands import get
    assert get("ask").sub == "ask"
    with pytest.raises(KeyError):
        get("missing")


def test_installed_names_includes_canonical_and_legacy():
    from mytermux.commands import COMMANDS, installed_names
    names = installed_names()
    for c in COMMANDS:
        assert c.name in names
        assert f"my-{c.name}" in names
    assert "start-my-termux" in names


def test_installed_names_has_no_duplicates():
    from mytermux.commands import installed_names
    names = installed_names()
    assert len(names) == len(set(names))


def test_legacy_prefix_does_not_collide_with_a_canonical_name():
    from mytermux.commands import BY_NAME
    assert "termux" in BY_NAME
    assert "my-termux" not in BY_NAME


def test_visible_names_excludes_hidden():
    from mytermux.commands import COMMANDS, visible_names
    vis = visible_names()
    for c in COMMANDS:
        if c.hidden:
            assert c.name not in vis
        else:
            assert c.name in vis


def test_completion_words_are_sorted_and_unique():
    from mytermux.commands import completion_words
    w = completion_words()
    assert w == sorted(set(w))
    assert "chat" in w and "media" in w
    # upper-case placeholders like PATH are not completable words
    assert not any(x.isupper() for x in w)


def test_hint_line_only_uses_real_commands():
    from mytermux.commands import BY_NAME, hint_line
    for part in hint_line().split():
        assert part in BY_NAME, f"hint line mentions unknown command {part!r}"


def test_describe_lists_visible_commands():
    from mytermux.commands import describe, visible_names
    text = describe()
    for n in visible_names():
        assert n in text


def test_registry_covers_every_cli_subparser():
    """If someone adds an argparse subcommand without registering it, the
    dashboard can never suggest it and tab-completion will miss it."""
    from mytermux import cli
    from mytermux.commands import BY_SUB

    parser = cli.build_parser()
    subs = set()
    for action in parser._actions:
        if isinstance(action, type(parser._subparsers._group_actions[0])) or \
                getattr(action, "choices", None) and hasattr(action, "dest") \
                and action.dest == "cmd":
            subs.update(action.choices.keys())
    assert subs, "could not introspect subparsers"
    for s in subs:
        assert s in BY_SUB or s == "help", f"subcommand {s!r} is not in the registry"


def test_readme_documents_every_visible_command():
    """Docs drift is how the dead-suggestion bug shipped in the first place."""
    from pathlib import Path
    from mytermux.commands import visible_names

    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    for name in visible_names():
        assert f"`{name}" in readme, f"README does not document the `{name}` command"


def test_readme_has_no_duplicated_sections():
    """The README once ended with a corrupted copy of its own tail."""
    from collections import Counter
    from pathlib import Path

    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    headings = [l.strip() for l in readme.splitlines() if l.startswith("#")]
    dupes = [h for h, n in Counter(headings).items() if n > 1]
    assert not dupes, f"duplicated README headings: {dupes}"
