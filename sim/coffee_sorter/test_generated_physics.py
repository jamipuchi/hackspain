"""Physics route validation for generated items, and the strict candidate gate.

Real MuJoCo runs here only for the two tracked route fixtures. Every heavy path
takes a temporary runtime lock, so no test touches the shared lock file. The
trainer gate is exercised through its pure functions with synthetic arrays and a
fake engine. No test runs a real training or a real closed-loop run.
"""
from __future__ import annotations

import contextlib
import copy
import dataclasses
import fcntl
import hashlib
import inspect
import io
import json
import platform
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import bootstrap_model
import profiles
import train_candidate
import validate_object_route
from object_catalog import (
    candidate_catalog,
    load_catalog,
    select_victim,
    type_definition_from_draft,
    write_catalog,
)
from test_object_catalog import star_draft
from bootstrap_model import MIN_LABEL_OBSERVATIONS, MIN_LABEL_UNIQUE_OBJECTS
from train_candidate import (
    MIN_KEEP_RESOLVED,
    anomaly_evidence,
    coverage,
    gate_failures,
    holdout_metrics,
    keep_outcome,
)
from validate_object_route import route_verdict, static_verdict

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "tests" / "fixtures"
RING = FIXTURES / "generated_ring" / "definition.json"
BOX = FIXTURES / "generated_box" / "definition.json"
PRESET = HERE / "configs" / "continuous_demo.json"


def accepted_trials(count=5):
    return [{"outcome": "accept", "mass_kg": 0.0004608, "margin_mm": 64.0, "sim_time_s": 0.45}
            for _ in range(count)]


class RouteCliTest(unittest.TestCase):
    """Every validator run uses its own temporary lock and its own output file."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.work = Path(folder.name)
        self.lock = self.work / "runtime.lock"

    def run_cli(self, *arguments):
        """Run the validator in process. Return (exit code, result or None)."""
        out = self.work / "result.json"
        with contextlib.redirect_stdout(io.StringIO()):
            code = validate_object_route.main([
                *arguments, "--json-out", str(out), "--runtime-lock", str(self.lock)])
        return code, json.loads(out.read_text()) if out.is_file() else None

    def box_arguments(self, definition=BOX, **changes):
        arguments = {"--preset": str(PRESET), "--seed": "8", "--background-rate": "0"}
        arguments.update(changes)
        flat = [item for pair in arguments.items() for item in pair]
        return ["--definition", str(definition), "--no-air", *flat]


class RingRouteTest(RouteCliTest):
    """The ring fixture stays blocked and never starts a simulation."""

    def test_ring_reaches_unsupported_proxy_without_any_simulation(self):
        with patch.object(validate_object_route, "run_trials",
                          side_effect=AssertionError("the ring must never simulate")):
            code, result = self.run_cli("--definition", str(RING), "--expect", "physics_unsupported")

        self.assertEqual(0, code)
        self.assertEqual("physics_unsupported", result["verdict"])
        self.assertEqual("unsupported_proxy", result["reason"])
        self.assertEqual([], result["trials"])
        self.assertIsNone(result["load"])
        self.assertIn("ring opening", result["unsupported_reason"])
        self.assertIn("box and capsule", result["limitations"])
        self.assertEqual("unmeasured_proxy_estimate", result["estimate_basis"])

    def test_the_ring_run_never_creates_the_lock_file(self):
        self.run_cli("--definition", str(RING))

        self.assertFalse(self.lock.exists())


class BoxRouteTest(RouteCliTest):
    """The tracked box fixture reaches accept on five seeded trials."""

    def test_box_accepts_five_seeded_trials(self):
        code, result = self.run_cli(*self.box_arguments(), "--expect", "accept")

        self.assertEqual(0, code)
        self.assertEqual("accept", result["verdict"])
        self.assertIsNone(result["reason"])
        self.assertEqual(8, result["seed"])
        self.assertEqual(5, len(result["trials"]))
        for trial in result["trials"]:
            with self.subTest(margin_mm=trial["margin_mm"]):
                self.assertEqual("accept", trial["outcome"])
                self.assertGreater(trial["margin_mm"], 0.0)
                self.assertAlmostEqual(0.0004608, trial["mass_kg"])
        self.assertEqual("unmeasured_proxy_estimate", result["estimate_basis"])
        self.assertEqual(hashlib.sha256(PRESET.read_bytes()).hexdigest(), result["preset_sha256"])

    def test_an_expected_verdict_that_does_not_match_exits_one(self):
        code, result = self.run_cli(*self.box_arguments(), "--expect", "physics_unsupported")

        self.assertEqual(1, code)
        self.assertEqual("accept", result["verdict"])


class RouteUsageTest(RouteCliTest):
    """Only the no-air route exists, and a busy lock stops before any heavy work."""

    def test_a_route_without_no_air_is_refused(self):
        with self.assertRaises(SystemExit) as raised, contextlib.redirect_stderr(io.StringIO()):
            validate_object_route.main(["--definition", str(BOX), "--preset", str(PRESET),
                                        "--runtime-lock", str(self.lock)])

        self.assertEqual(2, raised.exception.code)

    def test_a_busy_runtime_lock_exits_75_before_the_route_runs(self):
        holder = open(self.lock, "a")
        self.addCleanup(holder.close)
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)

        with patch.object(validate_object_route, "run_trials",
                          side_effect=AssertionError("a busy lock must stop the route")):
            with contextlib.redirect_stderr(io.StringIO()):
                code, result = self.run_cli(*self.box_arguments())

        self.assertEqual(75, code)
        self.assertIsNone(result)

    def test_a_free_runtime_lock_is_released_after_the_route(self):
        with patch.object(validate_object_route, "run_trials", return_value=accepted_trials()):
            code, _ = self.run_cli(*self.box_arguments())
        second = open(self.lock, "a")
        self.addCleanup(second.close)

        self.assertEqual(0, code)
        fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)


class RouteVerdictTest(unittest.TestCase):
    """The verdict is a pure function over the recorded trials."""

    def test_every_trial_must_accept(self):
        self.assertEqual(("accept", None), route_verdict(accepted_trials()))

    def test_one_injected_non_accept_trial_blocks_the_route(self):
        trials = accepted_trials(4)
        trials.append({"outcome": "reject", "mass_kg": 0.0004608, "margin_mm": -3.0,
                       "sim_time_s": 0.44})

        self.assertEqual(("physics_unsupported", "route_not_accepted"), route_verdict(trials))

    def test_an_unresolved_trial_and_an_empty_run_block_the_route(self):
        unresolved = [{"outcome": "unresolved", "mass_kg": 0.1, "margin_mm": None,
                       "sim_time_s": None}]

        self.assertEqual(("physics_unsupported", "route_not_accepted"), route_verdict(unresolved))
        self.assertEqual(("physics_unsupported", "route_not_accepted"), route_verdict([]))


class RepresentativeLoadTest(RouteCliTest):
    """Representative load is evidence only. It never changes the verdict."""

    def test_load_evidence_never_changes_an_accepted_verdict(self):
        evidence = {"evidence_only": True, "injected": 20,
                    "outcomes": {"accept": 2, "reject": 18}, "seconds": 2.0}
        with patch.object(validate_object_route, "run_trials", return_value=accepted_trials()), \
                patch.object(validate_object_route, "representative_load", return_value=evidence):
            code, result = self.run_cli(*self.box_arguments(), "--load-seconds", "2.0")

        self.assertEqual(0, code)
        self.assertEqual("accept", result["verdict"])
        self.assertEqual(evidence, result["load"])

    def test_no_load_runs_without_load_seconds_or_after_a_blocked_route(self):
        blocked = accepted_trials(4) + [{"outcome": "reject", "mass_kg": 0.1, "margin_mm": -2.0,
                                         "sim_time_s": 0.4}]
        with patch.object(validate_object_route, "run_trials", return_value=blocked), \
                patch.object(validate_object_route, "representative_load",
                             side_effect=AssertionError("a blocked route must not load")):
            code, result = self.run_cli(*self.box_arguments(), "--load-seconds", "2.0")

        self.assertEqual(0, code)
        self.assertEqual("route_not_accepted", result["reason"])
        self.assertIsNone(result["load"])


class StaticValidationTest(unittest.TestCase):
    """Dimensions, density, mass, proxy volume, units, and hashes are checked together."""

    def draft(self, **changes):
        value = json.loads(BOX.read_text())
        value["physics"].update(copy.deepcopy(changes))
        return value

    def test_a_valid_supported_draft_passes(self):
        self.assertEqual((None, None), static_verdict(json.loads(BOX.read_text())))

    def test_a_glb_hash_mismatch_is_invalid_physics(self):
        value = json.loads(BOX.read_text())
        value["visual"]["visual_asset_id"] = "sha256:" + "b" * 64

        self.assertEqual(("invalid_physics", "visual_asset_hash_mismatch"), static_verdict(value))

    def test_both_tracked_fixtures_verify_against_the_default_asset_root(self):
        self.assertEqual((None, None), static_verdict(json.loads(BOX.read_text())))
        self.assertEqual("unsupported_proxy", static_verdict(json.loads(RING.read_text()))[0])

    def test_inconsistent_or_impossible_physics_is_invalid_physics(self):
        cases = {
            "inconsistent mass": self.draft(mass_kg=0.5),
            "inconsistent volume": self.draft(proxy_volume_m3=1e-9),
            "negative density": self.draft(density_kg_m3=-1200.0),
            "non-finite density": self.draft(density_kg_m3=float("nan")),
            "non-finite dimension": self.draft(
                proxy={"shape": "box", "half_extents_m": [float("inf"), 0.006, 0.001]}),
            "negative dimension": self.draft(
                proxy={"shape": "box", "half_extents_m": [-0.008, 0.006, 0.001]}),
        }
        for name, value in cases.items():
            with self.subTest(case=name):
                self.assertEqual("invalid_physics", static_verdict(value)[0])

    def test_an_unsupported_proxy_is_reported_before_any_route(self):
        reason, detail = static_verdict(json.loads(RING.read_text()))

        self.assertEqual("unsupported_proxy", reason)
        self.assertIn("ring opening", detail)


class VisualAssetRootTest(unittest.TestCase):
    """The visual uri stays relative and inside the trusted asset root, and its hash is mandatory."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.work = Path(folder.name)
        self.root = self.work / "jobs"
        (self.root / "assets").mkdir(parents=True)
        payload = b"glTF fake asset bytes"
        (self.root / "assets" / "object.glb").write_bytes(payload)
        self.digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        self.outside = self.work / "outside.glb"
        self.outside.write_bytes(payload)

    def draft(self, uri, asset_id=None):
        value = json.loads(BOX.read_text())
        value["visual"]["uri"] = uri
        value["visual"]["visual_asset_id"] = asset_id or self.digest
        return value

    def test_a_verified_asset_under_a_non_default_root_passes(self):
        self.assertEqual((None, None),
                         static_verdict(self.draft("assets/object.glb"), self.root))

    def test_a_wrong_hash_is_refused(self):
        value = self.draft("assets/object.glb", "sha256:" + "b" * 64)

        self.assertEqual(("invalid_physics", "visual_asset_hash_mismatch"),
                         static_verdict(value, self.root))

    def test_a_missing_file_fails_verification(self):
        self.assertEqual(("invalid_physics", "visual_asset_missing"),
                         static_verdict(self.draft("assets/absent.glb"), self.root))

    def test_an_absolute_uri_is_refused_even_with_the_right_hash(self):
        inside = self.root / "assets" / "object.glb"
        for name, uri in (("inside the root", inside), ("outside the root", self.outside)):
            with self.subTest(uri=name):
                self.assertEqual(("invalid_physics", "visual_uri_not_relative"),
                                 static_verdict(self.draft(str(uri)), self.root))

    def test_a_parent_traversal_is_refused(self):
        uri = "../" * 20 + "outside.glb"

        self.assertEqual(("invalid_physics", "visual_uri_outside_asset_root"),
                         static_verdict(self.draft(uri), self.root))

    def test_a_symlink_that_leaves_the_asset_root_is_refused(self):
        (self.root / "escape.glb").symlink_to(self.outside)

        self.assertEqual(("invalid_physics", "visual_uri_outside_asset_root"),
                         static_verdict(self.draft("escape.glb"), self.root))

    def test_no_host_path_reaches_the_failure_detail(self):
        for uri in (str(self.outside), "../" * 20 + "outside.glb"):
            with self.subTest(relative=not Path(uri).is_absolute()):
                detail = static_verdict(self.draft(uri), self.root)[1]

                self.assertNotIn("/", detail)

    def test_an_unsupported_draft_is_checked_against_the_asset_first(self):
        value = json.loads(RING.read_text())
        value["visual"]["uri"] = "assets/object.glb"

        self.assertEqual(("invalid_physics", "visual_asset_hash_mismatch"),
                         static_verdict(value, self.root))


class AssetRootResultTest(RouteCliTest):
    """The result names the asset root by label only, and echoes a relative uri."""

    def test_the_result_carries_no_host_path(self):
        code, result = self.run_cli("--definition", str(RING))
        encoded = json.dumps(result)

        self.assertEqual(0, code)
        self.assertEqual("asset_root", result["asset_root"])
        self.assertTrue(result["asset_root_is_default"])
        self.assertEqual(json.loads(RING.read_text())["visual"]["uri"], result["visual_uri"])
        self.assertNotIn(str(HERE), encoded)
        self.assertNotIn(str(self.work), encoded)

    def test_a_non_default_asset_root_is_reported_as_non_default(self):
        code, result = self.run_cli("--definition", str(RING), "--asset-root", str(self.work))

        self.assertEqual(0, code)
        self.assertEqual("invalid_physics", result["reason"])
        self.assertEqual("visual_asset_missing", result["detail"])
        self.assertFalse(result["asset_root_is_default"])
        self.assertNotIn(str(self.work), json.dumps(result))


class CandidateGateTest(unittest.TestCase):
    """The strict gate keeps four kinds of evidence separate and derives none from another."""

    def validation(self, **changes):
        value = {
            "labels": ["star_token", "good", "stone"],
            "new_label": "star_token",
            "new_label_truth": {"defect": False, "severity": "none"},
            "label_order_ok": True,
            "classifier": {
                "holdout_accuracy": 0.97,
                "new_label_recall": 1.0,
                "holdout_observations": {"star_token": 77, "good": 900, "stone": 40},
                "holdout_unique_objects": {"star_token": 31, "good": 210, "stone": 12},
            },
            "anomaly": {"label": "star_token", "fraction_above_threshold": 0.0,
                        "median": 3.2, "threshold": 14.339, "observations": 77,
                        "anomaly_reference_labels": ["star_token", "good"]},
            "reference_coverage": {
                "train_observations": {"star_token": 120, "good": 2400, "stone": 90},
                "train_unique_objects": {"star_token": 38, "good": 600, "stone": 30},
                "reference_labels": ["good", "star_token", "stone"],
            },
            "preset_compatibility": {"loaded": True, "reason": None,
                                     "loader": "live.load_preset"},
            "pulses": {"runs": [{"seed": 8, "commanded": 0, "activated": 0, "jet_hits": 0}]},
            "keep_outcome": {"required": True, "runs": [
                {"seed": 8, "sim_seconds": 6.2, "resolved": 34, "accepted": 34,
                 "rejected": 0, "spilled": 0, "accept_fraction": 1.0}]},
        }
        for key, update in changes.items():
            if isinstance(update, dict) and isinstance(value.get(key), dict):
                value[key] = {**value[key], **update}
            else:
                value[key] = update
        return value

    def test_a_complete_candidate_passes(self):
        self.assertEqual([], gate_failures(self.validation()))

    def test_recall_one_with_a_keep_anomaly_fraction_of_one_fails(self):
        value = self.validation(anomaly={"fraction_above_threshold": 1.0, "median": 401.11})

        self.assertEqual(1.0, value["classifier"]["new_label_recall"])
        self.assertEqual(["anomaly_fraction"], gate_failures(value))

    def test_holdout_unique_objects_below_ten_fails(self):
        value = self.validation(classifier={"holdout_unique_objects": {
            "star_token": 9, "good": 210, "stone": 12}})

        self.assertEqual(["holdout_coverage"], gate_failures(value))

    def test_a_missing_label_in_the_coverage_counts_fails(self):
        value = self.validation(classifier={"holdout_unique_objects": {"good": 210, "stone": 12}})

        self.assertEqual(["holdout_coverage"], gate_failures(value))

    def test_accuracy_or_recall_below_the_gate_fails(self):
        self.assertEqual(["holdout_accuracy"],
                         gate_failures(self.validation(classifier={"holdout_accuracy": 0.89})))
        self.assertEqual(["new_label_recall"],
                         gate_failures(self.validation(classifier={"new_label_recall": 0.89})))

    def test_a_wrong_label_order_fails(self):
        self.assertEqual(["label_order"], gate_failures(self.validation(label_order_ok=False)))

    def test_keep_outcome_below_the_resolved_or_accept_gate_fails(self):
        few = self.validation(keep_outcome={"required": True, "runs": [
            {"seed": 8, "sim_seconds": 20.0, "resolved": 29, "accepted": 29, "rejected": 0,
             "spilled": 0, "accept_fraction": 1.0}]})
        low = self.validation(keep_outcome={"required": True, "runs": [
            {"seed": 8, "sim_seconds": 9.0, "resolved": 30, "accepted": 28, "rejected": 2,
             "spilled": 0, "accept_fraction": 28 / 30}]})

        self.assertEqual(["keep_outcome_resolved"], gate_failures(few))
        self.assertEqual(["keep_outcome_accept_fraction"], gate_failures(low))

    def test_a_candidate_without_a_closed_loop_run_fails(self):
        self.assertEqual(["keep_outcome_missing"],
                         gate_failures(self.validation(keep_outcome=None)))

    def test_a_defect_truth_candidate_is_gated_exactly_like_any_other(self):
        """Keep or Reject is a policy. Physical truth never switches a gate off."""
        value = self.validation(new_label_truth={"defect": True, "severity": "major"},
                                anomaly={"fraction_above_threshold": 1.0},
                                keep_outcome={"required": True, "runs": [
                                    {"seed": 8, "sim_seconds": 9.0, "resolved": 30, "accepted": 0,
                                     "rejected": 30, "spilled": 0, "accept_fraction": 0.0}]})

        self.assertEqual(["anomaly_fraction", "keep_outcome_accept_fraction"],
                         gate_failures(value))
        self.assertEqual({"defect": True, "severity": "major"}, value["new_label_truth"])

    def test_a_defect_truth_candidate_with_clean_evidence_passes(self):
        value = self.validation(new_label_truth={"defect": True, "severity": "foreign"})

        self.assertEqual([], gate_failures(value))

    def test_a_label_short_on_either_coverage_count_blocks_the_model(self):
        cases = {
            "short observations": {"train_observations": {
                "star_token": MIN_LABEL_OBSERVATIONS - 1, "good": 2400, "stone": 90}},
            "short unique objects": {"train_unique_objects": {
                "star_token": MIN_LABEL_UNIQUE_OBJECTS - 1, "good": 600, "stone": 30}},
        }
        for name, change in cases.items():
            with self.subTest(case=name):
                self.assertEqual(["insufficient_label_coverage"],
                                 gate_failures(self.validation(reference_coverage=change)))

    def test_a_trained_label_without_a_reference_blocks_the_model(self):
        value = self.validation(reference_coverage={"reference_labels": ["good", "stone"]})

        self.assertEqual(["insufficient_label_coverage"], gate_failures(value))

    def test_the_four_evidence_kinds_stay_separate(self):
        value = self.validation()
        classifier, anomaly = value["classifier"], value["anomaly"]
        pulses = value["pulses"]["runs"][0]
        outcomes = value["keep_outcome"]["runs"][0]

        for block in (classifier, anomaly, pulses, outcomes):
            self.assertIsNotNone(block)
        self.assertNotIn("accept_fraction", {**classifier, **anomaly, **pulses})
        self.assertNotIn("commanded", {**classifier, **anomaly, **outcomes})
        self.assertNotIn("new_label_recall", {**anomaly, **pulses, **outcomes})
        self.assertNotIn("fraction_above_threshold", {**classifier, **pulses, **outcomes})


class CandidateMetricsTest(unittest.TestCase):
    """Classifier, anomaly, and coverage numbers come from separate synthetic inputs."""

    def test_holdout_metrics_counts_observations_only(self):
        classes = ["star_token", "good"]
        probabilities = np.array([[0.9, 0.1], [0.8, 0.2], [0.1, 0.9], [0.6, 0.4]])
        truth = np.asarray(["star_token", "star_token", "good", "good"], dtype=object)

        metrics = holdout_metrics(classes, truth, probabilities, "star_token")

        self.assertEqual(1.0, metrics["new_label_recall"])
        self.assertEqual(0.75, metrics["holdout_accuracy"])
        self.assertEqual(4, metrics["observations"])

    def test_anomaly_evidence_reports_the_new_label_only(self):
        scores = np.array([401.0, 380.0, 2.0, 3.0])
        selected = np.asarray([True, True, False, False])

        evidence = anomaly_evidence(scores, 14.339, selected)

        self.assertEqual(1.0, evidence["fraction_above_threshold"])
        self.assertAlmostEqual(390.5, evidence["median"])
        self.assertEqual(14.339, evidence["threshold"])
        self.assertEqual(2, evidence["observations"])

    def test_coverage_reports_observations_and_unique_objects_separately(self):
        rows = [(9, 1, "star_token"), (9, 1, "star_token"), (9, 2, "star_token"), (9, 3, "good")]

        observations, unique = coverage(rows)

        self.assertEqual({"star_token": 3, "good": 1}, observations)
        self.assertEqual({"star_token": 2, "good": 1}, unique)


class FakeEngine:
    """Engine-like double. It resolves one object of one label per step."""

    def __init__(self, outcomes, label="star_token", dt=0.001, commanded=0):
        self.sim = SimpleNamespace(dt=dt, bean_of={})
        self.outcomes = list(outcomes)
        self.label = label
        self.commanded = commanded
        self.steps = 0

    def step(self):
        self.steps += 1
        if not self.outcomes:
            return
        uid = len(self.sim.bean_of)
        self.sim.bean_of[uid] = SimpleNamespace(
            uid=uid, cls=self.label, outcome=self.outcomes.pop(0),
            targeted=uid < self.commanded, fired_target=uid < self.commanded, jet_hits=uid < self.commanded)


class KeepOutcomeTest(unittest.TestCase):
    """The closed-loop gate reads physical outcomes, never a classifier number."""

    def test_a_clean_run_stops_at_the_resolved_minimum(self):
        engine = FakeEngine(["accept"] * 40)

        run = keep_outcome(engine, "star_token", seed=8, max_sim_seconds=0.1)

        self.assertEqual(MIN_KEEP_RESOLVED, run["outcomes"]["resolved"])
        self.assertEqual(MIN_KEEP_RESOLVED, run["outcomes"]["accepted"])
        self.assertEqual(1.0, run["outcomes"]["accept_fraction"])
        self.assertEqual(MIN_KEEP_RESOLVED, engine.steps)
        self.assertEqual(8, run["seed"])

    def test_the_run_stops_at_the_simulation_cap(self):
        engine = FakeEngine(["accept"] * 5)

        run = keep_outcome(engine, "star_token", seed=8, max_sim_seconds=0.02)

        self.assertEqual(5, run["outcomes"]["resolved"])
        self.assertEqual(20, engine.steps)
        self.assertAlmostEqual(0.02, run["sim_seconds"])

    def test_outcomes_and_pulses_stay_separate(self):
        engine = FakeEngine(["accept"] * 28 + ["reject", "spilled"], commanded=3)

        run = keep_outcome(engine, "star_token", seed=8, max_sim_seconds=0.1)

        self.assertEqual({"resolved": 30, "accepted": 28, "rejected": 1, "spilled": 1,
                          "accept_fraction": 28 / 30}, run["outcomes"])
        self.assertEqual({"commanded": 3, "activated": 3, "jet_hits": 3}, run["pulses"])

    def test_objects_of_another_label_are_ignored(self):
        engine = FakeEngine(["accept"] * 4, label="good")

        run = keep_outcome(engine, "star_token", seed=8, max_sim_seconds=0.01)

        self.assertEqual(0, run["outcomes"]["resolved"])
        self.assertEqual(0.0, run["outcomes"]["accept_fraction"])


class TrainerRuntimeLockTest(unittest.TestCase):
    """A busy runtime lock stops the trainer before any collection. No real training runs here."""

    def test_a_busy_runtime_lock_exits_75_before_any_collection(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        work = Path(folder.name)
        lock = work / "runtime.lock"
        holder = open(lock, "a")
        self.addCleanup(holder.close)
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        catalog = load_catalog()
        new_definition = type_definition_from_draft(
            star_draft(), rgb=[0.82, 0.68, 0.21],
            prior=0.03, source_sha256="c" * 64)
        write_catalog(work / "candidate",
                      candidate_catalog(catalog, new_definition, select_victim(catalog, [])))

        with patch.object(train_candidate, "train",
                          side_effect=AssertionError("a busy lock must stop the trainer")):
            with contextlib.redirect_stderr(io.StringIO()):
                code = train_candidate.main([
                    "--catalog-root", str(work / "candidate"), "--preset", str(PRESET),
                    "--out", str(work / "out"), "--runtime-lock", str(lock)])

        self.assertEqual(75, code)
        self.assertFalse((work / "out").exists())


class CandidatePolicyTest(unittest.TestCase):
    """Every candidate is validated as Keep, and the closed-loop run applies that policy."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.work = Path(folder.name)
        self.preset = json.loads(PRESET.read_text())
        self.catalog = load_catalog()
        self.profile = SimpleNamespace(
            names=["star_token", *[item.name for item in profiles.GREEN_ARABICA.classes]],
            classes=[SimpleNamespace(name="star_token", defect=True, severity="major"),
                     *profiles.GREEN_ARABICA.classes])

    def policy_file(self, value):
        path = self.work / "policy.json"
        path.write_text(json.dumps(value))
        return path

    def test_the_default_policy_rejects_the_survivors_and_never_the_new_label(self):
        reject, version, source = train_candidate.resolve_policy(
            self.profile, "star_token", self.preset, None)
        severities = set(self.preset["policy"]["reject_severities"])
        engine_default = sorted(item.name for item in profiles.GREEN_ARABICA.classes
                                if item.defect and item.severity in severities)

        self.assertEqual(engine_default, reject)
        self.assertNotIn("star_token", reject)
        self.assertIsNone(version)
        self.assertEqual("derived_default", source)

    def test_a_baseline_policy_file_is_used_and_recorded(self):
        path = self.policy_file({"reject_classes": ["stone", "black"],
                                 "policy_version": "policy-7"})

        reject, version, source = train_candidate.resolve_policy(
            self.profile, "star_token", self.preset, path)

        self.assertEqual(["black", "stone"], reject)
        self.assertEqual("policy-7", version)
        self.assertEqual("baseline_file", source)

    def test_a_policy_that_rejects_the_new_label_is_refused(self):
        path = self.policy_file({"reject_classes": ["stone", "star_token"]})

        with self.assertRaisesRegex(ValueError, "always activates as Keep"):
            train_candidate.resolve_policy(self.profile, "star_token", self.preset, path)

    def test_a_policy_with_an_unknown_label_is_refused(self):
        path = self.policy_file({"reject_classes": ["stone", "gravel"]})

        with self.assertRaisesRegex(ValueError, "outside the candidate catalog"):
            train_candidate.resolve_policy(self.profile, "star_token", self.preset, path)

    def test_a_malformed_policy_file_is_refused(self):
        for value in ({"reject_classes": "stone"}, {"reject_classes": [1]}, {},
                      {"reject_classes": [], "policy_version": 7}):
            with self.subTest(policy=value):
                with self.assertRaises(ValueError):
                    train_candidate.resolve_policy(self.profile, "star_token", self.preset,
                                                   self.policy_file(value))

    def test_the_closed_loop_run_applies_the_policy_before_its_first_step(self):
        calls = []
        profile = SimpleNamespace(name="green_arabica")
        validation = {}
        run = {"seed": 8, "sim_seconds": 9.0,
               "outcomes": {"resolved": 30, "accepted": 0, "rejected": 30, "spilled": 0,
                            "accept_fraction": 0.0},
               "pulses": {"commanded": 30, "activated": 30, "jet_hits": 30}}

        def build_engine(preset_path):
            calls.append(("preset", Path(preset_path).name))
            engine = SimpleNamespace(sim=SimpleNamespace(dt=0.001, bean_of={}),
                                     close=lambda: None, steps=0)
            engine.set_reject_classes = lambda values: (
                calls.append(("policy", tuple(values), engine.steps))
                or {"policy_version": "engine-policy-9"})
            engine.step = lambda: calls.append(("step", engine.steps))
            return engine

        with patch.object(train_candidate, "build_engine", side_effect=build_engine), \
                patch.object(train_candidate, "keep_outcome", return_value=run):
            with train_candidate.bound_candidate_profile(profile):
                version = train_candidate.record_keep_outcome(
                    validation, {"seed": 8, "profile": "green_arabica"},
                    Path("candidate.preset.json"), "star_token", ["black", "stone"])

        self.assertEqual("engine-policy-9", version)
        # The engine loads the immutable candidate preset, not a temporary one.
        self.assertEqual(("preset", "candidate.preset.json"), calls[0])
        self.assertEqual(("policy", ("black", "stone"), 0), calls[1])
        self.assertNotIn("star_token", calls[1][1])
        self.assertEqual(0.0, validation["keep_outcome"]["runs"][0]["accept_fraction"])
        self.assertEqual(30, validation["pulses"]["runs"][0]["commanded"])

    def test_a_defect_truth_candidate_keeps_its_truth_in_the_catalog(self):
        definition = type_definition_from_draft(
            star_draft(sorting_proposal={"class_name": "star_token", "defect": True,
                                         "severity": "major", "proposed_action": "reject"}),
            rgb=[0.82, 0.68, 0.21], prior=0.03, source_sha256="c" * 64)
        candidate = candidate_catalog(self.catalog, definition, select_victim(self.catalog, []))
        write_catalog(self.work / "candidate", candidate)
        loaded = load_catalog(self.work / "candidate")

        self.assertEqual({"defect": True, "severity": "major"}, definition["truth"])
        self.assertEqual({"defect": True, "severity": "major"},
                         loaded["definitions"][0]["truth"])
        self.assertEqual("star_token", loaded["definitions"][0]["classifier_label"])


class CandidateProfileBindingTest(unittest.TestCase):
    """One binding covers the whole validation scope, and it is always restored."""

    def setUp(self):
        self.before = dict(profiles.PROFILES)

    def record(self, build_engine):
        run = {"seed": 8, "sim_seconds": 6.2,
               "outcomes": {"resolved": 30, "accepted": 30, "rejected": 0, "spilled": 0,
                            "accept_fraction": 1.0},
               "pulses": {"commanded": 0, "activated": 0, "jet_hits": 0}}
        validation, profile = {}, SimpleNamespace(name="green_arabica")
        with patch.object(train_candidate, "build_engine", side_effect=build_engine), \
                patch.object(train_candidate, "keep_outcome", return_value=run):
            with train_candidate.bound_candidate_profile(profile):
                train_candidate.record_keep_outcome(
                    validation, {"seed": 8, "profile": "green_arabica"},
                    Path("candidate.preset.json"), "star_token", ["stone"])
        return validation, profile

    def assert_profiles_restored(self):
        self.assertEqual(set(self.before), set(profiles.PROFILES))
        for name, value in self.before.items():
            with self.subTest(profile=name):
                self.assertIs(value, profiles.PROFILES[name])

    def test_the_candidate_profile_is_bound_for_the_run_and_then_restored(self):
        seen = {}

        def build_engine(preset_path):
            seen.update(preset=Path(preset_path).name, bound=dict(profiles.PROFILES))
            return SimpleNamespace(sim=SimpleNamespace(dt=0.001, bean_of={}), close=lambda: None,
                                   set_reject_classes=lambda values: {"policy_version": "p"})

        validation, profile = self.record(build_engine)

        self.assertEqual("candidate.preset.json", seen["preset"])
        self.assertIs(profile, seen["bound"]["green_arabica"])
        self.assertIsNot(self.before["green_arabica"], seen["bound"]["green_arabica"])
        self.assertEqual(30, validation["keep_outcome"]["runs"][0]["resolved"])
        self.assertEqual(0, validation["pulses"]["runs"][0]["commanded"])
        self.assert_profiles_restored()

    def test_a_failing_engine_still_restores_profiles(self):
        def build_engine(preset_path):
            raise RuntimeError("engine start failed")

        with self.assertRaisesRegex(RuntimeError, "engine start failed"):
            self.record(build_engine)

        self.assert_profiles_restored()

    def test_a_raising_body_still_restores_profiles(self):
        with self.assertRaisesRegex(RuntimeError, "boom"):
            with train_candidate.bound_candidate_profile(SimpleNamespace(name="green_arabica")):
                raise RuntimeError("boom")

        self.assert_profiles_restored()


class _PicklableClassifier:
    """A saved Model needs a picklable estimator. The loader never calls it."""


class CandidateLoaderProofTest(unittest.TestCase):
    """The REAL live.load_preset must accept the REAL candidate bundle while it is bound."""

    def setUp(self):
        from classifier import Model

        self.before = dict(profiles.PROFILES)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.out = Path(folder.name)
        self.preset = json.loads(PRESET.read_text())
        builtin = profiles.GREEN_ARABICA
        # The candidate order: the new type first, the last built-in type removed.
        survivors = [item for item in builtin.classes if item.name != builtin.classes[-1].name]
        new_class = dataclasses.replace(survivors[0], name="star_token", prior=0.03)
        self.profile = profiles.Profile(builtin.name, builtin.belt_rgb, [new_class, *survivors])
        self.labels = self.profile.names
        self.preset_path = self.write_bundle(Model)

    def write_bundle(self, Model):
        """A genuine artifact set: a saved Model, its manifest, and the bundled preset."""
        from vision import FEATURES

        layout = self.preset["layout"]
        capture_every = self.preset["camera_every_steps"]
        provenance = {"config": {
            "profile": self.preset["profile"],
            "rate": self.preset["requested_rate"],
            "capture_every": capture_every,
            "physical_preset": {
                "layout": layout,
                "requested_rate": self.preset["requested_rate"],
                "camera_every_steps": capture_every,
                "capture_hz": 1.0 / (float(layout["timestep"]) * capture_every),
            },
        }}
        meta = {"profile": self.preset["profile"], "classes": self.labels,
                "features": FEATURES, "provenance": provenance}
        model = Model(list(self.labels), _PicklableClassifier(), np.zeros(4), np.eye(4), 1.0,
                      np.ones(4), meta)
        artifact = self.out / "candidate.joblib"
        model.save(artifact)
        (self.out / "candidate.manifest.json").write_text(json.dumps({
            "version": 1, "provenance": provenance, "features": FEATURES,
            "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}))
        preset_path = self.out / "candidate.preset.json"
        preset_path.write_text(json.dumps({**self.preset, "model_path": "candidate.joblib",
                                           "model_path_root": "preset"}))
        return preset_path

    def test_the_real_loader_accepts_the_bundle_inside_the_binding(self):
        with train_candidate.bound_candidate_profile(self.profile):
            result = train_candidate.preset_compatibility(self.preset_path)

        self.assertTrue(result["loaded"], result["reason"])
        self.assertIsNone(result["reason"])
        self.assertEqual("live.load_preset", result["loader"])

    def test_the_same_bundle_is_refused_against_the_original_catalog(self):
        """Proof that the binding makes it pass, not a weakened loader check."""
        result = train_candidate.preset_compatibility(self.preset_path)

        self.assertFalse(result["loaded"])
        self.assertIn("classes", result["reason"])

    def test_the_binding_restores_profiles_exactly(self):
        with train_candidate.bound_candidate_profile(self.profile):
            self.assertIs(self.profile, profiles.PROFILES["green_arabica"])

        self.assertEqual(set(self.before), set(profiles.PROFILES))
        for name, value in self.before.items():
            with self.subTest(profile=name):
                self.assertIs(value, profiles.PROFILES[name])

    def test_the_valid_bundle_passes_the_gate_end_to_end(self):
        """The positive path: the real loader accepts it and the gate raises no failure."""
        with train_candidate.bound_candidate_profile(self.profile):
            proof = train_candidate.preset_compatibility(self.preset_path)
        validation = CandidateGateTest().validation()
        validation["preset_compatibility"] = proof

        self.assertTrue(proof["loaded"], proof["reason"])
        self.assertEqual([], gate_failures(validation))

    def test_a_missing_or_unproven_compatibility_block_fails_the_gate(self):
        """Absence of evidence is never evidence: the proof must be present and true."""
        cases = {
            "missing block": None,
            "missing loaded key": {"reason": None, "loader": "live.load_preset"},
            "loaded is not true": {"loaded": "yes", "reason": None},
            "loaded is false": {"loaded": False, "reason": "ValueError: classes"},
        }
        for name, block in cases.items():
            with self.subTest(case=name):
                validation = CandidateGateTest().validation()
                # Replace the block outright: a merge would keep the default proof.
                validation["preset_compatibility"] = block
                self.assertIn("preset_incompatible", gate_failures(validation))
        validation = CandidateGateTest().validation()
        del validation["preset_compatibility"]
        self.assertIn("preset_incompatible", gate_failures(validation))

    def test_the_loader_proof_runs_inside_the_binding(self):
        """Order guard: this fails if preset_compatibility moves out of the scope."""
        seen = {}

        with patch("live.load_preset",
                   side_effect=lambda path: seen.update(
                       bound=profiles.PROFILES["green_arabica"].names)):
            with train_candidate.bound_candidate_profile(self.profile):
                train_candidate.preset_compatibility(self.preset_path)

        self.assertEqual(self.labels, seen["bound"])
        self.assertNotEqual(profiles.GREEN_ARABICA.names, seen["bound"])


class FakeModel:
    """Model-like double for the trainer write path. It fits nothing."""

    def __init__(self, classes):
        self.classes = list(classes)
        self.anomaly_thresh = 14.339

    def save(self, path):
        Path(path).write_bytes(b"fake candidate model")

    def references(self):
        return {name: {"thresh": 1.0} for name in self.classes}

    def set_anomaly_reference(self, labels):
        selected = set(labels)
        return [name for name in self.classes if name in selected]

    def predict(self, X):
        probabilities = np.zeros((len(X), len(self.classes)))
        probabilities[:, 0] = 1.0
        return probabilities, np.zeros(len(X))


class BundledArtifactTest(unittest.TestCase):
    """No bundled file carries an absolute path, a host name, or a timestamp."""

    DATE_PATTERNS = {
        "an ISO date": r"\d{4}-\d{2}-\d{2}",
        "a clock time": r"\d{2}:\d{2}:\d{2}",
        "a build date": r"[A-Z][a-z]{2} +\d{1,2} \d{4}",
    }

    def train_with_fakes(self, work):
        """Run the trainer write path with a fake collection, fit, and closed-loop run."""
        catalog = load_catalog()
        new_definition = type_definition_from_draft(
            star_draft(), rgb=[0.82, 0.68, 0.21], prior=0.03, source_sha256="c" * 64)
        root = work / "candidate"
        write_catalog(root, candidate_catalog(catalog, new_definition, select_victim(catalog, [])))
        labels = [value["classifier_label"] for value in load_catalog(root)["definitions"]]
        captured = {}

        def partition(seed, *rest):
            rows = [(seed, index, label) for index, label in enumerate(labels)]
            history = [{"seed": seed, "seconds": 4.0, "observations": {}, "unique_objects": {},
                        "short_labels": []}]
            return (np.zeros((len(labels), 4)), np.asarray(labels, dtype=object), rows, history)

        def fit(profile, X_train, y_train, X_holdout, y_holdout, meta):
            captured["meta"] = meta
            return FakeModel(profile.names), {"fit_seconds": 0.0, "confusion": []}

        with patch.object(train_candidate, "collect_covered", side_effect=partition), \
                patch.object(train_candidate, "fit_model", side_effect=fit), \
                patch.object(train_candidate, "record_keep_outcome", return_value="engine-policy"), \
                contextlib.redirect_stdout(io.StringIO()):
            code = train_candidate.main([
                "--catalog-root", str(root), "--preset", str(PRESET), "--out", str(work / "out"),
                "--runtime-lock", str(work / "runtime.lock")])
        return code, work / "out", captured

    def test_the_manifest_and_preset_carry_no_path_host_or_timestamp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        work = Path(folder.name)
        code, out, captured = self.train_with_fakes(work)
        host = platform.node()

        self.assertEqual(0, code)
        for name in ("candidate.manifest.json", "candidate.preset.json"):
            data = (out / name).read_text()
            with self.subTest(artifact=name):
                self.assertFalse(str(work) in data, f"{name} carries the output path")
                self.assertFalse(str(HERE) in data, f"{name} carries the source path")
                self.assertFalse(host and host in data, f"{name} carries the host name")
                self.assertFalse("/" in data.replace("candidate.joblib", ""),
                                 f"{name} carries a path separator")
                for label, pattern in self.DATE_PATTERNS.items():
                    self.assertIsNone(re.search(pattern, data), f"{name} carries {label}")

    def test_the_manifest_records_a_plain_interpreter_version(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        work = Path(folder.name)
        _, out, captured = self.train_with_fakes(work)
        manifest = json.loads((out / "candidate.manifest.json").read_text())

        self.assertEqual(platform.python_version(), manifest["provenance"]["runtime"]["python"])
        # live.load_preset compares the model meta provenance with the manifest provenance.
        self.assertEqual(manifest["provenance"], captured["meta"]["provenance"])


class TrainerInputTest(unittest.TestCase):
    """No preview and no beauty render ever reaches the trainer or its recorded sources."""

    def test_the_trainer_accepts_only_catalog_preset_output_and_lock_inputs(self):
        dests = {action.dest for action in train_candidate.build_parser()._actions}

        self.assertEqual({"help", "catalog_root", "preset", "out", "seconds", "runtime_lock",
                          "policy"}, dests)

    def test_recorded_source_files_contain_no_preview_or_render_path(self):
        for name in bootstrap_model.SOURCE_FILES:
            with self.subTest(source=name):
                self.assertNotIn("preview", name)
                self.assertNotIn("render", name)

    def test_collect_partition_keeps_its_default_path(self):
        signature = inspect.signature(bootstrap_model.collect_partition)
        parameters = list(signature.parameters)
        profile = signature.parameters["profile"]

        self.assertEqual(["seed", "seconds", "rate", "defect_boost", "layout", "capture_every",
                          "profile"], parameters)
        self.assertIsNone(profile.default)
        self.assertIsNone(signature.parameters["layout"].default)
        self.assertEqual(bootstrap_model.CAPTURE_EVERY, signature.parameters["capture_every"].default)


if __name__ == "__main__":
    unittest.main()
