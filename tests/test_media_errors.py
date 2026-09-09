"""Tests for the friendly-error handling added to `my-media` subcommands.

These cover the six bug-fix scenarios (placeholder path, missing file,
directory path, and KeyError-friendly info/rm/attach) plus the regression
scenarios listed in the review request.
"""
from __future__ import annotations

from pathlib import Path


# --------------------------------------------------------------------------
# BUG FIXES: `my-media add` friendly error handling
# --------------------------------------------------------------------------

def test_media_add_placeholder_path_prints_friendly_error(capsys):
    """`my-media add <something>.jpg` — literal angle brackets must be caught
    with a placeholder-specific message and exit 1 (no traceback)."""
    from mytermux import cli
    rc = cli.main(["media", "add", "<some-photo>.jpg"])
    out = capsys.readouterr().out
    assert rc == 1, f"expected exit code 1, got {rc}"
    assert "placeholder" in out.lower(), (
        f"expected 'placeholder' in message, got: {out!r}")
    # Should suggest tab-completion
    assert "tab" in out.lower(), (
        f"expected tab-completion hint, got: {out!r}")


def test_media_add_placeholder_startswith_angle_bracket(capsys):
    """Path that starts with < like `<path>/photo.jpg` should also be caught."""
    from mytermux import cli
    rc = cli.main(["media", "add", "<path>/photo.jpg"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "placeholder" in out.lower()


def test_media_add_missing_file_prints_tip(tmp_path, capsys):
    """Nonexistent file — must print `[error] file not found:` + tip, exit 1."""
    from mytermux import cli
    missing = tmp_path / "does" / "not" / "exist.jpg"
    rc = cli.main(["media", "add", str(missing)])
    out = capsys.readouterr().out
    assert rc == 1, f"expected exit code 1, got {rc}"
    assert "[error] file not found:" in out, (
        f"expected `[error] file not found:` prefix, got: {out!r}")
    assert "tip:" in out, f"expected `tip:` line, got: {out!r}"
    assert "ls ~/storage/shared/DCIM/Camera/" in out, (
        f"expected DCIM/Camera hint, got: {out!r}")
    assert "termux-setup-storage" in out, (
        f"expected termux-setup-storage hint, got: {out!r}")


def test_media_add_directory_prints_friendly_error(tmp_path, capsys):
    """Passing a directory path must error friendly, exit 1."""
    from mytermux import cli
    d = tmp_path / "somedir"
    d.mkdir()
    rc = cli.main(["media", "add", str(d)])
    out = capsys.readouterr().out
    assert rc == 1, f"expected exit code 1, got {rc}"
    assert "is a directory, not a file" in out, (
        f"expected directory error message, got: {out!r}")


# --------------------------------------------------------------------------
# BUG FIXES: KeyError-friendly info / rm / attach for nonexistent ids
# --------------------------------------------------------------------------

def test_media_info_missing_id_prints_friendly_error(capsys):
    """`my-media info 99999` — must print friendly error, no traceback,
    exit code 1."""
    from mytermux import cli
    rc = cli.main(["media", "info", "99999"])
    out = capsys.readouterr().out
    assert rc == 1, f"expected exit code 1, got {rc}"
    assert "[error]" in out, f"expected `[error]` prefix, got: {out!r}"
    assert "media #99999 not found" in out, (
        f"expected 'media #99999 not found', got: {out!r}")
    assert "media list" in out, (
        f"expected 'media list' hint, got: {out!r}")


def test_media_rm_missing_id_prints_friendly_error(capsys):
    from mytermux import cli
    rc = cli.main(["media", "rm", "99999"])
    out = capsys.readouterr().out
    assert rc == 1, f"expected exit code 1, got {rc}"
    assert "[error]" in out
    assert "media #99999 not found" in out
    assert "media list" in out


def test_media_attach_missing_id_prints_friendly_error(capsys):
    from mytermux import cli
    rc = cli.main(["media", "attach", "99999", "--session", "1"])
    out = capsys.readouterr().out
    assert rc == 1, f"expected exit code 1, got {rc}"
    assert "[error]" in out
    assert "media #99999 not found" in out
    assert "media list" in out


def test_media_open_missing_id_prints_friendly_error(capsys):
    """`my-media open 99999` — bonus coverage since cli.py also wraps this."""
    from mytermux import cli
    rc = cli.main(["media", "open", "99999"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "[error]" in out
    assert "media #99999 not found" in out


# --------------------------------------------------------------------------
# REGRESSIONS: happy-path media commands still work
# --------------------------------------------------------------------------

def test_media_add_valid_file_still_works(tmp_path, capsys):
    """A real, existing file must still be imported. Exit 0 and print the
    `[media] added #N kind=... path=...` line."""
    from mytermux import cli
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 32)  # jpeg-ish header
    rc = cli.main(["media", "add", str(f)])
    out = capsys.readouterr().out
    assert rc == 0, f"expected exit code 0, got {rc}. out={out!r}"
    assert "[media] added #" in out
    assert "kind=image" in out
    assert "path=" in out


def test_media_list_empty_shows_helpful_tip(capsys):
    """Empty vault must print `(no items)` with the new `tip:` suffix and exit 0."""
    from mytermux import cli
    rc = cli.main(["media", "list"])
    out = capsys.readouterr().out
    assert rc == 0, f"expected exit code 0, got {rc}"
    assert "(no items)" in out
    assert "tip:" in out
    assert "media add" in out


def test_media_list_after_add_shows_columns(tmp_path, capsys):
    """After adding a file, `my-media list` should show ID, KIND, SIZE, CLOUD, NAME columns."""
    from mytermux import cli
    f = tmp_path / "note.pdf"
    f.write_bytes(b"%PDF-1.4\n" + b"\x00" * 32)
    assert cli.main(["media", "add", str(f)]) == 0
    capsys.readouterr()  # clear
    rc = cli.main(["media", "list"])
    out = capsys.readouterr().out
    assert rc == 0
    # header
    for col in ("ID", "KIND", "SIZE", "CLOUD", "NAME"):
        assert col in out, f"expected column {col!r} in list header, got: {out!r}"
    # data row
    assert "doc" in out
    assert "note.pdf" in out


# --------------------------------------------------------------------------
# REGRESSIONS: cloud commands still behave when unconfigured
# --------------------------------------------------------------------------

def test_cloud_status_unconfigured_exits_zero(capsys):
    from mytermux import cli
    rc = cli.main(["cloud", "status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "configured:" in out.lower()
    assert "false" in out.lower()


def test_cloud_sync_without_creds_exits_one_with_hint(capsys):
    from mytermux import cli
    rc = cli.main(["cloud", "sync"])
    out = capsys.readouterr().out
    assert rc == 1
    # message must clearly mention Cloudinary not configured
    assert "not configured" in out.lower()
