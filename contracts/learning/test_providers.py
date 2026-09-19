"""Tests for provider parsing, safe request shape, and response caching."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import providers
from jev_policy import build_jev_request
from providers import credentials, parse_observation
from run_jev_experiment import _metrics, _swap_control, _teacher_demonstrations


class ProviderTests(unittest.TestCase):
    def test_malformed_vision_output_is_rejected(self) -> None:
        response = {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}

        with self.assertRaises(ValueError):
            parse_observation(response)

    def test_teacher_request_excludes_evaluator_and_fixture_metadata(self) -> None:
        records = [
            {
                "sample_id": "train-1",
                "split": "train",
                "fixture": {"body_name": "secret-fixture", "x_cm": 8.0},
                "evaluator": {"expected_kind": "screw"},
            }
        ]
        observations = [
            {
                "sample_id": "train-1",
                "observation": {
                    "outline": "elongated",
                    "central_opening": "absent",
                    "description": "long bright outline",
                    "visibility": "clear",
                    "object_count": 1,
                },
            }
        ]

        teacher = _teacher_demonstrations(records, observations)["baseline_3_demo"]
        request = build_jev_request(teacher, observations[0]["observation"])
        encoded = json.dumps(request)

        self.assertEqual(teacher[0]["destination"], "bin_B")
        self.assertNotIn("secret-fixture", encoded)
        self.assertNotIn("expected_kind", encoded)
        self.assertNotIn("fixture", encoded)

    def test_credential_reader_returns_only_provider_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("OTHER_SECRET=blocked\nOPENROUTER_API_KEY='router'\nTYPESAFE_API_KEY=jev\n")

            values = credentials(env_file)

        self.assertEqual(values, {"OPENROUTER_API_KEY": "router", "TYPESAFE_API_KEY": "jev"})

    def test_cached_call_reuses_mocked_response(self) -> None:
        payload = {"model": "test", "messages": []}
        result = {"response": {"model": "test", "choices": []}, "latency_s": 0.1}
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(providers, "post_json", return_value=result) as post:
                first, first_cached = providers.cached_call(
                    providers.OPENROUTER_URL, "key", payload, Path(directory)
                )
                second, second_cached = providers.cached_call(
                    providers.OPENROUTER_URL, "key", payload, Path(directory)
                )

        self.assertFalse(first_cached)
        self.assertTrue(second_cached)
        self.assertEqual(first, second)
        post.assert_called_once()

    def test_swapped_policy_scores_swapped_destinations_and_reports_control(self) -> None:
        records = {"test-1": {"evaluator": {"expected_kind": "screw"}}}
        original = [{"sample_id": "test-1", "destination": "bin_B"}]
        swapped = [{"sample_id": "test-1", "destination": "bin_C"}]

        metrics = _metrics(swapped, records, swapped_teacher_destinations=True)
        control = _swap_control(original, swapped)

        self.assertEqual(metrics["correct"], 1)
        self.assertEqual(metrics["scoring_destinations"], "swapped_teacher")
        self.assertEqual(control["followed_swapped_mapping"], 1)
        self.assertTrue(control["conclusive"])


if __name__ == "__main__":
    unittest.main()
