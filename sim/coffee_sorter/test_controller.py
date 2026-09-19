import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from controller import Controller, Policy, Track
from profiles import GREEN_ARABICA
from vision import Blobs, FEATURES


class FakeInspector:
    def __init__(self, blobs):
        self.blobs = iter(blobs)

    def detect(self, frame, t):
        return next(self.blobs)


class FakeModel:
    classes = ["black"]
    anomaly_thresh = 10.0

    def predict(self, X):
        return np.ones((len(X), 1)), np.zeros(len(X))


class FakeSim:
    def __init__(self):
        pitch = 0.5 / 64
        self.L = SimpleNamespace(
            belt_speed=3.0,
            cam_x=-0.12,
            belt_w=0.5,
            n_nozzles=64,
            nozzle_pitch=pitch,
            nozzle_y=lambda j: -0.25 + pitch * (j + 0.5),
            ej_x=0.1,
        )
        self.P = GREEN_ARABICA
        self.fires = []

    def fire(self, *args, **kwargs):
        self.fires.append((args, kwargs))


def blob(x, y=0.0):
    return Blobs(
        0.0,
        1,
        np.array([x]),
        np.array([y]),
        np.zeros(1),
        np.zeros(1),
        np.zeros((1, 4), int),
        np.zeros(1, bool),
        np.ones((1, len(FEATURES))),
    )


class ControllerTest(unittest.TestCase):
    def controller(self, blobs):
        sim = FakeSim()
        ctrl = Controller(sim, FakeInspector(blobs), FakeModel(), Policy(latency_floor=0.004))
        return sim, ctrl

    def test_decided_track_consumes_remaining_observations_through_pruning(self):
        sim, ctrl = self.controller([blob(-0.130), blob(-0.118), blob(-0.106)])
        with patch("controller.time.perf_counter", side_effect=[0.0, 0.001, 1.0, 1.001, 1.002]):
            ctrl.on_frame(None, 0.000)
            ctrl.on_frame(None, 0.004)
        decided = ctrl.tracks[0]
        ctrl.tracks.extend(Track(i + 1, 0, 0, 0, misses=2, done=True) for i in range(4000))
        with patch("controller.time.perf_counter", side_effect=[2.0, 2.001]):
            ctrl.on_frame(None, 0.008)

        self.assertEqual(len(ctrl.decisions), 1)
        self.assertEqual(len(sim.fires), 2)
        self.assertEqual(decided.n, 2)
        self.assertIn(decided, ctrl.tracks)
        self.assertEqual(len(ctrl.tracks), 1)

    def test_latency_adds_capture_delay_and_includes_finalize(self):
        _, ctrl = self.controller([blob(-0.118)])
        with patch("controller.time.perf_counter", side_effect=[10.0, 10.003, 10.005]):
            ctrl.on_frame(None, 1.0)

        self.assertAlmostEqual(ctrl.decisions[0].t_available, 1.007)
        self.assertAlmostEqual(ctrl.latency_ms[0], 9.0)
        self.assertFalse(ctrl.decisions[0].late)

    def test_deadline_is_checked_at_scheduling_point(self):
        sim, ctrl = self.controller([blob(0.09)])
        with patch("controller.time.perf_counter", side_effect=[20.0, 20.003, 20.005]):
            ctrl.on_frame(None, 1.0)

        self.assertAlmostEqual(ctrl.decisions[0].t_available, 1.007)
        self.assertTrue(ctrl.decisions[0].late)
        self.assertEqual(sim.fires, [])

    def test_induced_delay_changes_availability_and_prevents_late_pulse(self):
        sim = FakeSim()
        policy = Policy(latency_floor=0.004, induced_delay=0.080)
        ctrl = Controller(sim, FakeInspector([blob(-0.118)]), FakeModel(), policy)
        with patch("controller.time.perf_counter", side_effect=[10.0, 10.003, 10.005]):
            ctrl.on_frame(None, 1.0)

        self.assertAlmostEqual(ctrl.decisions[0].t_available, 1.087)
        self.assertAlmostEqual(ctrl.latency_ms[0], 89.0)
        self.assertTrue(ctrl.decisions[0].late)
        self.assertFalse(ctrl.decisions[0].scheduled)
        self.assertEqual(sim.fires, [])

    def test_fixed_latency_is_a_minimum_and_never_hides_slower_compute(self):
        sim = FakeSim()
        policy = Policy(latency_floor=0.004, fixed_latency=0.006)
        ctrl = Controller(sim, FakeInspector([blob(-0.118)]), FakeModel(), policy)
        with patch("controller.time.perf_counter", side_effect=[10.0, 10.003, 10.005]):
            ctrl.on_frame(None, 1.0)

        self.assertAlmostEqual(ctrl.decisions[0].t_available, 1.007)
        self.assertAlmostEqual(ctrl.latency_ms[0], 9.0)

    def test_target_nozzle_override_opens_exact_requested_count(self):
        for count in (1, 2, 3):
            with self.subTest(count=count):
                sim = FakeSim()
                policy = Policy(latency_floor=0.004, target_nozzles=count)
                ctrl = Controller(sim, FakeInspector([blob(-0.118)]), FakeModel(), policy)
                with patch("controller.time.perf_counter", side_effect=[10.0, 10.003, 10.005]):
                    ctrl.on_frame(None, 1.0)

                self.assertEqual(len(ctrl.decisions[0].nozzles), count)
                self.assertEqual(ctrl.decisions[0].nozzles[0], 32)

    def test_target_nozzle_override_stays_in_range_at_both_bank_edges(self):
        for y, expected in ((-0.249, [0, 1, 2]), (0.249, [63, 62, 61])):
            with self.subTest(y=y):
                sim = FakeSim()
                policy = Policy(latency_floor=0.004, target_nozzles=3)
                ctrl = Controller(sim, FakeInspector([blob(-0.118, y)]), FakeModel(), policy)
                with patch("controller.time.perf_counter", side_effect=[10.0, 10.003, 10.005]):
                    ctrl.on_frame(None, 1.0)

                self.assertEqual(ctrl.decisions[0].nozzles, expected)


if __name__ == "__main__":
    unittest.main()
