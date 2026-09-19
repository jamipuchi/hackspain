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


def calculate(beans, reject_severities):
    by_name = {c.name: c for c in CLASSES}
    sim = SimpleNamespace(
        P=SimpleNamespace(classes=CLASSES, by_name=by_name.__getitem__),
        beans=beans,
        n_fired=0,
        starved=0,
        L=SimpleNamespace(ej_x=0.1, cam_x=-0.12, belt_speed=3.0),
    )
    ctrl = SimpleNamespace(
        pol=SimpleNamespace(reject_severities=reject_severities),
        latency_ms=[],
        decisions=[],
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

    def test_empty_eligible_window_returns_zero_rates(self):
        result = calculate([bean(1, "black", "accept", spawn_t=1.6)], ("major",))

        self.assertEqual(result["beans_evaluated"], 0)
        self.assertEqual(result["defect_removal"], 0.0)
        self.assertEqual(result["good_yield_loss"], 0.0)
        self.assertEqual(result["accept_purity_defects_per_1000"], 0.0)
        self.assertEqual(result["spilled_rate"], 0.0)

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


if __name__ == "__main__":
    unittest.main()
