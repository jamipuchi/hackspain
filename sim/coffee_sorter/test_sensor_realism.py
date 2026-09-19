import unittest
from types import SimpleNamespace
from unittest.mock import patch
import json
from pathlib import Path
import tempfile

import cv2
import numpy as np

from controller import Controller
from profiles import BOX, CAPSULE, GREEN_ARABICA
import run_sensor_realism
from scene import Layout
from sim import SorterSim
from sensor_realism import (
    PhysicalConfig, SensorConfig, SensorSorterSim, diagnostic_metrics, motion_blur_kernel,
    transform_frame,
)


class SensorTransformTest(unittest.TestCase):
    def test_identity_transform_is_pixel_exact_and_does_not_alias(self):
        frame = np.arange(9 * 11 * 3, dtype=np.uint8).reshape(9, 11, 3)

        transformed = transform_frame(frame, SensorConfig(), np.random.default_rng(1))

        self.assertTrue(np.array_equal(transformed, frame))
        self.assertIsNot(transformed, frame)

    def test_motion_blur_uses_travel_rows_and_physical_length(self):
        frame = np.zeros((21, 21, 3), np.uint8)
        frame[10, 10] = 240
        config = SensorConfig(exposure_us=500.0)

        transformed = transform_frame(frame, config, np.random.default_rng(1))
        rows, columns = np.where(transformed[:, :, 0] > 0)

        self.assertAlmostEqual(config.blur_pixels, 6.0)
        self.assertEqual(config.blur_kernel_rows, 7)
        self.assertEqual(len(np.unique(rows)), 7)
        self.assertEqual(np.unique(columns).tolist(), [10])

    def test_fractional_nominal_exposure_changes_an_impulse_without_a_timestamp_shift(self):
        frame = np.zeros((21, 21, 3), np.uint8)
        frame[10, 10] = 240

        nominal = transform_frame(frame, SensorConfig(exposure_us=100.0), np.random.default_rng(1))
        stress = transform_frame(frame, SensorConfig(exposure_us=500.0), np.random.default_rng(1))
        nominal_rows, nominal_columns = np.where(nominal[:, :, 0] > 0)
        stress_rows, stress_columns = np.where(stress[:, :, 0] > 0)

        self.assertFalse(np.array_equal(nominal, frame))
        self.assertEqual(np.unique(nominal_columns).tolist(), [10])
        self.assertEqual(np.unique(nominal_rows).tolist(), [9, 10, 11])
        self.assertEqual(np.unique(stress_columns).tolist(), [10])
        self.assertAlmostEqual(np.average(stress_rows, weights=stress[stress_rows, 10, 0]), 10.0)
        self.assertAlmostEqual(float(motion_blur_kernel(1.2).sum()), 1.0, places=6)
        self.assertTrue(np.allclose(motion_blur_kernel(6.0),
                                    np.array([.5, 1, 1, 1, 1, 1, .5]) / 6))

    def test_noise_is_seed_reproducible_and_seed_sensitive(self):
        frame = np.full((20, 30, 3), 100, np.uint8)
        config = SensorConfig(shot_electrons_per_dn=4.0, read_noise_dn=3.0,
                              noise_label="ASSUMED lower-light")

        first = transform_frame(frame, config, np.random.default_rng(7))
        repeated = transform_frame(frame, config, np.random.default_rng(7))
        changed = transform_frame(frame, config, np.random.default_rng(8))

        self.assertTrue(np.array_equal(first, repeated))
        self.assertFalse(np.array_equal(first, changed))

    def test_brightness_endpoints_and_full_width_gradient_are_pixel_exact(self):
        frame = np.full((2, 2080, 3), 100, np.uint8)

        self.assertTrue(np.array_equal(
            transform_frame(frame, SensorConfig(brightness_gain=0.7), np.random.default_rng(1)),
            np.full_like(frame, 70)))
        self.assertTrue(np.array_equal(
            transform_frame(frame, SensorConfig(brightness_gain=1.3), np.random.default_rng(1)),
            np.full_like(frame, 130)))

        transformed = transform_frame(frame, SensorConfig(horizontal_gradient=0.3),
                                      np.random.default_rng(1))
        expected_columns = np.rint(100 * np.linspace(0.7, 1.3, 2080, dtype=np.float32)).astype(np.uint8)
        expected = np.broadcast_to(expected_columns[None, :, None], frame.shape)
        self.assertTrue(np.array_equal(transformed, expected))

    def test_noise_variance_matches_nominal_and_stress_assumptions(self):
        frame = np.full((1, 40_000, 3), 100, np.uint8)
        cases = (
            (SensorConfig(shot_electrons_per_dn=16.0, read_noise_dn=1.0,
                          noise_label="ASSUMED well-lit"), 7.25),
            (SensorConfig(shot_electrons_per_dn=4.0, read_noise_dn=3.0,
                          noise_label="ASSUMED lower-light"), 34.0),
        )
        for config, expected_variance in cases:
            transformed = transform_frame(frame, config, np.random.default_rng(19))
            variance = float(transformed.astype(np.float64).var())
            self.assertAlmostEqual(variance / expected_variance, 1.0, delta=0.05)

        read_only = SensorConfig(read_noise_dn=1.0, noise_label="ASSUMED read-only")
        shot = SensorConfig(shot_electrons_per_dn=16.0, read_noise_dn=1.0,
                           noise_label="ASSUMED well-lit")
        read_variances = []
        shot_variances = []
        for value in (20, 100):
            flat = np.full_like(frame, value)
            read_variances.append(float(transform_frame(
                flat, read_only, np.random.default_rng(value)).astype(np.float64).var()))
            shot_variances.append(float(transform_frame(
                flat, shot, np.random.default_rng(value)).astype(np.float64).var()))
        self.assertAlmostEqual(read_variances[0], read_variances[1], delta=0.05 * read_variances[1])
        self.assertGreater(shot_variances[1], 2.0 * shot_variances[0])

    def test_config_guards_reject_unlabeled_or_impossible_inputs(self):
        with self.assertRaisesRegex(ValueError, "labeled"):
            SensorConfig(read_noise_dn=1.0).validate()
        with self.assertRaisesRegex(ValueError, "positive"):
            SensorConfig(shot_electrons_per_dn=0.0, noise_label="bad").validate()
        with self.assertRaisesRegex(ValueError, "jitter_hz"):
            PhysicalConfig(belt_jitter_fraction=0.05).validate()
        with self.assertRaisesRegex(ValueError, "non-negative"):
            PhysicalConfig(crowded_spawn_gap_m=-0.001).validate()
        for config in (SensorConfig(brightness_gain=float("nan")),
                       SensorConfig(shot_electrons_per_dn=float("inf"), noise_label="bad"),
                       SensorConfig(read_noise_dn=float("nan")),
                       PhysicalConfig(belt_jitter_hz=float("nan")),
                       PhysicalConfig(belt_jitter_phase_rad=float("inf")),
                       PhysicalConfig(crowded_spawn_gap_m=float("nan"))):
            with self.assertRaisesRegex(ValueError, "finite"):
                config.validate()


class PhysicalRealismTest(unittest.TestCase):
    def test_jitter_changes_physics_but_preserves_nominal_controller_layout(self):
        layout = Layout(n_ellipsoid=1, n_half=1, n_box=1, n_capsule=1)
        physical = PhysicalConfig(belt_jitter_fraction=0.05, belt_jitter_hz=8.0,
                                  belt_jitter_phase_rad=np.pi / 2)
        sim = SensorSorterSim(GREEN_ARABICA, layout=layout, rate=0, seed=1,
                              physical_config=physical)
        sim.step()
        model = SimpleNamespace(classes=list(GREEN_ARABICA.names), anomaly_thresh=10.0)
        controller = Controller(sim, SimpleNamespace(), model)

        self.assertAlmostEqual(sim.actual_belt_speed_trace[0], 3.15)
        self.assertEqual(sim.L.belt_speed, 3.0)
        self.assertEqual(controller.L.belt_speed, 3.0)

    def test_jitter_waveform_hits_both_five_percent_limits_over_one_cycle(self):
        layout = Layout(n_ellipsoid=1, n_half=1, n_box=1, n_capsule=1)
        sim = SensorSorterSim(
            GREEN_ARABICA, layout=layout, rate=0, seed=2,
            physical_config=PhysicalConfig(belt_jitter_fraction=0.05, belt_jitter_hz=8.0,
                                           belt_jitter_phase_rad=np.pi / 2))
        for _ in range(round(1 / (8.0 * sim.dt))):
            sim.step()

        self.assertAlmostEqual(min(sim.actual_belt_speed_trace), 2.85, delta=1e-4)
        self.assertAlmostEqual(max(sim.actual_belt_speed_trace), 3.15, delta=1e-4)
        self.assertEqual(sim.L.belt_speed, 3.0)
        model = SimpleNamespace(classes=list(GREEN_ARABICA.names), anomaly_thresh=10.0)
        self.assertEqual(Controller(sim, SimpleNamespace(), model).L.belt_speed, 3.0)

    def _crowded_spot(self, incoming_half, existing_axes, existing_shape, x):
        layout = SimpleNamespace(feed_x=(-0.04, 0.04), belt_z=0.0, feed_drop=0.01)
        existing = SimpleNamespace(cls="existing", axes=np.asarray(existing_axes, dtype=float))
        fake = SimpleNamespace(
            physical_config=PhysicalConfig(crowded_feed_half_width_m=0.06, crowded_spawn_gap_m=0.0002),
            L=layout,
            active_state=lambda: (np.array([7]), np.array([[0.0, 0.0, 0.0]]), np.zeros((1, 3))),
            bean_of={7: existing},
            class_by_name={"existing": SimpleNamespace(shape=existing_shape)},
            crowded_spawn_failures=0,
            minimum_spawn_surface_gap_m=None,
        )
        fake.rng = SimpleNamespace(uniform=lambda low, high: x if (low, high) == layout.feed_x else 0.0)
        fake._spawn_footprint_radius = lambda bean: SensorSorterSim._spawn_footprint_radius(fake, bean)
        return SensorSorterSim._free_spot(fake, np.asarray(incoming_half, dtype=float), 0.0, tries=1)

    def test_crowded_capsule_spawn_uses_total_capsule_length(self):
        spot = self._crowded_spot([.001, .001, .013], [.012, .001, .001], CAPSULE, .0135)
        self.assertIsNone(spot)

    def test_crowded_box_spawn_uses_diagonal_bound(self):
        spot = self._crowded_spot([.006, .006, .001], [.006, .006, .001], BOX, .013)
        self.assertIsNone(spot)

    def test_product_stream_ignores_placement_rng_consumption(self):
        layout = Layout(n_ellipsoid=2, n_half=2, n_box=2, n_capsule=2)
        physical = PhysicalConfig()
        first = SensorSorterSim(GREEN_ARABICA, layout=layout, rate=0, seed=11, physical_config=physical)
        changed_placement = SensorSorterSim(GREEN_ARABICA, layout=layout, rate=0, seed=11,
                                            physical_config=physical)
        first_items = [first._next_feed_item() for _ in range(4)]
        changed_placement.rng.standard_normal(10_000)
        changed_items = [changed_placement._next_feed_item() for _ in range(4)]

        for left, right in zip(first_items, changed_items):
            self.assertEqual((left.planned_id, left.cls.name, left.material),
                             (right.planned_id, right.cls.name, right.material))
            self.assertTrue(np.array_equal(left.axes, right.axes))
            self.assertTrue(np.array_equal(left.rgba, right.rgba))
            self.assertTrue(np.array_equal(left.tilt, right.tilt))
            self.assertTrue(np.array_equal(left.velocity, right.velocity))

    def test_local_product_stream_spawns_each_shape(self):
        layout = Layout(n_ellipsoid=2, n_half=2, n_box=2, n_capsule=2)
        sim = SensorSorterSim(GREEN_ARABICA, layout=layout, rate=0, seed=3, physical_config=PhysicalConfig())
        specs = [GREEN_ARABICA.by_name(name) for name in ("good", "broken", "stone", "stick")]

        beans = [sim.spawn(spec) for spec in specs]

        self.assertTrue(all(beans))
        self.assertEqual([item.cls.name for item in sim.feed_items], [spec.name for spec in specs])

    def test_blocked_spawn_recovers_pending_product_without_uid_or_admission_duplication(self):
        layout = Layout(n_ellipsoid=1, n_half=0, n_box=0, n_capsule=0)
        sim = SensorSorterSim(GREEN_ARABICA, layout=layout, rate=0, seed=23,
                              physical_config=PhysicalConfig())
        good = GREEN_ARABICA.by_name("good")

        first = sim.spawn(good)
        self.assertIsNotNone(first)
        self.assertIsNone(sim.spawn(good))
        pending = sim.feed_items[1]
        pending_snapshot = (pending.planned_id, pending.cls.name, pending.axes.copy(), pending.mass)
        self.assertEqual(sim.pending_feed_item.planned_id, pending.planned_id)

        sim._park(first.body)
        sim.step()  # clears the per-step admission block while preserving the pending item
        recovered = sim.spawn(good)

        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.uid, 1)
        self.assertEqual(sim.pending_feed_item, None)
        self.assertEqual(sim.admitted_feed_ids, {0: 0, 1: pending.planned_id})
        self.assertEqual(len({bean.uid for bean in sim.beans}), 2)
        self.assertEqual(len({item.admitted_uid for item in sim.feed_items}), 2)
        self.assertEqual((pending.planned_id, pending.cls.name, pending.mass),
                         (pending_snapshot[0], pending_snapshot[1], pending_snapshot[3]))
        self.assertTrue(np.array_equal(pending.axes, pending_snapshot[2]))


class PhysicsParityTest(unittest.TestCase):
    def test_fixed_product_shape_mass_and_applied_force_match_base_sim(self):
        layout = Layout(n_ellipsoid=1, n_half=0, n_box=0, n_capsule=0)
        spec = GREEN_ARABICA.by_name("good")
        fixed_axes = np.array([0.005, 0.0035, 0.0025])
        fixed_rgba = np.array([0.4, 0.5, 0.3, 1.0])
        fixed_mass = 0.0002

        def fixed_instance(_spec, _rng):
            return fixed_axes.copy(), fixed_rgba.copy(), fixed_mass

        with patch("sim.sample_instance", side_effect=fixed_instance), \
             patch("profiles.sample_instance", side_effect=fixed_instance):
            base = SorterSim(GREEN_ARABICA, layout=layout, rate=0, seed=31)
            sensor = SensorSorterSim(GREEN_ARABICA, layout=layout, rate=0, seed=31,
                                     physical_config=PhysicalConfig())
            landing = lambda half, margin, tries=12: np.array([-0.8, 0.0, layout.belt_z + half[2]])
            base._free_spot = landing
            sensor._free_spot = landing
            base_bean = base.spawn(spec)
            sensor_bean = sensor.spawn(spec)

        self.assertTrue(np.array_equal(base_bean.axes, sensor_bean.axes))
        self.assertEqual(base_bean.mass, sensor_bean.mass)
        self.assertEqual(base.model.body_mass[base_bean.body], sensor.model.body_mass[sensor_bean.body])
        np.testing.assert_allclose(base.model.geom_size[base.body_geom[base_bean.body]],
                                   sensor.model.geom_size[sensor.body_geom[sensor_bean.body]])
        np.testing.assert_allclose(base.model.body_inertia[base_bean.body],
                                   sensor.model.body_inertia[sensor_bean.body])

        for sim, bean in ((base, base_bean), (sensor, sensor_bean)):
            qa, va = sim.body_qpos[bean.body], sim.body_qvel[bean.body]
            sim.data.qpos[qa:qa + 3] = [0.1, float(sim.nozzle_y[0]), layout.belt_z + 0.001]
            sim.data.qvel[va:va + 6] = 0.0
            sim.fire(0, sim.data.time, 0.01, force=0.07)
            sim.step()

        self.assertEqual(base.fires[0].force, sensor.fires[0].force)
        np.testing.assert_allclose(base.data.qvel[base.body_qvel[base_bean.body]:
                                                  base.body_qvel[base_bean.body] + 6],
                                   sensor.data.qvel[sensor.body_qvel[sensor_bean.body]:
                                                    sensor.body_qvel[sensor_bean.body] + 6])
        self.assertEqual(base.fires[0].force, 0.07)


class ContactAccountingTest(unittest.TestCase):
    def test_contacts_count_steps_once_ignore_proximity_and_key_pairs_by_uid(self):
        layout = Layout(n_ellipsoid=2, n_half=0, n_box=0, n_capsule=0)
        sim = SensorSorterSim(GREEN_ARABICA, layout=layout, rate=0, seed=41,
                              physical_config=PhysicalConfig(crowded_feed_half_width_m=0.06))
        good = GREEN_ARABICA.by_name("good")
        first, second = sim.spawn(good), sim.spawn(good)
        bodies = (first.body, second.body)
        contact_geoms = (sim.body_col[bodies[0]], sim.body_col[bodies[1]])

        class Contact:
            def __init__(self, geom1, geom2, dist):
                self.geom1, self.geom2, self.dist = geom1, geom2, dist

        def fake_base_step(current):
            current.data = SimpleNamespace(
                time=0.0, ncon=3,
                contact=[Contact(*contact_geoms, -1e-5), Contact(contact_geoms[1], contact_geoms[0], -1e-5),
                         Contact(*contact_geoms, 1e-5)])

        with patch.object(SorterSim, "step", fake_base_step):
            sim.step()
            sim.bean_of[bodies[0]] = SimpleNamespace(uid=2)
            sim.bean_of[bodies[1]] = SimpleNamespace(uid=3)
            sim.step()

        self.assertEqual(sim.crowded_contact_steps, 2)
        self.assertEqual(sim.crowded_contact_pairs, {(0, 1), (2, 3)})


class SummaryIntegrityTest(unittest.TestCase):
    def test_summarize_rejects_unequal_realized_1000_rate_cohort_hashes(self):
        scenarios = [
            {"slug": "base", "label": "base", "requested_rate": 1000.0, "comparison": "paired"},
            {"slug": "brightness", "label": "brightness", "requested_rate": 1000.0, "comparison": "paired"},
        ]
        config = {"assumptions": {}, "scenarios": scenarios}
        metrics = [{}, {}]
        rows = [
            {"requested_rate_beans_per_s": 1000.0, "comparison": "paired",
             "feed": {"eligible_product_identity_sha256": "hash-a"}},
            {"requested_rate_beans_per_s": 1000.0, "comparison": "paired",
             "feed": {"eligible_product_identity_sha256": "hash-b"}},
        ]
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(run_sensor_realism, "ROOT", Path(temporary)), \
             patch.object(run_sensor_realism, "complete_scenario", side_effect=metrics), \
             patch.object(run_sensor_realism, "summary_row", side_effect=rows):
            with self.assertRaisesRegex(RuntimeError, "unmatched realized product cohorts"):
                run_sensor_realism.summarize(config)


class ResumeIntegrityTest(unittest.TestCase):
    def test_in_memory_runner_refuses_source_edits_between_scenarios(self):
        with patch.object(run_sensor_realism, "source_hashes", return_value={"edited": "source"}):
            with self.assertRaisesRegex(RuntimeError, "changed since process start"):
                run_sensor_realism.run_scenario({}, {"slug": "never-started"})

    def test_source_change_refuses_completed_result_and_manifest_is_complete(self):
        config = {"shared": {"seconds": 4.0}}
        scenario = {"slug": "source-guard"}
        identity = {
            "config_sha256": "config", "scenario": scenario, "shared": config["shared"],
            "classifier_sha256": "model", "source_sha256": {name: "before" for name in run_sensor_realism.SOURCE_FILES},
            "environment": run_sensor_realism.environment_versions(),
        }
        changed = {name: "before" for name in run_sensor_realism.SOURCE_FILES}
        changed["render.py"] = "after"
        metrics = {"sensor_realism": {"identity": identity}}

        with patch.object(run_sensor_realism, "completed_metrics", return_value=metrics), \
             patch.object(run_sensor_realism, "artifacts_are_valid", return_value=True), \
             patch.object(run_sensor_realism, "sha256", side_effect=(lambda path: "config" if path == run_sensor_realism.CONFIG_PATH else "model")), \
             patch.object(run_sensor_realism, "source_hashes", return_value=changed):
            with self.assertRaisesRegex(RuntimeError, "has different config, source, or model"):
                run_sensor_realism.complete_scenario(config, scenario)
        self.assertEqual(set(run_sensor_realism.SOURCE_FILES), {
            "sensor_realism.py", "run_sensor_realism.py", "run.py", "controller.py", "sim.py",
            "vision.py", "evidence.py", "scene.py", "profiles.py", "classifier.py", "assets.py",
            "render.py", "run_characterization.py",
        })

    def test_recursive_config_guard_rejects_non_finite_numbers(self):
        with self.assertRaisesRegex(ValueError, "finite"):
            run_sensor_realism.require_finite_numbers({"scenario": {"rate": float("nan")}})

    def test_changed_metrics_or_evidence_refuses_completed_result(self):
        config, scenario = {"shared": {"seconds": 4.0}}, {"slug": "artifact-guard"}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / scenario["slug"]
            output.mkdir()
            (output / "decisions.csv").write_text("tid,reject\n1,1\n")
            evidence = [{"image": f"inspection_{index}.png"} for index in range(6)]
            (output / "inspection_evidence.json").write_text(json.dumps(evidence))
            (output / "feed_manifest.json").write_text(json.dumps({"items": []}))
            image = np.zeros((2, 2, 3), np.uint8)
            for index in range(6):
                self.assertTrue(cv2.imwrite(str(output / f"inspection_{index}.png"), image))
            with patch.object(run_sensor_realism, "ROOT", root):
                metrics = {"sensor_realism": {"identity": run_sensor_realism.expected_identity(config, scenario)}}
                metrics["sensor_realism"]["artifact_sha256"] = run_sensor_realism.artifact_hashes(output)
                metrics_path = output / "metrics.json"
                metrics_path.write_text(json.dumps(metrics))
                (output / "completion.json").write_text(json.dumps({
                    "metrics_sha256": run_sensor_realism.sha256(metrics_path),
                }))
                self.assertTrue(run_sensor_realism.artifacts_are_valid(output, metrics))
                metrics_path.write_text(json.dumps({**metrics, "physical_reject_accuracy": 0.123}))
                with patch.object(run_sensor_realism, "completed_metrics", return_value=metrics):
                    with self.assertRaisesRegex(RuntimeError, "invalid or modified artifacts"):
                        run_sensor_realism.complete_scenario(config, scenario)
                metrics_path.write_text(json.dumps(metrics))
                (output / "inspection_evidence.json").write_text(json.dumps(evidence, indent=2))
                with patch.object(run_sensor_realism, "completed_metrics", return_value=metrics):
                    with self.assertRaisesRegex(RuntimeError, "invalid or modified artifacts"):
                        run_sensor_realism.complete_scenario(config, scenario)


class DiagnosticMetricsTest(unittest.TestCase):
    def test_new_rates_keep_their_actual_denominators(self):
        raw = {
            "camera_blobs": {
                "single_observations": 8, "merged_observations": 2,
                "single_class_correct": 6,
                "single_action_tp": 3, "merged_action_tp": 1,
                "single_action_fn": 1, "merged_action_fn": 0,
                "single_action_fp": 2, "merged_action_fp": 1,
                "single_action_tn": 10, "merged_action_tn": 2,
            },
            "denominators": {"eligible_beans": 20},
            "camera_bean_cohorts": {
                "single_only": {"n": 12}, "ever_merged": {"n": 5},
                "never_observed": {"n": 3},
            },
            "reject_decisions": 7, "late_decisions": 1,
            "scheduled_reject_decisions": 6, "activated_reject_decisions": 5,
            "associated_reject_decisions": 6, "associated_activated": 5,
            "associated_jet_hit": 4, "associated_rejected": 3,
            "simulation_seconds": 4.0,
        }
        controller = SimpleNamespace(compute_ms=[2.0, 5.0], next_tid=10,
                                     decisions=[object()] * 8, frames=2)
        inspector = SimpleNamespace(sensor_transform_ms=[1.0, 3.0])
        sim = SimpleNamespace(actual_belt_speed_trace=[2.85, 3.15], nominal_belt_speed=3.0,
                              crowded_spawn_failures=2, minimum_spawn_surface_gap_m=0.0002)

        result = diagnostic_metrics(raw, controller, inspector, sim)

        self.assertEqual(result["vision"]["defect_action_recall_denominator"], 5)
        self.assertEqual(result["vision"]["good_false_eject_denominator"], 15)
        self.assertEqual(result["vision"]["good_false_eject_rate"], 3 / 15)
        self.assertEqual(result["camera_decision_coverage"]["eligible_beans"], 20)
        self.assertEqual(result["camera_decision_coverage"]["beans_seen_in_full_blob"], 17)
        self.assertEqual(result["merged_cohorts"]["ever_merged_occupancy"], 5 / 20)
        self.assertEqual(result["late_miss_own_hit_funnel"]["associated_activated_own_pulse_miss"], 1)
        self.assertEqual(result["runtime"]["detector_controller_cpu_overruns"], 1)


if __name__ == "__main__":
    unittest.main()
