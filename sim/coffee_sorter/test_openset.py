import json
import unittest
from types import SimpleNamespace

import numpy as np

from openset import (UNKNOWN_NAMES, _demo_unknown_record, _validate_sweep_records,
                     openset_profile, threshold_result)
from profiles import GREEN_ARABICA


class OpenSetExperimentTest(unittest.TestCase):
    def test_profile_keeps_trained_classes_and_adds_local_unknowns(self):
        profile = openset_profile()

        self.assertTrue(set(GREEN_ARABICA.names).issubset(profile.names))
        self.assertTrue(set(UNKNOWN_NAMES).isdisjoint(GREEN_ARABICA.names))
        self.assertEqual(sum(c.prior for c in profile.classes), 1.0)
        for name in UNKNOWN_NAMES:
            spec = profile.by_name(name)
            self.assertTrue(spec.defect)
            self.assertEqual(spec.severity, "foreign")

    def test_threshold_result_separates_detector_classifier_and_combined(self):
        records = [
            {"class": "good", "anomaly_max": 2.0, "p_reject_mean": 0.1},
            {"class": "good", "anomaly_max": 9.0, "p_reject_mean": 0.1},
            {"class": "plastic_chip", "anomaly_max": 10.0, "p_reject_mean": 0.1},
            {"class": "odd_colour", "anomaly_max": 2.0, "p_reject_mean": 0.8},
            {"class": "wrong_size", "anomaly_max": 2.0, "p_reject_mean": 0.1},
        ]

        result = threshold_result(records, threshold=8.0)

        self.assertEqual(result["denominators"], {"good_objects": 2, "unknown_objects": 3})
        self.assertEqual(result["anomaly_detector_alone"]["unknown_rejected"], 1)
        self.assertEqual(result["anomaly_detector_alone"]["good_false_ejects"], 1)
        self.assertEqual(result["classifier_alone"]["unknown_rejected"], 1)
        self.assertEqual(result["combined_classifier_or_anomaly"]["unknown_rejected"], 2)

    def test_wrong_size_is_larger_than_every_trained_good_axis(self):
        trained = GREEN_ARABICA.by_name("good")
        unknown = openset_profile().by_name("wrong_size")

        for trained_axis, unknown_axis in zip(trained.size_mm, unknown.size_mm):
            self.assertGreater(unknown_axis[0], trained_axis[1])

    def test_demo_evidence_converts_numpy_boole_to_json_native_values(self):
        bean = SimpleNamespace(uid=np.int64(7), cls="odd_colour", jet_hits=np.int64(2),
                               outcome="reject")
        decision = SimpleNamespace(
            anomaly=np.float64(24.0), probs=np.array([0.2, 0.8]),
            reject=np.bool_(True), scheduled=np.bool_(True), tid=np.int64(3),
        )

        record = _demo_unknown_record(
            bean, decision, np.array([False, True]), np.float64(18.5), {np.int64(3)},
            {(np.int64(3), np.int64(7))})

        json.dumps(record)
        self.assertIs(type(record["combined_command"]), bool)
        self.assertIs(type(record["scheduled"]), bool)
        self.assertIs(type(record["activated"]), bool)
        self.assertIs(type(record["own_pulse_hit"]), bool)
        self.assertTrue(record["own_pulse_hit"])

    def test_sweep_rejects_a_cohort_without_unknown_objects(self):
        with self.assertRaisesRegex(RuntimeError, "no eligible selected unknown"):
            _validate_sweep_records([{"class": "good"}])


if __name__ == "__main__":
    unittest.main()
