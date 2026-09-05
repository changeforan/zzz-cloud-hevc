#!/usr/bin/env python3
"""Fetch a checksum-pinned HTTPS archive, optionally extracting one safe root.

No fetched executable is run. Requires Python 3.12+ for tar's data filter.
Input tarballs must not contain hardlinks (use tar --hard-dereference).
"""

import argparse
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
import tempfile
from urllib.parse import urlsplit


def extract_archive(archive, destination):
    destination = Path(destination)
    if destination.exists():
        raise ValueError("extraction destination already exists")
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        names = set()
        symlinks = set()
        roots = set()
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise ValueError("unsafe archive member path")
            if any(ord(c) < 32 for c in member.name) or "\\" in member.name:
                raise ValueError("unsupported archive member name")
            name = str(path)
            if name in names:
                raise ValueError("duplicate archive member")
            names.add(name)
            roots.add(path.parts[0])
            if not (member.isfile() or member.isdir() or member.issym()):
                raise ValueError("archive contains special files or hardlinks")
            if member.issym():
                symlinks.add(name)
                target = PurePosixPath(member.linkname)
                if target.is_absolute() or "\\" in member.linkname:
                    raise ValueError("absolute or unsupported symlink target")
                resolved = os.path.normpath(str(path.parent / target))
                if resolved == ".." or resolved.startswith("../"):
                    raise ValueError("symlink escapes archive")
                if PurePosixPath(resolved).parts[0] != path.parts[0]:
                    raise ValueError("symlink escapes top-level directory")
        if len(roots) != 1:
            raise ValueError("archive must contain exactly one top-level directory")
        for member in members:
            if any(str(p) in symlinks for p in PurePosixPath(member.name).parents):
                raise ValueError("archive writes through a symlink")
        root = next(iter(roots))
        with tempfile.TemporaryDirectory(prefix="extract-", dir=destination.parent) as tmp:
            tar.extractall(tmp, filter="data")
            extracted = Path(tmp) / root
            if extracted.is_symlink() or not extracted.is_dir():
                raise ValueError("archive root must be a directory")
            for item in extracted.rglob("*"):
                if item.is_symlink():
                    resolved = item.resolve(strict=True)
                    if not resolved.is_relative_to(extracted.resolve()):
                        raise ValueError("symlink chain escapes archive root")
            extracted.rename(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--extract-to", type=Path)
    args = parser.parse_args()
    try:
        url = urlsplit(args.url)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("only HTTPS URLs without embedded credentials are accepted")
        if not re.fullmatch(r"[a-fA-F0-9]{64}", args.sha256):
            raise ValueError("expected a 64-digit SHA-256")
        if args.output.exists() or args.output.is_symlink():
            raise ValueError("download output already exists")
        if not args.output.parent.is_dir():
            raise ValueError("download parent directory must exist")
        with tempfile.TemporaryDirectory(prefix="download-", dir=args.output.parent) as tmp:
            archive = Path(tmp) / "input.tar.gz"
            subprocess.run([
                "curl", "--fail", "--silent", "--show-error", "--location",
                "--proto", "=https", "--proto-redir", "=https",
                "--connect-timeout", "30", "--max-time", "1800", "--retry", "2",
                "--output", str(archive), args.url,
            ], check=True)
            with archive.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != args.sha256.lower():
                raise ValueError("download SHA-256 mismatch")
            if args.extract_to:
                extract_archive(archive, args.extract_to)
            shutil.move(archive, args.output)
    except (ValueError, OSError, tarfile.TarError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Input rejected: {exc}\n")


if __name__ == "__main__":
    main()
