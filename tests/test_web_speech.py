import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "Node.js is required for the browser speech helper test")
class BrowserSpeechTests(unittest.TestCase):
    def test_final_recognition_result_is_consumed_once(self):
        helper = (ROOT / "web" / "speech.js").read_text(encoding="utf-8")
        assertions = r"""
const api = globalThis.RipwaveSpeech;
const handled = new Set();
const results = [
  { isFinal: false, 0: { transcript: 'Raise your', confidence: 0.9 } },
  { isFinal: true, 0: { transcript: 'right arm.', confidence: 0.91 } },
  { isFinal: true, 0: { transcript: 'now', confidence: 0.88 } }
];
const first = api.consumeFinalResults(results, 0, handled);
if (first.transcript !== 'right arm. now' || first.confidence !== 0.88) throw new Error(JSON.stringify(first));
const duplicate = api.consumeFinalResults(results, 0, handled);
if (duplicate.transcript !== '') throw new Error('Duplicate final result was consumed twice');
"""
        result = subprocess.run(
            [shutil.which("node"), "-"], input=helper + assertions,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
