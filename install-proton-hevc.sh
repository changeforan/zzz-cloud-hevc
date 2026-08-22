#!/bin/bash
# Rebuild the "Proton 11.0 (HEVC)" compat tool from stock Proton 11.0 plus our
# HEVC-enabled wine build.
#
# Why it works this way, learned the hard way:
#
#   * The Vulkan extension gate lives in the PE side of winevulkan
#     (is_device_extension_supported() -> ALL_VK_CLIENT_DEVICE_EXTS, generated
#     from make_vulkan).  Proton blocklists VK_KHR_video_decode_h265, so
#     without rebuilding winevulkan the extension is invisible to wined3d and
#     wined3d reports only the H.264 profile.
#
#   * A PE dll and its unix .so are ONE module split in half.  They are not
#     interchangeable between builds - even builds of identical source.  Mixing
#     them gives "Exception c0000005 in Unix call" or a failed UNIX_CALL
#     assertion.  So for every module we replace, BOTH halves must be ours, and
#     for every module we cannot build, BOTH halves must stay Valve's.
#
#   * Proton builds wine in WoW64 mode (--enable-archs), not --enable-win64.
#     A 64-bit-only build has no 32-bit loader and dies with
#     "/lib/ld-linux.so.2: could not open".
#
#   * --without-unwind is MANDATORY.  Steam launches Proton inside the Steam
#     Linux Runtime container, which has no libunwind.  An Arch-container build
#     links ntdll.so against libunwind.so.8 and dies inside the runtime with
#     "wine: could not load ntdll.so: libunwind.so.8: cannot open shared object
#     file" - while working fine when the proton script is run on the host.
#     Always test through the runtime, not just on the host:
#       SteamLinuxRuntime_4/_v2-entry-point --verb=run -- .../proton run APP.exe
#     To find other such deps, diff DT_NEEDED of each of our unix .so against
#     Valve's; anything present in ours but absent from the runtime is fatal.
#
# Build the tree first:
#   cd ~/wine-hevc && git clone --depth 1 --branch proton_11.0 \
#       https://github.com/ValveSoftware/wine.git proton-wine
#   cd proton-wine && ./tools/make_specfiles && ./tools/make_requests \
#       && autoreconf -f -i
#   # apply ~/wine-hevc/hevcpatches/*.patch, then regenerate the vulkan files:
#   cd dlls/winevulkan && python3 ./make_vulkan \
#       --xml ~/wine-hevc/vkxml/vk-1.4.339.xml \
#       --video-xml ~/wine-hevc/vkxml/video-1.4.339.xml
#   mkdir ~/wine-hevc/build-proton-wow64 && cd $_ \
#       && ../proton-wine/configure --enable-archs=x86_64,i386 --without-unwind \
#              --disable-tests \
#       && make -k -j$(nproc)

set -u

STOCK="/home/deck/.local/share/Steam/steamapps/common/Proton 11.0"
TOOL="/home/deck/.local/share/Steam/compatibilitytools.d/Proton-11.0-HEVC"
BUILD=/home/deck/wine-hevc/build-proton-wow64

[ -d "$STOCK" ] || { echo "stock Proton not found: $STOCK"; exit 1; }
[ -d "$BUILD" ] || { echo "build tree not found: $BUILD"; exit 1; }

echo "==> recreating $TOOL from stock"
chmod -R u+w "$TOOL" 2>/dev/null
rm -rf "$TOOL"
cp -a --reflink=auto "$STOCK" "$TOOL"
chmod -R u+w "$TOOL/files"

cat > "$TOOL/compatibilitytool.vdf" <<'EOF'
"compatibilitytools"
{
  "compat_tools"
  {
    "Proton-11.0-HEVC"
    {
      "display_name" "Proton 11.0 (HEVC)"
      "install_path" "."
      "from_oslist"  "windows"
      "to_oslist"    "linux"
    }
  }
}
EOF

# A module is replaced only if we can supply BOTH halves (or it has no unix
# half at all).  Anything else keeps Valve's pair intact.
have_unix() { find "$BUILD/dlls" -name "$1.so" \
    -not -path "*/x86_64-windows/*" -not -path "*/i386-windows/*" 2>/dev/null | head -1; }

echo "==> replacing unix modules"
n_unix=0
for f in "$STOCK/files/lib/wine/x86_64-unix"/*.so; do
    b=$(basename "$f" .so)
    src=$(have_unix "$b")
    [ -n "$src" ] || continue                      # Valve-only: leave alone
    cp "$src" "$TOOL/files/lib/wine/x86_64-unix/$b.so" && n_unix=$((n_unix+1))
done
echo "    $n_unix unix modules"

echo "==> replacing PE modules (skipping any whose unix half we lack)"
n_pe=0
for arch in x86_64-windows i386-windows; do
    for f in "$TOOL/files/lib/wine/$arch"/*; do
        b=$(basename "$f")
        stem="${b%.*}"
        # if Valve ships a unix half we cannot build, keep Valve's PE too
        if [ -f "$STOCK/files/lib/wine/x86_64-unix/$stem.so" ] && [ -z "$(have_unix "$stem")" ]; then
            continue
        fi
        src="$BUILD/dlls/$stem/$arch/$b"
        [ -f "$src" ] || src=$(find "$BUILD/dlls" -path "*/$arch/$b" 2>/dev/null | head -1)
        [ -n "$src" ] && [ -f "$src" ] || continue
        cp "$src" "$f" && n_pe=$((n_pe+1))
    done
done
echo "    $n_pe PE modules"

# wine and wineserver speak the generated server protocol to each other, so
# both must come from the same build - tools/make_requests regenerates
# include/wine/server_protocol.h and a stale wineserver will not match.
echo "==> replacing loader binaries"
for b in wine wineserver; do
    src="$BUILD/$b"
    [ -f "$src" ] || src="$BUILD/server/$b"
    [ -f "$src" ] || { echo "    WARNING: $b not found in build tree"; continue; }
    cp "$src" "$TOOL/files/bin/$b" && echo "    $b"
done

echo
echo "Done.  Restart Steam, then set the game's compatibility tool to"
echo "'Proton 11.0 (HEVC)' and add these launch options:"
echo
echo "    PROTON_USE_WINED3D=1 WINE_D3D_CONFIG=renderer=vulkan WINEDLLOVERRIDES=dxgi=b %command%"
echo
echo "dxgi=b is required: Steam's Gaming Mode injects WINEDLLOVERRIDES=dxgi=n,"
echo "which refuses Wine's builtin dxgi and kills the app during module load"
echo "(status c0000135) - Desktop Mode is unaffected, so it looks like a"
echo "Gaming-Mode-only crash a few seconds after launch."
