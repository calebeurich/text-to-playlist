"""The part that talks to Claude and Spotify. It is handed message text on stdin and
never opens the iMessage database (the sandbox it runs in cannot read it)."""
from __future__ import annotations

import json
import os
import sys

from . import extract, state
from .imessage import Message
from .links import find_links
from .resolve import resolve

CONFIG, STATE = "config.json", "state.json"


def sync_messages(chat_key: str, chat_name: str, msgs: list[Message], sp, st: dict, *, album_mode="singles",
                  album_tracks=2, links_only=False, dry_run=False, api_key=None, log=lambda s: None) -> dict:
    cs = state.chat_state(st, chat_key)
    result = {"chat": chat_name, "messages": len(msgs), "added": [], "unresolved": [], "notes": [],
              "dry_run": dry_run, "playlist_url": cs.get("playlist_url", "")}
    if not msgs:
        return result
    links = [l for m in msgs for l in find_links(m.text, m.rowid)]
    recs = []
    use_llm = bool(api_key or os.environ.get("ANTHROPIC_API_KEY")) and not links_only
    if use_llm:
        log("Reading messages for recs...")
        recs = extract.extract_recs(msgs, api_key)
    log(f"Found {len(links)} music links and {len(recs)} written recs. Matching on Spotify...")
    res = resolve(sp, links, recs, album_mode=album_mode, album_tracks=album_tracks, use_llm=use_llm, api_key=api_key)
    already = set(cs["added_uris"])
    new = [i for i in res.items if i.uri not in already]
    result.update(added=[i.label for i in new], unresolved=res.unresolved, notes=res.notes)
    if dry_run:
        return result
    if new:
        if not cs.get("playlist_id"):
            pl = sp.create_playlist(f"{chat_name[:80]} recs", "Song recs from the group chat, added by text-to-playlist")
            cs["playlist_id"] = pl["id"]
            cs["playlist_url"] = pl.get("external_urls", {}).get("spotify", "")
        sp.add_tracks(cs["playlist_id"], [i.uri for i in new])
        cs["added_uris"] += [i.uri for i in new]
    cs["name"] = chat_name
    cs["last_rowid"] = max(m.rowid for m in msgs)
    state.save(STATE, st)
    result["playlist_url"] = cs.get("playlist_url", "")
    return result


def main() -> int:
    from .spotify import Spotify

    payload = json.load(sys.stdin)
    cfg = state.load(CONFIG)
    extract.MODEL = cfg.get("model", extract.MODEL)
    msgs = [Message(**m) for m in payload["messages"]]
    result = sync_messages(
        payload["chat_key"], payload["chat_name"], msgs, Spotify(cfg["spotify_client_id"]), state.load(STATE),
        album_mode=cfg.get("album_mode", "singles"), album_tracks=cfg.get("album_tracks", 2),
        api_key=cfg.get("anthropic_api_key"), log=lambda s: print(s, file=sys.stderr),
    )
    json.dump(result, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
