"""Run the opaque-destination sorting policy experiment on a JSON manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Iterable, Mapping, Sequence

try:
    from .policy import Demonstration, LearnedClusterPolicy, fit_policy, save_policy
except ImportError:
    from policy import Demonstration, LearnedClusterPolicy, fit_policy, save_policy


TEACHER_DESTINATIONS = {"screw": "bin_B", "nut": "bin_C", "washer": "bin_A"}
SWAPPED_DESTINATIONS = {"bin_A": "bin_B", "bin_B": "bin_C", "bin_C": "bin_A"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    run_experiment(args.dataset, args.out)


def run_experiment(dataset_path: Path, out_dir: Path) -> dict[str, object]:
    records = _load_manifest(dataset_path)
    by_split = {split: [record for record in records if record["split"] == split] for split in _SPLITS}
    baseline_demos = _initial_demonstrations(by_split["train"])
    candidate_demos = _demonstrations(by_split["train"] + by_split["correction"])
    baseline = fit_policy(baseline_demos, "centroid")
    candidates = {
        method: fit_policy(candidate_demos, method)
        for method in ("centroid", "exemplar")
    }
    validation = {"baseline": _evaluate(baseline, by_split["validation"])}
    validation.update({method: _evaluate(policy, by_split["validation"]) for method, policy in candidates.items()})
    selected_method = max(
        candidates,
        key=lambda method: (validation[method]["total_accuracy"], method == "centroid"),
    )
    candidate = candidates[selected_method]
    promoted = validation[selected_method]["total_accuracy"] > validation["baseline"]["total_accuracy"]
    deployed = candidate if promoted else baseline

    test = {
        "baseline": _evaluate(baseline, by_split["test"]),
        "candidate": _evaluate(candidate, by_split["test"]),
        "deployed": _evaluate(deployed, by_split["test"]),
    }
    control = _label_swap_control(candidate, candidate_demos, by_split["test"])
    decisions = {
        "validation": _decisions(deployed, by_split["validation"]),
        "test": _decisions(deployed, by_split["test"]),
        "swapped_label_control": control["decisions"],
    }
    results: dict[str, object] = {
        "dataset": {
            "path": str(dataset_path),
            "sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
            "split_counts": {name: len(values) for name, values in sorted(by_split.items())},
        },
        "demonstration_counts": {"baseline": len(baseline_demos), "candidate": len(candidate_demos)},
        "validation_candidates": {method: validation[method] for method in candidates},
        "baseline": {"method": baseline.method, "validation": validation["baseline"], "test": test["baseline"]},
        "candidate": {
            "method": selected_method,
            "validation": validation[selected_method],
            "test": test["candidate"],
        },
        "promoted": promoted,
        "deployed_method": deployed.method,
        "test": test["deployed"],
        "swapped_label_control": {
            "test_features_reused": True,
            "predictions_follow_demonstrations": control["follows_mapping"],
            "checked_non_abstained": control["checked_non_abstained"],
            "conclusive": control["conclusive"],
            "metrics": control["metrics"],
        },
        "limits": "This experiment evaluates a simulated manifest. It does not establish human video learning or physical robot success.",
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    save_policy(deployed, out_dir / "policy.json")
    _write_json(out_dir / "results.json", results)
    _write_json(out_dir / "decisions.json", decisions)
    (out_dir / "report.md").write_text(_report(results), encoding="utf-8")
    return results


_SPLITS = {"train", "correction", "validation", "test"}


def _load_manifest(path: Path) -> list[dict[str, object]]:
    with path.open(encoding="utf-8") as stream:
        records = json.load(stream)
    if not isinstance(records, list):
        raise ValueError("dataset manifest must be a JSON list")
    for record in records:
        _validate_record(record)
    sample_ids = [record["sample_id"] for record in records]
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("sample IDs must be unique")
    present_splits = {record["split"] for record in records}
    if present_splits != _SPLITS:
        raise ValueError("dataset needs nonempty train, correction, validation, and test splits")
    return records


def _validate_record(record: Mapping[str, object]) -> None:
    required = {"sample_id", "split", "features", "valid", "image", "evaluator"}
    if not required <= set(record):
        raise ValueError("dataset record is missing required fields")
    if not isinstance(record["sample_id"], str) or not record["sample_id"]:
        raise ValueError("sample_id must be a nonempty string")
    if record["split"] not in _SPLITS:
        raise ValueError("unknown split")
    if not isinstance(record["features"], dict):
        raise ValueError("features must be an object")
    if not isinstance(record["valid"], bool) or not isinstance(record["image"], str):
        raise ValueError("invalid record metadata")
    evaluator = record["evaluator"]
    if not isinstance(evaluator, dict) or "expected_kind" not in evaluator:
        raise ValueError("invalid evaluator metadata")
    if evaluator["expected_kind"] not in TEACHER_DESTINATIONS:
        raise ValueError("unknown expected_kind")


def _demonstrations(records: Iterable[Mapping[str, object]], swapped: bool = False) -> list[Demonstration]:
    destinations = SWAPPED_DESTINATIONS if swapped else None
    demonstrations = []
    for record in records:
        if not record["valid"]:
            continue
        destination = TEACHER_DESTINATIONS[_expected_kind(record)]
        if destinations:
            destination = destinations[destination]
        demonstrations.append(Demonstration(record["features"], destination))
    if not demonstrations:
        raise ValueError("no valid demonstrations")
    return demonstrations


def _initial_demonstrations(records: Iterable[Mapping[str, object]]) -> list[Demonstration]:
    chosen: dict[str, Demonstration] = {}
    for demo in _demonstrations(records):
        chosen.setdefault(demo.destination, demo)
    if len(chosen) != 3:
        raise ValueError("baseline needs one valid training demonstration per destination")
    return [chosen[destination] for destination in sorted(chosen)]


def _evaluate(policy: LearnedClusterPolicy, records: Sequence[Mapping[str, object]], swapped: bool = False) -> dict[str, object]:
    destinations = SWAPPED_DESTINATIONS if swapped else None
    confusion: dict[str, dict[str, int]] = {}
    correct = 0
    abstentions = 0
    predictions = 0
    ungated_correct = 0
    ungated_total = 0
    for record in records:
        expected = TEACHER_DESTINATIONS[_expected_kind(record)]
        if destinations:
            expected = destinations[expected]
        prediction = policy.predict(record["features"], valid=record["valid"])
        if record["valid"]:
            ungated = policy.nearest_destination(record["features"])
            if ungated.destination is not None:
                ungated_total += 1
                ungated_correct += ungated.destination == expected
        actual = prediction.destination if prediction.destination is not None else "abstain"
        confusion.setdefault(expected, {}).setdefault(actual, 0)
        confusion[expected][actual] += 1
        if prediction.destination is None:
            abstentions += 1
        else:
            predictions += 1
            correct += prediction.destination == expected
    total = len(records)
    return {
        "total": total,
        "correct": correct,
        "abstentions": abstentions,
        "total_accuracy": correct / total if total else 0.0,
        "selective_accuracy": correct / predictions if predictions else 0.0,
        "coverage": predictions / total if total else 0.0,
        "ungated_nearest_accuracy": ungated_correct / ungated_total if ungated_total else 0.0,
        "ungated_total": ungated_total,
        "confusion_matrix": confusion,
    }


def _decisions(policy: LearnedClusterPolicy, records: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    decisions = []
    for record in records:
        prediction = policy.predict(record["features"], valid=record["valid"])
        decisions.append(
            {
                "sample_id": record["sample_id"],
                "destination": prediction.destination,
                "reason": prediction.reason,
                "distance": prediction.distance,
            }
        )
    return decisions


def _expected_kind(record: Mapping[str, object]) -> str:
    evaluator = record["evaluator"]
    return evaluator["expected_kind"]


def _label_swap_control(
    candidate: LearnedClusterPolicy,
    demonstrations: Sequence[Demonstration],
    test_records: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    swapped_demos = [
        Demonstration(demo.features, SWAPPED_DESTINATIONS[demo.destination])
        for demo in demonstrations
    ]
    control = fit_policy(swapped_demos, candidate.method)
    original = [candidate.predict(record["features"], valid=record["valid"]) for record in test_records]
    swapped = [control.predict(record["features"], valid=record["valid"]) for record in test_records]
    checked = sum(base.destination is not None for base in original)
    follows_mapping = checked > 0 and all(
        changed.destination == (SWAPPED_DESTINATIONS.get(base.destination) if base.destination else None)
        and changed.reason == base.reason
        for base, changed in zip(original, swapped, strict=True)
    )
    return {
        "follows_mapping": follows_mapping,
        "checked_non_abstained": checked,
        "conclusive": checked > 0,
        "metrics": _evaluate(control, test_records, swapped=True),
        "decisions": [
            {
                "sample_id": record["sample_id"],
                "destination": prediction.destination,
                "reason": prediction.reason,
                "distance": prediction.distance,
            }
            for record, prediction in zip(test_records, swapped, strict=True)
        ],
    }


def _write_json(path: Path, value: object) -> None:
    with path.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def _report(results: Mapping[str, object]) -> str:
    baseline = results["baseline"]
    candidate = results["candidate"]
    test = results["test"]
    return "\n".join(
        [
            "# Learned sorting experiment",
            "",
            "The policy uses opaque teacher destinations and numeric features only.",
            "Demonstrated destinations define the groups. The learner does not discover an unknown number of groups.",
            "",
            f"The baseline uses {results['demonstration_counts']['baseline']} demonstrations. The candidate uses {results['demonstration_counts']['candidate']} demonstrations.",
            "Validation uses separate poses of the three training fixtures.",
            "The test contains ten poses of each of three unseen M4 fixtures.",
            "",
            "| Policy | Validation total accuracy | Test total accuracy |",
            "| --- | ---: | ---: |",
            f"| Baseline | {baseline['validation']['total_accuracy']:.3f} | {baseline['test']['total_accuracy']:.3f} |",
            f"| Candidate ({candidate['method']}) | {candidate['validation']['total_accuracy']:.3f} | {candidate['test']['total_accuracy']:.3f} |",
            "",
            f"Candidate promoted: {results['promoted']}.",
            f"Test assignments: {test['correct']} correct, {test['total'] - test['correct'] - test['abstentions']} wrong, {test['abstentions']} deferred.",
            f"Deployed test total accuracy: {test['total_accuracy']:.3f}.",
            f"Deployed selective accuracy: {test['selective_accuracy']:.3f}.",
            f"Deployed coverage: {test['coverage']:.3f}.",
            f"Deployed abstentions: {test['abstentions']}.",
            f"Deployed ungated nearest accuracy: {test['ungated_nearest_accuracy']:.3f} across {test['ungated_total']} valid records.",
            f"Baseline ungated nearest accuracy: {baseline['test']['ungated_nearest_accuracy']:.3f}.",
            "The baseline defers most items. Its zero total accuracy does not imply zero recognition ability.",
            "Ungated accuracy is a diagnostic. It ignores the acceptance threshold and does not measure accepted robot actions.",
            "This comparison measures additional demonstrations. It does not isolate an improvement from changing the learning method.",
            "",
            "## Demonstrated destination control",
            "",
            "The control changes the demonstrated destinations and reuses identical test features.",
            f"Accepted predictions checked: {results['swapped_label_control']['checked_non_abstained']}.",
            f"Predictions follow the changed destinations: {results['swapped_label_control']['predictions_follow_demonstrations']}.",
            "",
            "## Deployed test confusion matrix",
            "",
            "```json",
            json.dumps(test["confusion_matrix"], indent=2, sort_keys=True),
            "```",
            "",
            "## Limits",
            "",
            results["limits"],
            "",
        ]
    )


if __name__ == "__main__":
    main()
