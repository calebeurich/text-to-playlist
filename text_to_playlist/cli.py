"""ttp: pick a chat, and its song recs land in a Spotify playlist."""
from __future__ import annotations

import argparse
import getpass
import os
import sys
import time
from datetime import datetime

from . import extract, imessage, state
from .links import find_links
from .resolve import ALBUM_MODES, resolve
from .spotify import REDIRECT_URI, Spotify

CONFIG, STATE = "config.json", "state.json"


def setup(cfg: dict) -> dict:
    print("One-time setup.\n")
    print("1) Spotify: create an app at developer.spotify.com/dashboard, add the redirect URI")
    print(f"   {REDIRECT_URI}  and tick 'Web API'. Then paste its Client ID here.")
    cfg["spotify_client_id"] = input("   Spotify Client ID: ").strip() or cfg.get("spotify_client_id", "")
    print("\n2) Claude API key (optional: without it only links are picked up, not free-text recs).")
    key = getpass.getpass("   Anthropic API key (input hidden, Enter to skip): ").strip()
    if key:
        cfg["anthropic_api_key"] = key
    state.save(CONFIG, cfg)
    print(f"\nSaved to {state.CONFIG_DIR}/{CONFIG} (readable only by you).\n")
    return cfg


def choose(chats: list[imessage.Chat], linked: set[str], limit: int) -> list[imessage.Chat]:
    shown = chats[:limit]
    for i, c in enumerate(shown, 1):
        mark = "*" if c.key in linked else " "
        when = datetime.fromtimestamp(c.last_ts).strftime("%b %d")
        print(f" {i:>2}. {mark} {c.name[:60]:<60} {when}")
    print("\n(* = already has a playlist)")
    raw = input("Which chat(s)? Numbers separated by spaces: ").replace(",", " ").split()
    return [shown[int(n) - 1] for n in raw if n.isdigit() and 1 <= int(n) <= len(shown)]


def sync_chat(chat, conn, sp, st, cfg, args, api_key) -> None:
    cs = state.chat_state(st, chat.key)
    first_time = cs["last_rowid"] == 0
    since = time.time() - args.since_days * 86400 if first_time else 0
    msgs = imessage.fetch_messages(conn, chat, after_rowid=cs["last_rowid"], since_ts=since)
    print(f"\n== {chat.name} ==")
    if not msgs:
        print("No new messages.")
        return
    scope = f"from the last {args.since_days} days" if first_time else "since the last sync"
    print(f"{len(msgs)} new messages {scope}.")

    links = [l for m in msgs for l in find_links(m.text, m.rowid)]
    recs = []
    use_llm = bool(api_key or os.environ.get("ANTHROPIC_API_KEY")) and not args.links_only
    if use_llm:
        print("Reading messages for recs...")
        recs = extract.extract_recs(msgs, api_key)
    print(f"Found {len(links)} music links and {len(recs)} written recs. Matching on Spotify...")

    res = resolve(sp, links, recs, album_mode=args.album_mode, album_tracks=args.album_tracks,
                  use_llm=use_llm, api_key=api_key)
    already = set(cs["added_uris"])
    new = [i for i in res.items if i.uri not in already]

    for i in new:
        print(f"  + {i.label}")
    for u in res.unresolved:
        print(f"  ? no match: {u}")
    for n in res.notes:
        print(f"  ! {n}")
    if args.dry_run:
        print(f"Dry run: {len(new)} track(s) would be added. Nothing was changed.")
        return

    if new:
        if not cs.get("playlist_id"):
            pl = sp.create_playlist(f"{chat.name[:80]} recs", "Song recs from the group chat, added by text-to-playlist")
            cs["playlist_id"] = pl["id"]
            cs["playlist_url"] = pl.get("external_urls", {}).get("spotify", "")
        sp.add_tracks(cs["playlist_id"], [i.uri for i in new])
        cs["added_uris"] += [i.uri for i in new]
    cs["name"] = chat.name
    cs["last_rowid"] = max(m.rowid for m in msgs)
    state.save(STATE, st)
    print(f"Added {len(new)} track(s). {cs.get('playlist_url', '')}")


def main(argv: list[str] | None = None) -> int:
    cfg = state.load(CONFIG)
    p = argparse.ArgumentParser(prog="ttp", description=__doc__)
    p.add_argument("--all", action="store_true", help="sync every chat that already has a playlist, no questions")
    p.add_argument("--chat", action="append", default=[], help="chat name to sync (repeatable); skips the picker")
    p.add_argument("--dry-run", action="store_true", help="show what would be added without touching Spotify")
    p.add_argument("--album-mode", choices=ALBUM_MODES, default=cfg.get("album_mode", "singles"))
    p.add_argument("--album-tracks", type=int, default=cfg.get("album_tracks", 2), help="tracks to take per album")
    p.add_argument("--since-days", type=int, default=90, help="how far back to look the first time a chat is synced")
    p.add_argument("--links-only", action="store_true", help="never send message text to Claude")
    p.add_argument("--dms", action="store_true", help="include one-to-one chats in the list")
    p.add_argument("--list", type=int, default=20, metavar="N", help="how many recent chats to show")
    p.add_argument("--setup", action="store_true", help="re-enter the Spotify client id / API key")
    p.add_argument("--db", default=str(imessage.DEFAULT_DB), help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    try:
        if args.setup or not (cfg.get("spotify_client_id") or os.environ.get("SPOTIFY_CLIENT_ID")):
            cfg = setup(cfg)
        client_id = os.environ.get("SPOTIFY_CLIENT_ID") or cfg.get("spotify_client_id")
        if not client_id:
            print("A Spotify Client ID is required.")
            return 1
        api_key = cfg.get("anthropic_api_key")

        conn = imessage.connect(args.db)
        chats = imessage.list_chats(conn, include_dms=args.dms)
        st = state.load(STATE)
        linked = {k for k, v in st.get("chats", {}).items() if v.get("playlist_id")}

        if args.all:
            picked = [c for c in chats if c.key in linked]
        elif args.chat:
            wanted = [w.lower() for w in args.chat]
            picked = [c for c in imessage.list_chats(conn, include_dms=True) if c.name.lower() in wanted]
        else:
            picked = choose(chats, linked, args.list)
        if not picked:
            print("No chats selected.")
            return 1

        sp = Spotify(client_id)
        for chat in picked:
            sync_chat(chat, conn, sp, st, cfg, args, api_key)
        return 0
    except imessage.MessagesAccessError as e:
        print(e, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
