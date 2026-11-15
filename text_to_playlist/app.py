"""The double-click app: pick a chat from a list, and its recs land in a Spotify playlist.

This process only shows dialogs and passes data between the two sandboxed halves
(see sandbox.py). It never opens the message database or the network itself.
"""
from __future__ import annotations

import sys
import time

from . import sandbox, state, ui
from .spotify import REDIRECT_URI

CONFIG, STATE = "config.json", "state.json"
FDA_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"


def first_run(cfg: dict) -> bool:
    ui.alert("One-time setup.\n\nNext you'll paste the Client ID of your Spotify developer app "
             f"(its redirect URI must be {REDIRECT_URI}), then your Claude API key.")
    client_id = ui.ask("Spotify Client ID:")
    if not client_id:
        return False
    cfg["spotify_client_id"] = client_id.strip()
    key = ui.ask("Claude API key (leave empty to only pick up links):", hidden=True)
    if key and key.strip():
        cfg["anthropic_api_key"] = key.strip()
    state.save(CONFIG, cfg)
    return True


def summarize(results: list[dict]) -> str:
    lines = []
    for r in results:
        lines.append(f"{r['chat']}: added {len(r['added'])} track(s) from {r['messages']} new message(s).")
        lines += [f"  + {x}" for x in r["added"][:15]]
        if len(r["added"]) > 15:
            lines.append(f"  ...and {len(r['added']) - 15} more")
        lines += [f"  ? no match: {x}" for x in r["unresolved"][:8]]
        lines += [f"  ! {x}" for x in r["notes"][:4]]
    return "\n".join(lines)


def read_or_explain(args: list[str]) -> list[dict] | None:
    try:
        return sandbox.read(args)
    except sandbox.SandboxError as e:
        if e.code == 2:
            if ui.alert("Text to Playlist needs permission to read Messages.\n\nIn the window that opens, switch on "
                        "'Text to Playlist' under Full Disk Access, then open the app again.",
                        ("Cancel", "Open Settings")) == "Open Settings":
                ui.open_url(FDA_PANE)
        else:
            ui.alert(f"Could not read Messages:\n{e}")
        return None


def pick_allowed(cfg: dict) -> list[str] | None:
    """Let the user choose which chats the app may ever read. Only chat names are loaded here."""
    chats = read_or_explain(["list", "--all"] + (["--dms"] if cfg.get("include_dms") else []))
    if chats is None:
        return None
    shown = chats[: cfg.get("list_size", 40)]
    current = set(state.load_allowed())
    labels = [("★ " if c["key"] in current else "") + c["name"][:70] for c in shown]
    picked = ui.choose("Which chats may this app read? It will never open any other chat.\n"
                       "(Hold Command to select more than one. ★ = currently allowed.)", labels)
    if not picked:
        return None
    keys = [shown[i]["key"] for i in picked]
    state.save_allowed(keys)
    return keys


def main() -> int:
    cfg = state.load(CONFIG)
    if not cfg.get("spotify_client_id") and not first_run(cfg):
        return 1

    problems = sandbox.verify_lockdown()
    if problems:
        ui.alert("Stopped: the safety check failed, so nothing was read or sent.\n\n" + "\n".join(problems))
        return 3

    if not state.load_allowed() and not pick_allowed(cfg):
        return 2
    todo = read_or_explain(["list"])  # allowed chats only
    if todo is None:
        return 2

    st = state.load(STATE)
    ui.notify(f"Checking {len(todo)} chat(s) for new song recs...")
    results = []
    for c in todo:
        after = st.get("chats", {}).get(c["key"], {}).get("last_rowid", 0)
        since = 0 if after else time.time() - cfg.get("since_days", 90) * 86400
        try:
            msgs = sandbox.read(["fetch", "--key", c["key"], "--after", str(after), "--since", str(since)])
            results.append(sandbox.sync({"chat_key": c["key"], "chat_name": c["name"], "messages": msgs}))
        except sandbox.SandboxError as e:
            ui.alert(f"Syncing '{c['name']}' failed:\n\n{e}")
            return 1

    urls = [r["playlist_url"] for r in results if r.get("playlist_url")]
    buttons = ("Change chats", "Open playlist", "Done") if len(urls) == 1 else ("Change chats", "Done")
    choice = ui.alert(summarize(results) or "No allowed chats found.", buttons)
    if choice == "Open playlist":
        ui.open_url(urls[0])
    elif choice == "Change chats":
        pick_allowed(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
