import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from run_characterization import completed_metrics
from run_generalization import (
    BASE_COMMIT, HERE, RUN, SHARED_SOURCES, commit_sha, expected_run_config,
    metric_row, preserve_provenance, sha256, validate_metrics_payload,
)


class GeneralizationExperimentTests(unittest.TestCase):
    def test_metric_row_preserves_counts_and_denominators(self):
        metrics = {
            "per_class": {
                "good": {"defect": False, "rejected": 2, "spilled": 1},
                "bad": {"defect": True, "rejected": 3, "spilled": 2},
            },
            "denominators": {
                "eligible_beans": 20, "resolved_beans": 19, "defects_to_remove": 5,
                "keep_beans": 15, "rejected_beans": 5,
            },
            "physical_reject_accuracy": 0.75, "physical_reject_recall": 0.6,
            "physical_reject_precision": 0.6, "good_false_eject_rate": 2 / 15,
            "spilled_rate": 0.15, "late_decisions": 1, "reject_decisions": 4,
            "throughput_beans_per_s": 10.0, "pool_starved": 0, "wall_seconds": 2.0,
            "measured_compute_ms": {}, "latency_ms": {}, "intervals_95": {},
            "classifier_sha256": "model", "source_sha256": {},
        }
        row = metric_row(metrics)
        self.assertEqual(row["counts"], {
            "defects_rejected": 3, "keep_rejected": 2, "spilled": 3,
            "late_reject_decisions": 1, "reject_decisions": 4,
        })
        self.assertEqual(row["defects_to_remove"], 5)
        self.assertEqual(row["keep_beans"], 15)
        self.assertEqual(row["late_reject_rate"], 0.25)

    def test_completed_metrics_requires_all_evidence_before_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            metrics = {
                "simulation_end_s": 4.0, "physical_reject_accuracy": 0.8,
                "physical_reject_recall": 0.5, "physical_reject_precision": 0.6,
                "good_false_eject_rate": 0.1, "denominators": {},
                "camera_blobs": {}, "config": {},
            }
            (output / "metrics.json").write_text(json.dumps(metrics))
            (output / "decisions.csv").write_text("header\n")
            (output / "inspection_evidence.json").write_text(json.dumps([{}] * 6))
            for index in range(5):
                (output / f"inspection_{index}.png").write_bytes(b"png")
            self.assertIsNone(completed_metrics(output, 4.0))
            (output / "inspection_5.png").write_bytes(b"png")
            self.assertEqual(completed_metrics(output, 4.0), metrics)

    def test_protected_sources_match_task_start_commit(self):
        for name in SHARED_SOURCES:
            with self.subTest(name=name):
                self.assertEqual(sha256(HERE / name), commit_sha(HERE / name, BASE_COMMIT))

    def test_resume_rejects_any_config_or_model_change(self):
        metrics = {
            "profile": "roasted", "classifier_sha256": "model", "seed": RUN["seed"],
            "rate": RUN["rate"], "seconds": RUN["seconds"], "policy": RUN["policy"],
            "threshold": RUN["threshold"], "config": expected_run_config(),
        }
        validate_metrics_payload("roasted", metrics, "model")
        changed = deepcopy(metrics)
        changed["config"]["policy"]["anomaly"] = False
        with self.assertRaisesRegex(RuntimeError, "config"):
            validate_metrics_payload("roasted", changed, "model")
        with self.assertRaisesRegex(RuntimeError, "classifier"):
            validate_metrics_payload("roasted", metrics, "different")

    def test_staged_training_manifest_cannot_rebless_changed_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "roasted"
            (destination / "model").mkdir(parents=True)
            (destination / "train").mkdir()
            (destination / "model" / "roasted.joblib").write_bytes(b"model-a")
            (destination / "train" / "report.json").write_text("report")
            (destination / "train" / "confusion.png").write_bytes(b"png")
            preserve_provenance(destination, "test")
            (destination / "model" / "roasted.joblib").write_bytes(b"model-b")
            with self.assertRaisesRegex(RuntimeError, "model_sha256"):
                preserve_provenance(destination, "test")


if __name__ == "__main__":
    unittest.main()
