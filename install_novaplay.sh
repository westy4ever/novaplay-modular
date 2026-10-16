#!/bin/sh
# NovaPlay installer / updater for Enigma2 (OpenATV, OpenPLi, ...)
#
#   From the internet (latest version on GitHub):
#     wget -qO /tmp/install_novaplay.sh https://raw.githubusercontent.com/westy4ever/novaplay-modular/main/install_novaplay.sh && sh /tmp/install_novaplay.sh
#
#   From a file you copied to the box (GitHub "Download ZIP", a .tar.gz, or a folder):
#     sh install_novaplay.sh /tmp/novaplay-modular-main.zip
#
#   Add --no-restart to skip the Enigma2 restart at the end.
#
# What it does:
#   1. gets the NovaPlay files (download, or the file/folder you give it)
#   2. backs up the current NovaPlay folder, keeping your api_keys.conf
#   3. installs into  /usr/lib/enigma2/python/Plugins/Extensions/NovaPlay
#   4. if the OLD "AdvancedArabicPlayer" plugin is installed, backs it up and
#      removes it (both cannot be installed at once: they share module names).
#      Your settings, favourites, history and watch positions are carried
#      over automatically by NovaPlay on its first start.
#   5. checks every Python file compiles, then restarts Enigma2

REPO="westy4ever/novaplay-modular"
BRANCH="main"
PLUGINS="/usr/lib/enigma2/python/Plugins/Extensions"
TARGET="$PLUGINS/NovaPlay"
OLD="$PLUGINS/AdvancedArabicPlayer"
WORK="/tmp/novaplay_install"
STAMP=$(date +%Y%m%d-%H%M%S)

SOURCE=""
RESTART=1
for arg in "$@"; do
    case "$arg" in
        --no-restart) RESTART=0 ;;
        *) SOURCE="$arg" ;;
    esac
done

say()  { echo "[NovaPlay] $*"; }
fail() { echo "[NovaPlay] ERROR: $*"; rm -rf "$WORK"; exit 1; }

# backups go to the hard disk when there is one (flash space is small)
if [ -d /media/hdd ] && [ -w /media/hdd ]; then
    BACKUPS="/media/hdd/NovaPlay/backups"
else
    BACKUPS="/home/root/NovaPlay-backups"
fi

[ -d "$PLUGINS" ] || fail "$PLUGINS not found -- is this an Enigma2 box?"
command -v python3 >/dev/null 2>&1 || command -v python >/dev/null 2>&1 || fail "Python not found"
PY=python3; command -v python3 >/dev/null 2>&1 || PY=python

rm -rf "$WORK"; mkdir -p "$WORK/src" "$BACKUPS" || fail "cannot create $WORK or $BACKUPS"

# ── 1. get the files ────────────────────────────────────────────────────
if [ -z "$SOURCE" ]; then
    URL="https://github.com/$REPO/archive/refs/heads/$BRANCH.tar.gz"
    say "Downloading $URL"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL -o "$WORK/nova.tar.gz" "$URL" || fail "download failed (curl)"
    elif command -v wget >/dev/null 2>&1; then
        wget -q -O "$WORK/nova.tar.gz" "$URL" || fail "download failed (wget) -- copy the ZIP to the box and run: sh install_novaplay.sh /tmp/<file>.zip"
    else
        fail "no curl or wget -- copy the ZIP to the box and run: sh install_novaplay.sh /tmp/<file>.zip"
    fi
    SOURCE="$WORK/nova.tar.gz"
fi

if [ -d "$SOURCE" ]; then
    cp -a "$SOURCE/." "$WORK/src/" || fail "cannot copy $SOURCE"
elif [ -f "$SOURCE" ]; then
    case "$SOURCE" in
        *.zip)          command -v unzip >/dev/null 2>&1 || fail "unzip not found -- use the .tar.gz instead"
                        unzip -q "$SOURCE" -d "$WORK/src" || fail "cannot unzip $SOURCE" ;;
        *.tar.gz|*.tgz) tar -xzf "$SOURCE" -C "$WORK/src" || fail "cannot extract $SOURCE" ;;
        *)              fail "unknown file type: $SOURCE (use .zip, .tar.gz or a folder)" ;;
    esac
else
    fail "not found: $SOURCE"
fi

PLUGIN_PY=$(find "$WORK/src" -maxdepth 3 -name plugin.py -type f | head -n 1)
[ -n "$PLUGIN_PY" ] || fail "plugin.py not found in the download -- wrong file?"
SRC=$(dirname "$PLUGIN_PY")
[ -f "$SRC/extractors/registry.py" ] || fail "this doesn't look like NovaPlay (no extractors/registry.py)"

# ── 2. back up the current install, remember the keys file ─────────────
KEYS=""
if [ -d "$TARGET" ]; then
    say "Backing up current NovaPlay to $BACKUPS/NovaPlay-$STAMP.tar.gz"
    tar -czf "$BACKUPS/NovaPlay-$STAMP.tar.gz" -C "$PLUGINS" NovaPlay || fail "backup failed -- nothing changed"
    [ -f "$TARGET/api_keys.conf" ] && cp -p "$TARGET/api_keys.conf" "$WORK/api_keys.conf" && KEYS="$WORK/api_keys.conf"
fi
if [ -z "$KEYS" ] && [ -f "$OLD/api_keys.conf" ]; then
    cp -p "$OLD/api_keys.conf" "$WORK/api_keys.conf" && KEYS="$WORK/api_keys.conf"
fi

# ── 3. install ──────────────────────────────────────────────────────────
say "Installing to $TARGET"
mkdir -p "$TARGET" || fail "cannot create $TARGET"
cp -a "$SRC/." "$TARGET/" || fail "copy failed"
# documents and repo files aren't needed on the box
rm -rf "$TARGET/.git" "$TARGET/.gitignore"
rm -f "$TARGET"/*.docx "$TARGET"/*.pdf
find "$TARGET" -type d -name __pycache__ | xargs rm -rf 2>/dev/null
if [ -n "$KEYS" ]; then
    cp -p "$KEYS" "$TARGET/api_keys.conf" && chmod 600 "$TARGET/api_keys.conf"
    say "Kept your api_keys.conf"
fi

# ── 4. retire the old Advanced Arabic Player plugin ────────────────────
if [ -d "$OLD" ]; then
    say "Old AdvancedArabicPlayer found: backing it up to $BACKUPS/AdvancedArabicPlayer-$STAMP.tar.gz and removing it"
    tar -czf "$BACKUPS/AdvancedArabicPlayer-$STAMP.tar.gz" -C "$PLUGINS" AdvancedArabicPlayer \
        && rm -rf "$OLD" \
        || say "WARNING: could not back up the old plugin, so it was left in place -- remove $OLD yourself"
fi

# ── 5. check, then restart ──────────────────────────────────────────────
say "Checking the Python files compile"
BAD=""
for f in "$TARGET"/*.py "$TARGET"/extractors/*.py; do
    $PY -m py_compile "$f" 2>/dev/null || BAD="$BAD $f"
done
find "$TARGET" -type d -name __pycache__ | xargs rm -rf 2>/dev/null
if [ -n "$BAD" ]; then
    say "These files don't compile:$BAD"
    say "Not restarting. To go back: rm -rf $TARGET && tar -xzf $BACKUPS/NovaPlay-$STAMP.tar.gz -C $PLUGINS"
    rm -rf "$WORK"; exit 1
fi

rm -rf "$WORK"
say "Installed. Settings: /etc/enigma2/novaplay_state.json   Log: /tmp/novaplay.log"
say "Optional: for Cloudflare-protected sites, install curl_cffi from NovaPlay's settings (or pip3 install curl_cffi brotli)."
if [ "$RESTART" = "1" ]; then
    say "Restarting Enigma2 in 3 seconds (Ctrl+C to skip)..."
    sleep 3
    init 4; sleep 3; init 3
else
    say "Restart Enigma2 to load it:  init 4 && init 3"
fi
