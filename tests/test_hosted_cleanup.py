import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("cleanup", Path(__file__).resolve().parents[1] / "tools/hosted-disk-preflight.py")
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)


class HostedCleanupTests(unittest.TestCase):
    def test_requires_all_hosted_guards(self):
        env = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
               "RUNNER_OS": "Linux", "ImageOS": "ubuntu24", "CONFIRM_EPHEMERAL_CLEANUP": "yes"}
        cleanup.check_runner(env)
        for key in env:
            with self.subTest(key=key), self.assertRaises(ValueError):
                cleanup.check_runner({k: v for k, v in env.items() if k != key})
        with self.assertRaises(ValueError):
            cleanup.check_runner({**env, "RUNNER_ENVIRONMENT": "self-hosted"})

    def test_rejects_arbitrary_or_broad_targets(self):
        for name in ("/", "/home", "/home/deck", "/usr", "/var/lib/docker", "/mnt/swapfile"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                cleanup.validate_target(Path(name), [])

    def test_cleanup_workflow_is_manual_and_has_no_build(self):
        text = (Path(__file__).resolve().parents[1] / ".github/workflows/cleanup-preflight.yml").read_text()
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("runs-on: ubuntu-24.04", text)
        self.assertNotIn("make redist", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("pull_request", text)
