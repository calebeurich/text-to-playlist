import sqlite3

import pytest

from text_to_playlist import imessage

SCHEMA = """
CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT, chat_identifier TEXT, display_name TEXT, style INTEGER);
CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
CREATE TABLE message (ROWID INTEGER PRIMARY KEY, date INTEGER, text TEXT, attributedBody BLOB,
                      is_from_me INTEGER, handle_id INTEGER, associated_message_type INTEGER);
CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
"""


def typedstream(text: str) -> bytes:
    """Mimic the layout of an NSAttributedString typedstream blob."""
    raw = text.encode()
    length = bytes([len(raw)]) if len(raw) < 0x80 else b"\x81" + len(raw).to_bytes(2, "little")
    return b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+" + length + raw + b"\x86\x84\x02iI\x01"


def apple_ns(unix_ts: float) -> int:
    return int((unix_ts - imessage.APPLE_EPOCH) * 1e9)


class FakeDB:
    def __init__(self, path):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        self.n = 0

    def chat(self, rowid, name, style=43, handles=("+15550001", "+15550002")):
        self.conn.execute("INSERT INTO chat VALUES (?,?,?,?,?)", (rowid, f"guid-{rowid}", f"chat{rowid}", name, style))
        for h in handles:
            hid = self.conn.execute("SELECT ROWID FROM handle WHERE id=?", (h,)).fetchone()
            hid = hid[0] if hid else self.conn.execute("INSERT INTO handle (id) VALUES (?)", (h,)).lastrowid
            self.conn.execute("INSERT INTO chat_handle_join VALUES (?,?)", (rowid, hid))
        self.conn.commit()

    def msg(self, chat_id, text, ts, handle_id=1, from_me=0, body=None, tapback=0):
        self.n += 1
        self.conn.execute("INSERT INTO message VALUES (?,?,?,?,?,?,?)",
                          (self.n, apple_ns(ts), text, body, from_me, handle_id, tapback))
        self.conn.execute("INSERT INTO chat_message_join VALUES (?,?)", (chat_id, self.n))
        self.conn.commit()
        return self.n


@pytest.fixture
def db(tmp_path):
    return FakeDB(tmp_path / "chat.db")


class FakeSpotify:
    def __init__(self, *_):
        self.tracks = {"1" * 22: {"uri": "spotify:track:" + "1" * 22, "name": "Linked Song", "artists": [{"name": "A"}]}}
        self.albums = {"a" * 22: {"id": "a" * 22, "name": "Blonde", "artists": [{"name": "Frank Ocean"}],
                                  "tracks": {"items": [{"uri": f"spotify:track:blonde{i}", "name": n} for i, n in
                                                       enumerate(["Nikes", "Ivy", "Pink + White", "Be Yourself"], 1)]}}}
        self.catalog = [
            {"uri": "spotify:track:vamp", "name": "Vampire Empire", "artists": [{"name": "Big Thief"}]},
            {"uri": "spotify:track:remaster", "name": "Heroes - 2017 Remaster", "artists": [{"name": "David Bowie"}]},
        ]
        self.playlists, self.added = [], []

    def search(self, query, kind, limit=5):
        if kind == "album":
            return [{"id": k, "name": a["name"], "artists": a["artists"]} for k, a in self.albums.items()]
        return self.catalog

    def track(self, i):
        return self.tracks[i]

    def album(self, i):
        return self.albums[i]

    def create_playlist(self, name, description=""):
        self.playlists.append(name)
        return {"id": "pl1", "external_urls": {"spotify": "https://open.spotify.com/playlist/pl1"}}

    def add_tracks(self, pid, uris):
        self.added += uris
