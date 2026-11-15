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

# A second, tiny app that updates the tool with a double-click (no Terminal needed).
UPD="$HOME/Applications/Update Text to Playlist.app"
REPO="$PWD"
mkdir -p "$UPD/Contents/MacOS" "$HOME/.config/text-to-playlist"
cat > "$UPD/Contents/MacOS/update" <<EOF
#!/bin/bash
export PATH="/usr/local/bin:/opt/homebrew/bin:/Library/Frameworks/Python.framework/Versions/Current/bin:\$PATH"
LOG="\$HOME/.config/text-to-playlist/update.log"
if { cd "$REPO" && git pull --ff-only && ./install.sh; } > "\$LOG" 2>&1; then open "$APP"; else pbcopy < "\$LOG"; osascript -e 'display dialog "The update failed. The details are on your clipboard: paste them into your chat with Claude." buttons {"OK"} default button 1'; fi
EOF
chmod +x "$UPD/Contents/MacOS/update"
cat > "$UPD/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Update Text to Playlist</string>
  <key>CFBundleIdentifier</key><string>local.text-to-playlist.update</string>
  <key>CFBundleExecutable</key><string>update</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSUIElement</key><true/>
</dict></plist>
PLIST

echo "3/3 Done."
echo
echo "Open 'Text to Playlist' from ~/Applications (or Spotlight)."
echo "The first time, it will ask you to switch on Full Disk Access for the app."
