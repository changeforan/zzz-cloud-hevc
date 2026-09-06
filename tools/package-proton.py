#!/usr/bin/env python3
"""Package an installed, pinned Proton plus a declared Wine overlay; never run inputs."""

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile

REPO = Path(__file__).resolve().parent.parent
BASE_VERSION = "1787334450 proton-11.0-2-x86_64"
WINE_COMMIT = "dc26e61847081a1b5cb0733dc30feba6ee575482"
OMIT = {"dist.lock", "__pycache__", "files/share/default_pfx",
        "files/share/default_pfx_arm64", "files/steampipe_fixups_mtime"}
ROOT_FILES = {"proton", "toolmanifest.vdf", "compatibilitytool.vdf", "version",
              "LICENSE", "LICENSE.OFL", "PATENTS.AV1", "filelock.py",
              "proton_3.7_tracked_files", "steampipe_fixups.json",
              "steampipe_fixups.py", "user_settings.sample.py"}
WINE = "files/lib/wine/"
ARCHES = ("x86_64-windows", "i386-windows")
CRITICAL = {"files/bin/wine", "files/bin/wineserver"} | {
    f"{WINE}{arch}/{name}.dll" for arch in ARCHES
    for name in ("d3d11", "wined3d", "winevulkan", "dxgi", "ntdll", "win32u")
} | {f"{WINE}x86_64-unix/{name}.so" for name in ("ntdll", "win32u", "winevulkan")}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def regular(path):
    require(path.is_file() and not path.is_symlink(), f"Expected regular file: {path}")
    require(path.stat().st_size > 0, f"File must not be empty: {path}")


def read_json(path):
    regular(path)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"Duplicate JSON key {key!r} in {path}")
            result[key] = value
        return result
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    require(isinstance(value, dict), f"Expected JSON object: {path}")
    require(type(value.get("schema_version")) is int and value["schema_version"] == 1,
            f"Expected schema_version=1: {path}")
    return value


def safe_name(name):
    require(isinstance(name, str) and name and "\\" not in name
            and not any(ord(c) < 32 or ord(c) == 127 for c in name),
            f"Unsafe path name: {name!r}")
    parts = name.split("/")
    require(not any(p in ("", ".", "..") for p in parts), f"Unsafe relative path: {name!r}")
    return PurePosixPath(name)


def inventory(root, *, overlay=False, final=False):
    """Walk without following directory links; omit runtime state before inspecting it."""
    entries = {}
    def walk(directory):
        for path in sorted(directory.iterdir()):
            name = path.relative_to(root).as_posix()
            if not overlay and not final and name in OMIT:
                continue
            safe_name(name)
            require(not re.search(r"\.(orig|bak)([._-]|$)", path.name, re.I)
                    and path.name != "user_settings.py", f"Remove private/backup file: {name}")
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                require(not overlay, f"Overlay symlinks are forbidden: {name}")
                target = path.resolve(strict=final)
                require(target.is_relative_to(root), f"Symlink escapes base/package: {name}")
                lexical = Path(os.path.abspath(path.parent / os.readlink(path)))
                require(lexical.is_relative_to(root), f"Symlink target escapes tree: {name}")
                require(not final or not os.path.isabs(os.readlink(path)),
                        f"Package symlink must be relative: {name}")
            else:
                require(stat.S_ISDIR(mode) or stat.S_ISREG(mode), f"Unsupported special file: {name}")
            entries[name] = path
            if stat.S_ISDIR(mode):
                walk(path)
    walk(root)
    return entries


def hashes(entries):
    return {name: sha256(path) for name, path in sorted(entries.items())
            if path.is_file() and not path.is_symlink()}


def links(entries):
    return {name: os.readlink(path) for name, path in sorted(entries.items()) if path.is_symlink()}


def check_artifacts(root, critical):
    for name in sorted(critical):
        path = root / name
        require(path.is_file() and path.stat().st_size, f"Missing critical artifact: {name} in {root}")
        magic = b"MZ" if name.endswith((".dll", ".drv", ".sys")) else b"\x7fELF"
        with path.open("rb") as stream:
            require(stream.read(len(magic)) == magic,
                    f"Invalid {'PE' if magic == b'MZ' else 'ELF'} artifact: {name}; "
                    "install the real binary, not a shell/build wrapper")
    for name in ("files/bin/wine", "files/bin/wineserver"):
        require((root / name).stat().st_mode & 0o111, f"Loader/server must be executable: {name}")


def validate_overlay(base_entries, overlay, provenance_path):
    entries = inventory(overlay, overlay=True)
    metadata = {"overlay-manifest.json"}
    if "build-provenance.json" in entries:
        require(provenance_path == overlay / "build-provenance.json",
                "Use --provenance OVERLAY/build-provenance.json for overlay provenance")
        metadata.add("build-provenance.json")
    require(all(n in metadata or n == "files" or n.startswith("files/") for n in entries),
            "Overlay may contain only files/ and declared root metadata")
    manifest = read_json(overlay / "overlay-manifest.json")
    require(manifest.get("wine_base_commit") == WINE_COMMIT, "Overlay wine_base_commit does not match pin")
    args = manifest.get("configure_args")
    require(isinstance(args, list) and all(isinstance(a, str) and a.strip() for a in args)
            and {"--enable-archs=x86_64,i386", "--without-unwind"}.issubset(args),
            "Overlay configure_args must include --enable-archs=x86_64,i386 and --without-unwind")
    require(all(a in ("--enable-archs=x86_64,i386", "--without-unwind") for a in args
                if a.split("=", 1)[0] in ("--enable-archs", "--disable-archs", "--with-unwind", "--without-unwind")),
            "Overlay configure_args contains conflicting architecture/unwind options")
    declared = manifest.get("files")
    require(isinstance(declared, dict), "overlay-manifest.json files must map relative paths to SHA256")
    for name, digest in declared.items():
        safe_name(name)
        require(name.startswith("files/") and isinstance(digest, str)
                and re.fullmatch(r"[0-9a-f]{64}", digest), f"Invalid overlay manifest entry: {name}")
        require(not any(name == omit or name.startswith(omit + "/") for omit in OMIT),
                f"Overlay must not restore runtime artifacts: {name}")
    actual = hashes({n: p for n, p in entries.items() if n not in metadata})
    require(actual == declared, "Overlay file manifest mismatch: check missing, unlisted, or modified files")
    for name in actual.keys() & base_entries.keys():
        if any(re.match(r"^(licen[cs]e|copying|patents)([._-]|$)", part, re.I)
               for part in PurePosixPath(name).parts):
            require(actual[name] == sha256(base_entries[name]), f"Overlay must retain base licenses: {name}")
    require(CRITICAL.issubset(actual), "Overlay missing critical replacements: " + ", ".join(sorted(CRITICAL - actual.keys())))
    paired = set(CRITICAL)
    for name in base_entries:
        if not name.startswith(WINE + "x86_64-unix/") or not name.endswith(".so"):
            continue
        stem = Path(name).stem
        for ext in (".dll", ".drv", ".sys"):
            peers = {f"{WINE}{arch}/{stem}{ext}" for arch in ARCHES}
            if peers.intersection(base_entries):
                group = peers | {name}
                if group.intersection(actual):
                    require(group.issubset(actual), "Paired Unix/PE modules must all be replaced: " + ", ".join(sorted(group)))
                    paired.update(group)
    check_artifacts(overlay, paired)
    return manifest, actual


def validate_provenance(path):
    value = read_json(path)
    for section, fields in (("base_source", ("url", "revision")),
                            ("wine_source", ("url", "revision")),
                            ("build", ("environment", "command"))):
        require(isinstance(value.get(section), dict), f"Provenance needs {section} object")
        for field in fields:
            text = value[section].get(field)
            require(isinstance(text, str) and text.strip(), f"Provenance needs nonempty {section}.{field}")
    require(re.fullmatch(r"[0-9a-fA-F]{40}", value["wine_source"]["revision"]),
            "Provenance wine_source.revision must be a full 40-hex commit")
    require(isinstance(value.get("test_status"), str) and value["test_status"].strip(),
            "Provenance needs a nonempty test_status")
    return value


def elf_dependencies(path):
    """Read DT_NEEDED directly, without executing a loader or trusting overlay metadata."""
    with path.open("rb") as stream:
        if stream.read(4) != b"\x7fELF":
            return None
        data = b"\x7fELF" + stream.read()
    require(len(data) >= 16, f"Truncated ELF identification: {path.name}")
    require(data[4] in (1, 2) and data[5] in (1, 2), f"Invalid ELF header: {path.name}")
    endian, wide = ("<" if data[5] == 1 else ">"), data[4] == 2
    def unpack(fmt, offset):
        require(0 <= offset <= len(data) - struct.calcsize(endian + fmt), f"Truncated ELF: {path.name}")
        return struct.unpack_from(endian + fmt, data, offset)
    phoff = unpack("Q" if wide else "I", 32 if wide else 28)[0]
    size, count = unpack("HH", 54 if wide else 42)
    require(size >= (56 if wide else 32) or count == 0, f"Invalid ELF program headers: {path.name}")
    loads, dynamic = [], None
    for index in range(count):
        fields = unpack("IIQQQQQQ" if wide else "IIIIIIII", phoff + index * size)
        kind = fields[0]
        offset, address, length = (fields[2], fields[3], fields[5]) if wide else (fields[1], fields[2], fields[4])
        if kind == 1:
            loads.append((offset, address, length))
        if kind == 2:
            dynamic = (offset, length)
    strings, needed, string_size = None, [], None
    if dynamic:
        for offset in range(dynamic[0], sum(dynamic), 16 if wide else 8):
            tag, value = unpack("qQ" if wide else "iI", offset)
            if tag == 0:
                break
            if tag == 1:
                needed.append(value)
            elif tag == 5:
                strings = value
            elif tag == 10:
                string_size = value
    names = []
    for value in needed:
        mapped = [(off + strings - addr, length - (strings - addr)) for off, addr, length in loads
                  if strings is not None and addr <= strings < addr + length]
        require(mapped and string_size is not None and value < string_size <= mapped[0][1], f"Invalid ELF string table: {path.name}")
        start = mapped[0][0] + value
        end = data.find(b"\0", start, min(len(data), mapped[0][0] + string_size))
        require(end >= start, f"Unterminated ELF dependency: {path.name}")
        names.append(data[start:end].decode("ascii"))
    require(not any("libunwind" in name.lower() for name in names), f"Forbidden libunwind dependency: {path.name}")
    return {"status": "DT_NEEDED inspected; runtime ABI unverified", "needed": sorted(set(names))}


def replace_file(root, name, source=None, text=None):
    destination = root / name
    for parent in reversed(destination.parents):
        if parent == root or root in parent.parents:
            require(not parent.is_symlink(), f"Refusing replacement through directory symlink: {name}")
            parent.mkdir(exist_ok=True)
    if destination.is_symlink() or destination.is_file():
        destination.unlink()  # Break symlinks/hardlinks; never overwrite their referents.
    require(not destination.exists(), f"Replacement destination is not a file: {name}")
    if source is not None:
        shutil.copyfile(source, destination)
        destination.chmod(0o755 if source.stat().st_mode & 0o111 else 0o644)
    else:
        destination.write_text(text, encoding="utf-8")
        destination.chmod(0o644)


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    path.chmod(0o644)


def clean_fixups(base):
    value = json.loads((base / "steampipe_fixups.json").read_text())
    require(isinstance(value, dict), "Invalid SteamPipe fixup object")
    for key in ("empty_dirs", "no_write_paths"):
        require(isinstance(value.get(key), list), f"SteamPipe fixups need {key} array")
        keep = []
        for name in value[key]:
            safe_name(name)
            if any(name == omit or name.startswith(omit + "/") for omit in OMIT):
                continue
            resolved = (base / name).resolve()
            require(resolved.is_relative_to(base), f"SteamPipe fixup escapes base: {name}")
            if key == "no_write_paths":
                require((base / name).is_file(), f"SteamPipe fixup target missing: {name}")
            keep.append(name)
        value[key] = keep
    # The ID must change when prefix-related fixups are removed.
    value["id"] = "hevc-" + hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]
    return value


def archive(root, output, epoch):
    entries = inventory(root, final=True)
    with output.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for path in [root] + [entries[n] for n in sorted(entries)]:
                info = tar.gettarinfo(str(path), arcname=path.relative_to(root.parent).as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = epoch
                info.pax_headers = {}
                info.mode = 0o777 if info.issym() else 0o755 if info.isdir() or info.mode & 0o111 else 0o644
                if info.isfile():
                    with path.open("rb") as stream:
                        tar.addfile(info, stream)
                else:
                    tar.addfile(info)


def package(args):
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", args.version),
            "VERSION must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}")
    base, overlay, output = (Path(v).resolve() for v in (args.base, args.overlay, args.output))
    provenance_path = Path(args.provenance).resolve(strict=True)
    source = Path(args.source_bundle).absolute()
    require(base.is_dir() and overlay.is_dir(), "--base and --overlay must be existing directories")
    require(not output.is_relative_to(base) and not output.is_relative_to(overlay),
            "Output directory must not reside within base or overlay")
    require(not base.is_relative_to(overlay) and not overlay.is_relative_to(base), "Base and overlay must be separate trees")
    regular(source)
    require(source.name.endswith(".tar.gz"), "Source bundle must be a nonempty regular .tar.gz file")
    with tarfile.open(source, "r:gz") as source_tar:
        require(next(iter(source_tar), None) is not None, "Source bundle must contain at least one archive member")
        for _ in source_tar:
            pass  # Read archive structure only; never extract or execute corresponding source.
    provenance = validate_provenance(provenance_path)
    pins = read_json(REPO / "packaging/pins.json")
    require(pins.get("proton_version") == BASE_VERSION and pins.get("wine_base_commit") == WINE_COMMIT,
            "packaging/pins.json does not match this packager's supported base/Wine pins")
    epoch_text = os.environ.get("SOURCE_DATE_EPOCH", "1787334450")
    require(re.fullmatch(r"[0-9]+", epoch_text), "SOURCE_DATE_EPOCH must be a nonnegative integer")
    epoch = int(epoch_text)
    require(epoch <= 0xFFFFFFFF, "SOURCE_DATE_EPOCH exceeds supported timestamp range")
    for path in base.iterdir():
        require(path.name in ROOT_FILES | {"files", "dist.lock", "__pycache__"},
                f"Unexpected base root entry {path.name!r}; use a clean Proton installation (no games, pfx, credentials)")
        if path.name in ROOT_FILES:
            require(path.is_file(), f"Expected a root file, not directory: {path.name}")
    entries = inventory(base)
    regular(base / "version")
    require((base / "version").read_text().rstrip("\r\n") == BASE_VERSION, f"Base version must equal {BASE_VERSION!r}")
    for name in ("proton", "toolmanifest.vdf", "LICENSE", "filelock.py",
                 "steampipe_fixups.py", "steampipe_fixups.json"):
        regular(base / name)
    fixups = clean_fixups(base)
    check_artifacts(base, CRITICAL)
    overlay_manifest, overlay_hashes = validate_overlay(entries, overlay, provenance_path)
    dependencies = {n: result for n in overlay_hashes if (result := elf_dependencies(overlay / n)) is not None}
    for name in ("COPYING.LIB", "LICENSE.md"):
        regular(REPO / name)
    identifier = "Proton-HEVC-" + args.version
    local_candidate = getattr(args, "local_candidate", False)
    source_label = "source-snapshot" if local_candidate else "corresponding-source"
    names = [identifier + ".tar.gz", f"{source_label}-{args.version}.tar.gz", "build-manifest.json", "SHA256SUMS"]
    require(all(not os.path.lexists(output / n) for n in names), "Output already exists; use a fresh output directory/version")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".package-proton-", dir=output) as temporary:
        work = Path(temporary)
        stage = work / identifier
        stage.mkdir()
        for name, path in entries.items():
            destination = stage / name
            if path.is_symlink():
                target = os.readlink(path)
                if os.path.isabs(target):
                    target = os.path.relpath(path.resolve(), path.parent)
                destination.symlink_to(target)
            elif path.is_dir():
                destination.mkdir()
            else:
                replace_file(stage, name, source=path)
        for name in sorted(overlay_hashes):
            replace_file(stage, name, source=overlay / name)
        replace_file(stage, "proton-original", source=base / "proton")
        replace_file(stage, "proton", source=REPO / "packaging/proton-launcher.py")
        (stage / "proton").chmod(0o755)
        replace_file(stage, "steampipe_fixups.json", text=json.dumps(fixups, sort_keys=True, indent=2) + "\n")
        replace_file(stage, "version", text=f"1787334450 proton-hevc-{args.version}\n")
        replace_file(stage, "compatibilitytool.vdf", text=(
            '"compatibilitytools"\n{\n  "compat_tools"\n  {\n'
            f'    "{identifier}"\n    {{\n      "display_name" "{identifier}"\n'
            '      "install_path" "."\n      "from_oslist" "windows"\n'
            '      "to_oslist" "linux"\n    }\n  }\n}\n'))
        for name in ("COPYING.LIB", "LICENSE.md"):
            replace_file(stage, "hevc-metadata/" + name, source=REPO / name)
        if local_candidate:
            replace_file(stage, "hevc-metadata/LOCAL-TEST-ONLY.txt", text=(
                "LOCAL TEST CANDIDATE - DO NOT PUBLISH\n"
                "The accompanying source snapshot is not asserted to be complete\n"
                "corresponding source for all retained Proton components.\n"
                "Source completeness/licensing and runtime validation remain release gates.\n"))
        replace_file(stage, "hevc-metadata/LICENSE", source=base / "LICENSE")
        write_json(stage / "hevc-metadata/overlay-manifest.json", overlay_manifest)
        final_entries = inventory(stage, final=True)
        check_artifacts(stage, CRITICAL)
        source_copy = work / names[1]
        shutil.copyfile(source, source_copy)
        recipe_paths = sorted(list((REPO / "patches").glob("*.patch")) + list((REPO / "tools").glob("*proton*"))
                              + list((REPO / "packaging").glob("*.py")))
        revision = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, check=False) if shutil.which("git") else None
        manifest = {"schema_version": 1, "tool_id": identifier, "version": args.version,
                    "base_version": BASE_VERSION, "source_date_epoch": epoch,
                    "pins": pins, "provenance": provenance, "runtime_status": "unverified",
                    "release_claim": False, "source_bundle": {"filename": names[1], "sha256": sha256(source_copy)},
                    "local_candidate": local_candidate,
                    "recipe": {"revision": revision.stdout.strip() if revision and revision.returncode == 0 else "unknown",
                               "files": {p.relative_to(REPO).as_posix(): sha256(p) for p in recipe_paths if p.is_file()}},
                    "elf_dependencies": dependencies,
                    "base_files": hashes(entries), "base_symlinks": links(entries),
                    "base_hash_scope": "retained files only; runtime/cache omissions excluded",
                    "omitted_paths": sorted(OMIT),
                    "overlay_files": overlay_hashes, "overlay_manifest": overlay_manifest,
                    "files": hashes(final_entries), "symlinks": links(final_entries),
                    "hash_exclusions": ["hevc-metadata/build-manifest.json"],
                    "hash_note": "Self-hash excluded; external SHA256SUMS covers build-manifest.json and both archives."}
        write_json(work / names[2], manifest)
        shutil.copyfile(work / names[2], stage / "hevc-metadata/build-manifest.json")
        archive(stage, work / names[0], epoch)
        (work / "SHA256SUMS").write_text("".join(f"{sha256(work / n)}  {n}\n" for n in sorted(names[:3])), encoding="utf-8")
        published = []
        try:
            for name in names:
                (work / name).chmod(0o644)
                os.link(work / name, output / name)  # Atomic no-clobber publication.
                published.append(name)
        except BaseException:
            for name in published:
                if (output / name).stat().st_ino == (work / name).stat().st_ino:
                    (output / name).unlink()
            raise
    print(f"Packaged {identifier} into {output}; runtime unverified, no release claim.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-candidate", action="store_true",
                        help="Label source as incomplete snapshot; local testing only, never for public release")
    for name in ("base", "overlay", "source-bundle", "provenance", "version", "output"):
        parser.add_argument("--" + name, required=True)
    try:
        package(parser.parse_args())
    except (OSError, ValueError, RuntimeError, tarfile.TarError) as error:
        parser.exit(1, f"package-proton: error: {error}\n")


if __name__ == "__main__":
    main()
