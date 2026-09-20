#!/usr/bin/env python3
"""Focused contract tests for the independent score checker."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CHECKER = ROOT / "audit_rolling_scores.py"


def run_capture(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), str(path)],
        capture_output=True,
        check=False,
        text=True,
    )


class ScoreAuditTest(unittest.TestCase):
    def test_edge_fixture_matches_each_snapshot(self) -> None:
        result = run_capture(ROOT / "edge-case-fixture.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshots = json.loads(result.stdout)["snapshots"]
        self.assertEqual(len(snapshots), 3)
        self.assertTrue(all(item["engine_comparison"]["pass"] for item in snapshots))
        self.assertEqual(snapshots[0]["unresolved"], {
            "denominator": 5,
            "numerator": 1,
            "value": 0.2,
        })
        self.assertEqual(snapshots[1]["sorting_accuracy"], {
            "denominator": 6,
            "numerator": 3,
            "value": 0.5,
        })
        self.assertEqual(snapshots[2]["defect_capture"], {
            "denominator": 0,
            "numerator": 0,
            "value": None,
        })

    def test_mismatch_exits_one(self) -> None:
        result = run_capture(ROOT / "mismatch-fixture.json")
        self.assertEqual(result.returncode, 1, result.stderr)
        comparison = json.loads(result.stdout)["snapshots"][0]["engine_comparison"]
        self.assertFalse(comparison["pass"])
        self.assertTrue(any("eligible_objects" in item for item in comparison["differences"]))

    def test_single_snapshot_root_format_matches(self) -> None:
        fixture = json.loads((ROOT / "edge-case-fixture.json").read_text())
        capture = copy.deepcopy(fixture["snapshots"][0])
        capture["window_seconds"] = fixture["window_seconds"]
        capture["settling_seconds"] = fixture["settling_seconds"]
        result = self.run_temporary(capture)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_incomplete_export_exits_two(self) -> None:
        result = run_capture(ROOT / "incomplete-export-fixture.json")
        self.assertEqual(result.returncode, 2)
        self.assertIn("rolling_scores.clock is required", result.stderr)

    def test_non_boolean_manual_flag_exits_two(self) -> None:
        result = run_capture(ROOT / "invalid-manual-fixture.json")
        self.assertEqual(result.returncode, 2)
        self.assertIn("manual_injection must be true or false", result.stderr)

    def test_same_epoch_cannot_change_version(self) -> None:
        capture = json.loads((ROOT / "edge-case-fixture.json").read_text())
        capture["snapshots"][1]["versions"]["source_revision"] = "source-changed"
        capture["snapshots"][1]["rolling_scores"]["versions"]["source_revision"] = "source-changed"
        result = self.run_temporary(capture)
        self.assertEqual(result.returncode, 2)
        self.assertIn("spans different score versions", result.stderr)

    def test_missing_source_revision_exits_two(self) -> None:
        fixture = json.loads((ROOT / "edge-case-fixture.json").read_text())
        capture = copy.deepcopy(fixture["snapshots"][0])
        del capture["versions"]["source_revision"]
        result = self.run_temporary(capture)
        self.assertEqual(result.returncode, 2)
        self.assertIn("versions.source_revision must be non-empty", result.stderr)

    def test_duplicate_object_in_epoch_exits_two(self) -> None:
        fixture = json.loads((ROOT / "edge-case-fixture.json").read_text())
        capture = copy.deepcopy(fixture["snapshots"][0])
        capture["rows"].append(copy.deepcopy(capture["rows"][0]))
        result = self.run_temporary(capture)
        self.assertEqual(result.returncode, 2)
        self.assertIn("duplicate object_id", result.stderr)

    def test_rows_cannot_be_shared_across_snapshots(self) -> None:
        capture = json.loads((ROOT / "edge-case-fixture.json").read_text())
        capture["rows"] = capture["snapshots"][0].pop("rows")
        result = self.run_temporary(capture)
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot be shared across snapshots", result.stderr)

    def run_temporary(self, capture: dict) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.json"
            path.write_text(json.dumps(capture))
            return run_capture(path)


if __name__ == "__main__":
    unittest.main()
