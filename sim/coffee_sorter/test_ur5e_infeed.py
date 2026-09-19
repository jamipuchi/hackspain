from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import numpy as np

    from run_ur5e_infeed import _reserve_output, _write_completion
    from ur5e_infeed import (
        MENAGERIE_REVISION,
        OVERSIZE_THRESHOLD_M,
        WORKSPACE_X_MAX_M,
        Detection,
        FeedItem,
        Scenario,
        _associate_detection,
        run_scenario,
        scenarios,
        select_oversize,
    )
except ModuleNotFoundError as error:
    if error.name in {"cv2", "imageio", "matplotlib", "mink", "mujoco", "numpy"}:
        raise unittest.SkipTest(
            f"optional picking dependencies unavailable: {error.name}"
        ) from error
    raise


DEFAULT_MENAGERIE = Path(
    os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))
) / "coffee-sorter" / "mujoco_menagerie"
MENAGERIE_DIR = Path(os.environ.get("MUJOCO_MENAGERIE_DIR", DEFAULT_MENAGERIE))
MODEL_DIR = MENAGERIE_DIR / "universal_robots_ur5e"
if os.environ.get("REQUIRE_UR5E_MODEL") == "1" and not MODEL_DIR.is_dir():
    raise RuntimeError(f"required UR5e model is absent: {MODEL_DIR}")


class PickingSelectionTest(unittest.TestCase):
    def test_threshold_is_inclusive_and_preserves_small_objects(self) -> None:
        detections = [
            Detection("small", 0, 0, 0, OVERSIZE_THRESHOLD_M - 0.001),
            Detection("edge", 0, 0, 0, OVERSIZE_THRESHOLD_M),
            Detection("large", 0, 0, 0, OVERSIZE_THRESHOLD_M + 0.001),
        ]
        self.assertEqual(
            [item.detection_id for item in select_oversize(detections)],
            ["edge", "large"],
        )

    def test_association_and_selection_do_not_gate_on_true_class(self) -> None:
        bean = FeedItem("bean", "bean", 0.0, 0.50, 0.060)
        scenario = Scenario("class-independent", 0.10, 1.0, (bean,))
        detection = Detection("pixel", 0.0, 0.50, -0.68, 0.060)
        association = _associate_detection(detection, [bean], scenario, 0.0)
        self.assertIsNotNone(association)
        self.assertEqual(association[0].item_id, "bean")
        self.assertEqual(select_oversize([detection]), [detection])

    def test_scenarios_include_burst_and_explicit_workspace_exclusion(self) -> None:
        configured = scenarios()
        self.assertEqual(set(configured), {"nominal", "burst", "workspace"})
        self.assertGreater(
            configured["workspace"].items[0].lane_x_m, WORKSPACE_X_MAX_M
        )
        for scenario in configured.values():
            self.assertGreater(sum(item.is_large_debris for item in scenario.items), 0)
            self.assertGreater(scenario.belt_speed_m_s, 0)

    def test_nonempty_output_requires_rerun_and_rerun_archives_all(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "suite"
            output.mkdir()
            (output / "stale.txt").write_text("old")
            with self.assertRaises(FileExistsError):
                _reserve_output(output, rerun=False)
            self.assertEqual((output / "stale.txt").read_text(), "old")
            with patch.dict(os.environ, {"XDG_CACHE_HOME": str(root / "cache")}):
                archive = _reserve_output(output, rerun=True)
            self.assertIsNotNone(archive)
            self.assertEqual((archive / "stale.txt").read_text(), "old")
            self.assertEqual(list(output.iterdir()), [])

    def test_completion_binds_saved_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "artifact.txt").write_text("evidence")
            _write_completion(output, ["case"])
            completion = json.loads((output / "completion.json").read_text())
            self.assertTrue(completion["complete"])
            self.assertEqual(completion["scenarios"], ["case"])
            self.assertEqual(
                completion["artifacts"]["artifact.txt"]["sha256"],
                "ee8250fb76e094b34b471f13a73dbbe51d1ae142e9df59d7c0d31ec20f0a0a8e",
            )


@unittest.skipUnless(MODEL_DIR.exists(), "run setup_ur5e_pick.sh first")
class PickingIntegrationTest(unittest.TestCase):
    def test_actual_ur5e_mink_scenarios_preserve_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            metrics = {
                scenario.name: run_scenario(
                    scenario, MODEL_DIR, Path(directory) / scenario.name, render=False
                )
                for scenario in scenarios().values()
            }
        for result in metrics.values():
            self.assertEqual(result["source"]["menagerie_revision"], MENAGERIE_REVISION)
            self.assertEqual(
                sum(result["outcomes"].values()),
                result["denominators"]["large_debris"],
            )
            self.assertEqual(result["nonlarge_selected"], 0)
        self.assertEqual(metrics["nominal"]["outcomes"]["success"], 2)
        self.assertGreater(metrics["burst"]["outcomes"]["missed"], 0)
        self.assertGreater(metrics["burst"]["queue_wait_s"]["max"], 3.0)
        self.assertEqual(
            len(metrics["burst"]["queue_wait_s"]["samples"]),
            metrics["burst"]["denominators"]["camera_selected"],
        )
        self.assertIn("a-large-4", metrics["burst"]["queue_wait_s"]["by_item"])
        self.assertEqual(metrics["workspace"]["outcomes"]["outside_workspace"], 1)

    def test_nonfinite_solver_velocity_fails_the_run(self) -> None:
        scenario = Scenario("nonfinite", 0.10, 0.0, ())
        with tempfile.TemporaryDirectory() as directory:
            with patch("ur5e_infeed.mink.solve_ik", return_value=np.full(6, np.nan)):
                with self.assertRaisesRegex(FloatingPointError, "solver velocity"):
                    run_scenario(scenario, MODEL_DIR, Path(directory) / "run", render=False)


if __name__ == "__main__":
    unittest.main()
