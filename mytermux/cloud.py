"""Optional Cloudinary sync for my-termux media vault.

- Uses the official `cloudinary` Python SDK when available.
- If credentials are missing or SDK not installed, all functions raise a clean
  RuntimeError with a helpful message — nothing crashes silently, but the vault
  keeps working offline.
- Resource-type routing per Cloudinary docs:
    image  -> resource_type=image
    video  -> resource_type=video
    audio  -> resource_type=video   (Cloudinary treats audio as video)
    doc/other -> resource_type=raw
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from . import db, media
from .config import load_config, save_config


PROVIDER = "cloudinary"
FOLDER_PREFIX = "my-termux"


class CloudNotConfigured(RuntimeError):
    pass


# ---- SDK loader -------------------------------------------------------------

def _sdk():
    try:
        import cloudinary  # type: ignore
        import cloudinary.uploader  # type: ignore
        import cloudinary.api  # type: ignore
        return cloudinary
    except Exception as e:
        raise CloudNotConfigured(
            "cloudinary SDK not installed. Run `pip install cloudinary` "
            "or `fix` to auto-install."
        ) from e


def _configured():
    cfg = load_config()
    cloud = (cfg.get("cloudinary_cloud_name") or "").strip()
    key = (cfg.get("cloudinary_api_key") or "").strip()
    secret = (cfg.get("cloudinary_api_secret") or "").strip()
    if not (cloud and key and secret):
        raise CloudNotConfigured(
            "Cloudinary not configured. Run `cloud setup` or edit "
            "~/my-termux/config/config.yaml (cloudinary_cloud_name / "
            "cloudinary_api_key / cloudinary_api_secret)."
        )
    sdk = _sdk()
    sdk.config(cloud_name=cloud, api_key=key, api_secret=secret, secure=True)
    return sdk, cfg


def is_configured() -> bool:
    try:
        _configured()
        return True
    except CloudNotConfigured:
        return False


def setup(cloud_name: str, api_key: str, api_secret: str) -> Dict:
    """Save Cloudinary creds. Returns the sanitised config."""
    cfg = load_config()
    cfg["cloudinary_cloud_name"] = cloud_name.strip()
    cfg["cloudinary_api_key"] = api_key.strip()
    cfg["cloudinary_api_secret"] = api_secret.strip()
    save_config(cfg)
    return {"cloud_name": cfg["cloudinary_cloud_name"], "configured": True}


# ---- resource routing -------------------------------------------------------

def _resource_type(kind: str) -> str:
    if kind == "image":
        return "image"
    if kind in ("video", "audio"):
        return "video"
    return "raw"


def _public_id_for(row: Dict) -> str:
    """my-termux/<kind>/<basename-without-ext>"""
    p = Path(row["path"])
    return f"{FOLDER_PREFIX}/{row['kind']}/{p.stem}"


# ---- operations -------------------------------------------------------------

def upload(media_id: int, overwrite: bool = False) -> Dict:
    sdk, _ = _configured()
    row = media.get(media_id)
    if row.get("cloud_public_id") and not overwrite:
        return row
    path = Path(row["path"])
    if not path.exists():
        raise FileNotFoundError(f"local file missing: {path}")

    rtype = _resource_type(row["kind"])
    public_id = _public_id_for(row)

    result = sdk.uploader.upload(
        str(path),
        public_id=public_id,
        resource_type=rtype,
        overwrite=True,
        unique_filename=False,
        use_filename=True,
        folder=None,  # folder is baked into public_id
    )
    url = result.get("secure_url") or result.get("url") or ""
    media.set_cloud(media_id, PROVIDER, result.get("public_id", public_id), url)
    db.log("info", "cloud", f"uploaded #{media_id} -> {url}")
    return media.get(media_id)


def sync_all(limit: int = 500) -> Dict:
    """Upload every un-synced media row. Returns a summary."""
    _configured()  # raise early if not set up
    ok = 0
    fail = 0
    errors: List[str] = []
    rows = [r for r in media.list_media(limit=limit) if not r.get("cloud_public_id")]
    for row in rows:
        try:
            upload(row["id"])
            ok += 1
        except Exception as e:
            fail += 1
            errors.append(f"#{row['id']}: {e}")
    return {"uploaded": ok, "failed": fail, "errors": errors, "pending_before": len(rows)}


def download(media_id: int, dest_dir: Path | None = None) -> Path:
    """Download a synced asset back to the phone (returns the path)."""
    sdk, _ = _configured()
    row = media.get(media_id)
    if not row.get("cloud_url"):
        raise RuntimeError(f"media #{media_id} has no cloud copy — upload it first")

    try:
        import httpx  # type: ignore
    except ImportError as e:
        raise RuntimeError("httpx not installed") from e

    url = row["cloud_url"]
    if dest_dir is None:
        media.ensure_media_dirs()
        dest_dir = media.MEDIA_DIR / media.KIND_DIRS.get(row["kind"], "other")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / Path(row["original_name"] or row["path"]).name

    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        r = client.get(url)
        r.raise_for_status()
        dest.write_bytes(r.content)
    db.log("info", "cloud", f"downloaded #{media_id} -> {dest}")
    return dest


def destroy_asset(media_id: int, also_local: bool = False) -> Dict:
    sdk, _ = _configured()
    row = media.get(media_id)
    pid = row.get("cloud_public_id")
    if not pid:
        raise RuntimeError(f"media #{media_id} has no cloud_public_id")
    rtype = _resource_type(row["kind"])
    result = sdk.uploader.destroy(pid, resource_type=rtype, invalidate=True)
    media.clear_cloud(media_id)
    if also_local:
        media.remove(media_id)
    db.log("info", "cloud", f"destroyed cloud #{media_id} rtype={rtype} -> {result}")
    return result


def list_remote(max_results: int = 100) -> List[Dict]:
    sdk, _ = _configured()
    out: List[Dict] = []
    for rtype in ("image", "video", "raw"):
        try:
            resp = sdk.api.resources(
                type="upload",
                resource_type=rtype,
                prefix=FOLDER_PREFIX + "/",
                max_results=max_results,
            )
            for r in resp.get("resources", []):
                out.append({
                    "public_id": r.get("public_id"),
                    "resource_type": rtype,
                    "bytes": r.get("bytes"),
                    "format": r.get("format"),
                    "secure_url": r.get("secure_url"),
                    "created_at": r.get("created_at"),
                })
        except Exception as e:
            db.log("warn", "cloud", f"list {rtype} failed: {e}")
    return out


def status() -> Dict:
    cfg = load_config()
    return {
        "configured": bool(cfg.get("cloudinary_cloud_name")
                           and cfg.get("cloudinary_api_key")
                           and cfg.get("cloudinary_api_secret")),
        "cloud_name": cfg.get("cloudinary_cloud_name", ""),
        "provider": PROVIDER,
        "folder_prefix": FOLDER_PREFIX,
    }
