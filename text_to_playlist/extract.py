"""Use Claude to find free-text music recs and to pick standout album tracks."""
from __future__ import annotations

import os
from typing import Literal

import anthropic
from pydantic import BaseModel

from .imessage import Message
from .links import strip_links

MODEL = os.environ.get("TTP_MODEL", "claude-haiku-5-5")
BETAS = ["server-side-fallback-2026-07-01"]
CHUNK = 400  # messages per request
CONTEXT = 15  # earlier messages repeated so references like "that album" resolve


class Rec(BaseModel):
    kind: Literal["track", "album", "artist"]
    title: str  # track or album title; empty string for an artist rec
    artist: str  # empty string if genuinely unknown
    message_id: int


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
- kind is "album" when they recommend a whole album/EP, "artist" when they only name an artist, else "track".
- Skip music that is only mentioned in passing, criticised, or is not actually being recommended.
- Skip anything you cannot pin to a specific title or artist. "[music link]" marks a link that is handled \
elsewhere: do not create a rec for it.
- Only create recs for messages in the NEW MESSAGES section; EARLIER CONTEXT is for reference only.
- message_id is the id of the message containing the recommendation."""

PICK_SYSTEM = """For each album, choose its best-known tracks: the lead singles and the songs most \
listeners would know it by. Answer with track numbers taken from the listing given. Order them from most \
to least well known. If you do not know the album, return an empty list for it rather than guessing."""


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


def extract_recs(messages: list[Message], api_key: str | None = None) -> list[Rec]:
    lines = [f"[{i}] {who}: {text}" for i, who, text in anonymize(messages)]
    client = _client(api_key)
    recs: list[Rec] = []
    for start in range(0, len(lines), CHUNK):
        context = lines[max(0, start - CONTEXT):start]
        new = lines[start:start + CHUNK]
        user = ""
        if context:
            user += "EARLIER CONTEXT:\n" + "\n".join(context) + "\n\n"
        user += "NEW MESSAGES:\n" + "\n".join(new)
        valid = {m.rowid for m in messages[start:start + CHUNK]}
        recs += [r for r in _parse(client, EXTRACT_SYSTEM, user, Extraction).recs if r.message_id in valid]
    return recs


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
