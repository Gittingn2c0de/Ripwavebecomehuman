import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SpatialWorkspaceTests(unittest.TestCase):
    def test_workspace_controller_is_served_and_has_interaction_contract(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        controller = (ROOT / "web" / "spatial-workspace.js").read_text(encoding="utf-8")
        self.assertNotIn('class="rail"', html)
        self.assertIn('id="traceNav"', html)
        self.assertNotIn("railEvents", html)
        self.assertIn("RIPWAVE BECOME HUMAN", html)
        self.assertIn("radial-gradient(circle", (ROOT / "web" / "style.css").read_text(encoding="utf-8"))
        for node in ("core", "medium", "input", "simulation"):
            self.assertIn(f'data-spatial-node="{node}"', html)
        for hook in ("pointerdown", "pointermove", "pointerup", "spatial-snapped",
                     "spatialThreadsToggle", "spatialReset", "prefers-reduced-motion",
                     "ripwave-is-human-spatial-layout", "saveLayout"):
            self.assertIn(hook, controller)
        self.assertIn('/spatial-workspace.js', html)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for JavaScript syntax validation")
    def test_workspace_controller_has_valid_javascript(self):
        result = subprocess.run(
            [shutil.which("node"), "--check", str(ROOT / "web" / "spatial-workspace.js")],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
