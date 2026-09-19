"""Tests for opaque learned sorting policies and experiment selection."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path
from unittest import mock

from policy import Demonstration, fit_policy, load_policy, save_policy
import run_experiment as experiment


DEMONSTRATIONS = [
    Demonstration({"a": 0.0, "b": 0.0}, "bin_A"),
    Demonstration({"a": 0.1, "b": 0.0}, "bin_A"),
    Demonstration({"a": 10.0, "b": 0.0}, "bin_B"),
    Demonstration({"a": 10.1, "b": 0.0}, "bin_B"),
    Demonstration({"a": 0.0, "b": 10.0}, "bin_C"),
    Demonstration({"a": 0.0, "b": 10.1}, "bin_C"),
]


class PolicyTests(unittest.TestCase):
    def test_schema_leakage_exclusion(self) -> None:
        self.assertEqual([field.name for field in fields(Demonstration)], ["features", "destination"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "records.json"
            dataset.write_text(json.dumps(_manifest()), encoding="utf-8")
            with mock.patch.object(experiment, "fit_policy", wraps=fit_policy) as fitted:
                experiment.run_experiment(dataset, root / "out")

        for call in fitted.call_args_list:
            for demonstration in call.args[0]:
                self.assertEqual([field.name for field in fields(demonstration)], ["features", "destination"])

    def test_fit_is_deterministic(self) -> None:
        first = fit_policy(DEMONSTRATIONS, "centroid").to_dict()
        second = fit_policy(DEMONSTRATIONS, "centroid").to_dict()

        self.assertEqual(first, second)

    def test_teacher_destination_changes_prediction(self) -> None:
        original = fit_policy(DEMONSTRATIONS, "exemplar")
        relabeled = fit_policy(
            [
                Demonstration(demo.features, {"bin_A": "bin_B", "bin_B": "bin_C", "bin_C": "bin_A"}[demo.destination])
                for demo in DEMONSTRATIONS
            ],
            "exemplar",
        )

        self.assertEqual(original.predict({"a": 0.0, "b": 0.0}).destination, "bin_A")
        self.assertEqual(relabeled.predict({"a": 0.0, "b": 0.0}).destination, "bin_B")

    def test_policy_json_round_trip_preserves_prediction(self) -> None:
        policy = fit_policy(DEMONSTRATIONS, "centroid")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            save_policy(policy, path)
            loaded = load_policy(path)

        self.assertEqual(loaded.to_dict(), policy.to_dict())
        self.assertEqual(loaded.predict({"a": 10.0, "b": 0.0}), policy.predict({"a": 10.0, "b": 0.0}))

    def test_invalid_input_abstains(self) -> None:
        policy = fit_policy(DEMONSTRATIONS, "centroid")

        self.assertEqual(policy.predict({"a": 0.0, "b": 0.0}, valid=False).reason, "invalid_segmentation")
        self.assertEqual(policy.predict({"a": 0.0}, valid=True).reason, "invalid_features")

    def test_test_records_do_not_change_chosen_policy(self) -> None:
        records = _manifest()
        altered = [dict(record) for record in records]
        for record in altered:
            if record["split"] == "test":
                record["features"] = {"a": 1000.0, "b": -1000.0}
                record["evaluator"] = {"expected_kind": "washer"}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_dataset = root / "first.json"
            second_dataset = root / "second.json"
            first_dataset.write_text(json.dumps(records), encoding="utf-8")
            second_dataset.write_text(json.dumps(altered), encoding="utf-8")
            experiment.run_experiment(first_dataset, root / "first")
            experiment.run_experiment(second_dataset, root / "second")
            first_policy = json.loads((root / "first" / "policy.json").read_text(encoding="utf-8"))
            second_policy = json.loads((root / "second" / "policy.json").read_text(encoding="utf-8"))

        self.assertEqual(first_policy, second_policy)


def _manifest() -> list[dict[str, object]]:
    records = []
    kinds = (("screw", 0.0, 0.0), ("nut", 10.0, 0.0), ("washer", 0.0, 10.0))
    for split, offset in (("train", 0.0), ("correction", 0.1), ("validation", 0.05), ("test", 0.02)):
        for index, (kind, a, b) in enumerate(kinds):
            records.append(
                {
                    "sample_id": f"{split}-{index}",
                    "split": split,
                    "features": {"a": a + offset, "b": b + offset},
                    "valid": True,
                    "image": f"{split}-{index}.png",
                    "fixture": {"body_name": f"fixture-{kind}", "rotation_rad": 0.1},
                    "evaluator": {"expected_kind": kind, "label_source": "fixture"},
                }
            )
    return records


if __name__ == "__main__":
    unittest.main()
