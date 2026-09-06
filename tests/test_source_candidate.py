import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("source_candidate", REPO / "tools/package-source-candidate.py")
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)


class SourceCandidateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source, self.build = self.root / "proton", self.root / "build"
        self.redist = self.build / "redist"
        self.pins = candidate.p.read_json(REPO / "packaging/source-build-pins.json")
        self.toolmanifest = ('"manifest" { "version" "2" "commandline" "/proton %verb%" '
                             '"require_tool_appid" "4183110" "compatmanager_layer_name" "proton" }\n')
        for name in ("proton", "LICENSE", "LICENSE.OFL", "PATENTS.AV1", "filelock.py"):
            self.put(self.redist, name, b"official redist content")
        self.put(self.redist, "version", b"1234567890 proton-11.0-2\n")
        self.put(self.redist, "toolmanifest.vdf", self.toolmanifest.encode())
        self.put(self.source, "toolmanifest_x86_64.vdf", self.toolmanifest.encode())
        self.put(self.source, "wine/dlls/wined3d/hevc.c", b"patched source")
        self.put(self.source, "wine/dependency/build/source.c", b"nested build must remain")
        self.put(self.source, "wine/dependency/test.orig", b"real upstream test fixture")
        self.put(self.source, "wine/.git", b"gitdir: elsewhere")
        self.put(self.source, "build/omit", b"top build state")
        self.put(self.source, ".cache/omit", b"cache")
        self.put(self.source, "contrib/xalia.zip", b"downloaded prebuilt")
        self.put(self.build, "src-wine/include/wine/vulkan.h", b"generated source")
        self.put(self.build, "Makefile", b"official generated makefile")
        self.put(self.root, "metadata/wine.diff", b"patch evidence")
        for name in candidate.p.CRITICAL:
            self.put(self.redist, name, b"MZfixture" if name.endswith(".dll") else b"\x7fELFfixture", 0o755)
        self.put(self.redist, "files/share/default_pfx/omit", b"generated prefix")
        (self.redist / "files/share/default_pfx/outside").symlink_to("/")
        (self.redist / "files/bin/msidb").symlink_to("wine")
        self.args = argparse.Namespace(source_dir=str(self.source), build_dir=str(self.build),
                                       version="test-1", output=str(self.root / "output"),
                                       sdk_image=self.pins["sdk_image"])
        self.mock_git = patch.object(candidate, "git", side_effect=self.git).start()
        self.addCleanup(patch.stopall)

    def put(self, root, name, data, mode=0o644):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)
        return path

    def git(self, root, *args):
        if args == ("rev-parse", "HEAD"):
            return self.pins["wine_base_commit" if root.name == "wine" else "proton_commit"] + "\n"
        if args[0] == "submodule":
            return " " + self.pins["wine_base_commit"] + " wine (pinned)\n"
        if args[0] in ("diff", "status"):
            return ""
        self.fail(f"Unexpected Git execution: {args}")

    def run_package(self):
        with contextlib.redirect_stdout(io.StringIO()):
            candidate.package(self.args)

    def reject(self, message):
        with self.assertRaisesRegex((ValueError, OSError, RuntimeError), message):
            self.run_package()
        self.assertFalse(Path(self.args.output).exists())
        self.assertEqual(list(self.root.glob(".source-candidate-*")), [])

    def test_official_redist_not_stock_and_full_snapshot(self):
        self.run_package()
        output = Path(self.args.output)
        manifest = json.loads((output / "build-manifest.json").read_text())
        self.assertFalse(manifest["release_claim"])
        self.assertEqual(manifest["source_snapshot_status"], "INCOMPLETE / REVIEW REQUIRED")
        self.assertEqual(manifest["redist_version"], "1234567890 proton-11.0-2")
        self.assertNotEqual(manifest["redist_version"], candidate.p.BASE_VERSION)
        with tarfile.open(output / "binary.tar.gz") as tar:
            prefix = manifest["tool_id"] + "/"
            read = lambda name: tar.extractfile(prefix + name).read()
            self.assertEqual(read("proton-original"), b"official redist content")
            self.assertEqual(read("proton"), (REPO / "packaging/proton-launcher.py").read_bytes())
            self.assertIn(b"proton-hevc-source-test-1", read("version"))
            self.assertEqual(read("toolmanifest.vdf").decode(), self.toolmanifest)
            self.assertIn(b"LOCAL TEST ONLY", read("compatibilitytool.vdf"))
            self.assertIn(b'"from_oslist" "windows"', read("compatibilitytool.vdf"))
            self.assertIn(b'"to_oslist" "linux"', read("compatibilitytool.vdf"))
            self.assertFalse(any("default_pfx" in name for name in tar.getnames()))
            self.assertFalse(any("steampipe_fixups" in name for name in tar.getnames()))
            for name, digest in manifest["files"].items():
                self.assertEqual(hashlib.sha256(read(name)).hexdigest(), digest)
        with tarfile.open(output / "source-snapshot.tar.gz") as tar:
            names = tar.getnames()
            for name in ("proton/contrib/xalia.zip", "proton/wine/dependency/build/source.c",
                         "proton/wine/dependency/test.orig", "build/src-wine/include/wine/vulkan.h",
                         "metadata/wine.diff", "evidence/revisions.json", "evidence/build/Makefile",
                         "evidence/LOCAL-TEST-ONLY.txt"):
                self.assertIn("source-snapshot/" + name, names)
            self.assertFalse(any(".git" in Path(n).parts or ".cache" in Path(n).parts
                                 or "/proton/build/" in n for n in names))
        for line in (output / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split("  ")
            self.assertEqual(candidate.p.sha256(output / name), digest)
        self.assertEqual((self.redist / "version").read_text(), "1234567890 proton-11.0-2\n")

    def test_launch_defaults(self):
        spec = importlib.util.spec_from_file_location("launcher", REPO / "packaging/proton-launcher.py")
        launcher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(launcher)
        env = launcher.launch_environment({"WINEDLLOVERRIDES": "dxgi=n;foo=n"})
        self.assertEqual(env["PROTON_USE_WINED3D"], "1")
        self.assertEqual(env["WINE_D3D_CONFIG"], "renderer=vulkan")
        self.assertEqual(env["WINEDLLOVERRIDES"], "foo=n;dxgi=b")

    def test_wrong_sdk(self):
        self.args.sdk_image = "sdk:latest"
        self.reject("exact pinned digest")

    def test_wrong_heads(self):
        for target in (self.source, self.source / "wine"):
            with self.subTest(target=target):
                self.mock_git.side_effect = lambda root, *args: "0" * 40 if root == target and args[0] == "rev-parse" else self.git(root, *args)
                self.reject("HEAD differs")

    def test_uninitialized_submodule(self):
        self.mock_git.side_effect = lambda root, *args: "-" + "a" * 40 + " wine\n" if args[0] == "submodule" else self.git(root, *args)
        self.reject("Recursive submodules")

    def test_dirty_proton(self):
        self.mock_git.side_effect = lambda root, *args: "proton\n" if root == self.source and args[0] == "diff" else self.git(root, *args)
        self.reject("tracked changes outside Wine")

    def test_missing_critical(self):
        (self.redist / "files/bin/wineserver").unlink()
        self.reject("Missing critical artifact")

    def test_runtime_mismatch(self):
        (self.redist / "toolmanifest.vdf").write_text(self.toolmanifest.replace("4183110", "1"))
        self.reject("Runtime toolmanifest differs")

    def test_invalid_version(self):
        self.args.version = "../bad"
        self.reject("Invalid VERSION")

    def test_no_overwrite_even_empty_or_symlink(self):
        output = Path(self.args.output)
        output.mkdir()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.run_package()
        self.assertEqual(list(output.iterdir()), [])
        output.rmdir()
        output.symlink_to(self.root / "missing")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.run_package()
        self.assertTrue(output.is_symlink())

    def test_atomic_publication_no_clobber(self):
        left, right = self.root / "left", self.root / "right"
        left.mkdir()
        right.mkdir()
        with self.assertRaises(FileExistsError):
            candidate.publish(left, right)
        self.assertTrue(left.is_dir())
        self.assertTrue(right.is_dir())

    def test_unsafe_symlinks_in_binary_and_source(self):
        for root in (self.redist, self.source, self.root / "metadata", self.build / "src-wine"):
            with self.subTest(root=root):
                link = root / "unsafe"
                link.symlink_to("/etc/passwd")
                self.reject("[Ss]ymlink")
                link.unlink()

    def test_optional_fixups_sanitized(self):
        self.put(self.redist, "steampipe_fixups.json", json.dumps({
            "id": "upstream", "empty_dirs": ["files/share/default_pfx/foo"],
            "no_write_paths": ["files/bin/wine"]}).encode())
        self.run_package()
        with tarfile.open(Path(self.args.output) / "binary.tar.gz") as tar:
            fixed = json.load(tar.extractfile("Proton-HEVC-Source-test-1/steampipe_fixups.json"))
        self.assertEqual(fixed["empty_dirs"], [])
        self.assertNotEqual(fixed["id"], "upstream")

    def test_generated_target_source_symlink_is_preserved(self):
        link = self.source / "raw-nnet-init"
        link.symlink_to("nnet-init")
        self.run_package()
        with tarfile.open(Path(self.args.output) / "source-snapshot.tar.gz") as tar:
            member = tar.getmember("source-snapshot/proton/raw-nnet-init")
            self.assertTrue(member.issym())
            self.assertEqual(member.linkname, "nnet-init")

    def test_dangling_escape_source_symlink_rejected(self):
        (self.source / "escape").symlink_to("../absent")
        self.reject("Unsafe source symlink")

    def test_dangling_binary_symlink_still_rejected(self):
        (self.redist / "missing-link").symlink_to("absent")
        self.reject("No such file")


if __name__ == "__main__":
    unittest.main()
