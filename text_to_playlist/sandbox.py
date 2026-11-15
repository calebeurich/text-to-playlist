"""Run the two halves of the tool in separate macOS sandboxes.

reader: may read the iMessage database, has NO network access.
syncer: has network access, CANNOT read anything in your home folder except
        this tool's own code and its config folder.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from . import state

SANDBOX_EXEC = "/usr/bin/sandbox-exec"
READER_PROFILE = "(version 1)(allow default)(deny network*)"


class SandboxError(RuntimeError):
    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


def _q(path) -> str:
    return '"' + os.path.realpath(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def syncer_profile() -> str:
    code = {sys.prefix, sys.base_prefix, sys.exec_prefix, str(Path(__file__).resolve().parent.parent)}
    allow_code = " ".join(f"(subpath {_q(p)})" for p in sorted(code))
    return (
        "(version 1)(allow default)"
        f"(deny file-read-data file-write* (subpath {_q(Path.home())}))"
        f"(allow file-read-data {allow_code})"
        f"(allow file-read-data file-write* (subpath {_q(state.CONFIG_DIR)}))"
    )


def _run(profile: str, args: list[str], stdin: str | None = None, timeout: int = 900) -> str:
    if not os.path.exists(SANDBOX_EXEC):
        raise SandboxError("macOS sandbox-exec not found; refusing to run unsandboxed.")
    state.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    p = subprocess.run([SANDBOX_EXEC, "-p", profile, sys.executable, *args], input=stdin, capture_output=True,
                       text=True, timeout=timeout)
    if p.returncode != 0:
        raise SandboxError(p.stderr.strip()[-1500:] or f"exit code {p.returncode}", p.returncode)
    return p.stdout


def read(args: list[str]) -> list[dict]:
    return json.loads(_run(READER_PROFILE, ["-m", "text_to_playlist.reader", *args]))


def sync(payload: dict) -> dict:
    return json.loads(_run(syncer_profile(), ["-m", "text_to_playlist.syncer"], stdin=json.dumps(payload)))


NET_PROBE = "import socket; socket.create_connection(('api.spotify.com', 443), timeout=8).close()"
DB_PROBE = "import os; open(os.path.expanduser('~/Library/Messages/chat.db'), 'rb').read(16)"


def _probe(profile: str, code: str) -> bool:
    try:
        _run(profile, ["-c", code], timeout=30)
        return True
    except (SandboxError, subprocess.TimeoutExpired):
        return False


def verify_lockdown() -> list[str]:
    """Actively test the walls. Returns a list of problems (empty = locked down)."""
    problems = []
    if _probe(READER_PROFILE, NET_PROBE):
        problems.append("The message reader was able to reach the internet.")
    if _probe(syncer_profile(), DB_PROBE):
        problems.append("The internet-facing part was able to read the iMessage database.")
    if not _probe(syncer_profile(), NET_PROBE):
        problems.append("The internet-facing part could not reach Spotify (offline, or the sandbox is too strict).")
    return problems
