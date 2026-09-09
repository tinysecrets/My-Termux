"""Tests for the Termux/Android device bridge.

The contract that matters most: on a machine with no termux-api (a laptop, CI,
a phone without the Termux:API app) nothing here may raise, hang, or slow the
dashboard down.
"""
import json


# ---- capability detection ---------------------------------------------------

def test_is_termux_is_a_bool():
    from mytermux import device
    assert isinstance(device.is_termux(), bool)


def test_force_env_makes_is_termux_true(monkeypatch):
    from mytermux import device
    monkeypatch.setenv("MYTERMUX_FORCE", "1")
    assert device.is_termux() is True


def test_api_available_false_without_binaries(monkeypatch):
    from mytermux import device
    monkeypatch.setattr(device.shutil, "which", lambda _: None)
    assert device.api_available() is False


def test_api_available_true_when_binary_present(monkeypatch):
    from mytermux import device
    monkeypatch.setattr(device.shutil, "which",
                        lambda n: "/usr/bin/" + n if n == "termux-battery-status" else None)
    assert device.api_available() is True


# ---- battery ----------------------------------------------------------------

def test_battery_none_when_api_missing(monkeypatch):
    from mytermux import device
    monkeypatch.setattr(device, "api_available", lambda: False)
    assert device.battery() is None


def test_battery_parses_termux_json_list(monkeypatch):
    from mytermux import device
    monkeypatch.setattr(device, "api_available", lambda: True)
    monkeypatch.setattr(device, "_run", lambda *a, **k: json.dumps(
        [{"health": "GOOD", "percentage": 73, "plugged": "USB",
          "status": "CHARGING", "temperature": 28.9}]))
    b = device.battery(use_cache=False)
    assert b["percent"] == 73
    assert b["plugged"] == "USB"
    assert b["status"] == "CHARGING"
    assert b["temperature"] == 28.9


def test_battery_handles_dict_instead_of_list(monkeypatch):
    from mytermux import device
    monkeypatch.setattr(device, "api_available", lambda: True)
    monkeypatch.setattr(device, "_run", lambda *a, **k: json.dumps({"percentage": 50}))
    assert device.battery(use_cache=False)["percent"] == 50


def test_battery_tolerates_garbage_output(monkeypatch):
    from mytermux import device
    monkeypatch.setattr(device, "api_available", lambda: True)
    for junk in ("", "not json", "[]", "null", '["x"]'):
        monkeypatch.setattr(device, "_run", lambda *a, _j=junk, **k: _j or None)
        assert device.battery(use_cache=False) is None, f"junk {junk!r} broke battery()"


def test_battery_tolerates_timeout(monkeypatch):
    from mytermux import device
    monkeypatch.setattr(device, "api_available", lambda: True)
    monkeypatch.setattr(device, "_run", lambda *a, **k: None)
    assert device.battery(use_cache=False) is None


def test_battery_is_cached(monkeypatch):
    from mytermux import device
    monkeypatch.setattr(device, "api_available", lambda: True)
    calls = []

    def fake_run(*a, **k):
        calls.append(1)
        return json.dumps([{"percentage": 42, "plugged": "UNPLUGGED"}])

    monkeypatch.setattr(device, "_run", fake_run)
    device.clear_cache()
    first = device.battery(use_cache=True, ttl=60)
    second = device.battery(use_cache=True, ttl=60)
    assert first == second
    assert len(calls) == 1, f"expected one probe, got {len(calls)} — cache missed"


def test_battery_cache_expires(monkeypatch):
    from mytermux import device
    monkeypatch.setattr(device, "api_available", lambda: True)
    monkeypatch.setattr(device, "_run",
                        lambda *a, **k: json.dumps([{"percentage": 42}]))
    device.clear_cache()
    device.battery(use_cache=True, ttl=60)
    assert device._cache_read("battery", 0) is None, "ttl=0 must miss"


# ---- storage / width --------------------------------------------------------

def test_storage_reports_free_and_total():
    from mytermux import device
    s = device.storage("/")
    assert s is not None
    assert s["free"] > 0 and s["total"] > 0
    assert 0 <= s["percent_free"] <= 100


def test_storage_none_for_missing_path():
    from mytermux import device
    assert device.storage("/no/such/mount/point/xyz") is None


def test_screen_width_is_clamped(monkeypatch):
    from mytermux import device
    monkeypatch.setenv("COLUMNS", "10")
    assert device.screen_width() >= 40
    monkeypatch.setenv("COLUMNS", "5000")
    assert device.screen_width() <= 200


def test_screen_width_ignores_garbage(monkeypatch):
    from mytermux import device
    monkeypatch.setenv("COLUMNS", "banana")
    assert 40 <= device.screen_width() <= 200


# ---- clipboard / vibrate ----------------------------------------------------

def test_clipboard_noops_without_termux_api(monkeypatch):
    from mytermux import device
    monkeypatch.setattr(device.shutil, "which", lambda _: None)
    assert device.clipboard_set("x") is False
    assert device.clipboard_get() is None
    assert device.vibrate() is False


# ---- presentation -----------------------------------------------------------

def test_human_bytes():
    from mytermux.device import human_bytes
    assert human_bytes(None) == "-"
    assert human_bytes(0) == "0B"
    assert human_bytes(512) == "512B"
    assert human_bytes(1536) == "1.5K"
    assert human_bytes(2 * 1024 ** 3) == "2.0G"


def test_battery_bar_length_is_stable():
    from mytermux.device import battery_bar
    for p in (None, -5, 0, 33, 100, 999, "junk"):
        assert len(battery_bar(p, width=10)) == 10


def test_battery_bar_fills_with_percent():
    from mytermux.device import battery_bar
    assert battery_bar(0, 10).count("\u2593") == 0
    assert battery_bar(100, 10).count("\u2593") == 10
    assert battery_bar(50, 10).count("\u2593") == 5


def test_battery_style_reflects_urgency():
    from mytermux.device import battery_style
    assert battery_style(None) == "dim"
    assert battery_style(10) == "red"
    assert battery_style(25) == "yellow"
    assert battery_style(90) == "green"
    assert battery_style(10, plugged="USB") == "green"


def test_battery_line_formats_and_degrades():
    from mytermux.device import battery_line
    assert battery_line({}) == "-"
    assert battery_line({"battery": None}) == "-"
    line = battery_line({"battery": {"percent": 78, "plugged": "USB"}})
    assert line.startswith("78%")
    assert "usb" in line
    assert "charging" in battery_line(
        {"battery": {"percent": 40, "plugged": "UNPLUGGED", "status": "CHARGING"}})


# ---- snapshot ---------------------------------------------------------------

def test_snapshot_never_raises_without_termux(monkeypatch):
    from mytermux import device
    monkeypatch.setattr(device.shutil, "which", lambda _: None)
    monkeypatch.setattr(device, "is_termux", lambda: False)
    device.clear_cache()
    snap = device.snapshot(use_cache=False)
    assert snap["battery"] is None
    assert snap["api"] is False
    assert snap["storage"]["free"] > 0
    assert snap["width"] >= 40


def test_snapshot_is_cached(monkeypatch):
    from mytermux import device
    monkeypatch.setattr(device.shutil, "which", lambda _: None)
    device.clear_cache()
    first = device.snapshot(use_cache=True, ttl=120)
    assert first.get("cached") is not True
    second = device.snapshot(use_cache=True, ttl=120)
    assert second.get("cached") is True
    assert {k: v for k, v in second.items() if k != "cached"} == first


def test_clear_cache_removes_file():
    from mytermux import device, paths
    device.snapshot(use_cache=False)
    assert paths.DEVICE_CACHE.exists()
    device.clear_cache()
    assert not paths.DEVICE_CACHE.exists()


def test_cache_survives_corrupt_file():
    from mytermux import device, paths
    paths.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    paths.DEVICE_CACHE.write_text("{not json at all", encoding="utf-8")
    device.clear_cache()
    # reading a corrupt cache must not raise
    assert device._cache_read("battery", 60) is None
