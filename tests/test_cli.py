import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CLITests(unittest.TestCase):
    def test_cli_emits_a_report_for_the_documented_demo(self):
        result = subprocess.run([sys.executable, str(ROOT / "cli.py"), str(ROOT / "data" / "demo.json")],
                                capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["source"]["kind"], "demo")
        self.assertIn("metrics", report)

    def test_bad_file_returns_nonzero_and_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as folder:
            bad = Path(folder) / "input.json"
            bad.write_text("[]", encoding="utf-8")
            target = Path(folder) / "output.json"
            result = subprocess.run([sys.executable, str(ROOT / "cli.py"), str(bad), "--output", str(target)],
                                    capture_output=True, encoding="utf-8")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("error", json.loads(result.stderr or "{}"))
            self.assertFalse(target.exists())

    def test_invalid_unicode_does_not_erase_existing_report(self):
        with tempfile.TemporaryDirectory() as folder:
            payload = json.loads((ROOT / "data" / "demo.json").read_text(encoding="utf-8"))
            payload["company"] = "\ud800"
            source = Path(folder) / "input.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            target = Path(folder) / "report.json"
            target.write_text("previous-report", encoding="utf-8")
            result = subprocess.run([sys.executable, str(ROOT / "cli.py"), str(source), "--output", str(target)],
                                    capture_output=True, encoding="utf-8")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(target.read_text(encoding="utf-8"), "previous-report")


if __name__ == "__main__":
    unittest.main()
