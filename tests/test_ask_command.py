"""End-to-end tests for the one-shot `ask` command.

These run a real local SSE server that speaks the OpenRouter streaming protocol,
so the whole path is exercised for real:

  cli.cmd_ask -> agent.run_turn -> openrouter.chat_stream (httpx, streaming)
              -> tool parse/run -> observation -> final answer -> SQLite

No mocking of the transport, because a mock that never streams is exactly the
kind of stand-in that hides a broken code path.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


def _sse(chunks, finish=None):
    """Build an OpenRouter-shaped SSE body."""
    lines = []
    for c in chunks:
        lines.append("data: " + json.dumps(
            {"choices": [{"delta": {"content": c}}]}) + "\n\n")
    if finish:
        lines.append("data: " + json.dumps(
            {"choices": [{"delta": {}, "finish_reason": finish}]}) + "\n\n")
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode("utf-8")


class _Handler(BaseHTTPRequestHandler):
    """Serves whatever body the test attached to the server instance."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.server.received.append(json.loads(self.rfile.read(length) or b"{}"))
        body = self.server.body
        self.send_response(self.server.status)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_openrouter():
    """Start a local SSE server and point openrouter at it."""
    from mytermux import openrouter

    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    srv.body = b""
    srv.status = 200
    srv.received = []
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    original = openrouter.API_URL
    openrouter.API_URL = f"http://127.0.0.1:{srv.server_address[1]}/chat/completions"
    try:
        yield srv
    finally:
        openrouter.API_URL = original
        srv.shutdown()
        srv.server_close()


def _set_key():
    from mytermux.config import set_value
    set_value("openrouter_api_key", "sk-or-v1-test-key")


def test_ask_streams_answer_and_exits(fake_openrouter, capsys):
    """The core promise of `ask`: one question, one answer, no REPL."""
    import argparse
    from mytermux import cli

    _set_key()
    fake_openrouter.body = _sse(["The answer ", "is 42."], finish="stop")

    rc = cli.cmd_ask(argparse.Namespace(question=["what is 6 times 7?"]))
    out = capsys.readouterr().out
    assert rc == 0
    assert "The answer is 42." in out


def test_ask_sends_the_question_to_the_model(fake_openrouter):
    import argparse
    from mytermux import cli

    _set_key()
    fake_openrouter.body = _sse(["ok"], finish="stop")
    cli.cmd_ask(argparse.Namespace(question=["how do I list files?"]))

    assert fake_openrouter.received, "no request reached the model"
    payload = fake_openrouter.received[0]
    assert payload["stream"] is True
    assert payload["messages"][-1]["role"] == "user"
    assert "how do I list files?" in payload["messages"][-1]["content"]


def test_ask_uses_the_agent_system_prompt_with_tools(fake_openrouter):
    import argparse
    from mytermux import cli

    _set_key()
    fake_openrouter.body = _sse(["ok"], finish="stop")
    cli.cmd_ask(argparse.Namespace(question=["hi"]))

    system = fake_openrouter.received[0]["messages"][0]["content"]
    assert "my-termux" in system
    assert "TOOLS AVAILABLE" in system
    assert "shell" in system


def test_ask_persists_the_exchange(fake_openrouter):
    """One-shot or not, the session must land in SQLite like any other."""
    import argparse
    from mytermux import cli, db

    _set_key()
    fake_openrouter.body = _sse(["Persisted answer."], finish="stop")
    cli.cmd_ask(argparse.Namespace(question=["remember this"]))

    row = db.get_last_session()
    assert row is not None
    assert row["summary"] == "one-shot ask"
    contents = [m["content"] for m in db.get_session_messages(int(row["id"]))]
    assert "remember this" in contents
    assert "Persisted answer." in contents


def test_ask_executes_a_tool_the_model_requests(fake_openrouter, tmp_path):
    """`ask` runs the full agent loop, not a bare completion."""
    import argparse
    from mytermux import cli

    _set_key()
    target = tmp_path / "made_by_tool.txt"
    tool_call = ('<tool name="write_file">'
                 + json.dumps({"path": str(target), "content": "hello"})
                 + '</tool>')
    # first response asks for a tool, second gives the final answer
    bodies = [_sse([tool_call], finish="stop"),
              _sse(["Wrote the file."], finish="stop")]
    fake_openrouter.body = bodies[0]

    class SequentialHandler(_Handler):
        def do_POST(self):
            super().do_POST()

    # serve the two bodies in order by swapping after the first request
    original_do = _Handler.do_POST

    def do_POST(self):
        original_do(self)
        if len(self.server.received) == 1:
            self.server.body = bodies[1]

    _Handler.do_POST = do_POST
    try:
        rc = cli.cmd_ask(argparse.Namespace(
            question=[f"create a file at {target}"]))
    finally:
        _Handler.do_POST = original_do

    assert rc == 0
    assert target.exists(), "agent never ran the write_file tool"
    assert target.read_text(encoding="utf-8") == "hello"


def test_ask_without_a_question_exits_nonzero(capsys):
    import argparse
    from mytermux import cli

    rc = cli.cmd_ask(argparse.Namespace(question=[]))
    out = capsys.readouterr().out
    assert rc == 1
    assert "[error]" in out
    assert "ask" in out


def test_ask_without_an_api_key_reports_clearly(fake_openrouter, capsys):
    """No key must produce the actionable message, not a traceback."""
    import argparse
    from mytermux import cli

    fake_openrouter.body = _sse(["never"], finish="stop")
    rc = cli.cmd_ask(argparse.Namespace(question=["hi"]))
    out = capsys.readouterr().out
    assert "OpenRouter API key" in out or rc != 0


def test_ask_joins_bare_words_into_one_question(fake_openrouter):
    """`ask what does this do` (unquoted) must still work."""
    import argparse
    from mytermux import cli

    _set_key()
    fake_openrouter.body = _sse(["ok"], finish="stop")
    cli.cmd_ask(argparse.Namespace(question=["what", "does", "this", "do"]))

    sent = fake_openrouter.received[0]["messages"][-1]["content"]
    assert "what does this do" in sent


def test_ask_survives_a_server_error(fake_openrouter, capsys):
    """A failing upstream must not traceback out of the CLI, and must exit 1."""
    import argparse
    from mytermux import cli

    _set_key()
    fake_openrouter.status = 402
    fake_openrouter.body = json.dumps(
        {"error": {"code": 402, "message": "payment required"}}).encode()
    rc = cli.cmd_ask(argparse.Namespace(question=["hi"]))
    out = capsys.readouterr().out
    assert rc == 1, "a failed ask must exit non-zero so scripts can detect it"
    assert "[error]" in out


def test_ask_surfaces_the_upstream_error_message(fake_openrouter, capsys):
    """Regression: inside client.stream() the body is unread, so .json() raised
    and the real OpenRouter reason was replaced with "unknown error"."""
    import argparse
    from mytermux import cli

    _set_key()
    fake_openrouter.status = 402
    fake_openrouter.body = json.dumps(
        {"error": {"code": 402, "message": "payment required"}}).encode()
    cli.cmd_ask(argparse.Namespace(question=["hi"]))
    out = capsys.readouterr().out
    assert "payment required" in out, (
        f"the upstream reason was swallowed: {out!r}")
    assert "unknown error" not in out


def test_parse_error_reads_a_streaming_response():
    """Unit-level guard for the ResponseNotRead trap."""
    from mytermux.openrouter import _parse_error

    class FakeStreamingResp:
        status_code = 429
        headers = {"Retry-After": "7"}
        _read = False

        def read(self):
            FakeStreamingResp._read = True

        def json(self):
            if not FakeStreamingResp._read:
                raise RuntimeError("ResponseNotRead")
            return {"error": {"code": 429, "message": "rate limited"}}

        @property
        def text(self):
            if not FakeStreamingResp._read:
                raise RuntimeError("ResponseNotRead")
            return '{"error": {"message": "rate limited"}}'

    code, msg, retry_after = _parse_error(FakeStreamingResp())
    assert code == 429
    assert msg == "rate limited"
    assert retry_after == "7"
