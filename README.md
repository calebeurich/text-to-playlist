# text-to-playlist

Pick an iMessage chat from a list; the song recs in it land in a private Spotify playlist for that chat.
Run it again later and only the new recs are added.

It understands Spotify links, Apple Music links, and plain-text recs ("you have to hear the new Big Thief album").

## Setup (once, on your Mac)

1. **Install**

   ```bash
   git clone https://github.com/calebeurich/text-to-playlist && cd text-to-playlist
   ```

   ```bash
   ./install.sh
   ```

   This puts the tool in `~/.local/share/text-to-playlist` and builds `Text to Playlist.app` in
   `~/Applications`. To update later, run `git pull` and then `./install.sh` again.

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
- **Start a chat over**: forget a chat so its next sync re-reads it into a new playlist.

Other settings live in `~/.config/text-to-playlist/config.json`: `include_dms` (offer one-to-one chats when
choosing), `model` (Claude model, default `claude-haiku-5-5`, the cheapest; `claude-opus-5-5` is stronger on
vague recs).

Album modes: `singles` asks Claude to pick the album's best-known tracks from its real track list,
`first` takes the opening tracks, `full` adds the whole album.

## How it is locked down

The app is split into two halves that run in separate macOS sandboxes:

| Half | Can | Cannot |
|---|---|---|
| Reader | Read the iMessage database, read-only | Use the network at all |
| Syncer | Talk to Claude and Spotify | Read anything in your home folder except its own code and config |

The reader only opens chats on your allow-list (stored where the syncer cannot change it) and hands the
syncer only their new messages, with phone numbers and emails
already replaced by "Person A", "Person B". On every launch the app tests both walls (it tries to reach
the internet from the reader and to open the message database from the syncer) and refuses to run if
either attempt succeeds.

- **Full Disk Access** is granted to this one app, not to your terminal. macOS has no narrower
  "Messages only" permission.
- **Spotify**: the login asks for one permission, `playlist-modify-private`. The tool can create and edit
  private playlists; it cannot see your listening history, library, or profile, or control playback. It only
  ever adds tracks, and only to playlists it created.
- **On disk**: `~/.config/text-to-playlist/` holds the Spotify token, the API key, and which tracks were
  already added per chat. Files are readable only by you. Songs you delete from a playlist are not re-added.

## Terminal version

`ttp` does the same thing from a terminal in a single unsandboxed process, and needs Full Disk Access
on the terminal itself. It is there for debugging: `ttp --dry-run` shows what would be added without
touching Spotify, and `ttp --help` lists the options.

## Tests

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]" && .venv/bin/pytest
```
