"""Local config and per-chat sync state, stored in ~/.config/text-to-playlist."""
from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("TTP_CONFIG_DIR", Path.home() / ".config" / "text-to-playlist"))


DATA_DIR = Path(os.environ.get("TTP_DATA_DIR", Path.home() / ".local" / "share" / "text-to-playlist"))


def _path(name: str) -> Path:
    return CONFIG_DIR / name


def load(name: str) -> dict:
    p = _path(name)
    return json.loads(p.read_text()) if p.exists() else {}


def save(name: str, data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    p = _path(name)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.chmod(tmp, 0o600)  # these files hold tokens / keys
    tmp.replace(p)


def chat_state(state: dict, key: str) -> dict:
    return state.setdefault("chats", {}).setdefault(key, {"last_rowid": 0, "added_uris": []})


def allowed_file() -> Path:
    return DATA_DIR / "allowed_chats.json"


def load_allowed() -> list[str]:
    """Keys of the chats the reader may open. Kept outside CONFIG_DIR: the
    internet-facing half can write to CONFIG_DIR but not here."""
    f = allowed_file()
    return json.loads(f.read_text()) if f.exists() else []


def save_allowed(keys: list[str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    allowed_file().write_text(json.dumps(sorted(set(keys)), indent=2))
