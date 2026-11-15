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


class SpotifyError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"Spotify API {status}: {body[:300]}")
        self.status = status


class SpotifyRateLimited(SpotifyError):
    """Spotify told us to back off for longer than is worth waiting in place."""

    def __init__(self, wait_seconds: int):
        mins = max(1, round(wait_seconds / 60))
        RuntimeError.__init__(self, f"Spotify is rate-limiting this app. Try again in about {mins} minute(s).")
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
            return r.status, (json.loads(raw) if raw else {}), dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        if e.code == 429:
            return 429, {}, dict(e.headers)
        raise SpotifyError(e.code, raw) from e


class Spotify:
    def __init__(self, client_id: str):
        self.client_id = client_id
        self.token = state.load(TOKEN_FILE)

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
        for _ in range(6):
            time.sleep(0.05)  # stay well under Spotify's per-app request rate
            status, data, headers = _http(
                method, url, headers={"Authorization": f"Bearer {self._access_token()}"}, body=body
            )
            if status != 429:
                return data
            wait = int(headers.get("Retry-After", "2") or 2)
            if wait > 90:  # a long penalty: stop cleanly instead of hammering on
                raise SpotifyRateLimited(wait)
            time.sleep(wait + 1)
        raise SpotifyRateLimited(120)

    # ---- catalog ----------------------------------------------------------
    def search(self, query: str, kind: str, limit: int = 5) -> list[dict]:
        data = self._call("GET", "/search", {"q": query, "type": kind, "limit": limit})
        return [i for i in data.get(f"{kind}s", {}).get("items", []) if i]

    def album(self, album_id: str) -> dict:
        return self._call("GET", f"/albums/{album_id}")

    def track(self, track_id: str) -> dict:
        return self._call("GET", f"/tracks/{track_id}")

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
