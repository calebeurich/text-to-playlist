"""Read-only access to the local iMessage database (macOS).

Only ever opens the database read-only, and only pulls messages for the chats
the caller names.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

APPLE_EPOCH = 978307200  # 2001-01-01 in unix time
GROUP_STYLE = 43
DEFAULT_DB = Path.home() / "Library" / "Messages" / "chat.db"


class MessagesAccessError(RuntimeError):
    pass


@dataclass
class Chat:
    key: str  # stable id used in the state file
    name: str
    rowids: list[int]
    is_group: bool
    last_ts: float
    participants: list[str] = field(default_factory=list)


@dataclass
class Message:
    rowid: int
    ts: float
    sender: str  # "me" or a handle (phone/email)
    text: str


def connect(path: Path = DEFAULT_DB) -> sqlite3.Connection:
    if not Path(path).exists():
        raise MessagesAccessError(f"No iMessage database at {path}. This tool has to run on your Mac.")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.execute("SELECT 1 FROM chat LIMIT 1")
    except sqlite3.Error as e:
        raise MessagesAccessError(
            f"Could not read {path} ({e}).\n"
            "Grant Full Disk Access to your terminal app: System Settings > "
            "Privacy & Security > Full Disk Access, then restart the terminal."
        ) from e
    conn.row_factory = sqlite3.Row
    return conn


def _to_unix(apple_date: int | None) -> float:
    d = apple_date or 0
    if d > 10**12:  # modern macOS stores nanoseconds
        d = d / 1e9
    return d + APPLE_EPOCH


def decode_attributed_body(blob: bytes | None) -> str | None:
    """Pull the plain text out of a typedstream-encoded NSAttributedString.

    Newer macOS versions leave message.text NULL and store the body only here.
    """
    if not blob:
        return None
    i = blob.find(b"NSString")
    if i < 0:
        return None
    rest = blob[i + len(b"NSString"):]
    j = rest.find(b"\x01+")
    if j < 0 or j + 3 > len(rest):
        return None
    rest = rest[j + 2:]
    n, start = rest[0], 1
    if n == 0x81:
        n, start = int.from_bytes(rest[1:3], "little"), 3
    elif n == 0x82:
        n, start = int.from_bytes(rest[1:5], "little"), 5
    return rest[start:start + n].decode("utf-8", "replace")


def list_chats(conn: sqlite3.Connection, include_dms: bool = False) -> list[Chat]:
    """Chats ordered by most recent activity. Same-named groups are merged
    (iMessage creates a new chat row when a group's membership changes)."""
    rows = conn.execute(
        """
        SELECT c.ROWID AS rowid, c.guid, c.display_name, c.style, MAX(m.date) AS last_date
        FROM chat c
        JOIN chat_message_join cmj ON cmj.chat_id = c.ROWID
        JOIN message m ON m.ROWID = cmj.message_id
        GROUP BY c.ROWID
        """
    ).fetchall()
    handles: dict[int, list[str]] = {}
    for r in conn.execute(
        "SELECT chj.chat_id, h.id FROM chat_handle_join chj JOIN handle h ON h.ROWID = chj.handle_id"
    ):
        handles.setdefault(r[0], []).append(r[1])

    chats: dict[str, Chat] = {}
    for r in rows:
        is_group = r["style"] == GROUP_STYLE
        if not is_group and not include_dms:
            continue
        people = handles.get(r["rowid"], [])
        name = r["display_name"] or ", ".join(people) or r["guid"]
        key = f"name:{r['display_name']}" if (is_group and r["display_name"]) else f"guid:{r['guid']}"
        ts = _to_unix(r["last_date"])
        if key in chats:
            chats[key].rowids.append(r["rowid"])
            chats[key].last_ts = max(chats[key].last_ts, ts)
        else:
            chats[key] = Chat(key, name, [r["rowid"]], is_group, ts, people)
    return sorted(chats.values(), key=lambda c: c.last_ts, reverse=True)


def fetch_messages(
    conn: sqlite3.Connection, chat: Chat, after_rowid: int = 0, since_ts: float = 0
) -> list[Message]:
    marks = ",".join("?" * len(chat.rowids))
    rows = conn.execute(
        f"""
        SELECT m.ROWID AS rowid, m.date, m.text, m.attributedBody, m.is_from_me, h.id AS handle
        FROM message m
        JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        LEFT JOIN handle h ON h.ROWID = m.handle_id
        WHERE cmj.chat_id IN ({marks}) AND m.ROWID > ?
          AND COALESCE(m.associated_message_type, 0) = 0  -- skip tapbacks
        ORDER BY m.date, m.ROWID
        """,
        (*chat.rowids, after_rowid),
    ).fetchall()
    out = []
    for r in rows:
        ts = _to_unix(r["date"])
        if ts < since_ts:
            continue
        text = r["text"] or decode_attributed_body(r["attributedBody"]) or ""
        text = text.replace("￼", "").strip()
        if text:
            out.append(Message(r["rowid"], ts, "me" if r["is_from_me"] else (r["handle"] or "unknown"), text))
    return out
