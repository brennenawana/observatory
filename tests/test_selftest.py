from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from observatory import FileCapture, JsonlLedger, ObservatoryError, request_key
from observatory.selftest import main


class SelftestBoundaryTests(unittest.TestCase):
    def test_capture_rejects_unsupported_values_before_storage(self) -> None:
        class SensitiveObject:
            def __str__(self) -> str:
                return "planted-sensitive-value"

        with tempfile.TemporaryDirectory() as tmp:
            capture = FileCapture(Path(tmp) / "capture.jsonl", mode="record")
            with self.assertRaisesRegex(ObservatoryError, "JSON-compatible values"):
                capture.through(request_key("object"), SensitiveObject)

            self.assertFalse(capture.path.exists())
            self.assertEqual(capture.recorded, 0)

    def test_capture_skips_final_row_truncated_inside_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capture.jsonl"
            path.write_bytes(
                b'{"key":"known","value":{"ok":true}}\n'
                b'{"key":"broken","value":"\xe2\x82'
            )

            capture = FileCapture(path, mode="replay")

            self.assertEqual(capture.through("known", lambda: None), {"ok": True})
            self.assertEqual(len(capture), 1)

    def test_selftest_uses_supplied_ledger_and_capture_factories(self) -> None:
        created = {"capture": 0, "ledger": 0}

        class TrackingLedger(JsonlLedger):
            def __init__(self, path: Path, **kwargs) -> None:
                created["ledger"] += 1
                super().__init__(path, **kwargs)

        class TrackingCapture(FileCapture):
            def __init__(self, path: Path, **kwargs) -> None:
                created["capture"] += 1
                super().__init__(path, **kwargs)

        with redirect_stdout(io.StringIO()):
            result = main(
                ledger_factory=TrackingLedger,
                capture_factory=TrackingCapture,
            )

        self.assertEqual(result, 0)
        self.assertGreater(created["ledger"], 0)
        self.assertGreater(created["capture"], 0)


if __name__ == "__main__":
    unittest.main()
