# Standalone patched Proton packaging

For the **no-manual-binary-upload GitHub-hosted route**, see
[SOURCE-CI.md](SOURCE-CI.md). It provides a bounded preflight and a separate,
explicit full-source candidate build; it is not an automatic release publisher.

This path **assembles already-built components**. It does not rebuild the whole
Proton project, install anything into Steam, log in to Steam, or publish a release.
The old `install-proton-hevc.sh` is a machine-specific local installer; do not run
it in CI. The new tools accept explicit paths and leave their inputs untouched.

## Pinned base

`pins.json` records the supported base:

- Proton `proton-11.0-2`, distribution version
  `1787334450 proton-11.0-2-x86_64`.
- Valve Wine base `dc26e61847081a1b5cb0733dc30feba6ee575482`.
- Vulkan XML `1.4.339`.
- Wine configured with `--enable-archs=x86_64,i386 --without-unwind`.

The version check is not a cryptographic identification of a Steam depot.
For CI, the archive SHA-256 pins the exact input bytes. Record the depot/build
information and acquisition method in the provenance. Updating the supported base
requires reviewing patches, the overlay, source obligations, and runtime tests.

## 1. Prepare the overlay

Use the patched Wine build described in the main README. Both PE architectures
and the Unix components must be available. An unrelated failed module cannot be
silently shipped as half of a replacement: complete base pairs are retained when
the corresponding replacement is unavailable; missing critical modules fail.

Write a provenance JSON file describing **what you actually built**, for example:

```json
{
  "schema_version": 1,
  "base_source": {
    "url": "https://github.com/ValveSoftware/Proton",
    "revision": "proton-11.0-2"
  },
  "wine_source": {
    "url": "https://github.com/YOUR_ACCOUNT/YOUR_SOURCE_REPO",
    "revision": "REPLACE_WITH_FULL_40_HEX_PATCHED_COMMIT"
  },
  "build": {
    "environment": "Record container image digest, compiler and dependency versions",
    "command": "Record generators, configure flags and actual make invocation"
  },
  "test_status": "Not yet tested as a packaged archive"
}
```

Do not substitute the original Wine base commit for your patched source revision.
Capture generated files, uncommitted changes and build dependencies in the source
bundle/recipe too. Provenance is a maintainer assertion, not proof of a build.

```sh
python3 tools/prepare-proton-overlay.py \
  --base '/path/to/stock/Proton 11.0' \
  --build /path/to/build-proton-wow64 \
  --provenance /path/to/build-provenance.json \
  --output /path/to/new-overlay
```

The output mirrors `files/` in the tool and includes `overlay-manifest.json`
(exact file hashes) and `build-provenance.json`. Unlike the local installer, this
does not modify the installed tool or a user's prefix. It selects real loader
binaries from the build tree, not a shell build wrapper.

## 2. Assemble a candidate locally

Prepare a `.tar.gz` containing the complete corresponding source and build
instructions for **all distributed components**, including unchanged base
components where their licences require it. Include relevant licences/notices and
the exact modified Wine tree. The packager cannot establish legal completeness;
supplying a source archive does not itself establish compliance. Review this
before any public binary release. Do not include credentials, prefixes, login
data, or the proprietary ZZZ client.

```sh
bash tools/package-proton.sh \
  --base '/path/to/stock/Proton 11.0' \
  --overlay /path/to/new-overlay \
  --provenance /path/to/new-overlay/build-provenance.json \
  --source-bundle /path/to/corresponding-source.tar.gz \
  --version 11.0-2-hevc.1 \
  --output /path/to/new-output
```

Outputs:

For a strictly local test before all upstream component sources are assembled,
add `--local-candidate`. This labels the source archive `source-snapshot-*` and
embeds `LOCAL-TEST-ONLY.txt` in the tool. Such artifacts must not be published;
the GitHub workflow deliberately does not expose this option.

- `Proton-HEVC-<version>.tar.gz`: one top-level compatibility-tool directory.
- `corresponding-source-<version>.tar.gz`: the supplied source bundle.
- `build-manifest.json`: pins, provenance, hashes and packaging metadata.
- `SHA256SUMS`: checksums for the output artifacts.

The archive has a distinct Steam tool identifier and version. Stock licences and
the runtime dependency in `toolmanifest.vdf` are preserved. Generated default
prefixes are excluded; Proton creates these on first use. SteamPipe fixup entries
that would recreate an empty default prefix are removed, and its stale marker is
omitted, so these cannot suppress Proton's first-run initialization. Escaping/dangling links
and undeclared overlay files are rejected. Existing outputs are never overwritten.
Archive ordering, ownership and timestamps are normalized for repeatability with
identical inputs (`SOURCE_DATE_EPOCH` defaults to the pinned base timestamp).

Local requirements: Linux, Python 3.12+, Bash and `objdump` (binutils) for overlay
preparation; `curl` for downloading workflow inputs. Git is used to record the
packaging recipe revision when available. Run `python3 -m unittest discover -s
tests -v` before using the tools. No test launches ZZZ or consumes cloud quota.

The archive is standalone relative to **stock Proton and your build tree**, not
relative to Steam's runtime or the GPU driver. It still requires the runtime
specified in its tool manifest and Vulkan Video HEVC support.

### Built-in launch defaults

Packaged releases need **no Steam launch options** for the HEVC path. Their
`proton` entry point sets `PROTON_USE_WINED3D=1` and
`WINE_D3D_CONFIG=renderer=vulkan` when unset, and ensures `dxgi=b` while wined3d
is enabled, replacing Gaming Mode's inherited `dxgi=n`. Unrelated DLL overrides
are preserved. The original Proton script is retained as `proton-original`.

Explicit backend/renderer settings remain possible. To disable all these defaults,
use `PROTON_HEVC_DEFAULTS=0 %command%`; this also permits deliberate native DXGI
overrides. This is a video-focused default, not a recommendation to replace DXVK
for unrelated games. The existing local installed tool is not changed by packaging.

## 3. Run GitHub Actions

After these files have been pushed to the default branch, open **Actions → Package
experimental Proton HEVC → Run workflow**. Supply a version plus HTTPS URL and
SHA-256 pairs for three archives: base, overlay, and corresponding source.

The base and overlay archives must each have one top-level directory. For example:

```sh
tar --hard-dereference -czf overlay.tar.gz -C /path/to new-overlay
sha256sum base.tar.gz overlay.tar.gz corresponding-source.tar.gz
```

Use a pristine redistributable base, not a used compatibility tool with a generated
`default_pfx`, user settings, lock files, or local backups. The download helper
rejects traversal, special files, hardlinks and unsafe symlink chains. If a base
contains generated runtime state, stage a clean copy locally before archiving it.

The workflow uses public/accessible HTTPS inputs, **not Steam credentials**. There
is currently no automated stock-Proton download source configured. Obtain the
pinned base through an authorized method and host inputs only where redistribution
is permitted. Never paste tokens or signed secret URLs into workflow inputs.

CI runs unit tests, checks input hashes, safely extracts inputs, packages, and
uploads `proton-hevc-candidate` with 14-day retention. It has read-only repository
permissions and no release-publishing step. It does not compile Wine, execute
downloaded binaries, or test hardware decode. Hosted-runner disk space may limit
large source bundles; do not omit required sources to fit the runner.

## Before publishing

1. Download the candidate, verify `SHA256SUMS`, and extract it under a **new**
   directory in `~/.local/share/Steam/compatibilitytools.d/`.
2. Restart Steam, select the versioned tool on a test shortcut, and leave launch
   options empty. Also test with inherited `WINEDLLOVERRIDES=dxgi=n` to verify the
   Gaming Mode conflict is corrected by the packaged entry point.

3. Test fresh-prefix setup, D3D11 profile/decoder creation, NV12 views, actual
   hardware decode (no silent FFmpeg software fallback), Desktop and Gaming Mode,
   audio and controller input. Do not treat archive creation as a playback test.
4. Review corresponding sources/licences and verify the package does not depend
   on the builder's paths. Record the actual GPU, driver and runtime tested.
5. Publish a GitHub Release manually with **both** binary and corresponding-source
   archives, manifest, checksums, limitations and test results. Actions artifacts
   expire and are not a substitute for durable corresponding-source availability.

ProtonPlus listing is a separate maintainer-reviewed integration after releases
exist. This workflow neither lists the tool there nor claims broad compatibility.
