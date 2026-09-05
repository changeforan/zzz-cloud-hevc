"""Synthetic overlay tests; no Wine binaries, compiler, or objdump required."""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


spec = importlib.util.spec_from_file_location(
    "prepare_overlay",
    Path(__file__).resolve().parents[1] / "tools/prepare-proton-overlay.py",
)
overlay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overlay)


class PrepareOverlayTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.base = self.root / "stock"
        self.build = self.root / "build"
        self.output = self.root / "overlay"
        self.provenance = self.root / "provenance.json"
        self.sources = {}
        self.write(self.base / "version", overlay.STOCK_VERSION.encode() + b"\n")
        self.write(self.build / "config.log",
                   b"  $ ../wine/configure --enable-archs=x86_64,i386 --without-unwind\n")
        self.write(self.provenance, b'{ "source": "synthetic", "attested": false }\n', 0o640)
        for stem in sorted(overlay.CRITICAL):
            self.module(stem + ".dll", unix=stem in overlay.CRITICAL_UNIX)
        self.module("display.drv", unix=True)
        self.module("device.sys", unix=True)
        self.module("optional.dll", unix=True)
        for name, source in (("wine", "tools/wine/wine"),
                             ("wineserver", "server/wineserver")):
            relative = Path("files/bin") / name
            self.write(self.base / relative, b"stock executable", 0o755)
            self.sources[relative] = self.write(
                self.build / source, b"\x7fELF" + source.encode(), 0o751)
        # Neither the build-root shell wrapper nor stock Unix loaders belong
        # in the overlay. They deliberately do not have valid ELF magic.
        self.write(self.build / "wine", b"#!/bin/sh\nexit 1\n", 0o755)
        for name in ("wine", "wine64", "wine-preloader", "wine64-preloader"):
            self.write(self.base / overlay.LIB / "x86_64-unix" / name, b"stock loader")
            self.write(self.build / "loader" / name, b"wrong loader")
        self.args = SimpleNamespace(base=self.base, build=self.build,
                                    provenance=self.provenance, output=self.output)
        # Keep real magic validation, copying, hashing, and publication; mock
        # only the external inspector because fixture ELF bodies are synthetic.
        patcher = mock.patch.object(overlay.subprocess, "run", return_value=
                                   subprocess.CompletedProcess(
                                       ["objdump"], 0,
                                       "  NEEDED libm.so.6\n  NEEDED libc.so.6\n"
                                       "  NEEDED libc.so.6\n", ""))
        self.objdump = patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def write(path, data, mode=0o644):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)
        return path

    def module(self, name, unix=False):
        stem = Path(name).stem
        for arch in overlay.ARCHES:
            relative = overlay.LIB / arch / name
            self.write(self.base / relative, b"stock PE")
            self.sources[relative] = self.write(
                self.build / "dlls" / stem / arch / name,
                b"MZ" + arch.encode() + b":" + name.encode(), 0o640)
        if unix:
            relative = overlay.LIB / "x86_64-unix" / (stem + ".so")
            self.write(self.base / relative, b"stock Unix")
            self.sources[relative] = self.write(
                self.build / "dlls" / stem / "x86_64-unix" / (stem + ".so"),
                b"\x7fELF" + stem.encode(), 0o750)

    def prepare(self):
        with contextlib.redirect_stdout(io.StringIO()):
            overlay.prepare(self.args)

    def assert_clean(self):
        self.assertFalse(os.path.lexists(self.output))
        self.assertEqual(list(self.root.glob(".overlay.tmp-*")), [])

    def assert_rejected(self, message, exception=ValueError):
        with self.assertRaisesRegex(exception, message):
            self.prepare()
        self.assert_clean()

    def test_success_hashes_modes_provenance_pairs_and_loader_selection(self):
        # Input links must become standalone regular files in the output.
        relative = overlay.LIB / "x86_64-windows" / "display.drv"
        source = self.sources[relative]
        target = self.build / "linked-display.drv"
        source.rename(target)
        source.symlink_to(target)
        self.prepare()
        manifest = json.loads((self.output / "overlay-manifest.json").read_text())
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["wine_base_commit"], overlay.WINE_BASE_COMMIT)
        self.assertEqual(manifest["configure_args"], overlay.CONFIGURE_ARGS)
        self.assertEqual(set(manifest["files"]), {p.as_posix() for p in self.sources})
        for relative, source in self.sources.items():
            with self.subTest(path=relative):
                destination = self.output / relative
                self.assertFalse(destination.is_symlink())
                self.assertEqual(destination.read_bytes(), source.read_bytes())
                self.assertEqual(stat.S_IMODE(destination.stat().st_mode),
                                 stat.S_IMODE(source.stat().st_mode))
                self.assertEqual(manifest["files"][relative.as_posix()],
                                 hashlib.sha256(source.read_bytes()).hexdigest())
        expected_elf = {p.as_posix() for p in self.sources
                        if p.suffix == ".so" or p.parent == Path("files/bin")}
        self.assertEqual(manifest["dependencies"],
                         {p: ["libc.so.6", "libm.so.6"] for p in expected_elf})
        self.assertEqual(self.objdump.call_count, len(expected_elf))
        for call in self.objdump.call_args_list:
            self.assertEqual(call.args[0][:2], ["objdump", "-p"])
            self.assertEqual(call.kwargs["env"]["LC_ALL"], "C")
        copied_provenance = self.output / "build-provenance.json"
        self.assertEqual(copied_provenance.read_bytes(), self.provenance.read_bytes())
        self.assertEqual(stat.S_IMODE(copied_provenance.stat().st_mode), 0o640)
        self.assertEqual({p.relative_to(self.output).as_posix()
                          for p in self.output.rglob("*") if p.is_file()},
                         set(manifest["files"]) |
                         {"build-provenance.json", "overlay-manifest.json"})
        self.assertEqual(list(self.root.glob(".overlay.tmp-*")), [])

    def test_optional_missing_unix_preserves_all_stock_halves(self):
        missing = overlay.LIB / "x86_64-unix" / "optional.so"
        self.sources[missing].unlink()
        self.prepare()
        manifest = json.loads((self.output / "overlay-manifest.json").read_text())
        for arch, name in (("x86_64-windows", "optional.dll"),
                           ("i386-windows", "optional.dll"),
                           ("x86_64-unix", "optional.so")):
            relative = overlay.LIB / arch / name
            self.assertNotIn(relative.as_posix(), manifest["files"])
            self.assertFalse((self.output / relative).exists())
            self.assertTrue((self.base / relative).is_file())

    def test_missing_critical_halves_are_rejected(self):
        relatives = [overlay.LIB / arch / (stem + ".dll")
                     for stem in sorted(overlay.CRITICAL) for arch in overlay.ARCHES]
        relatives += [overlay.LIB / "x86_64-unix" / (stem + ".so")
                      for stem in sorted(overlay.CRITICAL_UNIX)]
        for relative in relatives:
            with self.subTest(path=relative):
                source = self.sources[relative]
                data = source.read_bytes()
                source.unlink()
                try:
                    self.assert_rejected("incomplete critical module pair")
                finally:
                    source.write_bytes(data)

    def test_drv_and_sys_missing_pe_half_omit_the_entire_pair(self):
        for name in ("display.drv", "device.sys"):
            self.sources[overlay.LIB / "i386-windows" / name].unlink()
        self.prepare()
        manifest = json.loads((self.output / "overlay-manifest.json").read_text())
        for name in ("display.drv", "device.sys"):
            with self.subTest(name=name):
                relatives = [overlay.LIB / arch / name for arch in overlay.ARCHES]
                relatives.append(overlay.LIB / "x86_64-unix" / (Path(name).stem + ".so"))
                for relative in relatives:
                    self.assertNotIn(relative.as_posix(), manifest["files"])
                    self.assertFalse((self.output / relative).exists())
                    self.assertTrue((self.base / relative).is_file())

    def test_invalid_or_empty_pe_is_rejected(self):
        source = self.sources[overlay.LIB / "i386-windows" / "ntdll.dll"]
        for data, message in ((b"not PE", "invalid PE MZ artifact"),
                              (b"", "empty build artifact")):
            with self.subTest(data=data):
                source.write_bytes(data)
                self.assert_rejected(message)

    def test_missing_loader_or_server_is_rejected_and_cleaned(self):
        for name in ("wine", "wineserver"):
            with self.subTest(name=name):
                source = self.sources[Path("files/bin") / name]
                data = source.read_bytes()
                source.unlink()
                try:
                    self.assert_rejected(name, FileNotFoundError)
                finally:
                    source.write_bytes(data)

    def test_invalid_elf_is_rejected_and_staging_is_cleaned(self):
        for relative in (Path("files/bin/wine"),
                         overlay.LIB / "x86_64-unix" / "ntdll.so"):
            with self.subTest(path=relative):
                source = self.sources[relative]
                data = source.read_bytes()
                source.write_bytes(b"not ELF")
                try:
                    self.assert_rejected("invalid ELF artifact")
                finally:
                    source.write_bytes(data)

    def test_invalid_input_metadata_is_rejected_before_staging(self):
        cases = [(self.base / "version", b"wrong version\n", "stock Proton version"),
                 (self.build / "config.log", b"$ configure --without-unwind\n",
                  "configure invocation requires"),
                 (self.build / "config.log",
                  b"$ configure --enable-archs=x86_64,i386 --without-unwind --with-unwind\n",
                  "conflicting architecture/unwind"),
                 (self.provenance, b"not json", "Expecting value")]
        for path, data, message in cases:
            with self.subTest(path=path, data=data):
                original = path.read_bytes()
                path.write_bytes(data)
                try:
                    self.assert_rejected(message)
                finally:
                    path.write_bytes(original)
        self.objdump.assert_not_called()

    def test_existing_output_is_never_modified(self):
        for kind in ("file", "directory", "dangling symlink"):
            with self.subTest(kind=kind):
                if kind == "file":
                    self.output.write_bytes(b"keep me")
                elif kind == "directory":
                    self.output.mkdir()
                else:
                    self.output.symlink_to(self.root / "absent")
                try:
                    with self.assertRaisesRegex(ValueError, "output already exists"):
                        self.prepare()
                    self.assertTrue(os.path.lexists(self.output))
                    if kind == "file":
                        self.assertEqual(self.output.read_bytes(), b"keep me")
                    elif kind == "directory":
                        self.assertEqual(list(self.output.iterdir()), [])
                    else:
                        self.assertEqual(self.output.readlink(), self.root / "absent")
                    self.assertEqual(list(self.root.glob(".overlay.tmp-*")), [])
                finally:
                    if kind == "directory":
                        self.output.rmdir()
                    else:
                        self.output.unlink()
        self.objdump.assert_not_called()

    def test_libunwind_dependency_is_rejected_and_staging_is_cleaned(self):
        for library in ("libunwind.so.8", "libunwind-x86_64.so.8", "LIBUNWIND.so"):
            with self.subTest(library=library):
                self.objdump.return_value = subprocess.CompletedProcess(
                    ["objdump"], 0, "  NEEDED " + library + "\n", "")
                self.assert_rejected("forbidden libunwind dependency")

    def test_objdump_failure_is_rejected_and_staging_is_cleaned(self):
        self.objdump.return_value = subprocess.CompletedProcess(
            ["objdump"], 1, "", "synthetic inspection failure")
        self.assert_rejected("objdump failed.*synthetic inspection failure")
        inspected_path = Path(self.objdump.call_args.args[0][-1])
        self.assertTrue(inspected_path.is_relative_to(self.root))
        self.assertTrue(inspected_path.relative_to(self.root).parts[0].startswith(
            ".overlay.tmp-"))
        self.assertFalse(inspected_path.exists())


if __name__ == "__main__":
    unittest.main()
