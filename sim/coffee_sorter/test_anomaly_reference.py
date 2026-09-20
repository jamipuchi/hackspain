"""Per-label anomaly reference: fitting, the active set, and the policy change path.

Every score here comes from synthetic feature clouds. No test runs MuJoCo, no test
trains a real model, and no threshold is lowered, tuned, or made configurable:
`anomaly_thresh` stays the unchanged `good` threshold in every case.
"""
from __future__ import annotations

import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import bootstrap_model
from bootstrap_model import (
    MIN_LABEL_OBSERVATIONS,
    MIN_LABEL_UNIQUE_OBJECTS,
    collect_covered,
    collection_rounds,
    fit_label_reference,
    label_coverage,
    missing_references,
    require_label_coverage,
    require_usable_references,
    short_labels,
)
from classifier import MODELS, Model, usable_reference
from controller import Controller, Policy
from engine import Engine
from object_catalog import load_catalog
from profiles import GREEN_ARABICA
from test_controller import FakeInspector, FakeSim, blob

CANONICAL = MODELS / "live_green_arabica.joblib"
LABELS = ["good", "stone", "black"]
CENTRES = {"good": (0.0, 0.0, 0.0, 0.0), "stone": (8.0, 8.0, 0.0, 0.0),
           "black": (-8.0, 8.0, 0.0, 0.0)}
# An explicit index per label. Two labels of equal length must not share a noise stream.
LABEL_INDEX = {name: index for index, name in enumerate(CENTRES)}


class FakeClassifier:
    """Stand-in for the fitted classifier. The anomaly path never consults it."""

    def __init__(self, count):
        self.count = count

    def predict_proba(self, X):
        probabilities = np.zeros((len(X), self.count))
        probabilities[:, 0] = 1.0
        return probabilities


def cloud(name, count=200, spread=0.6, seed=3):
    rng = np.random.default_rng((seed, LABEL_INDEX[name]))
    return rng.normal(CENTRES[name], spread, (count, len(CENTRES[name])))


def synthetic_model(labels=tuple(LABELS)):
    """One Model with a fitted reference for every synthetic cloud."""
    references = {name: fit_label_reference(cloud(name)) for name in labels}
    good = references["good"]
    return Model(list(labels), FakeClassifier(len(labels)), good["mean"], good["icov"],
                 good["thresh"], good["scale"], {}, label_references=references)


def legacy_score(model, X):
    """Today's anomaly expression, unchanged."""
    d = (X - model.good_mean) / model.feature_scale
    return np.sqrt(np.einsum("ij,jk,ik->i", d, model.good_icov, d))


class ReferenceFitTest(unittest.TestCase):
    """One reference per trained label, with the exact formula of today's good cloud."""

    def test_the_reference_formula_matches_the_old_good_computation(self):
        values = cloud("good")
        scale = values.std(0) + 1e-6
        normalized = (values - values.mean(0)) / scale
        covariance = np.cov(normalized.T) + 0.05 * np.eye(normalized.shape[1])
        inverse = np.linalg.inv(covariance)
        distances = np.sqrt(np.einsum("ij,jk,ik->i", normalized, inverse, normalized))

        reference = fit_label_reference(values)

        np.testing.assert_array_equal(values.mean(0), reference["mean"])
        np.testing.assert_array_equal(scale, reference["scale"])
        np.testing.assert_array_equal(inverse, reference["icov"])
        self.assertEqual(float(np.percentile(distances, 99.7)), reference["thresh"])
        self.assertEqual(len(values), reference["n"])

    def test_every_label_owns_a_reference_and_good_drives_the_legacy_fields(self):
        model = synthetic_model()

        self.assertEqual(set(LABELS), set(model.references()))
        self.assertEqual(model.label_references["good"]["thresh"], model.anomaly_thresh)
        np.testing.assert_array_equal(model.label_references["good"]["mean"], model.good_mean)
        self.assertEqual([], model.active_reference_labels)

    def test_a_fitted_model_is_policy_independent_until_it_is_bound(self):
        model = synthetic_model()
        X = cloud("stone", count=20)

        self.assertEqual([], model.active_reference_labels)
        np.testing.assert_array_equal(legacy_score(model, X), model.predict(X)[1])


class AnomalyScoreTest(unittest.TestCase):
    """The active reference set follows the live policy, and no threshold ever moves."""

    def setUp(self):
        self.model = synthetic_model()
        self.threshold = self.model.anomaly_thresh

    def inside(self, name, count=20):
        """Points well inside one trained cloud.

        A reference threshold is the 99.7 percentile of its own train distances, so a
        fresh full-width sample legitimately carries tail points above it. This gate is
        about the cloud, not about its tail, so these points sit near a third of a sigma.
        """
        rng = np.random.default_rng(11)
        return rng.normal(CENTRES[name], 0.2, (count, len(CENTRES[name])))

    def anomalous(self, name=None, point=None):
        X = self.inside(name) if point is None else np.asarray([point], dtype=float)
        return self.model.predict(X)[1] > self.threshold

    def test_keep_all_accepts_every_trained_cloud_and_still_flags_a_far_point(self):
        self.assertEqual(LABELS, self.model.set_anomaly_reference(LABELS))

        self.assertFalse(self.anomalous("stone").any())
        self.assertFalse(self.anomalous("black").any())
        self.assertFalse(self.anomalous("good").any())
        self.assertTrue(self.anomalous(point=(400.0, -400.0, 90.0, 90.0)).all())

    def test_reject_all_falls_back_to_the_good_reference_with_finite_scores(self):
        self.assertEqual([], self.model.set_anomaly_reference([]))
        X = np.concatenate((cloud("good", count=10), cloud("stone", count=10)))

        scores = self.model.predict(X)[1]

        np.testing.assert_array_equal(legacy_score(self.model, X), scores)
        self.assertTrue(np.isfinite(scores).all())

    def test_keeping_stone_leaves_a_rejected_cloud_anomalous(self):
        self.model.set_anomaly_reference(["good", "stone"])

        self.assertFalse(self.anomalous("stone").any())
        self.assertFalse(self.anomalous("good").any())
        self.assertTrue(self.anomalous("black").all())

    def test_a_newly_activated_keep_type_becomes_a_reference(self):
        self.model.set_anomaly_reference(["good"])
        self.assertTrue(self.anomalous("stone").all())

        self.assertEqual(["good", "stone"], self.model.set_anomaly_reference(["good", "stone"]))
        self.assertFalse(self.anomalous("stone").any())
        self.assertTrue(self.anomalous(point=(400.0, -400.0, 90.0, 90.0)).all())

    def test_the_default_good_only_policy_scores_exactly_like_today(self):
        X = np.concatenate((cloud("good", count=30), cloud("black", count=30)))
        self.model.set_anomaly_reference(["good"])

        np.testing.assert_array_equal(legacy_score(self.model, X), self.model.predict(X)[1])

    def test_a_policy_change_takes_effect_on_the_next_predict_without_retraining(self):
        X = self.inside("stone")
        clf = self.model.clf
        self.model.set_anomaly_reference(["good"])
        before = self.model.predict(X)[1]

        self.model.set_anomaly_reference(["good", "stone"])
        after = self.model.predict(X)[1]

        self.assertIs(clf, self.model.clf)
        self.assertTrue((before > self.threshold).all())
        self.assertFalse((after > self.threshold).any())
        self.assertEqual(self.threshold, self.model.anomaly_thresh)

    def test_a_kept_label_without_a_reference_is_ignored(self):
        self.assertEqual(["good"], self.model.set_anomaly_reference(["good", "husk"]))

    def test_a_degenerate_reference_never_becomes_active(self):
        self.model.label_references["stone"]["thresh"] = 0.0

        self.assertEqual(["good"], self.model.set_anomaly_reference(["good", "stone"]))
        self.assertTrue(np.isfinite(self.model.predict(cloud("stone", count=5))[1]).all())


class OldModelFallbackTest(unittest.TestCase):
    """An artifact without per-label references predicts exactly as before."""

    def old_shape(self):
        model = copy.copy(synthetic_model())
        del model.__dict__["label_references"]
        del model.__dict__["active_reference_labels"]
        return model

    def test_a_synthetic_old_shape_object_takes_the_unchanged_path(self):
        model = self.old_shape()
        X = np.concatenate((cloud("good", count=20), cloud("stone", count=20)))

        self.assertFalse(hasattr(model, "label_references"))
        self.assertEqual({}, model.references())
        np.testing.assert_array_equal(legacy_score(model, X), model.predict(X)[1])

    def test_binding_an_old_model_is_a_safe_no_op(self):
        model = self.old_shape()
        X = cloud("stone", count=20)

        self.assertEqual([], model.set_anomaly_reference(["good", "stone"]))
        np.testing.assert_array_equal(legacy_score(model, X), model.predict(X)[1])

    @unittest.skipUnless(CANONICAL.is_file(), "the canonical artifact is a local build product")
    def test_the_canonical_artifact_unpickles_and_predicts(self):
        model = Model.load(CANONICAL)
        X = np.zeros((4, len(model.good_mean)))

        scores = model.predict(X)[1]

        self.assertEqual(GREEN_ARABICA.names, list(model.classes))
        np.testing.assert_array_equal(legacy_score(model, X), scores)
        self.assertTrue(np.isfinite(scores).all())


class CoverageGateTest(unittest.TestCase):
    """Every trained label needs coverage, counted as two separate numbers."""

    def rows(self, objects, per_object, label="good", seed=7):
        return [(seed, uid, label) for uid in range(objects) for _ in range(per_object)]

    def test_observations_and_unique_objects_are_separate_numbers(self):
        observations, unique = label_coverage(self.rows(12, 4) + self.rows(2, 1, "stone"))

        self.assertEqual({"good": 48, "stone": 2}, observations)
        self.assertEqual({"good": 12, "stone": 2}, unique)

    def test_a_label_short_on_either_count_misses_the_gate(self):
        plenty = {"good": 100, "stone": 100}
        cases = {
            "short observations": ({"good": 100, "stone": MIN_LABEL_OBSERVATIONS - 1}, plenty),
            "short unique objects": (plenty, {"good": 100, "stone": MIN_LABEL_UNIQUE_OBJECTS - 1}),
            "absent label": ({"good": 100}, {"good": 100}),
        }
        for name, (observations, unique) in cases.items():
            with self.subTest(case=name):
                self.assertEqual(["stone"], short_labels(["good", "stone"], observations, unique))
        self.assertEqual([], short_labels(["good", "stone"], plenty, plenty))

    def test_persistent_short_coverage_fails_explicitly(self):
        with self.assertRaisesRegex(RuntimeError, "insufficient_label_coverage"):
            require_label_coverage("training", ["good", "stone"], {"good": 100, "stone": 4},
                                   {"good": 100, "stone": 1})

    def collect(self, per_second, rounds=None):
        """Collect with a fake partition where a longer run replays the shorter one."""
        calls = []

        def partition(seed, seconds, *rest):
            calls.append(seconds)
            rows = self.rows(int(seconds * per_second), 3, seed=seed)
            return (np.zeros((len(rows), 4)),
                    np.asarray(["good"] * len(rows), dtype=object), rows)

        profile = SimpleNamespace(names=["good"])
        with patch.object(bootstrap_model, "collect_partition", side_effect=partition):
            X, y, rows, history = collect_covered(7, 500.0, 5, None, 4, profile,
                                                  rounds or collection_rounds(4.0))
        return calls, rows, history

    def test_each_round_replaces_the_previous_data_and_is_never_added(self):
        calls, rows, history = self.collect(per_second=1)

        self.assertEqual([4.0, 8.0, 16.0], calls)
        self.assertEqual(3, len(history))
        # 16 objects x 3 observations, from the longest run alone. A repeated prefix is
        # never counted twice, so the totals are not 4 + 8 + 16 objects.
        self.assertEqual(48, len(rows))
        self.assertEqual({"good": 48}, history[-1]["observations"])
        self.assertEqual({"good": 16}, history[-1]["unique_objects"])
        self.assertEqual([{"good": 12}, {"good": 24}, {"good": 48}],
                         [round["observations"] for round in history])
        self.assertEqual([], history[-1]["short_labels"])

    def test_collection_stops_at_the_first_round_that_meets_the_gate(self):
        calls, rows, history = self.collect(per_second=4)

        self.assertEqual([4.0], calls)
        self.assertEqual(1, len(history))
        self.assertEqual({"good": 16}, history[-1]["unique_objects"])

    def test_a_label_that_stays_short_records_every_round(self):
        calls, _, history = self.collect(per_second=0.5)

        self.assertEqual([4.0, 8.0, 16.0], calls)
        self.assertEqual(["good"], history[-1]["short_labels"])
        self.assertEqual([4.0, 8.0, 16.0], [round["seconds"] for round in history])

    def test_the_default_rounds_are_the_approved_durations(self):
        self.assertEqual((4.0, 8.0, 16.0), collection_rounds(4.0))

    def test_a_zero_variance_label_meets_the_counts_but_owns_no_usable_reference(self):
        """Counts alone never prove a reference, so the fitted model is checked as well."""
        identical = np.tile(CENTRES["stone"], (40, 1))
        rows = self.rows(10, 4, "stone") + self.rows(10, 4, "good")
        observations, unique = label_coverage(rows)
        reference = fit_label_reference(identical)

        self.assertEqual([], short_labels(["good", "stone"], observations, unique))
        self.assertEqual(40, observations["stone"])
        self.assertEqual(10, unique["stone"])
        self.assertEqual(0.0, reference["thresh"])
        self.assertFalse(usable_reference(reference))

    def test_a_model_missing_one_usable_reference_fails_the_shared_check(self):
        model = synthetic_model()
        model.label_references["stone"] = fit_label_reference(np.tile(CENTRES["stone"], (40, 1)))

        self.assertEqual(["stone"], missing_references(model.references(), LABELS))
        with self.assertRaisesRegex(RuntimeError, "insufficient_label_coverage.*stone"):
            require_usable_references("training", model, LABELS)
        require_usable_references("training", synthetic_model(), LABELS)


class UndecidedTrackResetTest(unittest.TestCase):
    """A policy change resets undecided anomaly maxima and leaves decided tracks alone."""

    class ScriptedModel:
        classes = ["black"]
        anomaly_thresh = 10.0

        def __init__(self, anomaly):
            self.anomaly = anomaly
            self.active_reference_labels = []

        def predict(self, X):
            return np.ones((len(X), 1)), np.full(len(X), self.anomaly)

        def set_anomaly_reference(self, labels):
            self.active_reference_labels = list(labels)
            return list(self.active_reference_labels)

    def controller(self, blobs, anomaly):
        sim = FakeSim()
        model = self.ScriptedModel(anomaly)
        return sim, model, Controller(sim, FakeInspector(blobs), model,
                                      Policy(latency_floor=0.004, threshold=2.0))

    def test_one_observation_after_the_reset_decides_with_the_new_score_only(self):
        sim, model, ctrl = self.controller([blob(-0.130), blob(-0.118)], anomaly=12.0)
        with patch("controller.time.perf_counter", return_value=0.0):
            ctrl.on_frame(None, 0.000)
        track = ctrl.tracks[0]
        self.assertEqual(12.0, track.anomaly)

        reset = ctrl.set_reject_classes(["black"])
        model.anomaly = 0.5
        with patch("controller.time.perf_counter", return_value=0.0):
            ctrl.on_frame(None, 0.004)

        self.assertEqual(1, reset)
        self.assertTrue(track.done)
        self.assertEqual(0.5, track.anomaly)
        self.assertFalse(ctrl.decisions[0].reject)

    def test_without_a_further_observation_the_classifier_decides_alone(self):
        sim, model, ctrl = self.controller([blob(-0.130), blob(-0.100)], anomaly=12.0)
        with patch("controller.time.perf_counter", return_value=0.0):
            ctrl.on_frame(None, 0.000)
        track = ctrl.tracks[0]

        reset = ctrl.set_reject_classes(["black"])
        with patch("controller.time.perf_counter", return_value=0.0):
            ctrl.on_frame(None, 0.050)

        self.assertEqual(1, reset)
        self.assertEqual(0.0, track.anomaly)
        self.assertTrue(track.done)
        self.assertFalse(ctrl.decisions[0].reject)

    def test_a_decided_track_and_its_pulse_survive_the_policy_change(self):
        sim, model, ctrl = self.controller([blob(-0.130), blob(-0.118)], anomaly=12.0)
        with patch("controller.time.perf_counter", return_value=0.0):
            ctrl.on_frame(None, 0.000)
            ctrl.on_frame(None, 0.004)
        decided = ctrl.tracks[0]
        before = (decided.anomaly, decided.done, copy.deepcopy(ctrl.decisions[0]), list(sim.fires))

        reset = ctrl.set_reject_classes(["good"])

        self.assertEqual(0, reset)
        self.assertEqual(before[0], decided.anomaly)
        self.assertEqual(before[1], decided.done)
        self.assertEqual(before[2], ctrl.decisions[0])
        self.assertEqual(before[3], sim.fires)


class PolicyChangePublicationTest(unittest.TestCase):
    """The engine binds the reference set and reports the reset count with the policy."""

    def engine(self):
        engine = Engine.__new__(Engine)
        engine.continuous = True
        engine.profile = GREEN_ARABICA
        engine.policy = Policy()
        engine.reject_classes = tuple(item.name for item in engine.profile.classes if item.defect)
        engine.policy_version = engine._policy_version()
        engine.score_epoch_id = "epoch-1"
        engine.score_epoch_started_sim_time_s = 0.0
        engine.policy_applied_sim_time_s = 0.0
        engine.preset = {"score_window_seconds": 60.0}
        engine._score_ledger = _EmptyLedger()
        engine.sim = SimpleNamespace(data=SimpleNamespace(time=5.0), bean_of={})
        engine._object_records = {}
        engine.model = UndecidedTrackResetTest.ScriptedModel(0.0)
        engine.model.classes = list(GREEN_ARABICA.names)
        engine.anomaly_reference_labels = []
        engine.controller = SimpleNamespace(set_reject_classes=lambda values: 3)
        engine.events = []
        engine._event = lambda name, **fields: engine.events.append({"type": name, **fields})
        return engine

    def test_the_policy_change_publishes_the_reference_labels_and_the_reset_count(self):
        engine = self.engine()

        ack = engine.set_reject_classes(["stone"])
        event = engine.events[-1]

        self.assertTrue(ack["changed"])
        self.assertEqual(3, ack["undecided_tracks_reset"])
        self.assertNotIn("stone", ack["anomaly_reference_labels"])
        self.assertIn("good", ack["anomaly_reference_labels"])
        self.assertEqual(ack["anomaly_reference_labels"], engine.model.active_reference_labels)
        self.assertEqual("reject_policy_changed", event["type"])
        self.assertEqual(3, event["undecided_tracks_reset"])
        self.assertEqual(ack["anomaly_reference_labels"], event["anomaly_reference_labels"])

    def test_an_unchanged_policy_reports_no_reset(self):
        engine = self.engine()

        ack = engine.set_reject_classes(list(engine.reject_classes))

        self.assertFalse(ack["changed"])
        self.assertEqual(0, ack["undecided_tracks_reset"])
        self.assertEqual([], ack["anomaly_reference_labels"])

    def test_reject_all_state_serializes_without_infinity_or_nan(self):
        """The reject-all fallback keeps every published anomaly number finite."""
        engine = self.engine()
        model = synthetic_model()
        engine.model = model
        model.classes = list(model.classes)
        engine.controller = SimpleNamespace(set_reject_classes=lambda values: 0)

        ack = engine.set_reject_classes(list(GREEN_ARABICA.names))
        X = np.concatenate((cloud("good", count=10), cloud("stone", count=10)))
        scores = model.predict(X)[1]
        state = {"reject_policy": ack, "anomaly": [float(value) for value in scores]}

        self.assertEqual([], ack["anomaly_reference_labels"])
        self.assertTrue(np.isfinite(scores).all())
        json.dumps(state, allow_nan=False)


class _EmptyLedger:
    def __len__(self):
        return 0


class StoneTruthTest(unittest.TestCase):
    """This commit never rewrites a truth value."""

    def test_stone_stays_a_foreign_defect_in_the_catalog_and_the_profile(self):
        stone = GREEN_ARABICA.by_name("stone")
        definition = next(value for value in load_catalog()["definitions"]
                          if value["classifier_label"] == "stone")

        self.assertTrue(stone.defect)
        self.assertEqual("foreign", stone.severity)
        self.assertEqual({"defect": True, "severity": "foreign"}, definition["truth"])

    def test_keeping_stone_changes_the_reference_set_and_not_its_truth(self):
        model = synthetic_model()

        model.set_anomaly_reference(["good", "stone"])

        self.assertEqual(["good", "stone"], model.active_reference_labels)
        self.assertTrue(GREEN_ARABICA.by_name("stone").defect)


if __name__ == "__main__":
    unittest.main()
