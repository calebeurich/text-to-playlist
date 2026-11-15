import time

from conftest import FakeSpotify, typedstream

from text_to_playlist import cli, extract, imessage, resolve, state
from text_to_playlist.links import find_links, strip_links

T, A = "1" * 22, "a" * 22
NOW = time.time()


def test_links():
    text = (f"yo https://open.spotify.com/track/{T}?si=abc and https://open.spotify.com/intl-de/album/{A} "
            "https://music.apple.com/us/album/blonde/1146195596?i=1146195712 "
            "https://music.apple.com/us/album/blonde/1146195596 https://spotify.link/xYz")
    got = [(l.service, l.kind, l.id) for l in find_links(text)]
    assert got == [("spotify", "track", T), ("spotify", "album", A), ("spotify_short", "unknown", ""),
                   ("apple", "track", "1146195712"), ("apple", "album", "1146195596")]
    assert "spotify" not in strip_links(text) and "apple" not in strip_links(text)


def test_imessage_reader(db):
    db.chat(1, "Music Heads")
    db.chat(2, "Music Heads")  # same group after a membership change
    db.chat(3, "", style=45, handles=("+15550009",))
    db.msg(1, "old one", NOW - 400 * 86400)
    db.msg(1, None, NOW - 100, body=typedstream("Vampire Empire by Big Thief is so good 🎸"))
    db.msg(2, None, NOW - 90, body=typedstream("x" * 300))
    db.msg(2, "Loved “Vampire Empire”", NOW - 80, tapback=2000)
    db.msg(3, "dm", NOW - 50)
    conn = imessage.connect(db.path)
    chats = imessage.list_chats(conn)
    assert [(c.name, c.rowids) for c in chats] == [("Music Heads", [1, 2])]
    assert len(imessage.list_chats(conn, include_dms=True)) == 2
    msgs = imessage.fetch_messages(conn, chats[0], since_ts=NOW - 86400)
    assert [m.text for m in msgs] == ["Vampire Empire by Big Thief is so good 🎸", "x" * 300]
    assert imessage.fetch_messages(conn, chats[0], after_rowid=msgs[-1].rowid) == []


def test_anonymize_hides_handles():
    msgs = [imessage.Message(1, 0, "+15550001", "hi"), imessage.Message(2, 0, "me", "yo"),
            imessage.Message(3, 0, "a@b.com", f"https://open.spotify.com/track/{T}")]
    assert extract.anonymize(msgs) == [(1, "Person A", "hi"), (2, "Me", "yo"), (3, "Person B", "[music link]")]


def test_resolve_modes():
    sp = FakeSpotify()
    links = find_links(f"https://open.spotify.com/track/{T} https://open.spotify.com/album/{A} "
                       "https://open.spotify.com/playlist/" + "p" * 22)
    recs = [extract.Rec(kind="track", title="vampire empire", artist="big thief", message_id=1),
            extract.Rec(kind="track", title="Heroes", artist="David Bowie", message_id=1),
            extract.Rec(kind="track", title="Nonexistent Song", artist="Nobody", message_id=1)]
    r = resolve.resolve(sp, links, recs, album_mode="first", album_tracks=2, use_llm=False)
    assert [i.uri for i in r.items] == ["spotify:track:" + T, "spotify:track:vamp", "spotify:track:remaster",
                                        "spotify:track:blonde1", "spotify:track:blonde2"]
    assert r.unresolved == ["track: Nonexistent Song - Nobody"] and len(r.notes) == 1
    full = resolve.resolve(sp, links[1:2], [], album_mode="full", use_llm=False)
    assert len(full.items) == 4


def test_singles_mode_uses_picks_and_pads(monkeypatch):
    monkeypatch.setattr(extract, "pick_standouts", lambda albums, n, key: {A: [3]})
    r = resolve.resolve(FakeSpotify(), find_links(f"https://open.spotify.com/album/{A}"), [],
                        album_mode="singles", album_tracks=2)
    assert [i.uri for i in r.items] == ["spotify:track:blonde3", "spotify:track:blonde1"]


def test_apple_link_goes_through_itunes_lookup():
    link = find_links("https://music.apple.com/us/album/x/1?i=2")[0]
    fake = lambda url: {"results": [{"trackName": "Vampire Empire", "artistName": "Big Thief"}]}
    r = resolve.resolve(FakeSpotify(), [link], [], use_llm=False, get_json=fake)
    assert [i.uri for i in r.items] == ["spotify:track:vamp"]


def test_cli_end_to_end(db, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(state, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "cid")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    sp = FakeSpotify()
    monkeypatch.setattr(cli, "Spotify", lambda cid: sp)
    db.chat(1, "Music Heads")
    db.msg(1, f"listen https://open.spotify.com/track/{T}", NOW - 100)
    args = ["--db", str(db.path), "--chat", "music heads"]

    assert cli.main(args + ["--dry-run"]) == 0 and sp.added == []
    assert cli.main(args) == 0
    assert sp.playlists == ["Music Heads recs"] and sp.added == ["spotify:track:" + T]

    # second run: nothing new; then a repeat of the same song plus an album
    assert cli.main(args) == 0 and len(sp.added) == 1
    db.msg(1, f"again https://open.spotify.com/track/{T} and https://open.spotify.com/album/{A}", NOW - 10)
    assert cli.main(["--db", str(db.path), "--all", "--album-mode", "first", "--album-tracks", "1"]) == 0
    assert sp.added == ["spotify:track:" + T, "spotify:track:blonde1"] and len(sp.playlists) == 1


def test_reader_outputs_aliased_json(db, capsys):
    import json

    from text_to_playlist import reader
    db.chat(1, "Music Heads")
    db.msg(1, "hello", NOW - 100, handle_id=1)
    db.msg(1, "mine", NOW - 90, from_me=1)
    assert reader.main(["list", "--db", str(db.path)]) == 0
    chats = json.loads(capsys.readouterr().out)
    assert chats[0]["name"] == "Music Heads" and chats[0]["rowids"] == [1]
    assert reader.main(["fetch", "--db", str(db.path), "--rowids", "1"]) == 0
    out = capsys.readouterr().out
    assert [m["sender"] for m in json.loads(out)] == ["Person A", "me"] and "+1555" not in out
    assert reader.main(["list", "--db", str(db.path) + ".missing"]) == 2


def test_syncer_profile_walls_off_home():
    from pathlib import Path

    from text_to_playlist import sandbox
    prof = sandbox.syncer_profile()
    assert f'(deny file-read-data file-write* (subpath "{Path.home().resolve()}"))' in prof
    assert "deny network" in sandbox.READER_PROFILE and "network" not in prof


def test_app_flow(db, tmp_path, monkeypatch):
    import json

    from text_to_playlist import app, reader, sandbox, syncer, ui
    monkeypatch.setattr(state, "CONFIG_DIR", tmp_path / "cfg")
    state.save("config.json", {"spotify_client_id": "cid"})
    db.chat(1, "Music Heads")
    db.chat(2, "Other")
    db.msg(1, f"https://open.spotify.com/track/{T}", NOW - 100)
    db.msg(2, "nothing here", NOW - 50)
    sp, shown = FakeSpotify(), []

    def fake_read(args):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert reader.main([args[0], "--db", str(db.path), *args[1:]]) == 0
        return json.loads(buf.getvalue())

    def fake_sync(payload):
        msgs = [imessage.Message(**m) for m in payload["messages"]]
        return syncer.sync_messages(payload["chat_key"], payload["chat_name"], msgs, sp, state.load("state.json"))

    monkeypatch.setattr(sandbox, "read", fake_read)
    monkeypatch.setattr(sandbox, "sync", fake_sync)
    monkeypatch.setattr(sandbox, "verify_lockdown", lambda: [])
    monkeypatch.setattr(ui, "notify", lambda m: None)
    monkeypatch.setattr(ui, "alert", lambda m, buttons=("OK",): shown.append(m))
    monkeypatch.setattr(ui, "choose", lambda prompt, items: [items.index("Music Heads")])
    assert app.main() == 0
    assert sp.added == ["spotify:track:" + T] and "added 1 track(s)" in shown[-1]

    # now linked: the list offers "update all" first, and a rerun adds nothing
    monkeypatch.setattr(ui, "choose", lambda prompt, items: [0] if items[0] == app.SYNC_ALL else [])
    assert app.main() == 0 and len(sp.added) == 1

    # failing safety check stops before anything is read
    monkeypatch.setattr(sandbox, "verify_lockdown", lambda: ["leak"])
    monkeypatch.setattr(sandbox, "read", lambda a: (_ for _ in ()).throw(AssertionError("must not read")))
    assert app.main() == 3
