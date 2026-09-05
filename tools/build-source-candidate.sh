#!/usr/bin/env bash
# Manual source-driven candidate build: no Steam distribution input or install.
set -Eeuo pipefail
usage() {
    cat <<'EOF'
Usage: WORK_DIR=/new/empty/path BUILD_VERSION=11.0-2-hevc.1 bash tools/build-source-candidate.sh preflight|build
  WORK_DIR       Required new or empty directory; failed work is never deleted.
  BUILD_VERSION  Required: [A-Za-z0-9][A-Za-z0-9._-]{0,63}.
  JOBS           1..16 (default 2). MIN_FREE_GIB: positive integer (default 60).
  preflight      Check tools, Docker daemon, disk, upstream tag and SDK manifest;
                 no source clone, SDK pull, or WORK_DIR writes.
  build          Fetch pinned sources, apply patches, configure/make redist, package.
  --help         Offline: no environment requirements or network requests.
The SDK is about 2.34 GB compressed, NOT its installed size. Total source/submodule,
SDK, build and archive space is unmeasured; 60 GiB is NOT a guarantee. Check the
Docker storage filesystem separately. Sources are the largest uncertain risk.
Use a dedicated Docker daemon/runner; no paid runner is provisioned. Build-local
Cargo/Docker configuration is used, with no shared compiler cache or secrets.
EOF
}
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
[[ $# == 1 ]] || { usage >&2; exit 2; }
case "$1" in --help|-h) usage; exit 0;; preflight|build) mode=$1;; *) usage >&2; exit 2;; esac
[[ ${BUILD_VERSION:-} =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || die 'invalid BUILD_VERSION'
[[ -n ${WORK_DIR:-} ]] || die 'WORK_DIR is required'
JOBS=${JOBS:-2}; MIN_FREE_GIB=${MIN_FREE_GIB:-60}
[[ $JOBS =~ ^([1-9]|1[0-6])$ ]] || die 'JOBS must be 1..16'
[[ $MIN_FREE_GIB =~ ^[1-9][0-9]{0,5}$ ]] || die 'MIN_FREE_GIB must be 1..999999'
for tool in python3 git docker make df awk tee cp mkdir grep dirname cat; do
    command -v "$tool" >/dev/null || die "missing tool: $tool"
done
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
PINS=$ROOT/packaging/source-build-pins.json
# Resolve before any writes; upstream make does not safely support special paths.
WORK_DIR=$(python3 - "$WORK_DIR" <<'PY'
import pathlib, re, sys
p = pathlib.Path(sys.argv[1]).expanduser().resolve()
if not re.fullmatch(r"/[A-Za-z0-9_./-]+", str(p)):
    sys.exit("WORK_DIR must resolve to an absolute path without spaces or shell/make metacharacters")
if p.exists() and (not p.is_dir() or any(p.iterdir())):
    sys.exit("WORK_DIR must be new or empty (including hidden files)")
print(p)
PY
)
pin() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$PINS" "$1"; }
PROTON_URL=$(pin proton_repository); PROTON_COMMIT=$(pin proton_commit)
PROTON_TAG=$(pin proton_tag); WINE_COMMIT=$(pin wine_base_commit); SDK_IMAGE=$(pin sdk_image)
SOURCE_DATE_EPOCH=$(pin source_date_epoch); export SOURCE_DATE_EPOCH
disk_parent=$WORK_DIR
while [[ ! -d $disk_parent ]]; do disk_parent=${disk_parent%/*}; [[ -n $disk_parent ]] || disk_parent=/; done
df -h -- "$disk_parent"
python3 - "$disk_parent" "$MIN_FREE_GIB" <<'PY'
import shutil, sys
free = shutil.disk_usage(sys.argv[1]).free / 2**30
print(f"WORK_DIR filesystem: {free:.1f} GiB available; guard: {sys.argv[2]} GiB")
if free < int(sys.argv[2]):
    sys.exit("Insufficient disk space; no sources or image downloaded")
PY
printf 'SDK: %s\nCompressed ~2.34 GB; total build footprint UNKNOWN, guard is not a guarantee.\n' "$SDK_IMAGE"
if [[ $mode == build ]]; then
    mkdir -p -- "$WORK_DIR/logs" "$WORK_DIR/metadata" "$WORK_DIR/docker-config" "$WORK_DIR/cargo-cache"
    exec > >(tee "$WORK_DIR/logs/build.log") 2>&1
    trap 'rc=$?; printf "Failed (status %s, line %s); work and logs retained at %s\n" "$rc" "$LINENO" "$WORK_DIR" >&2; exit "$rc"' ERR
    export DOCKER_CONFIG=$WORK_DIR/docker-config CARGO_HOME=$WORK_DIR/cargo-cache
fi
export GIT_TERMINAL_PROMPT=0
git --version; make --version; python3 --version; docker version
docker info --format 'Docker storage root: {{.DockerRootDir}} (check free space on daemon host)'
refs=$(git ls-remote --exit-code "$PROTON_URL" "refs/tags/$PROTON_TAG" "refs/tags/$PROTON_TAG^{}")
remote_commit=$(printf '%s\n' "$refs" | awk '$2 ~ /\^\{\}$/ { peeled=$1 } END { print peeled ? peeled : $1 }')
[[ $remote_commit == "$PROTON_COMMIT" ]] || die 'upstream tag does not match pinned Proton commit'
if [[ $mode == preflight ]]; then
    docker manifest inspect "$SDK_IMAGE" >/dev/null
    printf 'Preflight passed; no sources cloned or SDK image pulled.\n'
    exit 0
fi
META=$WORK_DIR/metadata
cp -- "$PINS" "$META/source-build-pins.json"
cp -- "${BASH_SOURCE[0]}" "$META/build-source-candidate.sh"
docker manifest inspect "$SDK_IMAGE" > "$META/sdk-manifest.json"
printf '%s\n' "$SDK_IMAGE" > "$META/sdk-image.txt"
printf '#!/usr/bin/env bash\nset -euo pipefail\n' > "$META/commands.sh"
run() { printf '%q ' "$@" >> "$META/commands.sh"; printf '\n' >> "$META/commands.sh"; "$@"; }
printf 'export SOURCE_DATE_EPOCH=%q CARGO_HOME=%q DOCKER_CONFIG=%q\n' \
    "$SOURCE_DATE_EPOCH" "$CARGO_HOME" "$DOCKER_CONFIG" >> "$META/commands.sh"
run git init "$WORK_DIR/proton"
run git -C "$WORK_DIR/proton" remote add origin "$PROTON_URL"
run git -C "$WORK_DIR/proton" fetch --depth 1 origin "$PROTON_COMMIT"
run git -C "$WORK_DIR/proton" checkout --detach FETCH_HEAD
[[ $(git -C "$WORK_DIR/proton" rev-parse HEAD) == "$PROTON_COMMIT" ]] || die 'wrong Proton checkout'
# Preserve the upstream tag for Proton's own git describe; do not create commits.
run git -C "$WORK_DIR/proton" fetch --depth 1 origin "refs/tags/$PROTON_TAG:refs/tags/$PROTON_TAG"
[[ $(git -C "$WORK_DIR/proton" rev-parse "$PROTON_TAG^{commit}") == "$PROTON_COMMIT" ]] || die 'tag changed during fetch'
[[ $(git -C "$WORK_DIR/proton" ls-tree HEAD wine) == "160000 commit $WINE_COMMIT"$'\t'"wine" ]] || die 'unexpected Wine gitlink'
run git -C "$WORK_DIR/proton" submodule update --init --recursive --depth 1 --jobs "$JOBS"
[[ $(git -C "$WORK_DIR/proton/wine" rev-parse HEAD) == "$WINE_COMMIT" ]] || die 'wrong Wine checkout'
git -C "$WORK_DIR/proton" submodule status --recursive > "$META/gitlinks.txt"
if grep -q '^[+-U]' "$META/gitlinks.txt"; then die 'uninitialized or mismatched submodule'; fi
mkdir -- "$META/patches"
for number in 0001 0002 0003 0004 0005; do
    patches=("$ROOT/patches/$number"-*.patch)
    [[ ${#patches[@]} == 1 && -f ${patches[0]} ]] || die "expected one patch $number"
    cp -- "${patches[0]}" "$META/patches/"
    patch=$META/patches/${patches[0]##*/}
    exclusions=()
    if [[ $number == 0003 ]]; then
        exclusions=(--exclude=include/wine/vulkan.h --exclude=dlls/winevulkan/vulkan_thunks.c)
    fi
    run git -C "$WORK_DIR/proton/wine" apply --check "${exclusions[@]}" "$patch"
    run git -C "$WORK_DIR/proton/wine" apply "${exclusions[@]}" "$patch"
done
git -C "$WORK_DIR/proton/wine" diff --binary > "$META/wine.diff"
git -C "$WORK_DIR/proton" diff --binary --submodule=diff > "$META/proton.diff"
# Proton's generator uses its own pinned Vulkan XML 1.4.339 in the build tree.
# Do not import MAKEFLAGS (-k/-j overrides), shared caches or Docker mount options.
unset MAKEFLAGS MFLAGS MAKEOVERRIDES DOCKER_OPTS ENABLE_CCACHE
for variable in ${!CCACHE_@}; do unset "$variable"; done
printf 'unset MAKEFLAGS MFLAGS MAKEOVERRIDES DOCKER_OPTS ENABLE_CCACHE\nfor variable in ${!CCACHE_@}; do unset "$variable"; done\n' >> "$META/commands.sh"
run mkdir "$WORK_DIR/build"
printf 'cd %q\n' "$WORK_DIR/build" >> "$META/commands.sh"
(
    cd -- "$WORK_DIR/build"
    run "$WORK_DIR/proton/configure.sh" --build-name "Proton-HEVC-$BUILD_VERSION" \
        --target-arch x86_64 --container-engine docker --proton-sdk-image "$SDK_IMAGE"
    run make -j "$JOBS" redist
)
docker image inspect --format '{{json .RepoDigests}}' "$SDK_IMAGE" > "$META/sdk-repodigests.json"
# The packager supplies the user launch wrapper; source/proton remains upstream.
run python3 "$ROOT/tools/package-source-candidate.py" --source-dir "$WORK_DIR/proton" \
    --build-dir "$WORK_DIR/build" --version "$BUILD_VERSION" --output "$WORK_DIR/output" --sdk-image "$SDK_IMAGE"
printf 'Candidate output: %s/output (not installed or published)\n' "$WORK_DIR"
