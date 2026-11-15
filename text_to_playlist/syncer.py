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


def plan_messages(chat_key: str, msgs: list[Message], sp, st: dict, *, album_mode="singles", album_tracks=2,
                  links_only=False, api_key=None, notes="", log=lambda s: None,
                  progress=lambda step, done=0, total=0: None) -> dict:
    """Work out which tracks the new messages point to. Changes nothing."""
    cs = state.chat_state(st, chat_key)
    plan = {"messages": len(msgs), "items": [], "unresolved": [], "notes": [],
            "oldest_ts": min((m.ts for m in msgs), default=0),
            "last_rowid": max((m.rowid for m in msgs), default=cs["last_rowid"])}
    if not msgs:
        return plan
    links = [l for m in msgs for l in find_links(m.text, m.rowid)]
    recs = []
    use_llm = bool(api_key or os.environ.get("ANTHROPIC_API_KEY")) and not links_only
    if use_llm:
        log("Reading messages for recs...")
        recs = extract.extract_recs(msgs, api_key, notes, progress)
    log(f"Found {len(links)} music links and {len(recs)} written recs. Matching on Spotify...")
    res = resolve(sp, links, recs, album_mode=album_mode, album_tracks=album_tracks, use_llm=use_llm, api_key=api_key,
                  progress=progress)
    # Row ids follow the order messages reached this Mac, which is not always the order they were sent.
    sent = {m.rowid: m.ts for m in msgs}
    res.items.sort(key=lambda i: sent.get(i.message_id, 0))
    already = set(cs["added_uris"])
    plan.update(items=[{"uri": i.uri, "label": i.label, "ts": sent.get(i.message_id, 0)}
                       for i in res.items if i.uri not in already],
                unresolved=res.unresolved, notes=res.notes)
    return plan


def apply_items(chat_key: str, chat_name: str, items: list[dict], last_rowid: int, sp, st: dict,
                oldest_ts: float = 0) -> dict:
    """Add the chosen tracks to the chat's playlist and remember how far we have read.

    New recs go at the bottom. When an older stretch of the chat is read later (a longer
    lookback), songs from before anything read so far go at the top, so the playlist stays
    in chat order."""
    cs = state.chat_state(st, chat_key)
    already = set(cs["added_uris"])
    new = [i for i in items if i["uri"] not in already]
    covered_from = cs.get("oldest_ts")
    if new:
        if not cs.get("playlist_id"):
            pl = sp.create_playlist(f"{chat_name[:80]} recs", "Song recs from the group chat, added by text-to-playlist")
            cs["playlist_id"] = pl["id"]
            cs["playlist_url"] = pl.get("external_urls", {}).get("spotify", "")
            state.save(STATE, st)  # remember it at once, so a later failure cannot lead to a second playlist
        older = [i for i in new if covered_from and i.get("ts") and i["ts"] < covered_from]
        newer = [i for i in new if i not in older]
        if older:
            sp.add_tracks(cs["playlist_id"], [i["uri"] for i in older], position=0)
            cs["added_uris"] += [i["uri"] for i in older]
            state.save(STATE, st)
        if newer:
            sp.add_tracks(cs["playlist_id"], [i["uri"] for i in newer])
            cs["added_uris"] += [i["uri"] for i in newer]
    cs["name"] = chat_name
    cs["last_rowid"] = max(cs["last_rowid"], last_rowid)
    if oldest_ts:
        cs["oldest_ts"] = min(covered_from, oldest_ts) if covered_from else oldest_ts
    state.save(STATE, st)
    return {"added": [i["label"] for i in new], "playlist_url": cs.get("playlist_url", "")}


def sync_messages(chat_key: str, chat_name: str, msgs: list[Message], sp, st: dict, *, dry_run=False, **opts) -> dict:
    plan = plan_messages(chat_key, msgs, sp, st, **opts)
    result = {"chat": chat_name, "messages": len(msgs), "added": [i["label"] for i in plan["items"]],
              "unresolved": plan["unresolved"], "notes": plan["notes"], "dry_run": dry_run,
              "playlist_url": state.chat_state(st, chat_key).get("playlist_url", "")}
    if msgs and not dry_run:
        result.update(apply_items(chat_key, chat_name, plan["items"], plan["last_rowid"], sp, st, plan["oldest_ts"]))
    return result


def main(argv: list[str] | None = None) -> int:
    from .spotify import Spotify

    from .spotify import SpotifyRateLimited

    command = (argv or sys.argv[1:])[0]
    payload = json.load(sys.stdin)
    cfg = state.load(CONFIG)
    extract.MODEL = cfg.get("model", extract.MODEL)
    sp, st = Spotify(cfg["spotify_client_id"]), state.load(STATE)
    try:
        if command == "plan":
            result = plan_messages(
                payload["chat_key"], [Message(**m) for m in payload["messages"]], sp, st,
                album_mode=cfg.get("album_mode", "singles"), album_tracks=cfg.get("album_tracks", 2),
                api_key=cfg.get("anthropic_api_key"), notes=state.read_notes(),
                log=lambda s: print(s, file=sys.stderr), progress=state.write_progress,
            )
            result["cost"], result["model"] = extract.cost_estimate(), extract.MODEL
        else:
            result = apply_items(payload["chat_key"], payload["chat_name"], payload["items"], payload["last_rowid"], sp, st,
                                 payload.get("oldest_ts", 0))
    except SpotifyRateLimited as e:
        print(e, file=sys.stderr)
        return 4
    json.dump(result, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
