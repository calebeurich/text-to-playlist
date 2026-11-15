"""Native macOS dialogs via osascript. Strings are passed as arguments, never spliced into script text."""
from __future__ import annotations

import re
import subprocess

from . import state


def _osa(script: str, *args: str) -> str | None:
    p = subprocess.run(["/usr/bin/osascript", "-e", script, *args], capture_output=True, text=True)
    if p.returncode != 0:
        if "-128" not in p.stderr:  # -128 is the user pressing Cancel
            state.log(f"dialog failed: {p.stderr.strip()}")
        return None
    return p.stdout.rstrip("\n")


# Builds an AppleScript list from argv items 2..n (argv item 1 is always the prompt).
ARGS_TO_LIST = (
    'set opts to {}\n'
    'repeat with i from 2 to (count of argv)\n'
    'set end of opts to (item i of argv) as text\n'
    'end repeat\n'
)


def choose(prompt: str, items: list[str]) -> list[int]:
    """Show a pick-list; returns the chosen indexes (empty if cancelled)."""
    numbered = [f"{i}. {s}" for i, s in enumerate(items, 1)]
    out = _osa(
        'on run argv\n' + ARGS_TO_LIST +
        'tell me to activate\n'
        'set picked to choose from list opts with title "Text to Playlist" '
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
    """Returns the button pressed. The last button is the default."""
    return _osa(
        'on run argv\n' + ARGS_TO_LIST +
        'tell me to activate\n'
        'set answer to display dialog (item 1 of argv) with title "Text to Playlist" '
        'buttons opts default button (last item of opts)\n'
        'return button returned of answer\nend run',
        message, *buttons,
    )


def notify(message: str) -> None:
    _osa('on run argv\ndisplay notification (item 1 of argv) with title "Text to Playlist"\nend run', message)


def open_url(url: str) -> None:
    subprocess.run(["/usr/bin/open", url])


def copy_to_clipboard(text: str) -> None:
    subprocess.run(["/usr/bin/pbcopy"], input=text, text=True)
