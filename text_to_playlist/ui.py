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


def _args_to_list(first: int) -> str:
    """AppleScript that collects argv items first..n into the list `opts`."""
    return ('set opts to {}\n'
            f'repeat with i from {first} to (count of argv)\n'
            'set end of opts to (item i of argv) as text\n'
            'end repeat\n')


def choose(prompt: str, items: list[str], multiple: bool = True, preselect: str = "none") -> list[int] | None:
    """Show a pick-list. Returns chosen indexes, [] if nothing was ticked, None if cancelled.
    preselect is "none", "first" or "all"."""
    numbered = [f"{i}. {s}" for i, s in enumerate(items, 1)]
    mode = "with multiple selections allowed and empty selection allowed" if multiple \
        else "without multiple selections allowed"
    out = _osa(
        'on run argv\n' + _args_to_list(3) +
        'set defs to {}\n'
        'if (item 2 of argv) is "all" then set defs to opts\n'
        'if (item 2 of argv) is "first" then set defs to {item 1 of opts}\n'
        'tell me to activate\n'
        'if (count of defs) is 0 then\n'
        f'set picked to choose from list opts with title "Text to Playlist" with prompt (item 1 of argv) {mode}\n'
        'else\n'
        'set picked to choose from list opts with title "Text to Playlist" '
        f'with prompt (item 1 of argv) default items defs {mode}\n'
        'end if\n'
        'if picked is false then error number -128\n'
        "set AppleScript's text item delimiters to linefeed\n"
        'return picked as text\nend run',
        prompt, preselect, *numbered,
    )
    if out is None:
        return None
    return [int(m[1]) - 1 for line in out.split("\n") if (m := re.match(r"(\d+)\. ", line))]


class progress:
    """A 'working...' window with a Stop button. It re-reads progress.txt every few seconds
    and stays up until close() is called."""

    SCRIPT = (
        'on run argv\n'
        'tell me to activate\n'
        'repeat\n'
        'set t to item 1 of argv\n'
        'try\n'
        'set t to t & return & return & (do shell script "cat " & quoted form of (item 2 of argv))\n'
        'end try\n'
        'set r to display dialog t with title "Text to Playlist" buttons {"Stop"} giving up after 6\n'
        'if gave up of r is false then return "stop"\n'
        'end repeat\n'
        'end run'
    )

    def __init__(self, message: str):
        self.closed = False
        status = state.CONFIG_DIR / "progress.txt"
        try:
            status.unlink()
        except OSError:
            pass
        self.proc = subprocess.Popen(["/usr/bin/osascript", "-e", self.SCRIPT, message, str(status)],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def stopped(self) -> bool:
        """True once the user has pressed Stop (a crashed window does not count)."""
        return not self.closed and self.proc.poll() == 0

    def close(self) -> None:
        self.closed = True
        self.proc.terminate()


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
        'on run argv\n' + _args_to_list(2) +
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


def open_text_file(path: str) -> None:
    subprocess.run(["/usr/bin/open", "-e", path])
