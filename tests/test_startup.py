"""Tests for cached startup: fast heal replay and the 'since last time' digest."""


# ---- heal cache -------------------------------------------------------------

def test_heal_if_stale_runs_then_replays(monkeypatch):
    from mytermux import startup

    calls = []

    def fake_heal():
        calls.append(1)
        return {"started_at": "x", "after": [{"name": "a", "ok": True, "detail": ""}],
                "repairs": [], "log_path": "/tmp/x.json"}

    monkeypatch.setattr(startup.heal_mod, "heal", fake_heal)

    first = startup.heal_if_stale()
    assert first["from_cache"] is False
    second = startup.heal_if_stale()
    assert second["from_cache"] is True
    assert len(calls) == 1, "heal() must not re-run while the cache is fresh"


def test_heal_if_stale_force_bypasses_cache(monkeypatch):
    from mytermux import startup
    calls = []
    monkeypatch.setattr(startup.heal_mod, "heal",
                        lambda: calls.append(1) or {"after": [{"name": "a", "ok": True}],
                                                     "repairs": []})
    startup.heal_if_stale()
    startup.heal_if_stale(force=True)
    assert len(calls) == 2


def test_heal_if_stale_reruns_after_max_age(monkeypatch):
    from mytermux import startup
    calls = []
    monkeypatch.setattr(startup.heal_mod, "heal",
                        lambda: calls.append(1) or {"after": [{"name": "a", "ok": True}],
                                                     "repairs": []})
    startup.heal_if_stale()
    # a tiny max-age means the cached verdict is immediately stale
    startup.heal_if_stale(max_age_hours=0.0)
    assert len(calls) == 2


def test_heal_cache_age_hours_none_without_cache():
    from mytermux import paths, startup
    paths.HEAL_CACHE.unlink(missing_ok=True)
    assert startup.heal_cache_age_hours() is None


def test_heal_cache_age_hours_is_non_negative(monkeypatch):
    from mytermux import startup
    monkeypatch.setattr(startup.heal_mod, "heal",
                        lambda: {"after": [{"name": "a", "ok": True}], "repairs": []})
    startup.heal_if_stale()
    age = startup.heal_cache_age_hours()
    assert age is not None and age >= 0.0


def test_outstanding_issues_splits_required_and_optional():
    from mytermux import startup
    report = {"after": [
        {"name": "pip:httpx", "ok": False, "detail": "missing"},
        {"name": "pip:cloudinary (optional)", "ok": False, "detail": "missing"},
        {"name": "db:file", "ok": True, "detail": ""},
    ]}
    issues = startup.outstanding_issues(report)
    assert [c["name"] for c in issues["required"]] == ["pip:httpx"]
    assert [c["name"] for c in issues["optional"]] == ["pip:cloudinary (optional)"]


def test_outstanding_issues_tolerates_empty_report():
    from mytermux import startup
    assert startup.outstanding_issues({}) == {"required": [], "optional": []}


# ---- digest -----------------------------------------------------------------

def test_digest_counts_activity_since_last_open():
    from mytermux import db, startup

    startup.mark_opened()
    # make "since" in the past so the new rows count
    from mytermux import paths
    paths.LAST_OPEN_CACHE.write_text('{"at": "2000-01-01T00:00:00+00:00"}', encoding="utf-8")

    sid = db.start_session("p")
    db.add_message(sid, "user", "hi")
    db.add_message(sid, "assistant", "hello there")
    tid = db.add_task("do the thing")
    db.complete_task(tid)

    d = startup.digest()
    assert d["sessions"] == 1
    assert d["messages"] == 2
    assert d["tasks_done"] == 1
    assert d["last_reply"] == "hello there"
    assert d["totals"]["tasks_pending"] == 0
    assert d["totals"]["tasks_done"] == 1


def test_digest_line_reads_well():
    from mytermux import startup
    assert startup.digest_line({"since_label": None}) == ""
    assert startup.digest_line({"since_label": "3h ago", "sessions": 0,
                                "messages": 0, "tasks_done": 0}) == "nothing new since 3h ago"
    line = startup.digest_line({"since_label": "2d ago", "sessions": 2,
                                "messages": 14, "tasks_done": 1})
    assert line == "2 sessions, 14 messages, 1 task done since 2d ago"


def test_digest_line_singular_forms():
    from mytermux import startup
    line = startup.digest_line({"since_label": "5m ago", "sessions": 1,
                                "messages": 1, "tasks_done": 1})
    assert "1 session," in line and "1 message," in line and "1 task done" in line


def test_digest_before_first_open_has_no_label():
    from mytermux import paths, startup
    paths.LAST_OPEN_CACHE.unlink(missing_ok=True)
    d = startup.digest()
    assert d["since"] is None
    assert d["since_label"] is None
    assert startup.digest_line(d) == ""


def test_mark_opened_roundtrip():
    from mytermux import startup
    stamp = startup.mark_opened()
    assert startup.last_opened_at() == stamp


def test_human_delta_buckets():
    from mytermux.startup import _human_delta
    assert _human_delta(5) == "just now"
    assert _human_delta(60 * 30) == "30m ago"
    assert _human_delta(3600 * 5) == "5h ago"
    assert _human_delta(86400 * 3) == "3d ago"
    assert _human_delta(86400 * 21) == "3w ago"


def test_snippet_skips_noise_and_truncates():
    from mytermux.startup import snippet
    assert snippet(None) == ""
    assert snippet("") == ""
    assert snippet("<think>hmm</think>\n\nRun this:\n\n```sh\nls\n```") == "Run this:"
    assert len(snippet("x" * 200, width=40)) == 40
    assert snippet("x" * 200, width=40).endswith("\u2026")


def test_snippet_collapses_whitespace():
    from mytermux.startup import snippet
    assert snippet("  hello    world  ") == "hello world"


# ---- db helpers the digest relies on ----------------------------------------

def test_count_since_rejects_unknown_table():
    from mytermux import db
    assert db.count_since("sqlite_master", "2000-01-01") == 0
    assert db.count_since("messages; DROP TABLE messages", "2000-01-01") == 0


def test_count_since_filters_by_where():
    from mytermux import db
    t1 = db.add_task("a")
    db.add_task("b")
    db.complete_task(t1)
    assert db.count_since("tasks", "2000-01-01", where="status='done'") == 1
    assert db.count_since("tasks", "2000-01-01", where="status='pending'") == 1


def test_last_message_role_filter():
    from mytermux import db
    sid = db.start_session("p")
    db.add_message(sid, "user", "question")
    db.add_message(sid, "assistant", "answer")
    assert db.last_message("user")["content"] == "question"
    assert db.last_message("assistant")["content"] == "answer"
    assert db.last_message()["content"] == "answer"


def test_last_message_none_when_empty():
    from mytermux import db
    assert db.last_message() is None


def test_totals_shape():
    from mytermux import db
    t = db.totals()
    for key in ("sessions", "messages", "tasks_pending", "tasks_done", "goals", "projects"):
        assert key in t and isinstance(t[key], int)
