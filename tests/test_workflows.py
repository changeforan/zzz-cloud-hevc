"""Static guardrails without third-party YAML dependencies on CI."""

from pathlib import Path
import re
import unittest


class WorkflowTests(unittest.TestCase):
    def test_source_route_is_manual_and_read_only(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / ".github/workflows/source-candidate.yml").read_text()
        self.assertIn("default: preflight", text)
        self.assertIn("MIN_FREE_GIB: '60'", text)
        self.assertIn("-manifest-only", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("pull_request_target", text)
        self.assertNotIn("gh release", text)
        for reference in re.findall(r"uses: ([^\s#]+)", text):
            self.assertRegex(reference, r"@[a-f0-9]{40}$")

    def test_manual_packaging_and_read_only_permissions(self):
        path = Path(__file__).resolve().parents[1] / ".github/workflows/package.yml"
        text = path.read_text()
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("pull_request_target", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("gh release", text)
        self.assertIn("contents: read", text)
        # User input stays in quoted environment variables, not shell source.
        run_blocks = re.findall(r"run: \|\n((?:          .*\n|\n)+)", text)
        self.assertTrue(run_blocks)
        for block in run_blocks:
            self.assertNotIn("${{", block)
        for reference in re.findall(r"uses: ([^\s#]+)", text):
            self.assertRegex(reference, r"@[a-f0-9]{40}$")
        for name in ("base_sha256", "overlay_sha256", "source_sha256"):
            self.assertIn(name, text)


if __name__ == "__main__":
    unittest.main()
