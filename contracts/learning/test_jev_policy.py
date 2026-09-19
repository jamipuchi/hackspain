"""Tests for the bounded Jev decision policy."""

from __future__ import annotations

import copy
import math
import unittest

from jev_policy import build_jev_request, parse_jev_response, predict_jev


OBSERVATION = {
    "outline": "round",
    "central_opening": "visible",
    "description": "bright ring with a central opening",
    "visibility": "clear",
    "object_count": 1,
}


def _response(probabilities: dict[str, float] | None = None, choice: str = "bin_A") -> dict[str, object]:
    return {
        "model": "jev-1.13.0",
        "answers": {
            "destination": {
                "type": "choice",
                "choice": choice,
                "probabilities": probabilities or {"bin_A": 0.9, "bin_B": 0.05, "bin_C": 0.03, "defer": 0.02},
                "confidence": 0.01,
            }
        }
    }


class JevPolicyTests(unittest.TestCase):
    def test_switched_demo_labels_appear_in_payload(self) -> None:
        first = {"observation": OBSERVATION, "destination": "bin_A"}
        second = {"observation": OBSERVATION, "destination": "bin_B"}

        first_request = build_jev_request([first], OBSERVATION)
        second_request = build_jev_request([second], OBSERVATION)

        self.assertEqual(first_request["state"]["demonstrations"][0]["destination"], "bin_A")
        self.assertEqual(second_request["state"]["demonstrations"][0]["destination"], "bin_B")
        self.assertEqual(first_request["model"], "jev-1.13.0")

    def test_payload_excludes_demonstration_metadata(self) -> None:
        demonstration = {
            "observation": OBSERVATION,
            "destination": "bin_C",
            "sample_id": "test-washer-01",
            "fixture": {"body_name": "fixture"},
            "evaluator": {"expected_kind": "washer"},
        }

        request = build_jev_request([demonstration], OBSERVATION)

        self.assertEqual(
            request["state"]["demonstrations"][0],
            {"observation": OBSERVATION, "destination": "bin_C"},
        )
        self.assertEqual(set(request["state"]["observation"]), set(OBSERVATION))

    def test_malformed_or_disallowed_distributions_are_rejected(self) -> None:
        cases = [
            _response({"bin_A": 0.9, "bin_B": 0.1, "bin_C": 0.0}),
            _response({"bin_A": 0.9, "bin_B": 0.05, "bin_C": 0.03, "defer": 0.02, "other": 0.0}),
            _response({"bin_A": 0.9, "bin_B": 0.05, "bin_C": 0.03, "defer": math.nan}),
            _response({"bin_A": 0.6, "bin_B": 0.3, "bin_C": 0.2, "defer": 0.1}),
            _response(choice="bin_B"),
        ]

        for response in cases:
            with self.subTest(response=response):
                with self.assertRaises(ValueError):
                    parse_jev_response(response)

    def test_missing_or_different_response_model_is_rejected(self) -> None:
        missing = _response()
        del missing["model"]
        different = _response()
        different["model"] = "jev-other"

        for response in (missing, different):
            with self.subTest(response=response):
                with self.assertRaises(ValueError):
                    parse_jev_response(response)

    def test_low_probability_or_margin_defers(self) -> None:
        low = _response({"bin_A": 0.55, "bin_B": 0.4, "bin_C": 0.03, "defer": 0.02})

        prediction = predict_jev(low, OBSERVATION)

        self.assertIsNone(prediction.destination)
        self.assertEqual(prediction.reason, "top_probability_below_threshold")

    def test_valid_response_is_accepted_by_probability_not_confidence(self) -> None:
        prediction = predict_jev(_response(), OBSERVATION)

        self.assertEqual(prediction.destination, "bin_A")
        self.assertIsNone(prediction.reason)
        self.assertEqual(prediction.confidence, 0.01)

    def test_unclear_observation_defers_locally(self) -> None:
        unclear = copy.deepcopy(OBSERVATION)
        unclear["visibility"] = "unclear"

        prediction = predict_jev(_response(), unclear)

        self.assertIsNone(prediction.destination)
        self.assertEqual(prediction.reason, "unclear_observation")

    def test_unknown_individual_property_still_reaches_jev(self) -> None:
        uncertain = copy.deepcopy(OBSERVATION)
        uncertain["central_opening"] = "unclear"

        prediction = predict_jev(_response(), uncertain)

        self.assertEqual(prediction.destination, "bin_A")

    def test_invalid_observation_values_are_rejected(self) -> None:
        for key, value in (("outline", []), ("description", ""), ("description", "x" * 1001), ("object_count", 101)):
            with self.subTest(key=key, value=value):
                observation = copy.deepcopy(OBSERVATION)
                observation[key] = value
                with self.assertRaises(ValueError):
                    build_jev_request([], observation)


if __name__ == "__main__":
    unittest.main()
