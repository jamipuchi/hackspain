import unittest
from types import SimpleNamespace

import numpy as np

from run import metrics
from sim import Bean


CLASSES = (
    SimpleNamespace(name="good", defect=False, severity="none"),
    SimpleNamespace(name="faded", defect=True, severity="minor"),
    SimpleNamespace(name="black", defect=True, severity="major"),
)


def bean(uid, cls, outcome, spawn_t=0.5):
    spec = next(c for c in CLASSES if c.name == cls)
    return Bean(uid, uid, cls, spec.defect, spawn_t, np.ones(3), 0.0002, outcome=outcome)


def calculate(beans, reject_severities, decisions=(), fired_targets=(), fire_hits=()):
    by_name = {c.name: c for c in CLASSES}
    sim = SimpleNamespace(
        P=SimpleNamespace(classes=CLASSES, by_name=by_name.__getitem__),
        beans=beans,
        bean_by_uid={b.uid: b for b in beans},
        n_fired=0,
        n_activated=0,
        fired_targets=set(fired_targets),
        fire_hits=set(fire_hits),
        starved=0,
        L=SimpleNamespace(ej_x=0.1, cam_x=-0.12, belt_speed=3.0),
    )
    ctrl = SimpleNamespace(
        pol=SimpleNamespace(reject_severities=reject_severities),
        latency_ms=[],
        decisions=list(decisions),
        frames=0,
    )
    return metrics(sim, ctrl, warmup=0.0, t_end=2.0)


class MetricsTest(unittest.TestCase):
    def test_accept_purity_handles_mutable_bean_records(self):
        result = calculate(
            [bean(1, "good", "accept"), bean(2, "black", "accept")],
            ("minor", "major", "foreign"),
        )

        self.assertEqual(result["beans_evaluated"], 2)
        self.assertEqual(result["accept_purity_defects_per_1000"], 500.0)
        self.assertEqual(result["accept_purity_defects_per_1000_incoming"], 500.0)

    def test_policy_changes_which_severities_count_as_defects(self):
        beans = [
            bean(1, "good", "accept"),
            bean(2, "faded", "reject"),
            bean(3, "black", "accept"),
        ]

        specialty = calculate(beans, ("minor", "major", "foreign"))
        commercial = calculate(beans, ("major", "foreign"))

        self.assertEqual(specialty["defect_removal"], 0.5)
        self.assertEqual(specialty["good_yield_loss"], 0.0)
        self.assertEqual(commercial["defect_removal"], 0.0)
        self.assertEqual(commercial["good_yield_loss"], 0.5)

    def test_physical_binary_metrics_have_explicit_denominators_and_count_spills_as_errors(self):
        result = calculate([
            bean(1, "black", "reject"),
            bean(2, "black", "accept"),
            bean(3, "good", "accept"),
            bean(4, "good", "reject"),
            bean(5, "good", "spilled"),
        ], ("major",))

        self.assertEqual(result["physical_reject_recall"], 0.5)
        self.assertEqual(result["defect_removal"], 0.5)
        self.assertEqual(result["physical_reject_precision"], 0.5)
        self.assertEqual(result["good_false_eject_rate"], 1 / 3)
        self.assertEqual(result["good_yield_loss"], 1 / 3)
        self.assertEqual(result["physical_reject_accuracy"], 2 / 5)
        self.assertEqual(result["denominators"]["eligible_beans"], 5)
        self.assertEqual(result["denominators"]["rejected_beans"], 2)

    def test_camera_bean_cohorts_are_mutually_exclusive(self):
        single = bean(1, "good", "accept"); single.camera_observations = 2
        merged = bean(2, "black", "reject"); merged.camera_observations = 1; merged.merged_observations = 1
        unseen = bean(3, "good", "spilled")

        cohorts = calculate([single, merged, unseen], ("major",))["camera_bean_cohorts"]

        self.assertEqual(cohorts["single_only"]["n"], 1)
        self.assertEqual(cohorts["ever_merged"]["n"], 1)
        self.assertEqual(cohorts["never_observed"]["n"], 1)
        self.assertEqual(sum(c["n"] for c in cohorts.values()), 3)

    def test_empty_eligible_window_returns_zero_rates(self):
        result = calculate([bean(1, "black", "accept", spawn_t=1.6)], ("major",))

        self.assertEqual(result["beans_evaluated"], 0)
        self.assertEqual(result["defect_removal"], 0.0)
        self.assertEqual(result["good_yield_loss"], 0.0)
        self.assertEqual(result["accept_purity_defects_per_1000"], 0.0)
        self.assertEqual(result["spilled_rate"], 0.0)

    def test_eligible_unresolved_beans_are_counted_in_denominator(self):
        result = calculate([bean(1, "black", None)], ("major",))

        self.assertEqual(result["beans_evaluated"], 1)
        self.assertEqual(result["beans_resolved"], 0)
        self.assertEqual(result["beans_unresolved"], 1)
        self.assertEqual(result["denominators"]["defects_to_remove"], 1)

    def test_physical_actuation_counts_target_and_jet_intersections(self):
        rejected = bean(1, "black", "reject")
        rejected.targeted = True
        rejected.jet_hits = 2
        accepted = bean(2, "black", "accept")
        accepted.targeted = True
        accepted.jet_hits = 1

        result = calculate([rejected, accepted], ("major",))["per_class"]["black"]

        self.assertEqual(result["jet_hit"], 2)
        self.assertEqual(result["targeted_rejected"], 1)
        self.assertEqual(result["jet_hit_rejected"], 1)

    def test_funnel_requires_activation_and_hit_from_the_decisions_own_pulse(self):
        target = bean(1, "black", "reject")
        target.jet_hits = 1
        decision = SimpleNamespace(tid=10, reject=True, scheduled=True, target_uids=(1,),
                                   t_fire=1.10, t_available=1.05, late=False, n_obs=2)

        other_pulse = calculate([target], ("major",), [decision], {10}, {(99, 1)})
        own_pulse = calculate([target], ("major",), [decision], {10}, {(10, 1)})

        self.assertEqual(other_pulse["activated_reject_decisions"], 1)
        self.assertEqual(other_pulse["associated_jet_hit"], 0)
        self.assertEqual(other_pulse["associated_rejected"], 0)
        self.assertAlmostEqual(other_pulse["deadline_headroom_ms"]["p50"], 50.0)
        self.assertEqual(own_pulse["associated_jet_hit"], 1)
        self.assertEqual(own_pulse["associated_rejected"], 1)


if __name__ == "__main__":
    unittest.main()
