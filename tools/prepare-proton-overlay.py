#!/usr/bin/env python3
"""Prepare a sparse Proton overlay; never install it or execute input binaries.

The pinned Wine commit is policy metadata, NOT verification of the build's
source revision. Supplied provenance is preserved, not independently attested.
DT_NEEDED inspection does not establish Steam Runtime ABI compatibility.
Stock x86_64-unix wine/wine64 and preloaders are deliberately left untouched.
"""

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile


STOCK_VERSION = "1787334450 proton-11.0-2-x86_64"
WINE_BASE_COMMIT = "dc26e61847081a1b5cb0733dc30feba6ee575482"
CONFIGURE_ARGS = ["--enable-archs=x86_64,i386", "--without-unwind"]
ARCHES = ("x86_64-windows", "i386-windows")
CRITICAL = {"ntdll", "win32u", "winevulkan", "d3d11", "wined3d", "dxgi"}
CRITICAL_UNIX = {"ntdll", "win32u", "winevulkan"}
PE_SUFFIXES = {".dll", ".drv", ".sys", ".exe"}
LIB = Path("files/lib/wine")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def artifact(path, magic=None):
    require(path.is_file(), f"missing build artifact: {path}")
    require(path.stat().st_size > 0, f"empty build artifact: {path}")
    if magic:
        kind = "ELF" if magic == b"\x7fELF" else "PE MZ"
        with path.open("rb") as stream:
            require(stream.read(len(magic)) == magic,
                    f"invalid {kind} artifact: {path}")


def configure_check(build):
    text = (build / "config.log").read_text(encoding="utf-8", errors="replace")
    commands = []
    for line in text.replace("\\\n", " ").splitlines():
        match = re.match(r"^\s*\$\s+(.+)$", line)
        if match:
            args = shlex.split(match.group(1))
            if args and Path(args[0]).name == "configure":
                commands.append(args[1:])
    require(len(commands) == 1, "config.log must record one configure invocation")
    args = commands[0]
    require(all(flag in args for flag in CONFIGURE_ARGS),
            "config.log configure invocation requires " + " ".join(CONFIGURE_ARGS))
    related = ("--enable-archs", "--disable-archs", "--with-unwind", "--without-unwind")
    require(all(arg in CONFIGURE_ARGS for arg in args
                if arg.split("=", 1)[0] in related),
            "config.log contains conflicting architecture/unwind options")


def index_candidate(index, key, path):
    require(key not in index, f"duplicate candidate mapping for {key}: "
            f"{index.get(key)} and {path}")
    artifact(path)
    index[key] = path


def collect(base, build):
    stock = {}
    for arch in ARCHES:
        directory = base / LIB / arch
        require(directory.is_dir(), f"missing stock module directory: {directory}")
        stock[arch] = {p.name for p in directory.iterdir()
                       if p.is_file() and p.suffix in PE_SUFFIXES}
    unix_dir = base / LIB / "x86_64-unix"
    require(unix_dir.is_dir(), f"missing stock Unix directory: {unix_dir}")
    stock_unix = {p.name for p in unix_dir.glob("*.so") if p.is_file()}
    dlls = build / "dlls"
    require(dlls.is_dir(), f"missing build module directory: {dlls}")
    pe, unix = {}, {}
    for arch in ARCHES:
        for path in sorted(dlls.glob(f"*/{arch}/*")):
            if path.name in stock[arch]:
                index_candidate(pe, (arch, path.name), path)
    for path in sorted(dlls.rglob("*.so")):
        if (path.name in stock_unix and
                not any(part.endswith("-windows") for part in path.relative_to(dlls).parts)):
            index_candidate(unix, path.name, path)

    names = stock[ARCHES[0]] | stock[ARCHES[1]]
    by_stem = {}
    for name in sorted(names):
        by_stem.setdefault(Path(name).stem, []).append(name)
    for stem, matches in by_stem.items():
        if stem + ".so" in stock_unix:
            require(len(matches) == 1, f"ambiguous Unix/PE mapping for {stem}: {matches}")

    selected = {}
    for name in sorted(names):
        stem = Path(name).stem
        so = stem + ".so"
        has_unix = so in stock_unix
        complete = all(name in stock[a] and (a, name) in pe for a in ARCHES)
        complete = complete and (not has_unix or so in unix)
        if stem in CRITICAL:
            require(complete, f"incomplete critical module pair: {name}")
        if not complete:
            continue  # Retain all stock halves for optional build gaps.
        for arch in ARCHES:
            source = pe[arch, name]
            artifact(source, b"MZ")
            selected[LIB / arch / name] = source
        if has_unix:
            selected[LIB / "x86_64-unix" / so] = unix[so]
    for stem in CRITICAL:
        for arch in ARCHES:
            require(LIB / arch / (stem + ".dll") in selected,
                    f"missing critical PE artifact: {arch}/{stem}.dll")
    for stem in CRITICAL_UNIX:
        require(LIB / "x86_64-unix" / (stem + ".so") in selected,
                f"missing critical Unix artifact: {stem}.so")
    # Do not use the build-root wine launcher (which can be a shell wrapper).
    selected[Path("files/bin/wine")] = build / "tools/wine/wine"
    selected[Path("files/bin/wineserver")] = build / "server/wineserver"
    for relative in (Path("files/bin/wine"), Path("files/bin/wineserver")):
        require((base / relative).is_file(), f"missing stock executable: {relative}")
    return selected


def dependencies(path):
    artifact(path, b"\x7fELF")
    result = subprocess.run(["objdump", "-p", str(path)], capture_output=True,
                            text=True, errors="replace", timeout=30,
                            env={**os.environ, "LC_ALL": "C"}, check=False)
    require(result.returncode == 0, f"objdump failed for {path}: {result.stderr.strip()}")
    needed = sorted(set(re.findall(r"^\s*NEEDED\s+(\S+)\s*$", result.stdout, re.M)))
    require(not any("libunwind" in item.lower() for item in needed),
            f"forbidden libunwind dependency in {path}: {needed}")
    return needed


def publish(temporary, output):
    # Linux atomic no-clobber rename: even an empty concurrent output is safe.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    require(rename is not None, "atomic no-clobber publication requires Linux renameat2")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                       ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(temporary), -100, os.fsencode(output), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(output))


def prepare(args):
    base = args.base.resolve(strict=True)
    build = args.build.resolve(strict=True)
    provenance = args.provenance.resolve(strict=True)
    require(base.is_dir() and build.is_dir(), "--base and --build must be directories")
    # Resolve the parent only, so an existing dangling output symlink is rejected.
    output = args.output.parent.resolve(strict=True) / args.output.name
    require(not os.path.lexists(output), f"output already exists: {output}")
    require(output.parent.is_dir(), f"output parent is not a directory: {output.parent}")
    for source in (base, build):
        require(not output.is_relative_to(source), f"output must not be inside input: {source}")
    require((base / "version").read_text(encoding="utf-8").rstrip("\r\n") == STOCK_VERSION,
            f"stock Proton version must be exactly {STOCK_VERSION!r}")
    configure_check(build)
    # Validate JSON without rewriting the user's provenance document.
    json.loads(provenance.read_bytes())
    selected = collect(base, build)
    manifest = {"schema_version": 1, "wine_base_commit": WINE_BASE_COMMIT,
                "configure_args": CONFIGURE_ARGS, "files": {}, "dependencies": {}}
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
    try:
        for relative, source in sorted(selected.items()):
            destination = temporary / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)  # Materialize links; preserve file modes.
            if relative.suffix == ".so" or relative.parent == Path("files/bin"):
                manifest["dependencies"][relative.as_posix()] = dependencies(destination)
            else:
                artifact(destination, b"MZ")
            with destination.open("rb") as stream:
                digest = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            manifest["files"][relative.as_posix()] = digest.hexdigest()
        shutil.copy2(provenance, temporary / "build-provenance.json")
        (temporary / "overlay-manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        publish(temporary, output)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    print(f"Prepared {len(selected)} overlay files in {output}; ABI compatibility not verified.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base", "build", "provenance", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    try:
        prepare(args)
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"error: {error}\n")


if __name__ == "__main__":
    main()
