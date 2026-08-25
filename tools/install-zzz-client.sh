#!/bin/bash
# Install Zenless Zone Zero Cloud straight into a normal folder.
#
# The obvious route - add the installer to Steam, run it, then add the installed
# client as a second shortcut - puts the game *inside* a Proton prefix
# (compatdata/<appid>/pfx/drive_c/users/steamuser/AppData/Local/Programs/...).
# That is fragile: deleting the installer's Steam entry deletes its prefix, and
# the game goes with it.  It also leaves the client permanently split across two
# prefixes - the exe in the installer's, the environment in the client's.
#
# The client's installer is NSIS, so it accepts the standard switches:
#     /S            silent
#     /D=<path>     install directory - must be LAST, and unquoted
#
# Wine's Z: drive maps the Unix filesystem root, so /D=Z:\home\deck\Games\...
# lands the files directly in a normal directory.  No Steam involved, and the
# prefix used here is a throwaway - the game never lives in it.
#
# Verified: the result is byte-identical to a Steam-installed client
# (exe b43b550967a6, media_engine_probe 93977f51, media_engine_receiver 7bbb65a8).

set -u

INSTALLER="${1:-/home/deck/Downloads/ZenlessZoneZeroCloud_pc_3.1.exe}"
TARGET="${2:-/home/deck/Games/ZenlessZoneZeroCloud}"
TOOL="${PROTON_TOOL:-/home/deck/.local/share/Steam/compatibilitytools.d/Proton-11.0-HEVC}"
SCRATCH="${SCRATCH_PREFIX:-/home/deck/wine-hevc/installer-prefix}"

[ -f "$INSTALLER" ] || { echo "installer not found: $INSTALLER"; exit 1; }
[ -x "$TOOL/files/bin/wine" ] || { echo "wine not found: $TOOL/files/bin/wine"; exit 1; }
case "$TARGET" in
    *[[:space:]]*) echo "target path must not contain spaces (NSIS /D= limitation): $TARGET"; exit 1 ;;
esac

if [ -e "$TARGET" ]; then
    echo "target already exists: $TARGET"
    echo "remove it first, or pass a different path as argument 2."
    exit 1
fi

# Z:\ is Wine's mapping of /, so a Unix path becomes a Windows one by
# prefixing Z: and flipping the slashes.
WINPATH="Z:$(echo "$TARGET" | tr '/' '\\')"

echo "installer : $INSTALLER"
echo "target    : $TARGET"
echo "  as Windows path: $WINPATH"
echo "scratch pfx: $SCRATCH  (throwaway - the game does not live here)"
echo

mkdir -p "$(dirname "$TARGET")" "$SCRATCH"

echo "==> running installer silently"
WINEPREFIX="$SCRATCH" DISPLAY="${DISPLAY:-:0}" WINEDEBUG=-all \
    "$TOOL/files/bin/wine" "$INSTALLER" /S /D=$WINPATH
rc=$?
echo "    installer exit: $rc"

EXE="$TARGET/Zenless Zone Zero Cloud.exe"
if [ ! -f "$EXE" ]; then
    echo "FAILED: $EXE was not created."
    echo "The installer may have ignored /D=.  Check $SCRATCH for where it went:"
    find "$SCRATCH" -name "Zenless Zone Zero Cloud.exe" 2>/dev/null | head -3
    exit 1
fi

echo
echo "Installed $(ls "$TARGET" | wc -l) entries, $(du -sh "$TARGET" | cut -f1)"
echo
echo "Next: add ONE non-Steam shortcut pointing at"
echo "    $EXE"
echo "set its compatibility tool to 'Proton 11.0 (HEVC)', and use launch options:"
echo
echo "    PROTON_USE_WINED3D=1 WINE_D3D_CONFIG=renderer=vulkan WINEDLLOVERRIDES=dxgi=b %command%"
echo
echo "The Steam prefix then holds only the Windows environment and your login;"
echo "the game itself stays in $TARGET and survives any prefix being deleted."
echo
echo "The scratch prefix can be removed:  rm -rf $SCRATCH"
