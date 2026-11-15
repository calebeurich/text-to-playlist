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
    assert sorted(i.uri for i in r.items) == sorted(["spotify:track:" + T, "spotify:track:vamp", "spotify:track:remaster",
                                                     "spotify:track:blonde1", "spotify:track:blonde2"])
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


def test_reader_enforces_allow_list(db, capsys, tmp_path, monkeypatch):
    import json

    from text_to_playlist import reader
    monkeypatch.setattr(state, "DATA_DIR", tmp_path / "data")
    db.chat(1, "Music Heads")
    db.chat(2, "Family")
    db.msg(1, "hello", NOW - 100, handle_id=1)
    db.msg(1, "mine", NOW - 90, from_me=1)
    db.msg(2, "private", NOW - 80)
    base = ["--db", str(db.path)]
    assert reader.main(["list", *base]) == 0 and json.loads(capsys.readouterr().out) == []
    assert reader.main(["fetch", *base, "--key", "name:Music Heads"]) == 3  # nothing allowed yet
    capsys.readouterr()
    assert reader.main(["list", "--all", *base]) == 0
    assert {c["name"] for c in json.loads(capsys.readouterr().out)} == {"Music Heads", "Family"}

    state.save_allowed(["name:Music Heads"])
    assert reader.main(["list", *base]) == 0
    assert [c["name"] for c in json.loads(capsys.readouterr().out)] == ["Music Heads"]
    assert reader.main(["fetch", *base, "--key", "name:Music Heads"]) == 0
    out = capsys.readouterr().out
    assert [m["sender"] for m in json.loads(out)] == ["Person A", "me"] and "+1555" not in out
    assert reader.main(["fetch", *base, "--key", "name:Family"]) == 3
    assert "private" not in capsys.readouterr().out
    assert reader.main(["list", "--db", str(db.path) + ".missing"]) == 2


def test_syncer_profile_walls_off_home():
    from pathlib import Path

    from text_to_playlist import sandbox
    prof = sandbox.syncer_profile()
    assert f'(deny file-read-data file-write* (subpath "{Path.home().resolve()}"))' in prof
    assert "deny network" in sandbox.READER_PROFILE and "network" not in prof


def test_app_flow(db, tmp_path, monkeypatch):
    import contextlib
    import io
    import json

    from text_to_playlist import app, reader, sandbox, syncer, ui
    monkeypatch.setattr(state, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(state, "DATA_DIR", tmp_path / "data")
    state.save("config.json", {"spotify_client_id": "cid"})
    db.chat(1, "Music Heads")
    db.chat(2, "Other")
    db.msg(1, f"https://open.spotify.com/track/{T} https://open.spotify.com/album/{A}", NOW - 100)
    db.msg(1, "ancient", NOW - 300 * 86400)
    db.msg(2, f"https://open.spotify.com/album/{A}", NOW - 50)
    sp, shown, prompts = FakeSpotify(), [], []

    def fake_read(args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()) as err:
            code = reader.main([args[0], "--db", str(db.path), *args[1:]])
        if code:
            raise sandbox.SandboxError(err.getvalue(), code)
        return json.loads(buf.getvalue())

    def fake_syncer(command, payload):
        st = state.load("state.json")
        if command == "plan":
            msgs = [imessage.Message(**m) for m in payload["messages"]]
            return syncer.plan_messages(payload["chat_key"], msgs, sp, st, album_mode="first", album_tracks=1)
        return syncer.apply_items(payload["chat_key"], payload["chat_name"], payload["items"], payload["last_rowid"], sp, st)

    class Busy:
        def __init__(self, m): pass
        def close(self): pass

    answers = {}

    def fake_choose(prompt, items, multiple=True, preselect="none"):
        prompts.append(prompt.split("\n")[0])
        for start, fn in answers.items():
            if prompt.startswith(start):
                return fn(items)
        raise AssertionError(f"unexpected dialog: {prompt}")

    monkeypatch.setattr(sandbox, "read", fake_read)
    monkeypatch.setattr(sandbox, "syncer", fake_syncer)
    monkeypatch.setattr(sandbox, "verify_lockdown", lambda: [])
    monkeypatch.setattr(ui, "progress", Busy)
    monkeypatch.setattr(ui, "alert", lambda m, buttons=("OK",): shown.append(m))
    monkeypatch.setattr(ui, "choose", fake_choose)

    # first launch: allow one chat, sync it, look back 30 days, untick the album track in review
    answers.update({
        "Which chats may": lambda items: [items.index("Music Heads")],
        "What would you": lambda items: [0],
        "First time reading": lambda items: [0],
        "Music Heads: found 2": lambda items: [0],
    })
    assert app.main() == 0
    assert state.load_allowed() == ["name:Music Heads"]
    assert sp.added == ["spotify:track:" + T] and sp.playlists == ["Music Heads recs"]
    assert "added 1 song(s) from 1 new message(s)" in shown[-1] and "Other" not in shown[-1]

    # second launch: nothing new, no lookback question, no second playlist
    n = len(prompts)
    assert app.main() == 0 and prompts[n:] == ["What would you like to do?"]
    assert len(sp.added) == 1 and len(sp.playlists) == 1

    # cancelling the review adds nothing and offers the messages again next time
    db.msg(1, f"https://open.spotify.com/album/{A}", NOW - 5)
    answers["Music Heads: found 1"] = lambda items: None
    assert app.main() == 0 and len(sp.added) == 1 and "cancelled" in shown[-1]
    answers["Music Heads: found 1"] = lambda items: [0]
    assert app.main() == 0 and sp.added[-1] == "spotify:track:blonde1"

    # toggling review off from the menu, then quitting
    seq = iter([[2], None])
    answers["What would you"] = lambda items: next(seq)
    assert app.main() == 0 and state.load("config.json")["review"] is False

    # failing safety check stops before anything is read
    monkeypatch.setattr(sandbox, "verify_lockdown", lambda: ["leak"])
    monkeypatch.setattr(sandbox, "read", lambda a: (_ for _ in ()).throw(AssertionError("must not read")))
    assert app.main() == 3


def test_playlist_id_saved_before_tracks_are_added(tmp_path, monkeypatch):
    from text_to_playlist import syncer
    monkeypatch.setattr(state, "CONFIG_DIR", tmp_path / "cfg")
    sp = FakeSpotify()
    sp.add_tracks = lambda pid, uris: (_ for _ in ()).throw(RuntimeError("network down"))
    items = [{"uri": "spotify:track:x", "label": "X"}]
    try:
        syncer.apply_items("k", "Chat", items, 5, sp, state.load("state.json"))
    except RuntimeError:
        pass
    sp2 = FakeSpotify()
    syncer.apply_items("k", "Chat", items, 5, sp2, state.load("state.json"))
    assert sp.playlists == ["Chat recs"] and sp2.playlists == [] and sp2.added == ["spotify:track:x"]


def test_bare_band_name_falls_back_to_artist():
    sp = FakeSpotify()
    r = resolve.resolve(sp, [], [extract.Rec(kind="track", title="Big Thief", artist="", message_id=1),
                                 extract.Rec(kind="track", title="Vampire", artist="Big Thief", message_id=1)],
                        use_llm=False)
    assert [i.uri for i in r.items] == ["spotify:track:vamp"] and r.unresolved == []


def test_contact_names_label_unnamed_chats(db, tmp_path):
    import sqlite3
    ab = tmp_path / "AddressBook" / "Sources" / "X"
    ab.mkdir(parents=True)
    c = sqlite3.connect(ab / "AddressBook-v22.abcddb")
    c.executescript("""
        CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, ZFIRSTNAME TEXT, ZLASTNAME TEXT, ZORGANIZATION TEXT);
        CREATE TABLE ZABCDPHONENUMBER (ZOWNER INTEGER, ZFULLNUMBER TEXT);
        CREATE TABLE ZABCDEMAILADDRESS (ZOWNER INTEGER, ZADDRESS TEXT);
        INSERT INTO ZABCDRECORD VALUES (1, 'Sam', 'Lee', NULL), (2, 'Ana', NULL, NULL);
        INSERT INTO ZABCDPHONENUMBER VALUES (1, '(415) 555-0001');
        INSERT INTO ZABCDEMAILADDRESS VALUES (2, 'Ana@Example.com');
    """)
    c.commit()
    contacts = imessage.load_contacts(tmp_path / "AddressBook")
    db.chat(1, "", handles=("+14155550001", "ana@example.com", "+14155559999"))
    db.msg(1, "hi", NOW)
    chats = imessage.list_chats(imessage.connect(db.path), contacts=contacts)
    assert chats[0].name == "Sam, Ana, +14155559999"
    assert imessage.load_contacts(tmp_path / "missing") == {}


def test_title_found_as_album_when_not_a_track():
    r = resolve.resolve(FakeSpotify(), [], [extract.Rec(kind="track", title="Blonde", artist="Frank Ocean", message_id=1)],
                        album_mode="first", album_tracks=1, use_llm=False)
    assert [i.uri for i in r.items] == ["spotify:track:blonde1"] and r.unresolved == []


def test_items_follow_chat_order_and_wrong_artist_is_recovered(tmp_path, monkeypatch):
    sp = FakeSpotify()
    sp.catalog.append({"uri": "spotify:track:girl", "name": "You Are Every Girl to Me", "artists": [{"name": "MJ Lenderman"}]})
    links = find_links(f"https://open.spotify.com/album/{A}", 5) + find_links(f"https://open.spotify.com/track/{T}", 9)
    recs = [extract.Rec(kind="track", title="You Are Every Girl to Me", artist="Geese", message_id=7),
            extract.Rec(kind="track", title="Vampire Empire", artist="Big Thief", message_id=2)]
    r = resolve.resolve(sp, links, recs, album_mode="first", album_tracks=1, use_llm=False)
    assert [i.uri for i in r.items] == ["spotify:track:vamp", "spotify:track:blonde1", "spotify:track:girl",
                                        "spotify:track:" + T]
    assert "chat said: Geese" in r.items[2].label and r.unresolved == []

    monkeypatch.setattr(state, "CONFIG_DIR", tmp_path / "cfg")
    assert state.read_notes() == ""
    state.notes_file().write_text(state.NOTES_TEMPLATE + "O Rod = Olivia Rodrigo\n")
    assert state.read_notes() == "O Rod = Olivia Rodrigo"


def test_second_look_lets_the_model_pick_from_spotify_results(monkeypatch):
    sp = FakeSpotify()
    asked = {}

    def fake_pick(wanted, key):
        asked["wanted"] = wanted
        cands = wanted[0]["candidates"]
        return {wanted[0]["id"]: next(i for i, c in enumerate(cands, 1) if c.startswith("Heroes"))}

    monkeypatch.setattr(extract, "pick_candidates", fake_pick)
    recs = [extract.Rec(kind="artist", title="", artist="O Rod", message_id=3),
            extract.Rec(kind="track", title="Zzz", artist="Qqq", message_id=4)]
    r = resolve.resolve(sp, [], recs, use_llm=True)
    assert [i.uri for i in r.items] == ["spotify:track:remaster"] and "best guess for: artist: O Rod" in r.items[0].label
    assert r.unresolved == ["track: Zzz - Qqq"] and len(asked["wanted"]) == 2
    assert resolve.resolve(sp, [], recs, use_llm=False).unresolved == ["artist: O Rod", "track: Zzz - Qqq"]


def test_cost_estimate(monkeypatch):
    monkeypatch.setattr(extract, "MODEL", "claude-sonnet-5-5")
    monkeypatch.setattr(extract, "USAGE", {"input": 40_000, "output": 20_000})
    assert round(extract.cost_estimate(), 2) == 0.28
