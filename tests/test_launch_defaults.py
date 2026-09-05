import importlib.util
from pathlib import Path
import unittest
import tempfile
import shutil
import subprocess
import json
import sys

spec = importlib.util.spec_from_file_location("launcher", Path(__file__).resolve().parents[1] / "packaging/proton-launcher.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class LaunchDefaultsTests(unittest.TestCase):
    def test_empty_environment(self):
        original = {}
        env = launcher.launch_environment(original)
        self.assertEqual(env, {"PROTON_USE_WINED3D": "1", "WINE_D3D_CONFIG": "renderer=vulkan", "WINEDLLOVERRIDES": "dxgi=b"})
        self.assertEqual(original, {})

    def test_gaming_override_preserves_unrelated_entries(self):
        env = launcher.launch_environment({"WINEDLLOVERRIDES": "dxgi=n;dinput8=n;*DXGI.dll,foo=n,b;bar="})
        self.assertEqual(env["WINEDLLOVERRIDES"], "dinput8=n;foo=n,b;bar=;dxgi=b")

    def test_opt_out(self):
        original = {"PROTON_HEVC_DEFAULTS": "0", "WINEDLLOVERRIDES": "dxgi=n"}
        self.assertEqual(launcher.launch_environment(original), original)

    def test_explicit_renderer_and_backend(self):
        env = launcher.launch_environment({"PROTON_USE_WINED3D": "0", "WINE_D3D_CONFIG": "renderer=gl", "WINEDLLOVERRIDES": "dxgi=n"})
        self.assertEqual(env["WINEDLLOVERRIDES"], "dxgi=n")
        self.assertEqual(env["WINE_D3D_CONFIG"], "renderer=gl")

    def test_exec_forwards_arguments_environment_and_exit_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copyfile(spec.origin, root / "proton")
            (root / "proton-original").write_text(
                'import json,os,sys\nprint(json.dumps([sys.argv[1:],dict(os.environ)]))\nsys.exit(7)\n')
            result = subprocess.run([sys.executable, str(root / "proton"), "run", "path with spaces.exe"],
                                    env={"WINEDLLOVERRIDES": "dxgi=n"}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 7)
            args, env = json.loads(result.stdout)
            self.assertEqual(args, ["run", "path with spaces.exe"])
            self.assertEqual(env["WINEDLLOVERRIDES"], "dxgi=b")
            self.assertEqual(env["PROTON_USE_WINED3D"], "1")
