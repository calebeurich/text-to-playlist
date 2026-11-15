"""Use Claude to find free-text music recs and to pick standout album tracks."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

import anthropic
from pydantic import BaseModel

from .imessage import Message
from .links import strip_links

MODEL = os.environ.get("TTP_MODEL", "claude-haiku-5-5")
BETAS = ["server-side-fallback-2026-07-01"]
CHUNK = 400  # messages per request
CONTEXT = 15  # earlier messages repeated so references like "that album" resolve


# $ per million tokens (input, output); used only to show an estimate after each sync.
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0)}
USAGE = {"input": 0, "output": 0}


def cost_estimate() -> float | None:
    price = PRICES.get(MODEL)
    return None if price is None else (USAGE["input"] * price[0] + USAGE["output"] * price[1]) / 1e6


class Rec(BaseModel):
    kind: Literal["track", "album", "artist"]
    title: str  # track or album title; empty string for an artist rec
    artist: str  # empty string if genuinely unknown
    message_id: int
    album: str = ""  # for a track: the album it is on, if the chat says so


class Pick(BaseModel):
    wanted_id: int
    candidate: int  # 0 = none of them


class Picks(BaseModel):
    picks: list[Pick]


class Extraction(BaseModel):
    recs: list[Rec]


class AlbumPick(BaseModel):
    album_id: str
    track_numbers: list[int]


class AlbumPicks(BaseModel):
    picks: list[AlbumPick]


EXTRACT_SYSTEM = """You find music recommendations in a group chat transcript.

Each line is "[message_id] sender: text". Return every song, album or artist that someone is \
recommending, sharing, or saying they are into, so it can be added to a shared listening playlist.

- Use context to resolve references ("the new one from them", "track 3 on that") into real titles and artists.
- Fix obvious typos and fill in the artist when you are confident who it is; leave artist empty if you are not.
- Expand nicknames and in-jokes to the real name when you recognise them ("O Rod" is Olivia Rodrigo). \
Names that are clearly the chat members' friends or other non-musicians are not recs.
- kind is "album" when the title is an album or EP (use what you know about the release, not just how they phrased it), "artist" when they only name an artist, else "track".
- When someone names a specific song and the album it is on ("Bastards off Bunny"), return a track rec for \
the song with album set to the album title. Add a separate album rec only if they recommend the album as a whole.
- album is the album a track is on when the chat says so, else an empty string. Leave it empty for album and artist recs.
- Skip music that is only mentioned in passing, criticised, or is not actually being recommended.
- Skip anything you cannot pin to a specific title or artist. "[music link]" marks a link that is handled \
elsewhere: do not create a rec for it.
- Only create recs for messages in the NEW MESSAGES section; EARLIER CONTEXT is for reference only.
- message_id is the id of the message containing the recommendation."""

PICK_SYSTEM = """For each album, choose its best-known tracks: the lead singles and the songs most \
listeners would know it by. Answer with track numbers taken from the listing given. Order them from most \
to least well known. If you do not know the album, return an empty list for it rather than guessing."""


CANDIDATE_SYSTEM = """Each WANTED line is a music recommendation taken from a group chat that an exact \
search could not find on Spotify. The chat text may contain typos, nicknames, a shortened title, or the wrong \
artist. Under it are numbered Spotify search results. For each WANTED item choose the result that is what the \
person meant, or 0 if none of them is. Prefer 0 over a doubtful match: a wrong song is worse than a missing one."""


def _client(api_key: str | None) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()


def _parse(client, system: str, user: str, schema):
    # Haiku has no server-side refusal fallback; the larger models do.
    extra = {} if "haiku" in MODEL else {"betas": BETAS, "fallbacks": "default"}
    resp = client.beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        **extra,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_format=schema,
    )
    USAGE["input"] += resp.usage.input_tokens
    USAGE["output"] += resp.usage.output_tokens
    if resp.stop_reason == "refusal":
        raise RuntimeError("Claude declined to process this batch of messages.")
    if resp.stop_reason == "max_tokens" or resp.parsed_output is None:
        raise RuntimeError("Claude's answer was cut off; try a smaller --since-days window.")
    return resp.parsed_output


def anonymize(messages: list[Message]) -> list[tuple[int, str, str]]:
    """Replace phone numbers / emails with 'Person A' etc. before text leaves the machine."""
    alias: dict[str, str] = {"me": "Me"}
    out = []
    for m in messages:
        if m.sender not in alias:
            n = len(alias) - 1
            alias[m.sender] = "Person " + (chr(65 + n) if n < 26 else str(n))
        out.append((m.rowid, alias[m.sender], strip_links(m.text)))
    return out


def extract_recs(messages: list[Message], api_key: str | None = None, notes: str = "") -> list[Rec]:
    lines = [f"[{i}] {who}: {text}" for i, who, text in anonymize(messages)]
    client = _client(api_key)
    system = EXTRACT_SYSTEM
    if notes.strip():
        system += ("\n\nNotes from the playlist owner about this group (nicknames for artists, people who are "
                   "friends rather than musicians, and so on). Apply them:\n" + notes.strip())

    def one_chunk(start: int) -> list[Rec]:
        context = lines[max(0, start - CONTEXT):start]
        user = ""
        if context:
            user += "EARLIER CONTEXT:\n" + "\n".join(context) + "\n\n"
        user += "NEW MESSAGES:\n" + "\n".join(lines[start:start + CHUNK])
        valid = {m.rowid for m in messages[start:start + CHUNK]}
        return [r for r in _parse(client, system, user, Extraction).recs if r.message_id in valid]

    # Long histories are many chunks; read a few at a time so a full-history sync takes minutes, not an hour.
    with ThreadPoolExecutor(max_workers=4) as pool:
        return [r for chunk in pool.map(one_chunk, range(0, len(lines), CHUNK)) for r in chunk]


def pick_standouts(albums: list[dict], n: int, api_key: str | None = None) -> dict[str, list[int]]:
    """albums: [{"id", "name", "artist", "tracks": [names in order]}] -> {id: [1-based track numbers]}"""
    if not albums:
        return {}
    blocks = []
    for a in albums:
        listing = "\n".join(f"  {i}. {t}" for i, t in enumerate(a["tracks"], 1))
        blocks.append(f"album_id: {a['id']}\n{a['name']} by {a['artist']}\n{listing}")
    user = f"Pick up to {n} track(s) per album.\n\n" + "\n\n".join(blocks)
    result = _parse(_client(api_key), PICK_SYSTEM, user, AlbumPicks)
    sizes = {a["id"]: len(a["tracks"]) for a in albums}
    out: dict[str, list[int]] = {}
    for p in result.picks:
        if p.album_id in sizes:
            nums = [t for t in dict.fromkeys(p.track_numbers) if 1 <= t <= sizes[p.album_id]]
            out[p.album_id] = nums[:n]
    return out


def pick_candidates(wanted: list[dict], api_key: str | None = None) -> dict[int, int]:
    """wanted: [{"id", "text", "candidates": [str]}] -> {id: 1-based candidate number} (misses with no pick are absent)."""
    if not wanted:
        return {}
    blocks = []
    for w in wanted:
        listing = "\n".join(f"  {i}. {c}" for i, c in enumerate(w["candidates"], 1))
        blocks.append(f"WANTED (id {w['id']}): {w['text']}\n{listing}")
    result = _parse(_client(api_key), CANDIDATE_SYSTEM, "\n\n".join(blocks), Picks)
    sizes = {w["id"]: len(w["candidates"]) for w in wanted}
    return {p.wanted_id: p.candidate for p in result.picks if 1 <= p.candidate <= sizes.get(p.wanted_id, 0)}
