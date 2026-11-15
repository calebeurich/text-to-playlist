"""Turn links and free-text recs into concrete Spotify track URIs."""
from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from . import extract
from .links import Link, find_links

ALBUM_MODES = ("singles", "first", "full")


@dataclass
class Item:
    uri: str
    label: str
    message_id: int = 0  # the message the rec came from; used to keep chat order


@dataclass
class Resolution:
    items: list[Item] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def norm(s: str) -> str:
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s.lower())
    s = re.sub(r"\s-\s.*$", "", s)  # "Song - 2011 Remaster"
    s = re.sub(r"[^\w ]", "", s)
    return " ".join(s.split())


def sim(a: str, b: str) -> float:
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def _artists(obj: dict) -> str:
    return ", ".join(a["name"] for a in obj.get("artists", []))


def best_match(candidates: list[dict], title: str, artist: str) -> dict | None:
    best, best_score = None, 0.0
    for c in candidates:
        t = sim(c["name"], title) if title else 1.0
        a = max((sim(x["name"], artist) for x in c.get("artists", [])), default=0.0) if artist else 1.0
        if artist and a >= 0.8 and norm(title) and norm(title) in norm(c["name"]):
            t = max(t, 0.6)  # right artist, and the rec is a shortened form of the real title
        if t >= 0.6 and a >= 0.5 and t + a > best_score:
            best, best_score = c, t + a
    return best


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read())


def _final_url(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.geturl()


def apple_to_rec(link: Link, get_json=_get_json) -> extract.Rec | None:
    """Look an Apple Music id up in Apple's public iTunes API (no login)."""
    results = get_json(f"https://itunes.apple.com/lookup?id={link.id}").get("results", [])
    if not results:
        return None
    r = results[0]
    if link.kind == "track":
        return extract.Rec(kind="track", title=r.get("trackName", ""), artist=r.get("artistName", ""),
                           message_id=link.message_id)
    return extract.Rec(kind="album", title=r.get("collectionName", ""), artist=r.get("artistName", ""),
                       message_id=link.message_id)


def _search(sp, kind: str, title: str, artist: str, album: str = "") -> dict | None:
    field_name = "album" if kind == "album" else "track"
    queries = []
    if kind == "track" and title and album:
        queries.append(f'track:"{title}" album:"{album}"' + (f' artist:"{artist}"' if artist else ""))
    if title and artist:
        queries.append(f'{field_name}:"{title}" artist:"{artist}"')
    queries.append(f"{title} {artist}".strip())
    for q in queries:
        hit = best_match(sp.search(q, kind), title, artist)
        if hit:
            return hit
    return None


def resolve(
    sp, links: list[Link], recs: list[extract.Rec], *, album_mode: str = "singles", album_tracks: int = 2,
    use_llm: bool = True, api_key: str | None = None, get_json=_get_json, final_url=_final_url,
) -> Resolution:
    out = Resolution()
    albums: dict[str, dict] = {}
    album_msg: dict[str, int] = {}
    recs = list(recs)
    misses: list[extract.Rec] = []

    def describe(rec: extract.Rec) -> str:
        return f"{rec.kind}: " + " - ".join(x for x in (rec.title, rec.artist) if x)

    def add_album(album_id: str, message_id: int) -> None:
        if album_id not in albums:
            albums[album_id] = sp.album(album_id)
            album_msg[album_id] = message_id

    def guarded(label: str, fn) -> None:
        try:
            fn()
        except Exception as e:  # one bad link or lookup must not sink the whole sync
            out.unresolved.append(f"{label} ({type(e).__name__}: {e})")

    def handle_link(link: Link) -> None:
        if link.service == "spotify_short":
            found = [l for l in find_links(final_url(link.url), link.message_id) if l.service == "spotify"]
            if not found:
                out.unresolved.append(link.url)
            for l in found:
                handle_link(l)
        elif link.service == "apple":
            rec = apple_to_rec(link, get_json)
            recs.append(rec) if rec else out.unresolved.append(link.url)
        elif link.kind == "track":
            t = sp.track(link.id)
            out.items.append(Item(t["uri"], f"{t['name']} - {_artists(t)}", link.message_id))
        elif link.kind == "album":
            add_album(link.id, link.message_id)
        else:
            out.notes.append(f"Skipped {link.kind} link: {link.url}")

    def add_artist(name: str, message_id: int) -> bool:
        hits = [t for t in sp.search(f'artist:"{name}"', "track", limit=10)
                if any(sim(x["name"], name) >= 0.8 for x in t.get("artists", []))]
        for t in hits[:album_tracks]:
            out.items.append(Item(t["uri"], f"{t['name']} - {_artists(t)}", message_id))
        return bool(hits)

    def title_only(rec: extract.Rec) -> bool:
        """The chat may credit the wrong artist. For a distinctive title (3+ words),
        accept a near-exact title match by someone else, and say so in the label."""
        if not rec.artist or len(norm(rec.title).split()) < 3:
            return False
        for t in sp.search(rec.title, "track"):
            if sim(t["name"], rec.title) >= 0.9:
                out.items.append(Item(t["uri"], f"{t['name']} - {_artists(t)} (chat said: {rec.artist})",
                                      rec.message_id))
                return True
        return False

    def handle_rec(rec: extract.Rec) -> None:
        if rec.kind == "artist":
            if not add_artist(rec.artist or rec.title, rec.message_id):
                misses.append(rec)
            return
        # People (and the model) often don't say whether a title is a song or an album,
        # so if it isn't found as one, try the other before giving up.
        kind = rec.kind
        hit = _search(sp, kind, rec.title, rec.artist, rec.album)
        if not hit:
            kind = "album" if rec.kind == "track" else "track"
            hit = _search(sp, kind, rec.title, rec.artist)
        if not hit:
            # a bare name with no artist is often a band the model took for a song title
            if not (not rec.artist and add_artist(rec.title, rec.message_id)) and not title_only(rec):
                misses.append(rec)
        elif kind == "album":
            add_album(hit["id"], rec.message_id)
        else:
            out.items.append(Item(hit["uri"], f"{hit['name']} - {_artists(hit)}", rec.message_id))

    for link in links:
        guarded(link.url, lambda: handle_link(link))
    for rec in recs:
        guarded(f"{rec.kind}: {rec.title} {rec.artist}", lambda: handle_rec(rec))

    # ---- second look: show the model what Spotify actually has for each miss -------
    if misses and use_llm:
        try:
            wanted, pools = [], {}
            for n, rec in enumerate(misses):
                q = f"{rec.title} {rec.artist}".strip()
                pool = [("track", t) for t in sp.search(q, "track")] + [("album", a) for a in sp.search(q, "album", limit=3)]
                if rec.title and rec.artist:
                    pool += [("track", t) for t in sp.search(rec.title, "track", limit=3)]
                seen_ids, uniq = set(), []
                for kind, c in pool:
                    cid = c.get("uri") or c.get("id")
                    if cid not in seen_ids:
                        seen_ids.add(cid)
                        uniq.append((kind, c))
                if uniq:
                    pools[n] = uniq
                    wanted.append({"id": n, "text": describe(rec),
                                   "candidates": [f"{c['name']} - {_artists(c)} ({kind})" for kind, c in uniq]})
            picks = extract.pick_candidates(wanted, api_key)
            for n, choice in picks.items():
                kind, c = pools[n][choice - 1]
                rec = misses[n]
                if kind == "album":
                    add_album(c["id"], rec.message_id)
                else:
                    out.items.append(Item(c["uri"], f"{c['name']} - {_artists(c)} (best guess for: {describe(rec)})",
                                          rec.message_id))
            misses = [rec for n, rec in enumerate(misses) if n not in picks]
        except Exception as e:
            out.notes.append(f"Second look at the misses failed ({type(e).__name__}: {e}).")
    out.unresolved += [describe(rec) for rec in misses]

    # ---- albums -> tracks ---------------------------------------------------
    picks: dict[str, list[int]] = {}
    if album_mode == "singles" and albums:
        if use_llm:
            try:
                picks = extract.pick_standouts(
                    [{"id": i, "name": a["name"], "artist": _artists(a),
                      "tracks": [t["name"] for t in a["tracks"]["items"]]} for i, a in albums.items()],
                    album_tracks, api_key,
                )
            except Exception as e:
                out.notes.append(f"Could not pick album singles ({e}); using first tracks instead.")
        else:
            out.notes.append("No Claude API key: album singles mode fell back to first tracks.")
    for album_id, a in albums.items():
        tracks = a["tracks"]["items"]
        if album_mode == "full":
            chosen = list(range(1, len(tracks) + 1))
        else:
            chosen = list(picks.get(album_id, []))
            chosen += [n for n in range(1, len(tracks) + 1) if n not in chosen]  # pad with opening tracks
            chosen = chosen[:album_tracks]
        for n in chosen:
            t = tracks[n - 1]
            out.items.append(Item(t["uri"], f"{t['name']} - {_artists(a)} (from the album {a['name']})",
                                  album_msg[album_id]))

    out.items.sort(key=lambda i: i.message_id)  # chat order: oldest rec first, newest last
    seen: set[str] = set()
    out.items = [i for i in out.items if not (i.uri in seen or seen.add(i.uri))]
    return out
