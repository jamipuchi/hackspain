import json
from pathlib import Path
import tempfile
import unittest

from run_characterization import completed_metrics


class CharacterizationRunnerTest(unittest.TestCase):
    def test_metrics_alone_is_not_a_complete_run(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "metrics.json").write_text("{}")

            self.assertIsNone(completed_metrics(output, 4))

    def test_complete_run_requires_parseable_metrics_evidence_and_six_images(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            metrics = {
                "simulation_end_s": 4.0,
                "physical_reject_accuracy": 1.0,
                "physical_reject_recall": 1.0,
                "physical_reject_precision": 1.0,
                "good_false_eject_rate": 0.0,
                "denominators": {},
                "camera_blobs": {},
                "config": {},
            }
            (output / "metrics.json").write_text(json.dumps(metrics))
            (output / "decisions.csv").write_text("header\n")
            (output / "inspection_evidence.json").write_text(json.dumps([{}] * 6))
            for i in range(6):
                (output / f"inspection_{i}.png").write_bytes(b"png")

            self.assertEqual(completed_metrics(output, 4), metrics)


if __name__ == "__main__":
    unittest.main()
