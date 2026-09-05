# GitHub-hosted source CI: current release path and gates

The workflow **Source-driven Proton release candidate** has two explicit modes:

- `preflight` (default): tests, runner disk capacity, pinned SDK manifest and
  upstream tag verification. A separate job attempts anonymous Steam
  **manifest-only** retrieval, bounded to 60 seconds, without credentials.
- `build`: explicitly attempts a complete patched Proton `make redist` in the
  digest-pinned official SDK. It does not use a Steam binary base and is not the
  same operation as compiling only a Wine overlay.

No binaries are committed to Git, and no manual large input upload is required
for the source route. The output archive still has to be uploaded as an artifact.
There is no automatic public release step.

## What was inspected

- Valve's `proton-11.0-2` GitHub release has **no binary assets**. Its source
  archive does not include recursive submodule contents.
- Exact Proton commit: `db9e6ffbf24a95b104fb699dd62532c70a2f9a51`.
- Wine gitlink: `dc26e61847081a1b5cb0733dc30feba6ee575482`.
- The official x86_64 SDK is Steam Runtime **4**, not the older sniper SDK:
  `registry.gitlab.steamos.cloud/proton/steamrt4/sdk/x86_64`.
  Its verified digest and upstream tag are in `source-build-pins.json`.
  The image manifest is anonymously accessible; compressed layers total roughly
  2.34 GB. Expanded SDK plus recursive sources/build/output disk usage is unmeasured.
- The installed stock release maps to Steam app `4628710`, depot `4628711`,
  manifest `3114679013132291065`, build `24867889`. These are local provenance
  observations, not proof of anonymous entitlement. The public app metadata
  omitted depot access details. A local anonymous probe failed to connect to
  Steam, so GitHub's bounded probe distinguishes network from entitlement issues.

## Capacity

Default GitHub-hosted runner capacity may be insufficient. The script requires
60 GiB free before cloning or pulling an SDK. This is a conservative initial
guard, **not proof that the complete build fits**. It does not delete preinstalled
runner software to fabricate free space, provision paid larger runners, or
silently use a self-hosted machine. Compilation is bounded by the hosted job's
350-minute timeout; default parallelism is two jobs.

If preflight fails for disk, select a suitably provisioned runner only after
reviewing costs/capacity, or plan a split build. If anonymous manifest retrieval
succeeds, that reopens the smaller stock-plus-overlay route, but full acquisition,
SDK overlay layout, and corresponding source still require validation.

## Build and source handling

The script checks out the exact Proton commit and recursive gitlinks, validates
Wine's commit, and applies our five patches. Patch 3 excludes old generated
Vulkan files: upstream's build regenerates those against its own XML revision.
It uses the official out-of-tree configure flags and fails on build errors;
there is no `make -k` partial-build release.

Source-built output is packaged separately, **never relabeled as the exact Steam
stock version** to bypass the overlay packager's pin. It embeds our launch
defaults and retains original licensing and runtime requirements. Sources and
metadata accompany candidate artifacts. Full-source building does not resolve
all corresponding-source obligations automatically: upstream also bundles
prebuilt Mono/Gecko/Xalia/ICU and other inputs. Source completeness remains a
manual gate; snapshots are labeled accordingly rather than claimed complete.

## Promotion to a real release

1. A real hosted build must finish, with its inputs and source snapshot retained.
2. Download that exact archive and test fresh-prefix initialization, forced HEVC
   decoding, Desktop/Gaming Mode, sound and input on a supported GPU.
3. Complete the corresponding-source/licensing review, including prebuilt inputs.
4. Explicitly authorize a versioned GitHub Release with immutable checksums and
   both binary and durable corresponding-source assets. CI artifacts expire.

The workflow token has `contents: read`. It cannot publish a release, and a green
preflight alone must never be described as a successful build or playback test.

Primary references:
- https://github.com/ValveSoftware/Proton/tree/proton-11.0-2
- https://github.com/ValveSoftware/Proton/releases/tag/proton-11.0-2
- https://github.com/SteamRE/DepotDownloader/tree/DepotDownloader_3.4.0
