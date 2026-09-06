import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import struct
import tarfile
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_proton", REPO / "tools/package-proton.py")
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


class PackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.base = self.root / "base"
        self.overlay = self.root / "overlay"
        self.base.mkdir()
        self.overlay.mkdir()
        for name in ("proton", "LICENSE", "LICENSE.OFL", "PATENTS.AV1", "filelock.py", "steampipe_fixups.py"):
            self.put(self.base, name, b"retained stock content")
        self.put(self.base, "steampipe_fixups.json", json.dumps({
            "id": "stock", "empty_dirs": ["files/share/default_pfx/drive_c/windows"],
            "no_write_paths": []}).encode())
        self.put(self.base, "toolmanifest.vdf", b'"manifest" { "require_tool_appid" "4183110" }\n')
        self.put(self.base, "version", (packager.BASE_VERSION + "\n").encode())
        for name in packager.CRITICAL:
            for root in (self.base, self.overlay):
                elf = bytearray(64)
                elf[:7] = b"\x7fELF\x02\x01\x01"
                self.put(root, name, (b"MZ" if name.endswith(".dll") else bytes(elf)) + root.name.encode(), 0o755)
        self.put(self.base, "files/lib/wine/x86_64-unix/vrclient.so", b"\x7fELFunchanged")
        (self.base / "files/bin/msidb").symlink_to("wine")
        for name in packager.OMIT:
            if name.endswith(("dist.lock", "steampipe_fixups_mtime")):
                self.put(self.base, name, b"runtime")
            else:
                directory = self.base / name
                directory.mkdir(parents=True, exist_ok=True)
                (directory / "outside").symlink_to("/")
        self.provenance = self.overlay / "build-provenance.json"
        self.provenance.write_text(json.dumps({
            "schema_version": 1,
            "base_source": {"url": "https://example.test/base", "revision": "proton-11.0-2"},
            "wine_source": {"url": "https://example.test/wine", "revision": "a" * 40},
            "build": {"environment": "synthetic fixture", "command": "make"},
            "test_status": "synthetic only",
        }))
        self.source = self.root / "source.tar.gz"
        with tarfile.open(self.source, "w:gz") as archive:
            info = tarfile.TarInfo("source/README")
            data = b"synthetic corresponding source"
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        self.args = argparse.Namespace(base=str(self.base), overlay=str(self.overlay),
                                       source_bundle=str(self.source), provenance=str(self.provenance),
                                       version="test-1", output=str(self.root / "output"))
        self.manifest()
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1787334450"}).start()

    def put(self, root, name, data, mode=0o644):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)
        return path

    def manifest(self):
        files = {p.relative_to(self.overlay).as_posix(): packager.sha256(p)
                 for p in (self.overlay / "files").rglob("*") if p.is_file()}
        (self.overlay / "overlay-manifest.json").write_text(json.dumps({
            "schema_version": 1, "files": files, "wine_base_commit": packager.WINE_COMMIT,
            "configure_args": ["--enable-archs=x86_64,i386", "--without-unwind"],
        }))

    def run_package(self):
        with contextlib.redirect_stdout(io.StringIO()):
            packager.package(self.args)

    def reject(self, pattern):
        with self.assertRaisesRegex((ValueError, OSError), pattern):
            self.run_package()
        output = Path(self.args.output)
        self.assertFalse(output.exists() and list(output.iterdir()), "partial output left behind")

    def test_end_to_end_deterministic_hashes_and_inputs_unchanged(self):
        before = packager.hashes(packager.inventory(self.base))
        self.run_package()
        output = Path(self.args.output)
        original = {p.name: p.read_bytes() for p in output.iterdir()}
        self.args.output = str(self.root / "second")
        self.run_package()
        self.assertEqual(original, {p.name: p.read_bytes() for p in Path(self.args.output).iterdir()})
        self.assertEqual(before, packager.hashes(packager.inventory(self.base)))
        self.assertTrue((self.base / "files/share/default_pfx/outside").is_symlink())
        self.assertEqual(original["corresponding-source-test-1.tar.gz"], self.source.read_bytes())
        manifest = json.loads(original["build-manifest.json"])
        self.assertEqual(manifest["runtime_status"], "unverified")
        self.assertFalse(manifest["release_claim"])
        with tarfile.open(output / "Proton-HEVC-test-1.tar.gz") as tar:
            prefix = "Proton-HEVC-test-1/"
            members = tar.getmembers()
            self.assertEqual([m.name for m in members], sorted(m.name for m in members))
            for member in members:
                self.assertEqual((member.uid, member.gid, member.mtime), (0, 0, 1787334450))
                self.assertIn(member.mode, (0o644, 0o755, 0o777))
                self.assertFalse(member.islnk())
                self.assertNotIn(member.name.removeprefix(prefix), packager.OMIT)
            for name, digest in manifest["files"].items():
                self.assertEqual(hashlib.sha256(tar.extractfile(prefix + name).read()).hexdigest(), digest)
            for name, target in manifest["symlinks"].items():
                self.assertEqual(tar.getmember(prefix + name).linkname, target)
            self.assertEqual(tar.extractfile(prefix + "hevc-metadata/build-manifest.json").read(), original["build-manifest.json"])
            compat = tar.extractfile(prefix + "compatibilitytool.vdf").read()
            self.assertIn(b'"from_oslist" "windows"', compat)
            self.assertIn(b'"to_oslist" "linux"', compat)
            self.assertIn(b'4183110', tar.extractfile(prefix + "toolmanifest.vdf").read())
        for line in original["SHA256SUMS"].decode().splitlines():
            digest, name = line.split("  ")
            self.assertEqual(hashlib.sha256(original[name]).hexdigest(), digest)

    def test_missing_source(self):
        self.source.unlink()
        self.reject("regular file")

    def test_invalid_source_archive(self):
        self.source.write_bytes(b"not a tar gzip")
        with self.assertRaises(tarfile.ReadError):
            self.run_package()

    def test_libunwind_rejected_from_elf_not_metadata(self):
        data = bytearray(512)
        data[:7] = b"\x7fELF\x02\x01\x01"
        struct.pack_into("<Q", data, 32, 64)
        struct.pack_into("<HH", data, 54, 56, 2)
        struct.pack_into("<IIQQQQQQ", data, 64, 1, 0, 0, 4096, 0, 512, 512, 1)
        struct.pack_into("<IIQQQQQQ", data, 120, 2, 0, 200, 4296, 0, 64, 64, 8)
        for offset, tag, value in ((200, 5, 4496), (216, 10, 32), (232, 1, 0), (248, 0, 0)):
            struct.pack_into("<qQ", data, offset, tag, value)
        data[400:415] = b"libunwind.so.8\0\0"
        self.put(self.overlay, "files/bin/wine", data, 0o755)
        self.manifest()
        self.reject("Forbidden libunwind")

    def test_wrong_base(self):
        (self.base / "version").write_text("wrong")
        self.reject("Base version")

    def test_invalid_version(self):
        for version in ("../escape", "", "a" * 65, "v with space", "-leading"):
            with self.subTest(version=version):
                self.args.version = version
                self.reject("VERSION")

    def test_hash_mismatch(self):
        (self.overlay / "files/bin/wine").write_bytes(b"changed")
        self.reject("manifest mismatch")

    def test_unlisted_file(self):
        self.put(self.overlay, "files/extra", b"unlisted")
        self.reject("manifest mismatch")

    def test_critical_missing(self):
        (self.overlay / "files/bin/wineserver").unlink()
        self.manifest()
        self.reject("missing critical")

    def test_incomplete_driver_pair(self):
        self.put(self.base, "files/lib/wine/x86_64-unix/winex11.so", b"\x7fELF")
        self.put(self.overlay, "files/lib/wine/x86_64-unix/winex11.so", b"\x7fELF")
        for arch in packager.ARCHES:
            self.put(self.base, f"files/lib/wine/{arch}/winex11.drv", b"MZ")
        self.manifest()
        self.reject("Paired Unix/PE")

    def test_shell_loader_rejected(self):
        self.put(self.overlay, "files/bin/wine", b"#!/bin/sh\nexit 0", 0o755)
        self.manifest()
        self.reject("Invalid ELF")

    def test_truncated_elf_rejected(self):
        self.put(self.overlay, "files/bin/wine", b"\x7fELF", 0o755)
        self.manifest()
        self.reject("Truncated ELF")

    def test_bootstrap_files_required(self):
        (self.base / "filelock.py").unlink()
        self.reject("Expected regular file")

    def test_local_candidate_labels_incomplete_sources(self):
        self.args.local_candidate = True
        self.run_package()
        output = Path(self.args.output)
        self.assertTrue((output / "source-snapshot-test-1.tar.gz").is_file())
        self.assertFalse((output / "corresponding-source-test-1.tar.gz").exists())
        with tarfile.open(output / "Proton-HEVC-test-1.tar.gz") as tar:
            self.assertIn("Proton-HEVC-test-1/hevc-metadata/LOCAL-TEST-ONLY.txt", tar.getnames())

    def test_prefix_fixups_removed(self):
        self.run_package()
        with tarfile.open(Path(self.args.output) / "Proton-HEVC-test-1.tar.gz") as tar:
            fixups = json.load(tar.extractfile("Proton-HEVC-test-1/steampipe_fixups.json"))
            self.assertEqual(fixups["empty_dirs"], [])
            self.assertNotEqual(fixups["id"], "stock")
            self.assertFalse(any("steampipe_fixups_mtime" in p for p in tar.getnames()))

    def test_fixup_traversal_rejected(self):
        self.put(self.base, "steampipe_fixups.json", json.dumps({
            "empty_dirs": ["../../outside"], "no_write_paths": []}).encode())
        self.reject("Unsafe relative path")

    def test_unsafe_base_links(self):
        link = self.base / "files/unsafe"
        for target in ("/", "../../outside", "missing"):
            with self.subTest(target=target):
                link.symlink_to(target)
                self.reject("escapes|No such file")
                link.unlink()

    def test_overlay_links_rejected(self):
        (self.overlay / "files/link").symlink_to("bin/wine")
        self.reject("Overlay symlinks")

    def test_private_and_backup_files_rejected(self):
        for name in ("user_settings.py", "pfx", ".credentials", "game.exe", "files/a.dll.orig", "files/a.bak-debug"):
            with self.subTest(name=name):
                path = self.put(self.base, name, b"private")
                self.reject("Unexpected base root|private/backup")
                path.unlink()

    def test_no_overwrite(self):
        self.run_package()
        output = Path(self.args.output)
        before = {p.name: p.read_bytes() for p in output.iterdir()}
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.run_package()
        self.assertEqual(before, {p.name: p.read_bytes() for p in output.iterdir()})

    def test_hardlinks_and_symlinks_replaced_without_following(self):
        target = self.put(self.base, "files/target", b"unchanged")
        hard = self.base / "files/hard"
        os.link(target, hard)
        (self.base / "files/link").symlink_to("target")
        for name in ("files/hard", "files/link"):
            packager.replace_file(self.base, name, text="replacement")
        self.assertEqual(target.read_bytes(), b"unchanged")

    def test_output_inside_input(self):
        for root in (self.base, self.overlay):
            self.args.output = str(root / "output")
            self.reject("must not reside")

    def test_missing_provenance_fields(self):
        value = json.loads(self.provenance.read_text())
        value["wine_source"]["revision"] = "short"
        self.provenance.write_text(json.dumps(value))
        self.reject("40-hex")

    def test_failed_publication_rolls_back_own_outputs(self):
        link = os.link
        count = 0
        def fail_second(source, destination):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("simulated publication failure")
            link(source, destination)
        with patch.object(packager.os, "link", side_effect=fail_second):
            self.reject("simulated publication failure")

    def test_wrapper_cli(self):
        result = subprocess.run([str(REPO / "tools/package-proton.sh"), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--source-bundle", result.stdout)


if __name__ == "__main__":
    unittest.main()
