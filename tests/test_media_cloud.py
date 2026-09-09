"""Tests for the local media vault + Cloudinary cloud sync layer."""
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# --------------------------------------------------------------------------
# media (local vault)
# --------------------------------------------------------------------------

def test_media_detect_kind(tmp_path):
    from mytermux import media
    assert media.detect_kind(tmp_path / "x.png") == "image"
    assert media.detect_kind(tmp_path / "x.MP4") == "video"
    assert media.detect_kind(tmp_path / "x.m4a") == "audio"
    assert media.detect_kind(tmp_path / "x.pdf") == "doc"
    assert media.detect_kind(tmp_path / "x.zzz") == "other"


def test_media_add_copies_and_registers(tmp_path):
    from mytermux import media
    src = tmp_path / "hello.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 40)
    row = media.add(src, tags="test")
    assert row["kind"] == "image"
    assert Path(row["path"]).exists()
    assert Path(row["path"]).parent.name == "images"
    assert row["tags"] == "test"
    assert row["size_bytes"] > 0
    assert row["sha256"], "sha256 should be computed"
    # source must still exist (copy, not move)
    assert src.exists()


def test_media_add_move_deletes_source(tmp_path):
    from mytermux import media
    src = tmp_path / "song.mp3"
    src.write_bytes(b"ID3" + b"\x00" * 32)
    row = media.add(src, move=True)
    assert row["kind"] == "audio"
    assert not src.exists()
    assert Path(row["path"]).exists()


def test_media_add_rejects_missing_file(tmp_path):
    from mytermux import media
    with pytest.raises(FileNotFoundError):
        media.add(tmp_path / "nope.jpg")


def test_media_list_and_filter(tmp_path):
    from mytermux import media
    (tmp_path / "a.png").write_bytes(b"x")
    (tmp_path / "b.mp4").write_bytes(b"y")
    (tmp_path / "c.pdf").write_bytes(b"z")
    media.add(tmp_path / "a.png")
    media.add(tmp_path / "b.mp4")
    media.add(tmp_path / "c.pdf")
    assert len(media.list_media()) == 3
    assert len(media.list_media(kind="image")) == 1
    assert len(media.list_media(kind="video")) == 1
    assert len(media.list_media(kind="doc")) == 1


def test_media_attach_and_remove(tmp_path):
    from mytermux import db, media
    (tmp_path / "x.txt").write_text("hi")
    row = media.add(tmp_path / "x.txt")
    sid = db.start_session("p")
    updated = media.attach(row["id"], session_id=sid, project="p", tags="chat")
    assert updated["session_id"] == sid
    assert updated["project"] == "p"
    assert updated["tags"] == "chat"

    path = Path(updated["path"])
    assert path.exists()
    media.remove(row["id"])
    assert not path.exists()
    assert media.list_media() == []


def test_media_remove_keep_file(tmp_path):
    from mytermux import media
    (tmp_path / "x.txt").write_text("keep")
    row = media.add(tmp_path / "x.txt")
    p = Path(row["path"])
    media.remove(row["id"], keep_file=True)
    assert p.exists(), "keep_file=True must preserve the file on disk"


def test_media_capture_requires_termux_api(monkeypatch):
    from mytermux import media
    monkeypatch.setattr(media.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError):
        media.capture_photo()
    with pytest.raises(RuntimeError):
        media.record_audio(1)


def test_media_open_without_termux_open(monkeypatch, tmp_path, capsys):
    from mytermux import media
    (tmp_path / "x.txt").write_text("hi")
    row = media.add(tmp_path / "x.txt")
    monkeypatch.setattr(media.shutil, "which", lambda _: None)
    assert media.open_with_android(row["id"]) is False
    out = capsys.readouterr().out
    assert "termux-open" in out


def test_media_set_and_clear_cloud(tmp_path):
    from mytermux import media
    (tmp_path / "x.png").write_bytes(b"x")
    row = media.add(tmp_path / "x.png")
    media.set_cloud(row["id"], "cloudinary", "my-termux/image/x",
                    "https://res.cloudinary.com/x/image/upload/my-termux/image/x.png")
    r2 = media.get(row["id"])
    assert r2["cloud_provider"] == "cloudinary"
    assert r2["cloud_public_id"] == "my-termux/image/x"
    assert r2["cloud_url"].startswith("https://")
    media.clear_cloud(row["id"])
    r3 = media.get(row["id"])
    assert r3["cloud_public_id"] is None


# --------------------------------------------------------------------------
# cloud (Cloudinary)
# --------------------------------------------------------------------------

def _install_fake_cloudinary(monkeypatch, uploader=None, api=None):
    """Install a fake `cloudinary` module in sys.modules and clear cached import."""
    fake = types.ModuleType("cloudinary")
    fake.config = MagicMock(return_value=None)
    fake.uploader = uploader or MagicMock()
    fake.api = api or MagicMock()

    fake_up = types.ModuleType("cloudinary.uploader")
    fake_up.upload = fake.uploader.upload
    fake_up.destroy = fake.uploader.destroy
    fake_api = types.ModuleType("cloudinary.api")
    fake_api.resources = fake.api.resources

    monkeypatch.setitem(sys.modules, "cloudinary", fake)
    monkeypatch.setitem(sys.modules, "cloudinary.uploader", fake_up)
    monkeypatch.setitem(sys.modules, "cloudinary.api", fake_api)
    return fake


def test_cloud_not_configured_by_default():
    from mytermux import cloud
    assert cloud.is_configured() is False
    with pytest.raises(cloud.CloudNotConfigured):
        cloud._configured()


def test_cloud_setup_saves_creds():
    from mytermux import cloud, config
    cloud.setup("mycloud", "keyA", "secretB")
    cfg = config.load_config()
    assert cfg["cloudinary_cloud_name"] == "mycloud"
    assert cfg["cloudinary_api_key"] == "keyA"
    assert cfg["cloudinary_api_secret"] == "secretB"


def test_cloud_upload_uses_correct_resource_type(monkeypatch, tmp_path):
    from mytermux import cloud, media

    # arrange creds + fake SDK
    cloud.setup("cn", "k", "s")
    uploader = MagicMock()
    uploader.upload = MagicMock(return_value={
        "public_id": "my-termux/image/x",
        "secure_url": "https://res.cloudinary.com/cn/image/upload/my-termux/image/x.png",
    })
    _install_fake_cloudinary(monkeypatch, uploader=uploader)

    src = tmp_path / "x.png"
    src.write_bytes(b"x")
    row = media.add(src)
    updated = cloud.upload(row["id"])
    assert updated["cloud_url"].startswith("https://res.cloudinary.com/")
    call = uploader.upload.call_args
    assert call.kwargs["resource_type"] == "image"
    assert call.kwargs["public_id"].startswith("my-termux/image/")

    # video routing
    uploader.upload.reset_mock()
    uploader.upload.return_value = {
        "public_id": "my-termux/video/v",
        "secure_url": "https://res.cloudinary.com/cn/video/upload/my-termux/video/v.mp4",
    }
    (tmp_path / "v.mp4").write_bytes(b"v")
    v_row = media.add(tmp_path / "v.mp4")
    cloud.upload(v_row["id"])
    assert uploader.upload.call_args.kwargs["resource_type"] == "video"

    # audio → video resource_type per Cloudinary
    uploader.upload.reset_mock()
    uploader.upload.return_value = {
        "public_id": "my-termux/audio/a",
        "secure_url": "https://res.cloudinary.com/cn/video/upload/my-termux/audio/a.mp3",
    }
    (tmp_path / "a.mp3").write_bytes(b"a")
    a_row = media.add(tmp_path / "a.mp3")
    cloud.upload(a_row["id"])
    assert uploader.upload.call_args.kwargs["resource_type"] == "video"

    # doc → raw
    uploader.upload.reset_mock()
    uploader.upload.return_value = {
        "public_id": "my-termux/doc/d",
        "secure_url": "https://res.cloudinary.com/cn/raw/upload/my-termux/doc/d.pdf",
    }
    (tmp_path / "d.pdf").write_bytes(b"d")
    d_row = media.add(tmp_path / "d.pdf")
    cloud.upload(d_row["id"])
    assert uploader.upload.call_args.kwargs["resource_type"] == "raw"


def test_cloud_upload_skips_already_uploaded(monkeypatch, tmp_path):
    from mytermux import cloud, media
    cloud.setup("cn", "k", "s")
    uploader = MagicMock()
    uploader.upload = MagicMock(return_value={
        "public_id": "my-termux/image/x",
        "secure_url": "https://res.cloudinary.com/cn/image/upload/x.png",
    })
    _install_fake_cloudinary(monkeypatch, uploader=uploader)

    (tmp_path / "x.png").write_bytes(b"x")
    row = media.add(tmp_path / "x.png")
    cloud.upload(row["id"])
    assert uploader.upload.call_count == 1
    # second call without --force must skip
    cloud.upload(row["id"])
    assert uploader.upload.call_count == 1
    # with overwrite=True it re-uploads
    cloud.upload(row["id"], overwrite=True)
    assert uploader.upload.call_count == 2


def test_cloud_sync_all_reports_summary(monkeypatch, tmp_path):
    from mytermux import cloud, media
    cloud.setup("cn", "k", "s")

    uploader = MagicMock()
    def _up(path, **kw):
        return {
            "public_id": kw["public_id"],
            "secure_url": f"https://res.cloudinary.com/cn/{kw['resource_type']}/upload/{kw['public_id']}",
        }
    uploader.upload = MagicMock(side_effect=_up)
    _install_fake_cloudinary(monkeypatch, uploader=uploader)

    for i, ext in enumerate([".png", ".mp4", ".txt"]):
        p = tmp_path / f"f{i}{ext}"
        p.write_bytes(b"data")
        media.add(p)

    report = cloud.sync_all()
    assert report["pending_before"] == 3
    assert report["uploaded"] == 3
    assert report["failed"] == 0
    # nothing pending after
    again = cloud.sync_all()
    assert again["pending_before"] == 0
    assert again["uploaded"] == 0


def test_cloud_destroy_and_clear(monkeypatch, tmp_path):
    from mytermux import cloud, media
    cloud.setup("cn", "k", "s")
    uploader = MagicMock()
    uploader.upload = MagicMock(return_value={
        "public_id": "my-termux/image/x",
        "secure_url": "https://res.cloudinary.com/cn/image/upload/x.png",
    })
    uploader.destroy = MagicMock(return_value={"result": "ok"})
    _install_fake_cloudinary(monkeypatch, uploader=uploader)

    (tmp_path / "x.png").write_bytes(b"x")
    row = media.add(tmp_path / "x.png")
    cloud.upload(row["id"])
    cloud.destroy_asset(row["id"])
    assert uploader.destroy.call_count == 1
    r2 = media.get(row["id"])
    assert r2["cloud_public_id"] is None


def test_cloud_download_uses_secure_url(monkeypatch, tmp_path):
    from mytermux import cloud, media
    cloud.setup("cn", "k", "s")

    uploader = MagicMock()
    uploader.upload = MagicMock(return_value={
        "public_id": "my-termux/image/x",
        "secure_url": "https://example/x.png",
    })
    _install_fake_cloudinary(monkeypatch, uploader=uploader)

    (tmp_path / "x.png").write_bytes(b"local-original")
    row = media.add(tmp_path / "x.png")
    cloud.upload(row["id"])

    # fake httpx
    class FakeResp:
        status_code = 200
        content = b"downloaded-bytes"
        def raise_for_status(self): pass

    class FakeClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url): return FakeResp()

    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(Client=FakeClient))

    dest_dir = tmp_path / "dl"
    dest = cloud.download(row["id"], dest_dir=dest_dir)
    assert dest.exists()
    assert dest.read_bytes() == b"downloaded-bytes"


def test_cloud_operations_fail_cleanly_without_creds():
    from mytermux import cloud
    with pytest.raises(cloud.CloudNotConfigured):
        cloud.sync_all()
    with pytest.raises(cloud.CloudNotConfigured):
        cloud.upload(1)


# --------------------------------------------------------------------------
# CLI wiring
# --------------------------------------------------------------------------

def test_cli_media_add_and_list(tmp_path, capsys):
    from mytermux import cli
    f = tmp_path / "pic.png"
    f.write_bytes(b"x")
    rc = cli.main(["media", "add", str(f), "--tags", "cli"])
    assert rc == 0
    rc = cli.main(["media", "list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "pic.png" in out
    assert "image" in out


def test_cli_media_attach_to_session(tmp_path, capsys):
    from mytermux import cli, db
    f = tmp_path / "n.txt"
    f.write_text("hi")
    cli.main(["media", "add", str(f)])
    sid = db.start_session("p")
    from mytermux import media
    mid = media.list_media()[0]["id"]
    rc = cli.main(["media", "attach", str(mid), "--session", str(sid)])
    assert rc == 0
    assert media.get(mid)["session_id"] == sid


def test_cli_cloud_status_when_not_configured(capsys):
    from mytermux import cli
    rc = cli.main(["cloud", "status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "configured:" in out.lower()
    assert "false" in out.lower()


def test_cli_cloud_sync_without_creds_errors(capsys):
    from mytermux import cli
    rc = cli.main(["cloud", "sync"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "Cloudinary not configured" in out or "not configured" in out.lower()


def test_heal_includes_media_and_cloud_checks():
    from mytermux import heal
    report = heal.heal()
    names = {c["name"] for c in report["after"]}
    assert any(n.startswith("dir:media/") for n in names)
    assert any("cloudinary" in n for n in names)
    assert any("pip:cloudinary" in n for n in names)


def test_install_script_has_media_and_cloud():
    root = Path(__file__).resolve().parents[1]
    ins = (root / "install.sh").read_text()
    assert "my-media" in ins
    assert "my-cloud" in ins
    assert "cloudinary" in ins.lower()


def test_dispatch_script_maps_new_commands():
    """`media` and `cloud` must resolve through the dispatcher (as do their
    legacy `my-` aliases)."""
    from tests.helpers import dispatch_as

    for name in ("media", "cloud", "my-media", "my-cloud"):
        rc, out, err = dispatch_as(name)
        assert rc == 0, f"{name} exited {rc}: {out}"
        assert out == name.replace("my-", ""), f"{name} -> {out!r}"
