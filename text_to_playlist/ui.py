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


# A real window with a progress bar that updates in place (JavaScript for Automation + Cocoa).
# It re-reads the status file twice a second and never re-activates itself, so it does not
# jump in front of whatever else you are doing.
PROGRESS_WINDOW_JS = r"""
ObjC.import('Cocoa');
function run(argv) {
  var title = argv[0], statusPath = argv[1], stopped = false;
  ObjC.registerSubclass({
    name: 'TTPStopTarget',
    methods: {'stop:': {types: ['void', ['id']], implementation: function (sender) { stopped = true; }}}
  });
  var target = $.TTPStopTarget.alloc.init;
  var app = $.NSApplication.sharedApplication;
  app.setActivationPolicy(1);  // accessory: a window, but no Dock icon
  app.finishLaunching;

  var win = $.NSWindow.alloc.initWithContentRectStyleMaskBackingDefer($.NSMakeRect(0, 0, 460, 170), 1, 2, false);
  win.setTitle('Text to Playlist');
  win.center;
  var heading = $.NSTextField.wrappingLabelWithString(title);
  heading.setFrame($.NSMakeRect(20, 110, 420, 44));
  var status = $.NSTextField.labelWithString('Starting...');
  status.setFrame($.NSMakeRect(20, 82, 420, 20));
  var bar = $.NSProgressIndicator.alloc.initWithFrame($.NSMakeRect(20, 56, 420, 20));
  bar.setMinValue(0);
  bar.setMaxValue(100);
  bar.setIndeterminate(true);
  bar.startAnimation(null);
  var button = $.NSButton.buttonWithTitleTargetAction('Stop', target, 'stop:');
  button.setFrame($.NSMakeRect(350, 12, 90, 32));
  var view = win.contentView;
  view.addSubview(heading); view.addSubview(status); view.addSubview(bar); view.addSubview(button);
  win.makeKeyAndOrderFront(null);
  app.activateIgnoringOtherApps(true);

  var mode = $('kCFRunLoopDefaultMode'), last = '';
  while (!stopped) {
    var until = $.NSDate.dateWithTimeIntervalSinceNow(0.5);
    while (true) {
      var e = app.nextEventMatchingMaskUntilDateInModeDequeue(4294967295, until, mode, true);
      if (!e || e.isNil()) break;
      app.sendEvent(e);
    }
    var raw = $.NSString.stringWithContentsOfFileEncodingError(statusPath, 4, null);
    var text = (raw && !raw.isNil()) ? raw.js : '';
    if (text && text !== last) {
      last = text;
      var step = text.split('\n')[0];
      var m = text.match(/(\d+) of (\d+)/);
      if (m && Number(m[2]) > 0) {
        status.setStringValue(step + '  (' + m[1] + ' of ' + m[2] + ')');
        bar.setIndeterminate(false);
        bar.setDoubleValue(100 * Number(m[1]) / Number(m[2]));
      } else {
        status.setStringValue(step);
        bar.setIndeterminate(true);
        bar.startAnimation(null);
      }
    }
  }
  return 'stop';
}
"""

# Used only if the window above fails to start: one plain dialog that never refreshes.
PLAIN_DIALOG = (
    'on run argv\n'
    'display dialog (item 1 of argv) with title "Text to Playlist" buttons {"Stop"} giving up after 14400\n'
    'end run'
)


class progress:
    """A 'working...' window with a progress bar and a Stop button; stays up until close()."""

    def __init__(self, message: str):
        self.closed = False
        self.fallback = False
        self.message = message
        status = state.CONFIG_DIR / "progress.txt"
        try:
            status.unlink()
        except OSError:
            pass
        self.proc = subprocess.Popen(
            ["/usr/bin/osascript", "-l", "JavaScript", "-e", PROGRESS_WINDOW_JS, message, str(status)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

    def stopped(self) -> bool:
        """True once the user has pressed Stop. If the window itself failed, fall back to a plain dialog."""
        code = self.proc.poll()
        if self.closed or code is None:
            return False
        if code == 0:
            return True
        if not self.fallback:
            self.fallback = True
            err = self.proc.stderr.read() if self.proc.stderr else ""
            state.log(f"progress window failed (code {code}): {err.strip()[-500:]}")
            self.proc = subprocess.Popen(
                ["/usr/bin/osascript", "-e", PLAIN_DIALOG,
                 self.message + "\n\n(The progress bar could not be shown; this is still working.)"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return False

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
