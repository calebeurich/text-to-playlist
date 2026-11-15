"""Minimal Spotify Web API client.

Auth is Authorization Code + PKCE (no client secret) with a single scope,
playlist-modify-private: the token can create and edit private playlists and
nothing else (no listening history, library, playback or profile email).
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import state

API = "https://api.spotify.com/v1"
ACCOUNTS = "https://accounts.spotify.com"
REDIRECT_PORT = 8888
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORT}/callback"
SCOPE = "playlist-modify-private"
TOKEN_FILE = "spotify_token.json"
CACHE_FILE = "spotify_cache.json"
MAX_WAIT = 15 * 60  # longest pause we will sit through when Spotify says "slow down"


def _slim(obj: dict) -> dict:
    """Keep only the fields the tool uses, so the lookup cache stays small."""
    out = {k: obj[k] for k in ("id", "uri", "name") if k in obj}
    out["artists"] = [{"name": a["name"]} for a in obj.get("artists", [])]
    if "tracks" in obj:
        out["tracks"] = {"items": [{"uri": t["uri"], "name": t["name"]} for t in obj["tracks"].get("items", [])]}
    return out


class SpotifyError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"Spotify API {status}: {body[:300]}")
        self.status = status


class SpotifyRateLimited(SpotifyError):
    """Spotify told us to back off for longer than is worth waiting in place."""

    def __init__(self, wait_seconds: int):
        mins = max(1, round(wait_seconds / 60))
        when = f"{mins} minute(s)" if mins < 90 else f"{round(mins / 60)} hour(s)"
        RuntimeError.__init__(self, f"Spotify is rate-limiting this app. Try again in about {when}. "
                                    "What was already looked up is saved, so the next try has less to do.")
        self.status = 429


def _http(method: str, url: str, *, headers=None, form=None, body=None) -> tuple[int, dict, dict]:
    data = None
    headers = dict(headers or {})
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {}), {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        if e.code == 429:
            return 429, {}, {k.lower(): v for k, v in e.headers.items()}
        raise SpotifyError(e.code, raw) from e


class Spotify:
    def __init__(self, client_id: str):
        self.client_id = client_id
        self.token = state.load(TOKEN_FILE)
        self.cache = state.load(CACHE_FILE)  # answers to earlier lookups; a re-run costs no new requests
        self.unsaved = 0
        self.delay = 0.5  # pause between requests; grows if Spotify pushes back
        self.on_wait = lambda seconds: None

    # ---- auth -------------------------------------------------------------
    def _store(self, tok: dict) -> None:
        tok["expires_at"] = time.time() + tok.get("expires_in", 3600) - 60
        tok.setdefault("refresh_token", self.token.get("refresh_token"))
        self.token = tok
        state.save(TOKEN_FILE, tok)

    def login(self) -> None:
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        csrf = secrets.token_urlsafe(16)
        url = f"{ACCOUNTS}/authorize?" + urllib.parse.urlencode({
            "client_id": self.client_id, "response_type": "code", "redirect_uri": REDIRECT_URI,
            "scope": SCOPE, "code_challenge_method": "S256", "code_challenge": challenge, "state": csrf,
        })
        result: dict = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                result.update({k: v[0] for k, v in q.items()})
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<h3>Done. You can close this tab and go back to the terminal.</h3>")

            def log_message(self, *a):
                pass

        server = HTTPServer(("127.0.0.1", REDIRECT_PORT), Handler)
        print(f"Opening Spotify login in your browser. If nothing opens, visit:\n{url}\n", file=sys.stderr)
        webbrowser.open(url)
        while "code" not in result and "error" not in result:
            server.handle_request()
        server.server_close()
        if result.get("state") != csrf or "code" not in result:
            raise RuntimeError(f"Spotify login failed: {result.get('error', 'state mismatch')}")
        _, tok, _ = _http("POST", f"{ACCOUNTS}/api/token", form={
            "grant_type": "authorization_code", "code": result["code"], "redirect_uri": REDIRECT_URI,
            "client_id": self.client_id, "code_verifier": verifier,
        })
        self._store(tok)

    def _access_token(self) -> str:
        if not self.token.get("refresh_token"):
            self.login()
        elif time.time() >= self.token.get("expires_at", 0):
            try:
                _, tok, _ = _http("POST", f"{ACCOUNTS}/api/token", form={
                    "grant_type": "refresh_token", "refresh_token": self.token["refresh_token"],
                    "client_id": self.client_id,
                })
                self._store(tok)
            except SpotifyError:
                self.login()
        return self.token["access_token"]

    def _call(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict:
        url = f"{API}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
        for _ in range(8):
            time.sleep(self.delay)
            status, data, headers = _http(
                method, url, headers={"Authorization": f"Bearer {self._access_token()}"}, body=body
            )
            if status != 429:
                return data
            wait = int(headers.get("retry-after", "5") or 5)
            if wait > MAX_WAIT:
                self.flush()
                raise SpotifyRateLimited(wait)
            self.delay = min(1.0, self.delay * 2)  # ease off for the rest of this run
            self.on_wait(wait)
            time.sleep(wait + 1)
        self.flush()
        raise SpotifyRateLimited(MAX_WAIT)

    def _cached(self, key: str, fetch):
        if key not in self.cache:
            self.cache[key] = fetch()
            self.unsaved += 1
            if self.unsaved >= 20:
                self.flush()
        return self.cache[key]

    def flush(self) -> None:
        if self.unsaved:
            state.save(CACHE_FILE, self.cache)
            self.unsaved = 0

    # ---- catalog ----------------------------------------------------------
    def search(self, query: str, kind: str, limit: int = 5) -> list[dict]:
        def fetch():
            data = self._call("GET", "/search", {"q": query, "type": kind, "limit": limit})
            return [_slim(i) for i in data.get(f"{kind}s", {}).get("items", []) if i]
        return self._cached(f"search|{kind}|{limit}|{query}", fetch)

    def prefetch_tracks(self, ids: list[str]) -> None:
        """Look up many track links in a few requests (50 at a time) instead of one each."""
        missing = [i for i in dict.fromkeys(ids) if f"track|{i}" not in self.cache]
        for k in range(0, len(missing), 50):
            try:
                data = self._call("GET", "/tracks", {"ids": ",".join(missing[k:k + 50])})
            except SpotifyRateLimited:
                raise
            except SpotifyError:
                return  # batch lookup not offered to this app; they will be fetched one by one
            for t in data.get("tracks", []):
                if t:
                    self.cache[f"track|{t['id']}"] = _slim(t)
                    self.unsaved += 1
        self.flush()

    def album(self, album_id: str) -> dict:
        return self._cached(f"album|{album_id}", lambda: _slim(self._call("GET", f"/albums/{album_id}")))

    def track(self, track_id: str) -> dict:
        return self._cached(f"track|{track_id}", lambda: _slim(self._call("GET", f"/tracks/{track_id}")))

    # ---- playlists --------------------------------------------------------
    def create_playlist(self, name: str, description: str = "") -> dict:
        return self._call("POST", "/me/playlists", body={"name": name, "public": False, "description": description})

    def add_tracks(self, playlist_id: str, uris: list[str], position: int | None = None) -> None:
        """Append, or insert starting at `position` (0 = top), keeping the given order."""
        for i in range(0, len(uris), 100):
            body: dict = {"uris": uris[i:i + 100]}
            if position is not None:
                body["position"] = position + i
            try:  # endpoint was renamed /tracks -> /items in Spotify's Feb 2026 API changes
                self._call("POST", f"/playlists/{playlist_id}/items", body=body)
            except SpotifyError as e:
                if e.status != 404:
                    raise
                self._call("POST", f"/playlists/{playlist_id}/tracks", body=body)
