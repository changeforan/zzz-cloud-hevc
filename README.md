# HEVC hardware decode for Wine / Proton (D3D11 video)

Adds **HEVC Main** hardware decode to Wine's `wined3d` over Vulkan Video, so
Direct3D 11 applications that require `D3D11_DECODER_PROFILE_HEVC_VLD_MAIN` can
run. Wine 11 ships D3D11 decode over Vulkan Video for **H.264 only**; this adds
HEVC alongside it, plus four supporting fixes.

Written to get **Zenless Zone Zero Cloud** running under Proton on a Steam Deck
(AMD 780M / RADV), but nothing here is game-specific — it is ordinary wined3d
and d3d11 work.

Status: HEVC decode is bit-exact against software decode on a six-stream matrix,
H.264 is unregressed, and the target application runs with working audio and
gamepad input under a patched Proton 11.0.

## What's here

```
patches/      the five commits, as git-format-patch files
install-proton-hevc.sh   build a "Proton 11.0 (HEVC)" compatibility tool
tools/vidprobe.c         list D3D11 decoder profiles and try to create each
tools/nv12srv.c          minimal repro: NV12 plane shader-resource views
tools/play-zzz.sh        launcher for a plain (non-Proton) Wine build
```

Live branches with the same commits applied:

| branch | base |
|---|---|
| [`hevc-decode`](https://github.com/changeforan/wine/tree/hevc-decode) | `wine-11.0` |
| [`hevc-11.16`](https://github.com/changeforan/wine/tree/hevc-11.16) | `wine-11.16` |

## The patches

| # | commit | why |
|---|---|---|
| 1 | `wined3d: Use the UNORM variants as the canonical NV12 plane formats.` | `format_plane_info[]` declared NV12's planes as `R8_UINT`/`R8G8_UINT`. D3D11 uses the `_UNORM` variants for plane views, and Vulkan *requires* `VK_FORMAT_R8_UNORM` for a `PLANE_0` view of `G8_B8R8_2PLANE_420_UNORM`. Without it `CreateShaderResourceView` on an NV12 plane returns `E_INVALIDARG`. |
| 2 | `wined3d: Increase WINED3D_DECODER_MAX_PROFILE_COUNT.` | The profile array is stack-allocated by callers of `get_profiles()`; a bound of 1 is only safe while exactly one codec exists. Reporting a second profile smashes the caller's stack. |
| 3 | `winevulkan: Enable VK_KHR_video_decode_h265.` | The extension is in `UNSUPPORTED_EXTENSIONS`. It defines no new commands — only structs and enums — so no extra thunking is needed. |
| 4 | `d3d11: Implement CheckVideoDecoderFormat() and the decoder config getters.` | All three were `E_NOTIMPL`, so the profile stayed invisible to applications and FFmpeg's d3d11va silently fell back to software. |
| 5 | `wined3d: Implement HEVC Main decoding over Vulkan Video.` | Translates `DXVA_PicParams_HEVC` into `StdVideoH265` VPS/SPS/PPS and submits slices via `vkCmdDecodeVideoKHR`. |

Two details in patch 5 worth knowing if you touch it:

* DXVA's `RefPicSetStCurrBefore` / `…StCurrAfter` / `…LtCurr` hold indices into
  `RefPicList`, whereas Vulkan wants **DPB slot indices**. They share names and
  bounds with the Vulkan fields but live in a different index space, so they are
  remapped while walking the reference list. The `0xff` "absent" sentinel must
  survive the remap. (The scaling lists in the same struct genuinely do match, and
  are `memcpy`'d.)
* The quantisation-matrix buffer is sized per codec: `DXVA_Qmatrix_HEVC` is 1000
  bytes against 224 for H.264. Getting this wrong only breaks streams that
  actually carry scaling lists.

### Upstream status

Patches 2–5 still apply to Wine 11.16 and nothing equivalent has landed upstream.

Patch 1 needs rewriting before submission. Upstream fixed the *symptom* differently
in `view.c` — `94ccf1b8` "wined3d: Allow any typed format for planar texture views"
relaxed `find_format_plane_idx()` to compare `typeless_id`, so `R8_UNORM` and
`R8_UINT` both match and the `E_INVALIDARG` is gone. But `utils.c` still names the
`_UINT` variants, and those formats also drive **capability derivation**, so NV12
is still reported as non-blendable:

```
CheckFormatSupport(NV12)  upstream: 0x8013a0   with patch 1: 0x80b3a0
                                                       ^^ BLENDABLE | MIP_AUTOGEN
```

So on master the justification is capability derivation, not view rejection — and
the patch should probably also mask `GEN_MIPMAP` out of the derived planar caps,
which this version does not.

## Building for Proton

`install-proton-hevc.sh` builds a **separate** compatibility tool and leaves stock
Proton untouched. Read its header comment before running — it documents the build.

```sh
git clone --depth 1 --branch proton_11.0 \
    https://github.com/ValveSoftware/wine.git proton-wine
cd proton-wine
git am /path/to/patches/000{1,2,4,5}-*.patch
git apply --exclude=include/wine/vulkan.h \
          --exclude=dlls/winevulkan/vulkan_thunks.c patches/0003-*.patch

./tools/make_specfiles && ./tools/make_requests && autoreconf -f -i

# regenerate the Vulkan headers against the revision this Proton pins
cd dlls/winevulkan && python3 ./make_vulkan \
    --xml vk-1.4.339.xml --video-xml video-1.4.339.xml && cd ../..

mkdir ../build && cd ../build
../proton-wine/configure --enable-archs=x86_64,i386 --without-unwind --disable-tests
make -k -j$(nproc)
```

Then run `install-proton-hevc.sh`, restart Steam, select **Proton 11.0 (HEVC)**
and set launch options:

```
PROTON_USE_WINED3D=1 WINE_D3D_CONFIG=renderer=vulkan WINEDLLOVERRIDES=dxgi=b %command%
```

All three matter:

* `PROTON_USE_WINED3D=1` selects wined3d over DXVK. On its own it leaves wined3d on
  the **OpenGL** renderer, where no decoder exists.
* `WINE_D3D_CONFIG=renderer=vulkan` switches it to the Vulkan renderer, where the
  decoder lives.
* `WINEDLLOVERRIDES=dxgi=b` forces Wine's **builtin** dxgi. Without it the app dies
  in Steam's Gaming Mode — see below.

Pin the wine revision to the one your Proton actually ships:

```sh
gh api repos/ValveSoftware/Proton/contents/wine?ref=proton-11.0-2 --jq .sha
# -> dc26e61847081a1b5cb0733dc30feba6ee575482
```

### Six things that will bite you

1. **The Vulkan extension gate is in the PE half of winevulkan.** `loader.c`
   filters the driver's extension list through `is_device_extension_supported()`
   (and again in `vkCreateDevice`), driven by the generated
   `ALL_VK_CLIENT_DEVICE_EXTS`. A blocklisted extension is invisible to wined3d no
   matter what the driver reports — patching wined3d alone gets you nothing.
2. **A PE `.dll` and its unix `.so` are one module split in half, and the halves
   are not interchangeable between builds** — not even builds of identical source.
   Mixing them gives `Exception c0000005 in Unix call` or a failed `UNIX_CALL`
   assertion. Replace **both halves or neither**. This also binds `wine` to
   `wineserver` (they speak the regenerated `server_protocol.h`).
3. **`--without-unwind` is mandatory.** Steam runs Proton inside the Steam Linux
   Runtime container, which has no libunwind. Otherwise `ntdll.so` picks up
   `libunwind.so.8` and dies with `wine: could not load ntdll.so` — *while working
   fine* if you run the `proton` script directly on the host. Test the real path:
   ```sh
   SteamLinuxRuntime_4/_v2-entry-point --verb=run -- .../proton run APP.exe
   ```
   To find similar problems, diff `DT_NEEDED` of each unix `.so` you built against
   Valve's; anything yours needs that the runtime lacks is fatal.
4. **Proton builds WoW64** (`--enable-archs=x86_64,i386`). A `--enable-win64` build
   has no 32-bit loader and fails with `/lib/ld-linux.so.2: could not open`.
5. **Proton stages the D3D DLLs from `files/share/default_pfx/`**, not from
   `files/lib/wine/`. A prefix left untouched after a launch attempt means startup
   died before staging.
6. **Steam's Gaming Mode injects `WINEDLLOVERRIDES=dxgi=n`** (native-only dxgi).
   There is no native `dxgi.dll` in the prefix, so Wine's builtin is refused, the
   `d3d11.dll -> dxgi.dll` import cannot resolve, and the application dies during
   module loading — before `main`, so it writes no log of its own:
   ```
   err:module:import_dll  dxgi.dll (needed by "C:\windows\system32\d3d11.dll") not found
   err:module:loader_init Importing dlls for "App.exe" failed, status c0000135
   ```
   Symptom: works in Desktop Mode, dies after ~3 seconds in Gaming Mode. **Fix:
   `WINEDLLOVERRIDES=dxgi=b` in the launch options.** This affects *any*
   `PROTON_USE_WINED3D=1` title, not just video-decode ones — wined3d needs the
   builtin dxgi, and that override demands a native one. The variable comes from
   the Gaming Mode session itself; it is not in `shortcuts.vdf`, `config.vdf`, or
   `user_settings.py`. Note `PROTON_LOG=1` is what makes this diagnosable: the
   header prints `System WINEDLLOVERRIDES` before anything else happens.

Wine's git tree also needs `tools/make_specfiles`, `tools/make_requests` and
`autoreconf -f -i` before `configure` — none of that generated output is committed.

## Verifying

`tools/vidprobe.c` lists the decoder profiles and tries to create each one:

```
GetVideoDecoderProfileCount -> 2
  [0] {1b81be68-...}  H264_VLD_NoFGT     supported YES   CREATED
  [1] {5b11d51b-...}  HEVC_VLD_Main      supported YES   CREATED
```

`tools/nv12srv.c` is the minimal repro for patch 1 — it creates an NV12 texture and
an `R8_UNORM` / `R8G8_UNORM` shader-resource view per plane. `0x80070057` before the
patch, `OK` after. Both build with mingw-w64:

```sh
x86_64-w64-mingw32-gcc -o vidprobe.exe vidprobe.c -ld3d11 -ldxgi -lole32 -luuid
```

For decode correctness, compare hardware against software output with FFmpeg —
they should be byte-identical:

```sh
wine ffmpeg.exe -y -hwaccel d3d11va -i in.h265 -f rawvideo -pix_fmt yuv420p hw.yuv
wine ffmpeg.exe -y              -i in.h265 -f rawvideo -pix_fmt yuv420p sw.yuv
md5sum hw.yuv sw.yuv
```

Cover B-pyramid references, scaling lists, and non-multiple-of-64 dimensions —
a decoder can pass simple streams and still be wrong.

## Not implemented

HEVC Main10, AV1, and encode. `wined3d` reports `supported 0` for those, which is
honest — the DXVA→Vulkan translation for them is not written.

## Licence

The patches are modifications to Wine and are LGPL-2.1-or-later, like Wine itself.
The helper scripts and test programs are trivial; treat them as public domain.
