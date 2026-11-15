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
   python3 -m venv .venv && .venv/bin/pip install -e .
   ```

2. **Let your terminal read Messages**: System Settings > Privacy & Security > Full Disk Access, switch on
   your terminal app, then restart it.

3. **Spotify app** (needs Premium): at <https://developer.spotify.com/dashboard> create an app, add the
   redirect URI `http://127.0.0.1:8888/callback`, and tick "Web API". Copy the Client ID.

4. **Claude API key**: in the Anthropic Console create a separate workspace for this tool, give it a low
   monthly spend limit, and create a key in it.

5. **Run it**. The first run asks for the Client ID and the key, then opens Spotify's login page once.

   ```bash
   .venv/bin/ttp
   ```

## Use

| Command | What it does |
|---|---|
| `ttp` | Shows your recent group chats; type a number and it syncs that chat. |
| `ttp --all` | Syncs every chat that already has a playlist, no questions. |
| `ttp --chat "Music Heads"` | Syncs one chat by name. |
| `ttp --dry-run` | Shows what would be added without touching Spotify. |

Options: `--album-mode singles|first|full` (default `singles`), `--album-tracks N` (default 2),
`--since-days N` (how far back the first sync of a chat looks, default 90), `--dms` (include one-to-one
chats), `--links-only` (never send text to Claude).

Album modes: `singles` asks Claude to pick the album's best-known tracks from its real track list,
`first` takes the opening tracks, `full` adds the whole album.

## What it can and can't touch

- **Messages**: the database is opened read-only, and only the chats you pick are read.
- **Claude**: receives the text of new messages in the chosen chat. Phone numbers and emails are replaced
  with "Person A", "Person B" first. With `--links-only` nothing is sent.
- **Spotify**: the login asks for one permission, `playlist-modify-private`. The tool can create and edit
  private playlists; it cannot see your listening history, library, or profile, or control playback. It only
  ever adds tracks, and only to playlists it created.
- **On disk**: `~/.config/text-to-playlist/` holds the Spotify token, the API key, and which tracks were
  already added per chat. Files are readable only by you. Songs you delete from a playlist are not re-added.

## Tests

```bash
.venv/bin/pip install pytest && .venv/bin/pytest
```
