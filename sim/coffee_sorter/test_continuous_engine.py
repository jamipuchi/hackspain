import json
import tempfile
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from controller import Policy
from engine import HERE, Engine, MAX_COMPLETED_INJECTIONS
from profiles import PROFILES
from rolling_scores import SETTLING_SECONDS, RollingScoreLedger
from sim import Fire


VERSIONS = {
    "score_epoch_id": "session",
    "model_version": "model",
    "policy_version": "policy",
    "source_revision": "source",
}


class RollingScoreLedgerTest(unittest.TestCase):
    def scores(self, ledger, now):
        return ledger.scores(as_of_sim_time_s=now, **VERSIONS)

    def test_uses_exclusive_start_inclusive_end_and_settling_delay(self):
        first = RollingScoreLedger(60.0)
        first.add(0, 0.0, False)
        first.resolve(0, "accept")
        self.assertEqual(self.scores(first, SETTLING_SECONDS)["eligible_objects"], 1)

        ledger = RollingScoreLedger(60.0)
        ledger.add(1, 0.0, False)
        ledger.add(2, 0.1, True)
        ledger.add(3, 60.1, False)
        ledger.add(4, 60.2, False)
        ledger.resolve(1, "accept")
        ledger.resolve(2, "reject")
        ledger.resolve(3, "accept")

        scores = self.scores(ledger, 60.1 + SETTLING_SECONDS)

        self.assertAlmostEqual(scores["window_start_exclusive_s"], 0.1)
        self.assertAlmostEqual(scores["window_end_inclusive_s"], 60.1)
        self.assertEqual(scores["eligible_objects"], 1)
        self.assertEqual(scores["settling_objects"], 1)
        self.assertEqual(scores["sorting_accuracy"], {
            "numerator": 1, "denominator": 1, "value": 1.0,
        })
        self.assertEqual(len(ledger), 2)

    def test_pending_object_and_late_outcome_share_settling_deadline(self):
        ledger = RollingScoreLedger(60.0)
        ledger.add(1, 1.0, True)

        pending = self.scores(ledger, 1.7)
        self.assertEqual(pending["eligible_objects"], 0)
        self.assertEqual(pending["settling_objects"], 1)

        ledger.resolve(1, "reject")
        at_outcome = self.scores(ledger, 1.85)
        self.assertEqual(at_outcome["eligible_objects"], 0)
        self.assertEqual(at_outcome["settling_objects"], 1)

        after_deadline = self.scores(ledger, 1.0 + SETTLING_SECONDS + 0.01)
        self.assertEqual(after_deadline["eligible_objects"], 1)
        self.assertEqual(after_deadline["reject_capture"], {
            "numerator": 1, "denominator": 1, "value": 1.0,
        })

    def test_counts_spills_unresolved_and_empty_denominators(self):
        ledger = RollingScoreLedger(60.0)
        ledger.add(1, 1.0, False)
        ledger.add(2, 1.1, False)
        ledger.add(3, 1.2, True)
        ledger.add(4, 1.3, True)
        ledger.resolve(1, "spilled")
        ledger.resolve(3, "reject")
        ledger.resolve(4, "accept")

        scores = self.scores(ledger, 1.3 + SETTLING_SECONDS)

        self.assertEqual(scores["sorting_accuracy"]["numerator"], 1)
        self.assertEqual(scores["sorting_accuracy"]["denominator"], 4)
        self.assertEqual(scores["defect_capture"], {
            "numerator": 1, "denominator": 2, "value": 0.5,
        })
        self.assertEqual(scores["good_loss"], {
            "numerator": 1, "denominator": 2, "value": 0.5,
        })
        self.assertEqual(scores["unresolved"], {
            "numerator": 1, "denominator": 4, "value": 0.25,
        })

        empty = self.scores(RollingScoreLedger(60.0), 0.2)
        self.assertEqual(set(empty), {
            "schema_version", "clock", "score_epoch_id", "as_of_sim_time_s",
            "window_seconds", "settling_seconds", "window_start_exclusive_s",
            "window_end_inclusive_s", "available_seconds", "warming_up",
            "manual_injections_excluded", "score_basis", "legacy_score_basis", "settling_objects",
            "eligible_objects", "sorting_accuracy", "reject_capture", "keep_loss",
            "defect_capture", "good_loss", "unresolved", "versions",
        })
        self.assertEqual(empty["available_seconds"], 0.0)
        self.assertTrue(empty["warming_up"])
        for name in ("sorting_accuracy", "defect_capture", "good_loss", "unresolved"):
            self.assertIsNone(empty[name]["value"])

    def test_delayed_outcome_updates_retained_row_and_expired_row_is_discarded(self):
        ledger = RollingScoreLedger(2.0)
        ledger.add(1, 0.0, True)

        self.assertEqual(
            self.scores(ledger, SETTLING_SECONDS + 0.1)["unresolved"]["numerator"], 1
        )
        ledger.resolve(1, "reject")
        self.assertEqual(
            self.scores(ledger, SETTLING_SECONDS + 0.1)["defect_capture"]["numerator"], 1
        )

        expired = self.scores(ledger, 2.0 + SETTLING_SECONDS)
        self.assertEqual(expired["eligible_objects"], 0)
        self.assertEqual(len(ledger), 0)
        ledger.resolve(1, "accept")
        self.assertEqual(len(ledger), 0)

    def test_epoch_availability_starts_at_policy_boundary(self):
        ledger = RollingScoreLedger(60.0, start_sim_time_s=120.0)

        self.assertEqual(self.scores(ledger, 120.5)["available_seconds"], 0.0)
        self.assertAlmostEqual(
            self.scores(ledger, 120.0 + SETTLING_SECONDS + 0.5)["available_seconds"], 0.5
        )

    def test_policy_scores_and_legacy_defect_scores_keep_distinct_truth(self):
        ledger = RollingScoreLedger(60.0)
        ledger.add(1, 1.0, True, physical_defect=False)
        ledger.add(2, 1.1, False, physical_defect=True)
        ledger.resolve(1, "reject")
        ledger.resolve(2, "accept")

        scores = self.scores(ledger, 1.1 + SETTLING_SECONDS + 0.01)

        self.assertEqual(scores["reject_capture"], {
            "numerator": 1, "denominator": 1, "value": 1.0,
        })
        self.assertEqual(scores["keep_loss"], {
            "numerator": 0, "denominator": 1, "value": 0.0,
        })
        self.assertEqual(scores["defect_capture"], {
            "numerator": 0, "denominator": 1, "value": 0.0,
        })
        self.assertEqual(scores["good_loss"], {
            "numerator": 1, "denominator": 1, "value": 1.0,
        })


class ContinuousRetentionTest(unittest.TestCase):
    def policy_engine(self):
        engine = Engine.__new__(Engine)
        engine.continuous = True
        engine.session_id = "session"
        engine.profile = PROFILES["green_arabica"]
        engine.policy = Policy()
        engine.reject_classes = tuple(item.name for item in engine.profile.classes if item.defect)
        engine.policy_version = engine._policy_version()
        engine.model_version = "model"
        engine.source_revision = "source"
        engine.score_epoch_id = "epoch-1"
        engine.score_epoch_started_sim_time_s = 0.0
        engine.policy_applied_sim_time_s = 0.0
        engine.preset = {"score_window_seconds": 60.0}
        engine._score_ledger = RollingScoreLedger(60.0)
        engine._score_ledger.add(1, 1.0, True, physical_defect=True)
        engine.sim = SimpleNamespace(
            data=SimpleNamespace(time=5.0),
            bean_of={1: SimpleNamespace(uid=1), 2: SimpleNamespace(uid=2)},
        )
        engine._object_records = {
            1: {"injected": False},
            2: {"injected": True},
        }
        engine.model = SimpleNamespace(
            classes=list(engine.profile.names),
            set_anomaly_reference=lambda labels: list(labels),
        )
        engine.anomaly_reference_labels = []
        # The controller reports how many undecided tracks it reset.
        engine.controller = SimpleNamespace(set_reject_classes=lambda values: setattr(
            engine.controller, "applied", tuple(values)
        ) or 0)
        engine._event = lambda *args, **kwargs: None
        return engine

    def test_class_catalog_previews_use_profile_midpoints_and_shape_axes(self):
        engine = Engine.__new__(Engine)
        engine.profile = PROFILES["green_arabica"]

        catalog = {item["name"]: item for item in engine.class_catalog()}

        self.assertEqual(catalog["good"]["preview"], {
            "schema_version": 1,
            "source": "profile",
            "shape": "ellipsoid",
            "axes_m": [0.0049, 0.00355, 0.00255],
            "rgb": [0.5, 0.6, 0.46],
        })
        self.assertEqual(catalog["broken"]["preview"]["shape"], "half")
        self.assertEqual(catalog["broken"]["preview"]["axes_m"], [
            0.0049, 0.00355, 0.00255,
        ])
        self.assertEqual(catalog["stone"]["preview"]["shape"], "box")
        self.assertEqual(catalog["stone"]["preview"]["axes_m"], [
            0.00375, 0.003, 0.0025,
        ])
        self.assertEqual(catalog["stick"]["preview"], {
            "schema_version": 1,
            "source": "profile",
            "shape": "capsule",
            "axes_m": [0.012, 0.00115, 0.00115],
            "rgb": [0.42, 0.3, 0.16],
        })

    def test_policy_change_starts_isolated_score_epoch_and_excludes_in_flight(self):
        engine = self.policy_engine()

        result = engine.set_reject_classes(["stone", "black"])

        self.assertTrue(result["changed"])
        self.assertEqual(result["in_flight_excluded"], 1)
        self.assertEqual(result["feed_score_rows_excluded"], 1)
        self.assertEqual(engine.reject_classes, ("black", "stone"))
        self.assertEqual(engine.controller.applied, ("black", "stone"))
        self.assertNotEqual(engine.score_epoch_id, "epoch-1")
        self.assertEqual(engine.score_epoch_started_sim_time_s, 5.0)
        self.assertEqual(len(engine._score_ledger), 0)
        self.assertEqual(engine.rolling_scores()["available_seconds"], 0.0)

    def test_identical_policy_is_noop_and_keeps_score_window(self):
        engine = self.policy_engine()
        ledger = engine._score_ledger

        result = engine.set_reject_classes(list(engine.reject_classes))

        self.assertFalse(result["changed"])
        self.assertEqual(engine.score_epoch_id, "epoch-1")
        self.assertIs(engine._score_ledger, ledger)

    def test_policy_supports_every_catalog_class_and_rejects_unknown_classes(self):
        engine = self.policy_engine()

        result = engine.set_reject_classes(["good"])
        self.assertEqual(result["reject_classes"], ["good"])
        with self.assertRaisesRegex(ValueError, "unsupported reject classes: unknown"):
            engine.set_reject_classes(["unknown"])

    def test_new_object_truth_uses_class_policy_after_cutover(self):
        engine = self.policy_engine()
        engine.set_reject_classes(["stone"])
        engine.sim.body_geom = {3: 0, 4: 1}
        engine.sim.model = SimpleNamespace(geom_rgba=np.ones((2, 4)))
        engine._object_records = {}
        engine._injected_ids = set()
        engine._active_injections = set()
        engine._event = lambda *args, **kwargs: None
        black = SimpleNamespace(uid=3, cls="black", body=3, defect=True, spawn_t=5.1,
                                axes=np.ones(3), outcome=None, resolved_t=None, jet_hits=0)
        stone = SimpleNamespace(uid=4, cls="stone", body=4, defect=True, spawn_t=5.2,
                                axes=np.ones(3), outcome=None, resolved_t=None, jet_hits=0)

        engine._register_bean(black)
        engine._register_bean(stone)

        self.assertFalse(engine._object_records[3]["required_reject"])
        self.assertTrue(engine._object_records[3]["physical_defect"])
        self.assertTrue(engine._object_records[4]["required_reject"])
        self.assertTrue(engine._object_records[4]["physical_defect"])

    def test_normally_accepted_class_can_become_policy_reject(self):
        engine = self.policy_engine()
        engine.set_reject_classes(["good"])
        engine.sim.body_geom = {5: 0}
        engine.sim.model = SimpleNamespace(geom_rgba=np.ones((1, 4)))
        engine._object_records = {}
        engine._injected_ids = set()
        engine._active_injections = set()
        engine._event = lambda *args, **kwargs: None
        bean = SimpleNamespace(uid=5, cls="good", body=5, defect=False, spawn_t=5.1,
                               axes=np.ones(3), outcome=None, resolved_t=None, jet_hits=0)

        engine._register_bean(bean)

        self.assertTrue(engine._object_records[5]["required_reject"])
        self.assertFalse(engine._object_records[5]["physical_defect"])

    def test_pending_fire_records_each_object_contact_once(self):
        fire = Fire(nozzle=1, t_on=0.0, t_off=0.01, force=0.06, uid=42)

        self.assertTrue(fire.records_first_hit(7))
        self.assertFalse(fire.records_first_hit(7))
        self.assertTrue(fire.records_first_hit(8))
        self.assertEqual(fire.hit_objects, {7, 8})

    def test_engine_exposes_session_scoped_scores_only_in_continuous_mode(self):
        engine = Engine.__new__(Engine)
        engine.continuous = True
        engine.session_id = "session"
        engine.model_version = "model"
        engine.policy_version = "policy"
        engine.score_epoch_id = "session"
        engine.source_revision = "source"
        engine.sim = SimpleNamespace(data=SimpleNamespace(time=SETTLING_SECONDS))
        engine._score_ledger = RollingScoreLedger(60.0)
        engine._score_ledger.add(1, 0.0, False)

        scores = engine.rolling_scores()

        self.assertEqual(scores["score_epoch_id"], "session")
        self.assertEqual(scores["eligible_objects"], 1)
        engine.continuous = False
        with self.assertRaisesRegex(RuntimeError, "continuous mode"):
            engine.rolling_scores()

    def test_report_waits_for_shared_settling_deadline_before_cohorting(self):
        bean = SimpleNamespace(uid=1, spawn_t=0.8, outcome=None)
        engine = Engine.__new__(Engine)
        engine.continuous = False
        engine.sim = SimpleNamespace(
            data=SimpleNamespace(time=0.8 + 0.7), beans=[bean], n_spawned=1,
            starved=False, dt=0.001,
        )
        engine._object_evidence = lambda current, cohort: {
            "in_cohort": cohort,
            "required_reject": True,
            "physical_defect": True,
            "outcome": current.outcome,
            "missed_category": None,
            "captured_without_own_pulse_hit": False,
        }
        engine._wall_elapsed = lambda: 0.0
        engine.session_id = "session"
        engine.preset = {"requested_rate": 1.0, "camera_every_steps": 1}
        engine.model_version = "model"
        engine.policy_version = "policy"
        engine.source_revision = "source"
        engine.source_hashes = {}
        engine.preset_version = "preset"
        engine.packages = {}
        engine.startup_seconds = 0.0
        engine._peak_active = 1
        engine._timings = {name: [] for name in (
            "physics_ms", "render_ms", "evaluation_ms", "snapshot_ms",
            "control_path_ms", "camera_frame_ms",
        )}
        engine.controller = SimpleNamespace(
            detection_ms=[], inference_ms=[], control_ms=[],
        )
        engine._decision_evidence = []
        engine._event_id = 0
        engine._events = []
        engine._event_counts = {}

        pending = engine.report()
        self.assertEqual(pending["quality"]["eligible_objects"], 0)

        bean.outcome = "reject"
        engine.sim.data.time = 0.8 + SETTLING_SECONDS + 0.01
        settled = engine.report()
        self.assertEqual(settled["quality"]["eligible_objects"], 1)
        self.assertEqual(settled["quality"]["captured_required_reject_objects"], 1)

    def test_manual_injections_do_not_enter_feed_ledger(self):
        engine = Engine.__new__(Engine)
        engine.continuous = True
        engine.session_id = "session"
        engine.profile = SimpleNamespace(
            by_name=lambda name: SimpleNamespace(
                name=name, defect=True, severity="major", shape="ellipsoid",
            )
        )
        engine.policy = SimpleNamespace(reject_severities=("major",))
        engine.reject_classes = ("black",)
        engine.policy_version = "policy"
        engine.sim = SimpleNamespace(
            body_geom={1: 0},
            model=SimpleNamespace(geom_rgba=np.ones((1, 4))),
            data=SimpleNamespace(time=SETTLING_SECONDS - 0.01),
            fire_hits=set(),
            fired_targets=set(),
        )
        engine._object_records = {}
        engine._injected_ids = set()
        engine._active_injections = set()
        engine._score_ledger = RollingScoreLedger(60.0)
        engine._event = lambda *args, **kwargs: None
        bean = SimpleNamespace(
            uid=1, cls="black", body=1, defect=True, spawn_t=0.0,
            axes=np.ones(3), outcome=None, resolved_t=None, jet_hits=0,
        )

        engine._register_bean(bean, injected=True)

        self.assertEqual(len(engine._score_ledger), 0)
        self.assertEqual(engine._active_injections, {1})
        engine._decision_by_uid = {}
        self.assertFalse(engine._snapshot_object(1, active=True)["overdue"])
        engine.sim.data.time = SETTLING_SECONDS + 0.01
        self.assertTrue(engine._snapshot_object(1, active=True)["overdue"])

    def test_manual_injection_expectation_survives_later_policy_changes(self):
        def register(reject_classes, policy_version, uid):
            engine = Engine.__new__(Engine)
            engine.continuous = True
            engine.session_id = "session"
            engine.policy_version = policy_version
            engine.reject_classes = reject_classes
            engine.profile = SimpleNamespace(
                by_name=lambda name: SimpleNamespace(
                    name=name, defect=True, severity="foreign", shape="box",
                )
            )
            engine.sim = SimpleNamespace(
                body_geom={uid: 0},
                model=SimpleNamespace(geom_rgba=np.ones((1, 4))),
                data=SimpleNamespace(time=1.0),
            )
            engine._object_records = {}
            engine._injected_ids = set()
            engine._active_injections = set()
            engine._score_ledger = RollingScoreLedger(60.0)
            engine._event = lambda *args, **kwargs: None
            bean = SimpleNamespace(
                uid=uid, cls="stone", body=uid, defect=True, spawn_t=0.5,
                axes=np.ones(3), outcome=None, resolved_t=None, jet_hits=0,
            )
            engine._register_bean(bean, injected=True)
            return engine

        keep = register((), "keep-stone-policy", 11)
        reject = register(("stone",), "reject-stone-policy", 12)
        keep.reject_classes = ("stone",)
        keep.policy_version = "later-reject-policy"
        reject.reject_classes = ()
        reject.policy_version = "later-keep-policy"

        self.assertEqual(keep.injection_expectation(11), {
            "expected_outcome": "accept",
            "expectation_policy_version": "keep-stone-policy",
        })
        self.assertEqual(reject.injection_expectation(12), {
            "expected_outcome": "reject",
            "expectation_policy_version": "reject-stone-policy",
        })
        keep._decision_by_uid = {}
        reject._decision_by_uid = {}
        self.assertEqual(keep._snapshot_object(11, active=True)["expected_outcome"], "accept")
        self.assertEqual(
            keep._snapshot_object(11, active=True)["expectation_policy_version"],
            "keep-stone-policy",
        )
        self.assertEqual(reject._snapshot_object(12, active=False)["expected_outcome"], "reject")
        self.assertEqual(
            reject._snapshot_object(12, active=False)["expectation_policy_version"],
            "reject-stone-policy",
        )

    def test_prunes_stale_full_records_and_track_evidence(self):
        engine = Engine.__new__(Engine)
        engine.continuous = True
        engine._score_ledger = RollingScoreLedger(60.0)
        engine._score_ledger.add(99, 0.0, False)
        engine._object_records = {uid: {} for uid in (1, 2, 3, 4)}
        engine._decision_by_uid = {uid: object() for uid in (1, 2, 3, 4)}
        engine._recent_resolved = deque([2], maxlen=200)
        engine._completed_injections = deque([3])
        engine._active_injections = {1, 4}
        engine._injected_ids = {1, 3, 4}
        engine._decision_by_track = {10: object(), 11: object(), 12: object()}
        engine._track_members = {10: {}, 11: {}, 12: {}}
        engine._seen_fired_tracks = {10, 11, 12}
        engine._seen_fire_hits = {(track_id, 1) for track_id in range(10_000)}
        engine._seen_outcomes = {2, 3, 4}
        engine.sim = SimpleNamespace(
            data=SimpleNamespace(time=60.0 + SETTLING_SECONDS),
            bean_of={100: SimpleNamespace(uid=1)},
            fires=[SimpleNamespace(uid=10)],
        )
        engine.controller = SimpleNamespace(tracks=[SimpleNamespace(tid=11)])

        engine._prune_continuous_state()

        self.assertEqual(set(engine._object_records), {1, 2, 3})
        self.assertEqual(set(engine._decision_by_uid), {1, 2, 3})
        self.assertEqual(set(engine._decision_by_track), {10, 11})
        self.assertEqual(set(engine._track_members), {10, 11})
        self.assertEqual(engine._active_injections, {1})
        self.assertEqual(engine._injected_ids, {1, 3})
        self.assertEqual(engine._seen_fire_hits, set())
        self.assertEqual(len(engine._score_ledger), 0)

    def test_completed_injection_history_has_fixed_limit(self):
        engine = Engine.__new__(Engine)
        engine.continuous = True
        engine._seen_outcomes = set()
        engine._completed_injections = deque()
        engine._injection_history_evicted = 0
        engine._object_records = {}
        engine._event = lambda *args, **kwargs: None
        for uid in range(MAX_COMPLETED_INJECTIONS + 3):
            engine._object_records[uid] = {
                "outcome": None,
                "resolved_time_s": None,
                "jet_hits": 0,
                "spawn_wall": None,
                "pos": None,
                "injected": True,
            }
            bean = SimpleNamespace(
                uid=uid, outcome="accept", resolved_t=1.0,
                jet_hits=0, last_pos=None,
            )
            engine._record_outcome(bean)

        self.assertEqual(len(engine._completed_injections), MAX_COMPLETED_INJECTIONS)
        self.assertEqual(engine._injection_history_evicted, 3)
        self.assertEqual(engine._completed_injections[0], 3)


class ModelPathRootTest(unittest.TestCase):
    """A bundle preset binds its model to its own directory and keeps it there."""

    def stub(self, preset, preset_path):
        engine = Engine.__new__(Engine)
        engine.preset = preset
        engine.preset_path = Path(preset_path)
        engine.closed = []
        engine.inspector = SimpleNamespace(close=lambda: engine.closed.append(True))
        return engine

    def test_a_relative_model_path_resolves_beside_the_preset(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        directory = Path(folder.name).resolve()
        (directory / "candidate.joblib").write_bytes(b"model")
        engine = self.stub({"model_path": "candidate.joblib", "model_path_root": "preset"},
                           directory / "candidate.preset.json")

        engine._resolve_model_path()

        self.assertEqual(directory / "candidate.joblib", engine.model_path)
        self.assertEqual([], engine.closed)

    def test_a_model_path_cannot_leave_the_preset_directory(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name).resolve() / "nested" / "candidate.preset.json"
        path.parent.mkdir()
        cases = {"parent traversal": "../x.joblib",
                 "absolute path": str(Path(folder.name) / "x.joblib"),
                 "unknown root": "candidate.joblib"}
        for name, model_path in cases.items():
            with self.subTest(case=name):
                preset = {"model_path": model_path,
                          "model_path_root": "source" if name == "unknown root" else "preset"}
                engine = self.stub(preset, path)
                with self.assertRaises(ValueError):
                    engine._resolve_model_path()
                self.assertEqual([True], engine.closed)

    def test_without_the_field_the_model_path_stays_source_relative(self):
        engine = self.stub({"model_path": "models/absent.joblib"}, Path("/tmp/p.json"))

        with self.assertRaisesRegex(FileNotFoundError, "trusted model is missing"):
            engine._resolve_model_path()

        self.assertEqual(HERE / "models/absent.joblib", engine.model_path)


class InitialRejectClassesTest(unittest.TestCase):
    """A bundle preset starts the engine with its policy inside the one session epoch.

    The real constructor runs. Only the simulator, the camera, the model, and the
    controller are fakes, so no physics and no trained artifact is needed.
    """

    def build(self, initial=None):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        directory = Path(folder.name).resolve()
        (directory / "candidate.joblib").write_bytes(b"model")
        preset = json.loads((HERE / "configs/continuous_demo.json").read_text())
        preset.update(model_path="candidate.joblib", model_path_root="preset")
        if initial is not None:
            preset["policy"]["initial_reject_classes"] = initial
        (directory / "preset.json").write_text(json.dumps(preset))
        self.controller = SimpleNamespace(applied=[])
        self.controller.set_reject_classes = (
            lambda values: self.controller.applied.append(tuple(values)) or 0)
        model = SimpleNamespace(classes=list(PROFILES["green_arabica"].names),
                                set_anomaly_reference=lambda labels: list(labels))
        with patch("engine.SorterSim") as self.sim, patch("engine.Inspector"), \
                patch("engine.Model.load", return_value=model), \
                patch("engine.Controller", return_value=self.controller), \
                patch("engine.subprocess.check_output", return_value="source\n"):
            return Engine(directory / "preset.json")

    def test_the_engine_starts_with_exactly_that_set_in_its_one_epoch(self):
        engine = self.build(["stone", "black"])

        self.assertEqual(engine.reject_classes, ("black", "stone"))
        self.assertEqual(self.controller.applied, [("black", "stone")])
        self.assertEqual(engine.anomaly_reference_labels,
                         [name for name in engine.profile.names if name not in ("black", "stone")])
        # One epoch: the session epoch at time zero, and no policy change event.
        self.assertEqual(engine.score_epoch_id, engine.session_id)
        self.assertEqual(engine.score_epoch_started_sim_time_s, 0.0)
        self.assertEqual(engine.policy_applied_sim_time_s, 0.0)
        self.assertEqual(list(engine._events), [])
        self.assertEqual(engine.policy_version, engine._policy_version())
        self.assertNotEqual(engine.policy_version, self.build().policy_version)

    def test_an_empty_list_keeps_every_class(self):
        engine = self.build([])

        self.assertEqual(engine.reject_classes, ())
        self.assertEqual(self.controller.applied, [()])
        self.assertEqual(engine.score_epoch_id, engine.session_id)

    def test_an_absent_field_keeps_the_severity_default_and_the_controller_untouched(self):
        engine = self.build()

        self.assertEqual(engine.reject_classes, tuple(
            item.name for item in engine.profile.classes
            if item.defect and item.severity in engine.policy.reject_severities))
        self.assertEqual(self.controller.applied, [])
        self.assertEqual(engine.score_epoch_id, engine.session_id)

    def test_an_invalid_field_is_a_clear_error_before_the_simulator_exists(self):
        cases = {"unknown label": (["stone", "star_token"],
                                   "unsupported reject classes: star_token"),
                 "duplicate": (["stone", "stone"], "must not contain duplicates"),
                 "not a list": ("stone", "must be a list")}
        for name, (initial, message) in cases.items():
            with self.subTest(case=name):
                with self.assertRaisesRegex(
                        ValueError, rf"policy\.initial_reject_classes: .*{message}"):
                    self.build(initial)
                self.sim.assert_not_called()


if __name__ == "__main__":
    unittest.main()
