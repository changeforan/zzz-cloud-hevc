import importlib.util
import io
import hashlib
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "package_inputs", Path(__file__).resolve().parents[1] / "tools/fetch-package-input.py")
inputs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inputs)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def archive(self, entries):
        path = self.root / "input.tar.gz"
        with tarfile.open(path, "w:gz") as tar:
            for name, kind, value in entries:
                info = tarfile.TarInfo(name)
                if kind == "file":
                    info.size = len(value)
                    tar.addfile(info, io.BytesIO(value))
                elif kind == "link":
                    info.type = tarfile.SYMTYPE
                    info.linkname = value
                    tar.addfile(info)
                else:
                    info.type = tarfile.LNKTYPE
                    info.linkname = value
                    tar.addfile(info)
        return path

    def test_safe_relative_link(self):
        archive = self.archive([("root/a", "file", b"ok"), ("root/b", "link", "a")])
        inputs.extract_archive(archive, self.root / "out")
        self.assertEqual((self.root / "out/b").read_bytes(), b"ok")

    def test_reject_unsafe_archives(self):
        cases = [
            [("../escape", "file", b"bad")],
            [("/absolute", "file", b"bad")],
            [("root/a", "link", "../../escape")],
            [("root/a", "link", "/etc/passwd")],
            [("root/a", "file", b"a"), ("root/a", "file", b"b")],
            [("root/a", "file", b"a"), ("other/a", "file", b"b")],
            [("root/a", "hardlink", "root/b")],
            [("root/a", "link", "b"), ("root/a/file", "file", b"bad")],
            [("root/a", "link", "missing")],
        ]
        for entries in cases:
            with self.subTest(entries=entries):
                archive = self.archive(entries)
                with self.assertRaises((ValueError, OSError, tarfile.TarError)):
                    inputs.extract_archive(archive, self.root / "out")
                self.assertFalse((self.root / "out").exists())

    def test_download_checksum_gate(self):
        archive = self.archive([("root/a", "file", b"ok")]).read_bytes()
        def download(command, **kwargs):
            Path(command[command.index("--output") + 1]).write_bytes(archive)
        for checksum, succeeds in (("0" * 64, False), (hashlib.sha256(archive).hexdigest(), True)):
            with self.subTest(succeeds=succeeds):
                argv = ["fetch", "--url", "https://example.test/base.tar.gz", "--sha256", checksum,
                        "--output", str(self.root / "download.tar.gz"), "--extract-to", str(self.root / "out")]
                with patch.object(sys, "argv", argv), patch.object(inputs.subprocess, "run", side_effect=download):
                    if succeeds:
                        inputs.main()
                        self.assertEqual((self.root / "out/a").read_bytes(), b"ok")
                    else:
                        with self.assertRaises(SystemExit), patch.object(sys, "stderr", io.StringIO()):
                            inputs.main()
                        self.assertFalse((self.root / "out").exists())
                        self.assertFalse((self.root / "download.tar.gz").exists())


if __name__ == "__main__":
    unittest.main()
