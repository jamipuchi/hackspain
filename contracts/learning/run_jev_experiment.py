"""Run the bounded OpenRouter vision and TypeSafe Jev experiment."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
from typing import Mapping

from jev_policy import (
    MIN_PROBABILITY_LEAD,
    MIN_TOP_PROBABILITY,
    build_jev_request,
    parse_jev_response,
    predict_jev,
)
from providers import (
    OPENROUTER_URL,
    TYPESAFE_URL,
    VISION_MODEL,
    ProviderError,
    cached_call,
    credentials,
    parse_observation,
    vision_payload,
)
from run_experiment import SWAPPED_DESTINATIONS, TEACHER_DESTINATIONS


JEV_MODEL = "jev-1.13.0"
DESCRIBED_SPLITS = {"train", "correction", "test"}
MAX_WORKERS = 4


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--env-file", default=Path(".env"), type=Path)
    parser.add_argument("--vision-model", default=VISION_MODEL)
    parser.add_argument("--jev-model", default=JEV_MODEL)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    run_experiment(
        args.dataset,
        args.out,
        args.env_file,
        args.vision_model,
        args.jev_model,
        live=args.live,
    )


def run_experiment(
    dataset_path: Path,
    out_dir: Path,
    env_file: Path,
    vision_model: str = VISION_MODEL,
    jev_model: str = JEV_MODEL,
    *,
    live: bool = False,
) -> dict[str, object]:
    records = _load_records(dataset_path)
    described = [record for record in records if record["split"] in DESCRIBED_SPLITS]
    if len(described) != 51:
        raise ValueError("dataset must contain 51 train, correction, and test records")
    out_dir.mkdir(parents=True, exist_ok=True)
    keys = credentials(env_file)
    observations = _describe_records(
        described,
        dataset_path.parent,
        out_dir / "cache" / "vision",
        keys.get("OPENROUTER_API_KEY", ""),
        vision_model,
        live,
    )
    _write_json(out_dir / "observations.json", observations)
    print(f"vision complete: {len(observations)} records, {_error_count(observations)} errors")

    by_id = {record["sample_id"]: record for record in records}
    teacher = _teacher_demonstrations(records, observations)
    _write_json(
        out_dir / "policy.json",
        {
            "vision_model": vision_model,
            "jev_model": jev_model,
            "baseline_demonstrations": teacher["baseline_3_demo"],
            "candidate_demonstrations": teacher["candidate_21_demo"],
            "thresholds": {
                "minimum_top_probability": MIN_TOP_PROBABILITY,
                "minimum_probability_lead": MIN_PROBABILITY_LEAD,
                "clarity_gate": "visibility=clear and object_count=1",
            },
        },
    )
    tests = [record for record in records if record["split"] == "test"]
    decisions = _run_decisions(
        tests,
        observations,
        teacher,
        out_dir / "cache" / "jev",
        out_dir / "requests",
        keys.get("TYPESAFE_API_KEY", ""),
        jev_model,
        live,
    )
    _write_json(out_dir / "decisions.json", decisions)
    decision_count = sum(len(events) for events in decisions.values())
    print(f"decision complete: {decision_count} policy records")

    results = {
        "dataset": {
            "path": str(dataset_path),
            "sha256": _sha256_file(dataset_path),
            "split_counts": _split_counts(records),
            "described_records": len(described),
            "described_splits": sorted(DESCRIBED_SPLITS),
        },
        "models": {"vision_requested": vision_model, "jev_requested": jev_model},
        "policy_config": {
            "minimum_top_probability": MIN_TOP_PROBABILITY,
            "minimum_probability_lead": MIN_PROBABILITY_LEAD,
            "clarity_gate": "visibility=clear and object_count=1",
            "network_enabled": live,
            "max_concurrent_calls": MAX_WORKERS,
            "automatic_billable_retries": False,
        },
        "teacher_examples": {name: len(examples) for name, examples in teacher.items()},
        "vision": _provider_summary(observations),
        "policies": {
            "baseline_3_demo": _metrics(decisions["baseline_3_demo"], by_id),
            "candidate_21_demo": _metrics(decisions["candidate_21_demo"], by_id),
            "swapped_21_demo": _metrics(
                decisions["swapped_21_demo"], by_id, swapped_teacher_destinations=True
            ),
        },
        "swapped_label_control": _swap_control(
            decisions["candidate_21_demo"], decisions["swapped_21_demo"]
        ),
        "centroid_v1_descriptive_baseline": _v1_baseline(dataset_path),
        "comparison_limit": (
            "The vision model and the decision model changed from v1. "
            "This comparison cannot isolate the effect of Jev."
        ),
        "limits": (
            "This runner reuses simulated test images. It does not execute physical actions, "
            "learn from human video, or establish physical sorting success."
        ),
    }
    _write_json(out_dir / "results.json", results)
    return results


def _load_records(path: Path) -> list[dict[str, object]]:
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError("dataset must be a JSON list")
    required = {"sample_id", "split", "image", "evaluator"}
    if any(not isinstance(record, dict) or not required <= set(record) for record in data):
        raise ValueError("dataset record is missing required metadata")
    return data


def _describe_records(
    records: list[dict[str, object]],
    image_root: Path,
    cache_dir: Path,
    key: str,
    model: str,
    live: bool,
) -> list[dict[str, object]]:
    def describe(record: dict[str, object]) -> dict[str, object]:
        image_path = image_root / str(record["image"])
        payload = vision_payload(image_path, model)
        result, cached, error = _provider_result(OPENROUTER_URL, key, payload, cache_dir, live)
        event = {
            "sample_id": record["sample_id"],
            "split": record["split"],
            "image": record["image"],
            "image_sha256": _sha256_file(image_path),
            "cached": cached,
        }
        if error:
            event["error"] = error
            return event
        try:
            event["observation"] = parse_observation(result["response"])
        except (KeyError, ValueError) as exc:
            event["error"] = f"malformed_vision_output: {exc}"
        event["provider"] = _provider_event(result)
        return event

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        return list(executor.map(describe, records))


def _teacher_demonstrations(
    records: list[dict[str, object]], observations: list[dict[str, object]]
) -> dict[str, list[dict[str, object]]]:
    observation_by_id = {event["sample_id"]: event.get("observation") for event in observations}

    def examples(splits: set[str], swapped: bool = False) -> list[dict[str, object]]:
        result = []
        for record in records:
            if record["split"] not in splits:
                continue
            observation = observation_by_id.get(record["sample_id"])
            if not isinstance(observation, dict):
                continue
            destination = TEACHER_DESTINATIONS[_expected_kind(record)]
            result.append(
                {
                    "observation": observation,
                    "destination": SWAPPED_DESTINATIONS[destination] if swapped else destination,
                }
            )
        return result

    return {
        "baseline_3_demo": examples({"train"}),
        "candidate_21_demo": examples({"train", "correction"}),
        "swapped_21_demo": examples({"train", "correction"}, swapped=True),
    }


def _run_decisions(
    tests: list[dict[str, object]],
    observations: list[dict[str, object]],
    teachers: dict[str, list[dict[str, object]]],
    cache_dir: Path,
    request_dir: Path,
    key: str,
    model: str,
    live: bool,
) -> dict[str, list[dict[str, object]]]:
    observation_by_id = {event["sample_id"]: event for event in observations}
    request_dir.mkdir(parents=True, exist_ok=True)
    tasks = []
    outputs = {name: [] for name in teachers}
    for policy_name, demonstrations in teachers.items():
        for record in tests:
            event = observation_by_id.get(record["sample_id"], {})
            observation = event.get("observation")
            if not isinstance(observation, dict):
                outputs[policy_name].append(_deferred(record, "vision_error"))
            elif observation["visibility"] != "clear" or observation["object_count"] != 1:
                outputs[policy_name].append(_deferred(record, "unclear_observation"))
            else:
                tasks.append((policy_name, record, demonstrations, observation))

    def decide(task: tuple[str, dict[str, object], list[dict[str, object]], dict[str, object]]) -> tuple[str, dict[str, object]]:
        policy_name, record, demonstrations, observation = task
        if not demonstrations:
            return policy_name, _deferred(record, "no_teacher_examples")
        request = build_jev_request(demonstrations, observation, model)
        result, cached, error = _provider_result(TYPESAFE_URL, key, request, cache_dir, live)
        event = {"sample_id": record["sample_id"], "cached": cached, "request": request}
        if error:
            event.update(_deferred(record, error))
        else:
            try:
                prediction = predict_jev(result["response"], observation, expected_model=model)
                event.update(
                    {
                        "destination": prediction.destination,
                        "reason": prediction.reason,
                        "probabilities": prediction.probabilities,
                        "confidence": prediction.confidence,
                        "provider": _provider_event(result),
                    }
                )
            except (KeyError, ValueError) as exc:
                event.update(_deferred(record, f"malformed_jev_output: {exc}"))
                event["provider"] = _provider_event(result)
        _write_json(request_dir / f"{policy_name}_{record['sample_id']}.json", event)
        return policy_name, event

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for policy_name, event in executor.map(decide, tasks):
            outputs[policy_name].append(event)
    for events in outputs.values():
        events.sort(key=lambda event: str(event["sample_id"]))
    return outputs


def _provider_result(
    url: str, key: str, payload: dict, cache_dir: Path, live: bool
) -> tuple[dict[str, object] | None, bool, str | None]:
    cache_path = _cache_path(url, payload, cache_dir)
    if cache_path.exists():
        return json.loads(cache_path.read_text()), True, None
    if not live:
        return None, False, "live_calls_disabled"
    if not key:
        return None, False, "provider_credential_missing"
    try:
        result, cached = cached_call(url, key, payload, cache_dir)
        return result, cached, None
    except ProviderError as exc:
        return None, False, str(exc)


def _cache_path(url: str, payload: dict, cache_dir: Path) -> Path:
    digest = hashlib.sha256(json.dumps([url, payload], sort_keys=True).encode()).hexdigest()
    return cache_dir / f"{digest}.json"


def _deferred(record: Mapping[str, object], reason: str) -> dict[str, object]:
    return {"sample_id": record["sample_id"], "destination": None, "reason": reason}


def _metrics(
    events: list[dict[str, object]],
    records: Mapping[str, dict[str, object]],
    *,
    swapped_teacher_destinations: bool = False,
) -> dict[str, object]:
    correct = 0
    deferred = 0
    wrong = 0
    for event in events:
        destination = event.get("destination")
        if destination is None:
            deferred += 1
        elif destination == _expected_destination(
            records[str(event["sample_id"])], swapped_teacher_destinations
        ):
            correct += 1
        else:
            wrong += 1
    total = len(events)
    accepted = correct + wrong
    return {
        "total": total,
        "correct": correct,
        "wrong": wrong,
        "deferred": deferred,
        "coverage": accepted / total if total else 0.0,
        "selective_accuracy": correct / accepted if accepted else 0.0,
        "total_accuracy": correct / total if total else 0.0,
        "scoring_destinations": "swapped_teacher" if swapped_teacher_destinations else "teacher",
    }


def _swap_control(original: list[dict[str, object]], swapped: list[dict[str, object]]) -> dict[str, object]:
    swapped_by_id = {event["sample_id"]: event for event in swapped}
    accepted = [event for event in original if event.get("destination") is not None]
    followed = 0
    mismatches = 0
    deferred = 0
    for event in accepted:
        swapped_destination = swapped_by_id.get(event["sample_id"], {}).get("destination")
        if swapped_destination is None:
            deferred += 1
        elif swapped_destination == SWAPPED_DESTINATIONS[event["destination"]]:
            followed += 1
        else:
            mismatches += 1
    return {
        "original_accepted_predictions": len(accepted),
        "followed_swapped_mapping": followed,
        "mismatches_after_swap": mismatches,
        "deferred_after_swap": deferred,
        "conclusive": bool(accepted),
    }


def _provider_event(result: Mapping[str, object]) -> dict[str, object]:
    response = result.get("response")
    return {
        "endpoint": result.get("endpoint"),
        "request_sha256": result.get("request_sha256"),
        "latency_s": result.get("latency_s"),
        "returned_model": response.get("model") if isinstance(response, dict) else None,
        "usage": response.get("usage") if isinstance(response, dict) else None,
        "raw_response": response,
    }


def _provider_summary(observations: list[dict[str, object]]) -> dict[str, object]:
    errors = _error_count(observations)
    models = sorted(
        {
            event["provider"]["returned_model"]
            for event in observations
            if isinstance(event.get("provider"), dict) and event["provider"].get("returned_model")
        }
    )
    return {"total": len(observations), "errors": errors, "returned_models": models}


def _expected_kind(record: Mapping[str, object]) -> str:
    evaluator = record["evaluator"]
    if not isinstance(evaluator, Mapping) or evaluator.get("expected_kind") not in TEACHER_DESTINATIONS:
        raise ValueError("record has no known evaluator label")
    return str(evaluator["expected_kind"])


def _expected_destination(record: Mapping[str, object], swapped: bool) -> str:
    destination = TEACHER_DESTINATIONS[_expected_kind(record)]
    return SWAPPED_DESTINATIONS[destination] if swapped else destination


def _error_count(events: list[dict[str, object]]) -> int:
    return sum("error" in event for event in events)


def _split_counts(records: list[dict[str, object]]) -> dict[str, int]:
    return {split: sum(record["split"] == split for record in records) for split in sorted({str(record["split"]) for record in records})}


def _v1_baseline(dataset_path: Path) -> dict[str, object] | None:
    path = dataset_path.parent.parent / "evaluation" / "results.json"
    if not path.exists():
        return None
    result = json.loads(path.read_text())
    return {
        "baseline_test": result.get("baseline", {}).get("test"),
        "candidate_test": result.get("candidate", {}).get("test"),
    }


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
