"""Native macOS dialogs via osascript. Strings are passed as arguments, never spliced into script text."""
from __future__ import annotations

import re
import subprocess


def _osa(script: str, *args: str) -> str | None:
    p = subprocess.run(["/usr/bin/osascript", "-e", script, *args], capture_output=True, text=True)
    return p.stdout.rstrip("\n") if p.returncode == 0 else None  # non-zero = user cancelled


def choose(prompt: str, items: list[str]) -> list[int]:
    """Show a pick-list; returns the chosen indexes (empty if cancelled)."""
    numbered = [f"{i}. {s}" for i, s in enumerate(items, 1)]
    out = _osa(
        'on run argv\n'
        'tell me to activate\n'
        'set picked to choose from list (rest of argv) with title "Text to Playlist" '
        'with prompt (item 1 of argv) with multiple selections allowed\n'
        'if picked is false then error number -128\n'
        "set AppleScript's text item delimiters to linefeed\n"
        'return picked as text\nend run',
        prompt, *numbered,
    )
    if not out:
        return []
    return [int(m[1]) - 1 for line in out.split("\n") if (m := re.match(r"(\d+)\. ", line))]


def ask(prompt: str, hidden: bool = False) -> str | None:
    return _osa(
        'on run argv\ntell me to activate\n'
        'return text returned of (display dialog (item 1 of argv) with title "Text to Playlist" default answer ""'
        + (" with hidden answer" if hidden else "") + ")\nend run",
        prompt,
    )


def alert(message: str, buttons: tuple[str, ...] = ("OK",)) -> str | None:
    """Returns the button pressed."""
    return _osa(
        'on run argv\ntell me to activate\n'
        'return button returned of (display dialog (item 1 of argv) with title "Text to Playlist" '
        'buttons (rest of argv) default button (count of (rest of argv)))\nend run',
        message, *buttons,
    )


def notify(message: str) -> None:
    _osa('on run argv\ndisplay notification (item 1 of argv) with title "Text to Playlist"\nend run', message)


def open_url(url: str) -> None:
    subprocess.run(["/usr/bin/open", url])
