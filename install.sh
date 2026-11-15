#!/bin/bash
# Installs or updates Text to Playlist on a Mac. Safe to run again after `git pull`.
set -euo pipefail
cd "$(dirname "$0")"

[ "$(uname)" = "Darwin" ] || { echo "This installer is for macOS."; exit 1; }

python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null || {
  echo "Python 3.10 or newer is needed. Install it from https://www.python.org/downloads/ ,"
  echo "then close this Terminal window, open a new one, and run ./install.sh again."
  exit 1
}

SHARE="$HOME/.local/share/text-to-playlist"
APP="$HOME/Applications/Text to Playlist.app"

echo "1/3 Installing the tool into $SHARE"
mkdir -p "$SHARE"
python3 -m venv "$SHARE/venv"
"$SHARE/venv/bin/pip" install --quiet --upgrade pip
"$SHARE/venv/bin/pip" install --quiet --upgrade --force-reinstall --no-deps .
"$SHARE/venv/bin/pip" install --quiet .

echo "2/3 Building the app at $APP"
mkdir -p "$APP/Contents/MacOS"
cc -O2 -o "$APP/Contents/MacOS/TextToPlaylist" macos/launcher.c
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Text to Playlist</string>
  <key>CFBundleIdentifier</key><string>local.text-to-playlist</string>
  <key>CFBundleExecutable</key><string>TextToPlaylist</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSUIElement</key><true/>
</dict></plist>
PLIST
codesign --force --sign - "$APP"

echo "3/3 Done."
echo
echo "Open 'Text to Playlist' from ~/Applications (or Spotlight)."
echo "The first time, it will ask you to switch on Full Disk Access for the app."
