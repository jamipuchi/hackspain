import unittest
from collections import deque
from types import SimpleNamespace

import numpy as np

from engine import Engine, MAX_COMPLETED_INJECTIONS
from rolling_scores import RollingScoreLedger


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
        self.assertEqual(self.scores(first, 0.6)["eligible_objects"], 1)

        ledger = RollingScoreLedger(60.0)
        ledger.add(1, 0.0, False)
        ledger.add(2, 0.1, True)
        ledger.add(3, 60.1, False)
        ledger.add(4, 60.2, False)
        ledger.resolve(1, "accept")
        ledger.resolve(2, "reject")
        ledger.resolve(3, "accept")

        scores = self.scores(ledger, 60.7)

        self.assertAlmostEqual(scores["window_start_exclusive_s"], 0.1)
        self.assertAlmostEqual(scores["window_end_inclusive_s"], 60.1)
        self.assertEqual(scores["eligible_objects"], 1)
        self.assertEqual(scores["settling_objects"], 1)
        self.assertEqual(scores["sorting_accuracy"], {
            "numerator": 1, "denominator": 1, "value": 1.0,
        })
        self.assertEqual(len(ledger), 2)

    def test_counts_spills_unresolved_and_empty_denominators(self):
        ledger = RollingScoreLedger(60.0)
        ledger.add(1, 1.0, False)
        ledger.add(2, 1.1, False)
        ledger.add(3, 1.2, True)
        ledger.add(4, 1.3, True)
        ledger.resolve(1, "spilled")
        ledger.resolve(3, "reject")
        ledger.resolve(4, "accept")

        scores = self.scores(ledger, 2.0)

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
            "manual_injections_excluded", "settling_objects", "eligible_objects",
            "sorting_accuracy", "defect_capture", "good_loss", "unresolved", "versions",
        })
        self.assertEqual(empty["available_seconds"], 0.0)
        self.assertTrue(empty["warming_up"])
        for name in ("sorting_accuracy", "defect_capture", "good_loss", "unresolved"):
            self.assertIsNone(empty[name]["value"])

    def test_delayed_outcome_updates_retained_row_and_expired_row_is_discarded(self):
        ledger = RollingScoreLedger(2.0)
        ledger.add(1, 0.0, True)

        self.assertEqual(self.scores(ledger, 1.0)["unresolved"]["numerator"], 1)
        ledger.resolve(1, "reject")
        self.assertEqual(self.scores(ledger, 1.1)["defect_capture"]["numerator"], 1)

        expired = self.scores(ledger, 2.6)
        self.assertEqual(expired["eligible_objects"], 0)
        self.assertEqual(len(ledger), 0)
        ledger.resolve(1, "accept")
        self.assertEqual(len(ledger), 0)


class ContinuousRetentionTest(unittest.TestCase):
    def test_engine_exposes_session_scoped_scores_only_in_continuous_mode(self):
        engine = Engine.__new__(Engine)
        engine.continuous = True
        engine.session_id = "session"
        engine.model_version = "model"
        engine.policy_version = "policy"
        engine.source_revision = "source"
        engine.sim = SimpleNamespace(data=SimpleNamespace(time=0.6))
        engine._score_ledger = RollingScoreLedger(60.0)
        engine._score_ledger.add(1, 0.0, False)

        scores = engine.rolling_scores()

        self.assertEqual(scores["score_epoch_id"], "session")
        self.assertEqual(scores["eligible_objects"], 1)
        engine.continuous = False
        with self.assertRaisesRegex(RuntimeError, "continuous mode"):
            engine.rolling_scores()

    def test_manual_injections_do_not_enter_feed_ledger(self):
        engine = Engine.__new__(Engine)
        engine.continuous = True
        engine.session_id = "session"
        engine.profile = SimpleNamespace(
            by_name=lambda name: SimpleNamespace(severity="major", shape="ellipsoid")
        )
        engine.policy = SimpleNamespace(reject_severities=("major",))
        engine.sim = SimpleNamespace(
            body_geom={1: 0},
            model=SimpleNamespace(geom_rgba=np.ones((1, 4))),
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
        engine._seen_fire_hits = {(10, 1), (12, 4)}
        engine._seen_outcomes = {2, 3, 4}
        engine.sim = SimpleNamespace(
            data=SimpleNamespace(time=61.0),
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
        self.assertEqual(engine._seen_fire_hits, {(10, 1)})
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


if __name__ == "__main__":
    unittest.main()
