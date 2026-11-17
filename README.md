# text-to-playlist

Pick an iMessage chat from a list; the song recs in it land in a private Spotify playlist for that chat.
Run it again later and only the new recs are added.

It understands Spotify links, Apple Music links, and plain-text recs ("you have to hear the new Big Thief album").

## Setup (once, on your Mac)

Needs macOS, Apple's command line tools (`xcode-select --install`), Python 3.10 or newer, a Spotify
Premium account and a Claude API key.

1. **Install**

   ```bash
   git clone https://github.com/calebeurich/text-to-playlist && cd text-to-playlist
   ```

   ```bash
   ./install.sh
   ```

   This puts the tool in `~/.local/share/text-to-playlist` and builds `Text to Playlist.app` in
   `~/Applications`, plus `Update Text to Playlist.app`, which pulls the latest code and reinstalls with a
   double-click.

2. **Spotify app** (needs Premium): at <https://developer.spotify.com/dashboard> create an app, add the
   redirect URI `http://127.0.0.1:8888/callback`, and tick "Web API". Copy the Client ID.

3. **Claude API key**: in the Anthropic Console create a separate workspace for this tool, give it a low
   monthly spend limit, and create a key in it.

4. **Open "Text to Playlist"**. The first launch asks for the Client ID and the key, then asks you to switch
   on Full Disk Access for the app (not for your terminal), then opens Spotify's login page once.

## Use

The first launch asks which chats the app may read. After that, opening the app shows a short menu:

- **Sync now**: checks your chats for new recs. The first time it reads a chat it asks how far back to
  look. It then shows the songs it found, all ticked; untick any you don't want and press OK.
- **Choose which chats to read**: change the allow-list.
- **Review songs before adding**: switch the review list off to add everything without asking.
- **Albums**: how many tracks to take when someone recommends a whole album.
- **Re-read a chat from the start**: either keep its playlist and only pick up songs that were missed,
  or forget it and build a new playlist.
- **Nicknames and notes for the AI**: a text file of hints such as "Biggie = The Notorious B.I.G." or
  "Alex Smith is a friend, not a musician".
- **AI model**: Haiku (default, cheapest), Sonnet or Opus. Each sync's summary shows what the AI calls cost.

Recs that an exact search can't find get a second look: the model is shown Spotify's actual results and
picks one or declines. Those appear in the review list marked "best guess for".

Songs are added in chat order, oldest first, so the newest recs are at the bottom of the playlist. If you
start with a short lookback and later re-read the chat with a longer one (keeping the playlist), the older
songs are inserted at the top.

Other settings live in `~/.config/text-to-playlist/config.json`: `include_dms` (offer one-to-one chats when
choosing), `model` (Claude model, default `claude-haiku-5-5`, the cheapest; `claude-opus-5-5` is stronger on
vague recs).

Album modes: `singles` asks Claude to pick the album's best-known tracks from its real track list,
`first` takes the opening tracks, `full` adds the whole album.

## Spotify rate limits

Spotify limits how many lookups a personal developer app can make, and heavy use can earn a time-out of
many hours. The app paces itself at about two requests a second, waits out short pauses on its own, and
saves every lookup so nothing is asked twice. If a long time-out hits mid-sync, it adds what it has
(song links need no lookup), saves the rest, and picks them up on the next sync. For a very long chat
history, start with "Last year" rather than "Everything".

## Troubleshooting

Every run is logged to `~/.config/text-to-playlist/app.log`. If a run fails, the recent log is copied to
the clipboard. While a sync is running, `~/.config/text-to-playlist/progress.txt` holds the current step.

## How it is locked down

The app is split into two halves that run in separate macOS sandboxes:

| Half | Can | Cannot |
|---|---|---|
| Reader | Read the iMessage database, read-only | Use the network at all |
| Syncer | Talk to Claude and Spotify | Read anything in your home folder except its own code and config |

The reader only opens chats on your allow-list (stored where the syncer cannot change it) and hands the
syncer only their new messages, with phone numbers and emails already replaced by "Person A", "Person B". On every launch the app tests both walls (it tries to reach
the internet from the reader and to open the message database from the syncer) and refuses to run if
either attempt succeeds.

- **Full Disk Access** is granted to this one app, not to your terminal. macOS has no narrower
  "Messages only" permission.
- **Spotify**: the login asks for one permission, `playlist-modify-private`. The tool can create and edit
  private playlists; it cannot see your listening history, library, or profile, or control playback. It only
  ever adds tracks, and only to playlists it created.
- **On disk**: `~/.config/text-to-playlist/` holds the Spotify token, the API key, and which tracks were
  already added per chat, plus saved lookup results (song and album names, never message text). Files are
  readable only by you. Songs you delete from a playlist are not re-added.

## Terminal version

`ttp` does the same thing from a terminal in a single unsandboxed process, and needs Full Disk Access
on the terminal itself. It is there for debugging: `ttp --dry-run` shows what would be added without
touching Spotify, and `ttp --help` lists the options.

## Tests

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]" && .venv/bin/pytest
```
