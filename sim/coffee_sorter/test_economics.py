import unittest

from economics import calculate


ASSUMPTIONS = {
    "object_mass_g": 0.2,
    "duty_cycle": 0.8,
    "base_feed_price_eur_per_kg": 6.0,
    "accepted_stream_premium_eur_per_kg": 0.5,
    "rejected_salvage_eur_per_kg": 0.0,
    "spilled_or_unresolved_sale_eur_per_kg": 0.0,
    "operating_cost_eur_per_h": 0.0,
    "capital_cost_eur_per_h": 0.0,
    "labor_cost_eur_per_h": 0.0,
}


def metrics(policy=("minor", "major", "foreign"), rate=1000.0):
    return {
        "throughput_beans_per_s": rate,
        "rate": 99999.0,
        "policy": "specialty",
        "config": {"policy": {"name": "specialty", "reject_severities": list(policy)}},
        "per_class": {
            "good": {"n": 70, "rejected": 5, "spilled": 3, "unresolved": 2,
                     "defect": False, "severity": "none"},
            "minor": {"n": 10, "rejected": 4, "spilled": 1, "unresolved": 0,
                      "defect": True, "severity": "minor"},
            "major": {"n": 20, "rejected": 10, "spilled": 0, "unresolved": 0,
                      "defect": True, "severity": "major"},
        },
        "denominators": {
            "eligible_beans": 100, "resolved_beans": 98, "defects_to_remove": 30,
            "keep_beans": 70, "accepted_beans": 75, "rejected_beans": 19,
        },
    }


class EconomicsTest(unittest.TestCase):
    def test_arithmetic_and_mass_conservation(self):
        row = calculate(metrics(), ASSUMPTIONS)
        self.assertAlmostEqual(row["mass_kg_h"]["input"], 576.0)
        self.assertAlmostEqual(sum(row["mass_kg_h"][key] for key in ("accepted", "rejected", "spilled", "unresolved")), 576.0)
        self.assertEqual(row["counts"]["accepted"], 75)
        self.assertAlmostEqual(row["mass_kg_h"]["defect_rejected"], 80.64)

    def test_uses_effective_not_requested_rate(self):
        row = calculate(metrics(rate=250.0), ASSUMPTIONS)
        self.assertEqual(row["effective_rate_beans_per_s"], 250.0)
        self.assertAlmostEqual(row["mass_kg_h"]["input"], 144.0)

    def test_false_ejections_and_spills_are_separate(self):
        row = calculate(metrics(), ASSUMPTIONS)
        self.assertEqual(row["counts"]["good_rejected"], 5)
        self.assertEqual(row["counts"]["spilled"], 4)
        self.assertEqual(row["counts"]["good_spilled"], 3)
        self.assertEqual(row["counts"]["good_unresolved"], 2)
        self.assertEqual(row["counts"]["defect_spilled"], 1)
        self.assertEqual(row["counts"]["defect_unresolved"], 0)
        self.assertAlmostEqual(row["cost_eur_h"]["good_false_ejections"], 172.8)
        self.assertAlmostEqual(row["cost_eur_h"]["spills"], 138.24)
        self.assertAlmostEqual(row["mass_kg_h"]["defect_spilled"], 5.76)
        self.assertAlmostEqual(row["cost_eur_h"]["good_spills"], 103.68)

    def test_empty_cohort_and_derived_overflow_fail_cleanly(self):
        empty = metrics()
        for record in empty["per_class"].values():
            for key in ("n", "rejected", "spilled", "unresolved"):
                record[key] = 0
        empty["denominators"] = dict.fromkeys(empty["denominators"], 0)
        with self.assertRaisesRegex(ValueError, "no eligible input"):
            calculate(empty, ASSUMPTIONS)
        with self.assertRaisesRegex(ValueError, "finite"):
            calculate(metrics(rate=1e308), ASSUMPTIONS)
        with self.assertRaisesRegex(ValueError, "finite"):
            calculate(metrics(), {**ASSUMPTIONS, "base_feed_price_eur_per_kg": 1e308})

    def test_grade_assumption_zero_premium_and_break_even(self):
        row = calculate(metrics(), ASSUMPTIONS)
        self.assertAlmostEqual(row["defect_count_percent"]["incoming"], 30.0)
        self.assertAlmostEqual(row["defect_count_percent"]["residual_accepted"], 20.0)
        self.assertAlmostEqual(row["revenue_eur_h"]["at_zero_premium"], -864.0)
        self.assertAlmostEqual(row["break_even_accepted_stream_premium_eur_per_kg"], 2.0)

    def test_commercial_policy_counts_minor_as_good_for_loss(self):
        document = metrics(policy=("major", "foreign"))
        document["policy"] = "commercial"
        document["config"]["policy"]["name"] = "commercial"
        document["denominators"].update(defects_to_remove=20, keep_beans=80)
        row = calculate(document, ASSUMPTIONS)
        self.assertEqual(row["counts"]["defect_input"], 20)
        self.assertEqual(row["counts"]["good_rejected"], 9)

    def test_missing_or_inconsistent_counts_fail(self):
        missing = metrics()
        del missing["per_class"]["good"]["spilled"]
        with self.assertRaisesRegex(ValueError, "spilled"):
            calculate(missing, ASSUMPTIONS)
        inconsistent = metrics()
        inconsistent["per_class"]["good"]["rejected"] = 99
        with self.assertRaisesRegex(ValueError, "exceeds n"):
            calculate(inconsistent, ASSUMPTIONS)
        negative = metrics()
        negative["per_class"]["good"]["spilled"] = -1
        with self.assertRaisesRegex(ValueError, "must not be negative"):
            calculate(negative, ASSUMPTIONS)
        bad_denominator = metrics()
        bad_denominator["denominators"]["accepted_beans"] = 0
        with self.assertRaisesRegex(ValueError, "accepted_beans"):
            calculate(bad_denominator, ASSUMPTIONS)


if __name__ == "__main__":
    unittest.main()
