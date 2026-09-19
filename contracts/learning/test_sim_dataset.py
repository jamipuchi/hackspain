"""Tests for the image-only simulator dataset seam."""

from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path

import cv2
import numpy as np

from sim_dataset import extract_features, generate_dataset


class SimDatasetTests(unittest.TestCase):
    def test_empty_frame_is_invalid(self) -> None:
        result = extract_features(np.zeros((80, 80, 3), dtype=np.uint8))

        self.assertFalse(result["valid"])
        self.assertEqual(result["values"]["area_px"], 0.0)

    def test_features_depend_on_pixels_without_metadata(self) -> None:
        rectangle = np.full((120, 120, 3), 30, dtype=np.uint8)
        circle = rectangle.copy()
        cv2.rectangle(rectangle, (20, 50), (100, 70), (220, 220, 220), -1)
        cv2.circle(circle, (60, 60), 26, (220, 220, 220), -1)

        rectangle_features = extract_features(rectangle)
        circle_features = extract_features(circle)

        self.assertTrue(rectangle_features["valid"])
        self.assertTrue(circle_features["valid"])
        self.assertGreater(rectangle_features["values"]["aspect_ratio"], circle_features["values"]["aspect_ratio"])
        self.assertNotEqual(rectangle_features["values"]["circularity"], circle_features["values"]["circularity"])

    @unittest.skipUnless(
        os.environ.get("RUN_RENDER_TESTS") == "1",
        "requires host CoreGraphics for MuJoCo rendering; run with RUN_RENDER_TESTS=1",
    )
    def test_capture_generates_requested_splits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            records = generate_dataset(directory)

            counts = {split: sum(record["split"] == split for record in records) for split in ("train", "correction", "validation", "test")}
            self.assertEqual(counts, {"train": 3, "correction": 18, "validation": 18, "test": 30})
            self.assertTrue((Path(directory) / "records.json").is_file())
            self.assertTrue((Path(directory) / "contact_sheet.png").is_file())
            self.assertEqual(records[0]["evaluator"]["label_source"], "simulated_demonstration")
            self.assertNotIn("expected_kind", records[0])


if __name__ == "__main__":
    unittest.main()
