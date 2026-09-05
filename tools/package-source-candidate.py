#!/usr/bin/env python3
"""Package official make redist output for LOCAL TESTING, never release approval."""

import argparse
import ctypes
import gzip
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile
import tempfile

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("proton_helpers", REPO / "tools/package-proton.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
NOTICE = ("LOCAL-TEST-ONLY / NOT RELEASE APPROVED\n"
          "Source snapshot: INCOMPLETE / REVIEW REQUIRED. Not asserted to be complete\n"
          "corresponding source. Upstream includes downloaded/prebuilt xalia, Wine Mono,\n"
          "Gecko, ICU, OpenVR and other dependencies. Cargo/SDK sources and licensing\n"
          "need review. Runtime, ABI, HEVC and full-build validation are unverified.\n")
CACHES = {".git", "__pycache__", ".cache", "autom4te.cache"}


def git(root, *args):
    # Never run make, source scripts, hooks, external diff or textconv here.
    return subprocess.run(["git", "--no-optional-locks", "-C", str(root), *args],
                          check=True, capture_output=True, text=True).stdout


def revisions(source, pins):
    for root, key in ((source, "proton_commit"), (source / "wine", "wine_base_commit")):
        p.require(git(root, "rev-parse", "HEAD").strip() == pins[key], f"HEAD differs from {key} pin")
    modules = git(source, "submodule", "status", "--recursive")
    p.require(modules.strip() and all(line.startswith(" ") for line in modules.splitlines()),
              "Recursive submodules must be initialized and match pinned gitlinks")
    roots = ["."]
    for line in modules.splitlines():
        match = re.fullmatch(r" [0-9a-f]{40} (.+?)(?: \(.*\))?", line)
        p.require(match, "Invalid submodule status")
        name = match[1]
        p.safe_name(name)
        p.require((source / name).resolve().is_relative_to(source), "Submodule escapes source")
        roots.append(name)
    result = {}
    for name in roots:
        root = source / name
        dirty = git(root, "diff", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all", "HEAD", "--name-only")
        p.require(not dirty or name == "wine", f"Unexpected tracked changes outside Wine: {name}")
        result[name] = {"head": git(root, "rev-parse", "HEAD").strip(),
                        "status": git(root, "status", "--porcelain", "--untracked-files=all"),
                        "diff": git(root, "diff", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all", "--binary", "HEAD")}
    return {"submodule_status": modules, "repositories": result,
            "wine_changes": "Captured, not independently certified as the intended HEVC patch set"}


def source_entries(root, *, omit_top_build=False):
    """Keep nested build/ directories (often source); omit only top-level build/."""
    p.require(root.is_dir() and not root.is_symlink(), f"Expected source directory: {root}")
    def walk(directory):
        for path in sorted(directory.iterdir()):
            name = path.relative_to(root).as_posix()
            if path.name in CACHES or (omit_top_build and name == "build"):
                continue
            p.safe_name(name)
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                target = os.readlink(path)
                lexical = Path(os.path.abspath(path.parent / target))
                p.require(not os.path.isabs(target) and lexical.is_relative_to(root)
                          and path.resolve(strict=True).is_relative_to(root), f"Unsafe source symlink: {name}")
            else:
                p.require(stat.S_ISDIR(mode) or stat.S_ISREG(mode), f"Unsupported source file: {name}")
            yield name, path
            if stat.S_ISDIR(mode):
                yield from walk(path)
    yield from walk(root)


def snapshot(trees, output, epoch):
    # Stream full recursive sources instead of making another multi-GB source copy.
    with output.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for prefix, root in sorted(trees.items()):
                for name, path in source_entries(root, omit_top_build=prefix == "proton"):
                    tar.inodes.clear()  # Store hardlinked inputs as independent regular files.
                    info = tar.gettarinfo(str(path), arcname=f"source-snapshot/{prefix}/{name}")
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime, info.pax_headers = epoch, {}
                    info.mode = 0o777 if info.issym() else 0o755 if info.isdir() or info.mode & 0o111 else 0o644
                    if info.isfile():
                        with path.open("rb") as stream:
                            tar.addfile(info, stream)
                    else:
                        tar.addfile(info)


def publish(work, output):
    # Linux renameat2 gives atomic directory publication WITHOUT replacing even an
    # empty directory or dangling symlink created by another process during work.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(work), -100, os.fsencode(output), 1):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(output))


def package(args):
    p.require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", args.version), "Invalid VERSION")
    pins = p.read_json(REPO / "packaging/source-build-pins.json")
    p.require(args.sdk_image == pins["sdk_image"], "SDK image must equal exact pinned digest")
    source, build = (Path(v).resolve(strict=True) for v in (args.source_dir, args.build_dir))
    output = Path(args.output).absolute()
    p.require(not os.path.lexists(output), "Output already exists; use a new directory")
    output = output.resolve()
    p.require(output.parent.is_dir(), "Output parent must exist")
    p.require(not source.is_relative_to(build) and not build.is_relative_to(source), "Build must be out of tree")
    p.require(not output.is_relative_to(source) and not output.is_relative_to(build), "Output must be outside inputs")
    redist, metadata = build / "redist", build.parent / "metadata"
    p.require(redist.is_dir() and not redist.is_symlink(), "Missing official build/redist directory")
    p.require(metadata.is_dir() and not metadata.is_symlink(), "Missing sibling build metadata directory")
    revs = revisions(source, pins)
    entries = p.inventory(redist)
    for name in ("proton", "version", "toolmanifest.vdf", "LICENSE", "LICENSE.OFL", "PATENTS.AV1", "filelock.py"):
        p.regular(redist / name)
    p.check_artifacts(redist, p.CRITICAL)
    # Validate against the pinned source manifest, not a guessed runtime AppID.
    p.regular(source / "toolmanifest_x86_64.vdf")
    toolmanifest = (redist / "toolmanifest.vdf").read_text()
    p.require(toolmanifest == (source / "toolmanifest_x86_64.vdf").read_text(), "Runtime toolmanifest differs from pinned source")
    runtime = re.findall(r'"require_tool_appid"\s+"([0-9]+)"', toolmanifest)
    p.require(len(runtime) == 1 and int(runtime[0]) > 0, "Missing or ambiguous runtime requirement")
    identifier = "Proton-HEVC-Source-" + args.version
    epoch = pins["source_date_epoch"]
    with tempfile.TemporaryDirectory(prefix=".source-candidate-", dir=output.parent) as temporary:
        work = Path(temporary)
        stage, result, evidence = work / identifier, work / "result", work / "evidence"
        for path in (stage, result, evidence):
            path.mkdir()
        for name, path in entries.items():
            if path.is_symlink():
                (stage / name).symlink_to(os.path.relpath(path.resolve(), path.parent)
                                         if os.path.isabs(os.readlink(path)) else os.readlink(path))
            elif path.is_dir():
                (stage / name).mkdir()
            else:
                p.replace_file(stage, name, source=path)
        p.replace_file(stage, "proton-original", source=redist / "proton")
        p.replace_file(stage, "proton", source=REPO / "packaging/proton-launcher.py")
        (stage / "proton").chmod(0o755)
        p.replace_file(stage, "version", text=f"{epoch} proton-hevc-source-{args.version}\n")
        # Preserve runtime/layer semantics byte-for-byte; compatibilitytool.vdf
        # below supplies the distinct user-visible and internal tool identity.
        p.replace_file(stage, "compatibilitytool.vdf", text=(
            '"compatibilitytools" { "compat_tools" {\n'
            f'"{identifier}" {{ "display_name" "{identifier} (LOCAL TEST ONLY)"\n'
            '"install_path" "." "from_oslist" "linux" "to_oslist" "windows" } } }\n'))
        if "steampipe_fixups.json" in entries:
            p.replace_file(stage, "steampipe_fixups.json", text=json.dumps(p.clean_fixups(redist), sort_keys=True) + "\n")
        for name in ("COPYING.LIB", "LICENSE.md"):
            p.replace_file(stage, "hevc-metadata/" + name, source=REPO / name)
        for root in (stage, evidence, result):
            p.replace_file(root, "LOCAL-TEST-ONLY.txt", text=NOTICE)
        p.write_json(evidence / "revisions.json", revs)
        for name in ("Makefile", "config.log", "config.status", "vk.xml", "video.xml"):
            if (build / name).exists():
                p.regular(build / name)
                p.replace_file(evidence, "build/" + name, source=build / name)
        recipes = [REPO / "packaging/source-build-pins.json", REPO / "packaging/proton-launcher.py",
                   REPO / "tools/package-proton.py", Path(__file__), REPO / "tools/build-source-candidate.sh",
                   REPO / ".github/workflows/source-candidate.yml"]
        for path in recipes + sorted((REPO / "patches").glob("*.patch")):
            p.replace_file(evidence, "recipe/" + path.relative_to(REPO).as_posix(), source=path)
        trees = {"proton": source, "metadata": metadata, "evidence": evidence}
        for name in ("src-wine", "contrib"):
            if os.path.lexists(build / name):
                trees["build/" + name] = build / name
        snapshot(trees, result / "source-snapshot.tar.gz", epoch)
        final = p.inventory(stage, final=True)
        p.check_artifacts(stage, p.CRITICAL)
        manifest = {"schema_version": 1, "tool_id": identifier, "version": args.version,
                    "status": "LOCAL-TEST-ONLY / NOT RELEASE APPROVED", "release_claim": False,
                    "runtime_status": "unverified", "runtime_appid": runtime[0], "pins": pins,
                    "source_snapshot_status": "INCOMPLETE / REVIEW REQUIRED", "source_notice": NOTICE,
                    "source_revisions": revs, "source_snapshot_trees": sorted(trees),
                    "source_exclusions": sorted(CACHES) + ["proton/build/ only (not nested source build/ directories)"],
                    "redist_version": (redist / "version").read_text().strip(),
                    "source_snapshot_sha256": p.sha256(result / "source-snapshot.tar.gz"),
                    "files": p.hashes(final), "symlinks": p.links(final),
                    "omitted_paths": sorted(p.OMIT), "hash_exclusions": ["hevc-metadata/build-manifest.json"]}
        p.write_json(result / "build-manifest.json", manifest)
        p.replace_file(stage, "hevc-metadata/build-manifest.json", source=result / "build-manifest.json")
        p.archive(stage, result / "binary.tar.gz", epoch)
        p.replace_file(result, "SHA256SUMS", text="".join(
            f"{p.sha256(path)}  {path.name}\n" for path in sorted(result.iterdir())))
        publish(result, output)
    print(f"Packaged {identifier}: {output}; LOCAL-TEST-ONLY / NOT RELEASE APPROVED")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-dir", "build-dir", "version", "output", "sdk-image"):
        parser.add_argument("--" + name, required=True)
    try:
        package(parser.parse_args())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError, tarfile.TarError) as error:
        parser.exit(1, f"package-source-candidate: error: {error}\n")


if __name__ == "__main__":
    main()
