"""Config loader / saver for my-termux.

Uses PyYAML if available, otherwise falls back to a tiny built-in parser
so that the CLI still boots on a broken Python env (self-heal can then fix it).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from .paths import CONFIG_FILE, CONFIG_DIR

DEFAULT_CONFIG: Dict[str, Any] = {
    "openrouter_api_key": "",
    "openrouter_title": "my-termux",
    "model_order": [
        "deepseek/deepseek-chat-v3.1:free",
        "google/gemini-2.0-flash-exp:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "openrouter/auto",
    ],
    "github_token": "",
    "github_username": "",
    "current_project": "",
    "auto_dashboard": True,
    "auto_pip_install": True,
    "theme": "dark",
    "notifications": True,
    "cloudinary_cloud_name": "",
    "cloudinary_api_key": "",
    "cloudinary_api_secret": "",
    "media_auto_sync": False,
}


def _yaml():
    try:
        import yaml  # type: ignore
        return yaml
    except Exception:
        return None


def _fallback_load(text: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    current_list_key = None
    for raw in text.splitlines():
        line = raw.rstrip()
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("- ") and current_list_key:
            data.setdefault(current_list_key, []).append(
                s[2:].strip().strip('"').strip("'")
            )
            continue
        current_list_key = None
        if s.endswith(":") and ":" in s and not s.startswith("-"):
            current_list_key = s[:-1].strip()
            data[current_list_key] = []
            continue
        if ":" in s:
            k, v = s.split(":", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if v.lower() in ("true", "false"):
                data[k] = v.lower() == "true"
            else:
                data[k] = v
    return data


def _fallback_dump(data: Dict[str, Any]) -> str:
    lines = []
    for k, v in data.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {item}")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        else:
            lines.append(f"{k}: {v}")
    return "\n".join(lines) + "\n"


def load_config() -> Dict[str, Any]:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    text = CONFIG_FILE.read_text(encoding="utf-8")
    yaml = _yaml()
    if yaml is not None:
        loaded = yaml.safe_load(text) or {}
    else:
        loaded = _fallback_load(text)
    # merge with defaults so new keys land automatically
    merged = dict(DEFAULT_CONFIG)
    merged.update({k: v for k, v in loaded.items() if v is not None})
    return merged


def save_config(cfg: Dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    yaml = _yaml()
    if yaml is not None:
        text = yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True)
    else:
        text = _fallback_dump(cfg)
    tmp = CONFIG_FILE.with_suffix(".yaml.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(CONFIG_FILE)


def set_value(key: str, value: Any) -> Dict[str, Any]:
    cfg = load_config()
    cfg[key] = value
    save_config(cfg)
    return cfg


def get_secret(key: str, default: Any = None) -> Any:
    """Resolve a secret by checking (in order): env vars, local config, cloud secrets.

    This helper prefers environment variables for runtime overrides. If a
    cloud secret mechanism is configured (AWS_SECRETS_MANAGER_ARN), it will
    attempt to fetch the secret value from AWS Secrets Manager as a best-effort
    (requires `boto3` and appropriate IAM permissions).
    """
    # 1) environment variable
    val = os.environ.get(key)
    if val is not None:
        return val

    # 2) local config file
    cfg = load_config()
    if key in cfg and cfg[key]:
        return cfg[key]

    # 3) AWS Secrets Manager (optional)
    arn = os.environ.get("AWS_SECRETS_MANAGER_ARN")
    if arn:
        try:
            import boto3
            client = boto3.client("secretsmanager")
            # name may be passed as full arn or simple name
            secret_name = arn
            resp = client.get_secret_value(SecretId=secret_name)
            secret_string = resp.get("SecretString")
            if secret_string:
                # assume JSON mapping of keys
                try:
                    import json as _json
                    data = _json.loads(secret_string)
                    return data.get(key, default)
                except Exception:
                    return secret_string
        except Exception:
            pass

    return default
