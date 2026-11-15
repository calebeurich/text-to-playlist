"""Find music links in message text. Pure string work, no network."""
from __future__ import annotations

import re
from dataclasses import dataclass

SPOTIFY_RE = re.compile(
    r"https?://open\.spotify\.com/(?:intl-[a-z-]+/)?(track|album|playlist|artist)/([A-Za-z0-9]{22})\S*"
)
SPOTIFY_SHORT_RE = re.compile(r"https?://spotify\.link/\S+")
APPLE_RE = re.compile(r"https?://music\.apple\.com/[a-z]{2}/(album|song)/(?:[^/\s?]+/)?(\d+)(?:\?\S*)?")
APPLE_TRACK_PARAM = re.compile(r"[?&]i=(\d+)")


@dataclass(frozen=True)
class Link:
    service: str  # "spotify" | "spotify_short" | "apple"
    kind: str  # "track" | "album" | "playlist" | "artist" | "unknown"
    id: str
    url: str
    message_id: int = 0


def find_links(text: str, message_id: int = 0) -> list[Link]:
    links = [Link("spotify", m[1], m[2], m[0], message_id) for m in SPOTIFY_RE.finditer(text)]
    links += [Link("spotify_short", "unknown", "", m[0], message_id) for m in SPOTIFY_SHORT_RE.finditer(text)]
    for m in APPLE_RE.finditer(text):
        track = APPLE_TRACK_PARAM.search(m[0])
        if m[1] == "song" or track:
            links.append(Link("apple", "track", track[1] if track else m[2], m[0], message_id))
        else:
            links.append(Link("apple", "album", m[2], m[0], message_id))
    return links


def strip_links(text: str) -> str:
    """Remove the links we handle ourselves so the LLM doesn't re-extract them."""
    for rx in (SPOTIFY_RE, SPOTIFY_SHORT_RE, APPLE_RE):
        text = rx.sub("[music link]", text)
    return text
