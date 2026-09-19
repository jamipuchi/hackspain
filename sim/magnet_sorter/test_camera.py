import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from camera import TableCalibration, draw_metric_grid
import scene_def as sd


class MetricGridTests(unittest.TestCase):
    def test_falls_back_to_in_frame_signed_axis_labels(self):
        cal = TableCalibration()
        cal.H_inv = np.array([[2400.0, 0.0, 100.0], [0.0, -2000.0, 100.0], [0.0, 0.0, 1.0]])
        cal.H = np.linalg.inv(cal.H_inv)
        frame = np.zeros((600, 640, 3), np.uint8)
        labels = []
        original = cv2.putText

        def capture(image, text, origin, *args, **kwargs):
            if text.startswith(("x", "y")):
                labels.append((text, origin))
            return original(image, text, origin, *args, **kwargs)

        with patch("camera.cv2.putText", side_effect=capture):
            draw_metric_grid(frame, cal)

        self.assertTrue(any(text.startswith("x") for text, _ in labels))
        self.assertTrue(any(text.startswith("y") for text, _ in labels))
        self.assertTrue(all("+" in text or "-" in text for text, _ in labels))
        for text, (x, y) in labels:
            (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
            self.assertGreaterEqual(x, 2)
            self.assertLess(x + tw + 2, frame.shape[1])
            self.assertGreaterEqual(y - th, 2)
            self.assertLess(y + base + 2, frame.shape[0])

        bx, by = sd.BOARD["center"]
        hx, hy = sd.BOARD["size"][0] / 2, sd.BOARD["size"][1] / 2
        self.assertGreater(cal.table_to_pixel((bx + hx), by)[0], frame.shape[1])
        self.assertLess(cal.table_to_pixel(bx, by + hy)[1], 0)


if __name__ == "__main__":
    unittest.main()
