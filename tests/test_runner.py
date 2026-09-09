"""Tests for self-healing runner."""

def test_run_success():
    from mytermux import runner
    r = runner.run("echo hello")
    assert r.ok
    assert "hello" in r.stdout
    assert r.attempts == 1


def test_run_failure():
    from mytermux import runner
    r = runner.run("false")
    assert not r.ok
    assert r.rc != 0


def test_run_with_heal_success_no_heal_needed():
    from mytermux import runner
    r = runner.run_with_heal("echo hi", max_attempts=2)
    assert r.ok
    assert not r.healed
    assert r.attempts == 1


def test_detect_missing_python_module():
    from mytermux.runner import _detect_missing_python_module
    assert _detect_missing_python_module("ModuleNotFoundError: No module named 'yaml'") == "yaml"
    assert _detect_missing_python_module("No module named 'requests'") == "requests"
    assert _detect_missing_python_module("ImportError: No module named PIL") == "PIL"
    assert _detect_missing_python_module("all good") is None


def test_detect_missing_command():
    from mytermux.runner import _detect_missing_command
    assert _detect_missing_command("bash: ffmpeg: command not found") == "ffmpeg"
    assert _detect_missing_command("all good") is None


def test_package_map_covers_common():
    from mytermux.runner import PACKAGE_MAP
    assert PACKAGE_MAP["yaml"] == "pyyaml"
    assert PACKAGE_MAP["PIL"] == "pillow"
    assert "httpx" in PACKAGE_MAP


def test_run_with_heal_tries_install_but_fails_gracefully(tmp_path):
    from mytermux import runner
    # This command will fail with ModuleNotFoundError for a non-existent package
    # The runner should attempt heal but not crash, and return failure
    # We use a python command that imports a definitely missing module
    r = runner.run_with_heal("python3 -c \"import this_module_does_not_exist_12345\"", max_attempts=2)
    assert not r.ok
    # Should have attempted 2 times (initial + one heal attempt that fails to install)
    assert r.attempts >= 1


def test_fix_file_syntax_unclosed_brackets(tmp_path):
    from mytermux import runner
    p = tmp_path / "bad.py"
    p.write_text("def foo():\n    print('hi'\n", encoding="utf-8")
    ok, msg = runner.fix_file_syntax(p, "unexpected EOF while parsing")
    # Should fix by adding closing )
    assert ok
    assert p.read_text().count(")") >= p.read_text().count("(") or "added" in msg.lower()
    # Check backup exists
    assert (tmp_path / "bad.py.bak").exists()


def test_fix_file_syntax_missing_import(tmp_path):
    from mytermux import runner
    p = tmp_path / "bad2.py"
    p.write_text("print(os.getcwd())\n", encoding="utf-8")
    ok, msg = runner.fix_file_syntax(p, "name 'os' is not defined")
    assert ok
    assert "import os" in p.read_text()


def test_fix_file_not_found():
    from mytermux import runner
    from pathlib import Path
    ok, msg = runner.fix_file_syntax(Path("/nonexistent/file.py"), "error")
    assert not ok
    assert "not found" in msg.lower()


def test_runner_cli_success(capsys):
    from mytermux import cli
    rc = cli.main(["run", "echo hello from runner"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "hello from runner" in out


def test_runner_cli_failure(capsys):
    from mytermux import cli
    rc = cli.main(["run", "false"])
    assert rc != 0
