"""The double-click app: a small menu, a review list, and your recs land in Spotify.

This process only shows dialogs and passes data between the two sandboxed halves
(see sandbox.py). It never opens the message database or the network itself.
"""
from __future__ import annotations

import fcntl
import sys
import time
import traceback

from . import sandbox, state, ui
from .spotify import REDIRECT_URI

CONFIG, STATE = "config.json", "state.json"
FDA_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
LOOKBACK = [("Last 30 days", 30), ("Last 3 months", 90), ("Last 6 months", 182), ("Last year", 365),
            ("Everything", None)]
ALBUMS = [("1 best-known track per album", "singles", 1), ("2 best-known tracks per album", "singles", 2),
          ("3 best-known tracks per album", "singles", 3), ("First track of each album", "first", 1),
          ("The whole album", "full", 2)]


MODELS = [("Haiku 5.5 (cheapest)", "claude-haiku-5-5"),
          ("Sonnet 5.5 (smarter, about 20x the cost)", "claude-sonnet-5-5"),
          ("Opus 5.5 (smartest, about 40x the cost)", "claude-opus-5-5")]


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


def read_or_explain(args: list[str]) -> list[dict] | None:
    try:
        return sandbox.read(args)
    except sandbox.SandboxError as e:
        state.log(f"reading Messages failed (code {e.code}): {e}")
        if e.code == 2:
            if ui.alert("Text to Playlist needs permission to read Messages.\n\nIn the window that opens, switch on "
                        "'Text to Playlist' under Full Disk Access, then open the app again.",
                        ("Cancel", "Open Settings")) == "Open Settings":
                ui.open_url(FDA_PANE)
        else:
            ui.alert(f"Could not read Messages:\n{e}")
        return None


def pick_allowed(cfg: dict) -> bool:
    """Let the user choose which chats the app may ever read. Only chat names are loaded here."""
    chats = read_or_explain(["list", "--all"] + (["--dms"] if cfg.get("include_dms") else []))
    if chats is None:
        return False
    shown = chats[: cfg.get("list_size", 200)]
    current = set(state.load_allowed())
    labels = [("★ " if c["key"] in current else "") + c["name"][:70] for c in shown]
    picked = ui.choose("Which chats may this app read? It will never open any other chat.\n"
                       "Most recent first. Hold Command to tick more than one. ★ = currently allowed.", labels)
    if not picked:
        return False
    state.save_allowed([shown[i]["key"] for i in picked])
    state.log(f"allowed chats set: {len(picked)}")
    return True


def album_label(cfg: dict) -> str:
    mode, n = cfg.get("album_mode", "singles"), cfg.get("album_tracks", 2)
    return next((label for label, m, k in ALBUMS if m == mode and (k == n or m == "full")), ALBUMS[1][0])


def menu(cfg: dict, n_chats: int) -> str | None:
    items = [
        f"Sync now ({n_chats} chat{'s' if n_chats != 1 else ''})",
        "Choose which chats to read...",
        f"Review songs before adding: {'ON' if cfg.get('review', True) else 'OFF'}",
        f"Albums: {album_label(cfg)}",
        "Re-read a chat from the start...",
        "Nicknames and notes for the AI...",
        "AI model: " + next((l for l, m in MODELS if m == cfg.get("model", MODELS[0][1])), cfg.get("model", "")),
    ]
    picked = ui.choose("What would you like to do?", items, multiple=False, preselect="first")
    return ["sync", "chats", "review", "albums", "reset", "notes", "model"][picked[0]] if picked else None


def sync_chat(c: dict, cfg: dict) -> str:
    """Sync one chat; returns a summary for the final dialog."""
    name, key = c["name"], c["key"]
    after = state.load(STATE).get("chats", {}).get(key, {}).get("last_rowid", 0)
    since = 0.0
    if not after:
        picked = ui.choose(f"First time reading '{name}'.\nHow far back should I look for song recs?",
                           [label for label, _ in LOOKBACK], multiple=False, preselect="first")
        if not picked:
            return f"{name}: skipped."
        days = LOOKBACK[picked[0]][1]
        since = time.time() - days * 86400 if days else 0.0

    busy = ui.progress(f"Reading '{name}' and finding the songs...\n\nA few minutes for a long history. "
                       "Nothing is added to Spotify until you have seen the list.")
    try:
        msgs = sandbox.read(["fetch", "--key", key, "--after", str(after), "--since", str(since)], cancel=busy.stopped)
        state.log(f"'{key}': {len(msgs)} new messages; planning")
        plan = sandbox.syncer("plan", {"chat_key": key, "chat_name": name, "messages": msgs}, cancel=busy.stopped)
    except sandbox.Cancelled:
        state.log(f"'{key}': stopped by user")
        return f"{name}: stopped. Nothing was added; these messages will be offered again next time."
    finally:
        busy.close()
    state.log(f"'{key}': {len(plan['items'])} tracks found, {len(plan['unresolved'])} unmatched")

    items = plan["items"]
    if items and cfg.get("review", True):
        picked = ui.choose(
            f"{name}: found {len(items)} song(s) in {plan['messages']} new message(s).\n"
            "Everything ticked will be added. Command-click to untick anything you don't want.",
            [i["label"][:90] for i in items], preselect="all")
        if picked is None:
            return f"{name}: cancelled, nothing added. These messages will be offered again next time."
        items = [items[i] for i in picked]

    busy = ui.progress(f"Adding {len(items)} song(s) to the '{name}' playlist...")
    try:
        done = sandbox.syncer("apply", {"chat_key": key, "chat_name": name, "items": items,
                                        "last_rowid": plan["last_rowid"], "oldest_ts": plan.get("oldest_ts", 0)})
    finally:
        busy.close()
    state.log(f"'{key}': added {len(done['added'])} tracks")

    lines = [f"{name}: added {len(done['added'])} song(s) from {plan['messages']} new message(s)."]
    lines += [f"  ? couldn't find: {x}" for x in plan["unresolved"][:10]]
    lines += [f"  ! {x[:110]}" for x in plan["notes"][:5]]
    if plan.get("cost") is not None:
        lines.append(f"  AI cost for this sync: about ${plan['cost']:.2f}" if plan["cost"] >= 0.005
                     else "  AI cost for this sync: under 1 cent")
    if done.get("playlist_url"):
        lines.append(f"  {done['playlist_url']}")
    return "\n".join(lines)


def reset_chat() -> None:
    st = state.load(STATE)
    chats = list(st.get("chats", {}).items())
    if not chats:
        ui.alert("No chat has been synced yet.")
        return
    picked = ui.choose("Which chat should be read again from the start?",
                       [v.get("name", k) for k, v in chats], multiple=False)
    if not picked:
        return
    key = chats[picked[0]][0]
    how = ui.alert("Keep playlist: re-read the chat and only offer songs that aren't in its playlist yet "
                   "(use this to pick up songs that were missed).\n\n"
                   "New playlist: forget everything and build a fresh playlist.",
                   ("Cancel", "New playlist", "Keep playlist"))
    if how == "Keep playlist":
        st["chats"][key]["last_rowid"] = 0
    elif how == "New playlist":
        del st["chats"][key]
    else:
        return
    state.save(STATE, st)
    ui.alert("Done. Choose 'Sync now' to read it again."
             + (" The old playlist is still in Spotify; delete it there." if how == "New playlist" else ""))


def run() -> int:
    state.log("--- app opened")
    cfg = state.load(CONFIG)
    if not cfg.get("spotify_client_id") and not first_run(cfg):
        return 0

    problems = sandbox.verify_lockdown()
    state.log(f"safety check: {problems or 'passed'}")
    if problems:
        ui.alert("Stopped: the safety check failed, so nothing was read or sent.\n\n" + "\n".join(problems))
        return 3

    if not state.load_allowed() and not pick_allowed(cfg):
        return 2

    while True:
        chats = read_or_explain(["list"])  # allowed chats only
        if chats is None:
            return 2
        action = menu(cfg, len(chats))
        state.log(f"menu: {action}")
        if action is None:
            return 0
        if action == "chats":
            pick_allowed(cfg)
        elif action == "review":
            cfg["review"] = not cfg.get("review", True)
            state.save(CONFIG, cfg)
        elif action == "albums":
            picked = ui.choose("When someone recommends a whole album, add:", [a[0] for a in ALBUMS], multiple=False)
            if picked:
                _, cfg["album_mode"], cfg["album_tracks"] = ALBUMS[picked[0]]
                state.save(CONFIG, cfg)
        elif action == "reset":
            reset_chat()
        elif action == "model":
            picked = ui.choose("Which Claude model should read the chats?", [l for l, _ in MODELS], multiple=False)
            if picked:
                cfg["model"] = MODELS[picked[0]][1]
                state.save(CONFIG, cfg)
        elif action == "notes":
            ui.open_text_file(str(state.notes_file()))
            ui.alert("The notes file is open in TextEdit. Add one note per line, save, then come back here.")
        else:
            break

    summaries = []
    for c in chats:
        try:
            summaries.append(sync_chat(c, cfg))
        except sandbox.SandboxError as e:
            state.log(f"sync failed: {e}")
            summaries.append(f"{c['name']}: FAILED.\n  {str(e)[-300:]}")
    urls = [u for u in (state.load(STATE).get("chats", {}).get(c["key"], {}).get("playlist_url") for c in chats) if u]
    buttons = ("Open playlist", "Done") if len(urls) == 1 else ("Done",)
    if ui.alert("\n\n".join(summaries) or "No chats to sync.", buttons) == "Open playlist":
        ui.open_url(urls[0])
    return 1 if any("FAILED" in s for s in summaries) else 0


def copy_report() -> None:
    """Put the recent log on the clipboard so a failure can be pasted straight into a chat."""
    try:
        lines = (state.CONFIG_DIR / "app.log").read_text().splitlines()[-60:]
        ui.copy_to_clipboard("Text to Playlist log:\n" + "\n".join(lines))
    except OSError:
        pass


def main() -> int:
    state.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(state.CONFIG_DIR / "app.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        ui.alert("Text to Playlist is already running. Look for its window, or wait for it to finish.")
        return 0
    try:
        code = run()
        state.log(f"finished with code {code}")
    except Exception:
        code = 1
        state.log(traceback.format_exc())
        copy_report()
        ui.alert("Text to Playlist hit an unexpected error and stopped.\n\nA report is on your clipboard: "
                 "paste it into your chat with Claude.")
        return code
    if code not in (0, 2):
        copy_report()
        ui.alert("That didn't fully work. A report is on your clipboard: paste it into your chat with Claude.")
    return code


if __name__ == "__main__":
    sys.exit(main())
