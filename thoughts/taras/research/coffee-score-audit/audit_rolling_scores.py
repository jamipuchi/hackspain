#!/usr/bin/env python3
"""Calculate continuous coffee scores from exported evaluator rows.

This tool does not import the rolling-score implementation. It accepts a JSON
capture with a `rows` list and optional `snapshots` list. Every row needs:
`object_id`, `spawn_time_s`, `required_reject`, and `score_epoch_id`.
`outcome` is optional. Set `manual_injection` for manually injected rows.
Top-level `as_of_sim_time_s`, `window_seconds`, and `settling_seconds` set
defaults for snapshots. A snapshot may override them.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


METRICS = ("sorting_accuracy", "defect_capture", "good_loss", "unresolved")
VALID_OUTCOMES = {None, "accept", "reject", "spilled"}


class InputError(ValueError):
    """The capture cannot support an independent calculation."""


def number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise InputError(f"{field} must be finite")
    return result


def metric(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def read_capture(path: str) -> dict[str, Any]:
    source = sys.stdin if path == "-" else Path(path).open(encoding="utf-8")
    try:
        capture = json.load(source)
    except json.JSONDecodeError as error:
        raise InputError(f"invalid JSON: {error.msg}") from error
    finally:
        if source is not sys.stdin:
            source.close()
    if not isinstance(capture, dict):
        raise InputError("capture root must be an object")
    if not isinstance(capture.get("rows"), list):
        raise InputError("capture.rows must be a list")
    return capture


def row_epoch(row: dict[str, Any]) -> str:
    epoch = row.get("score_epoch_id")
    if not isinstance(epoch, str) or not epoch:
        raise InputError("each row needs a non-empty score_epoch_id")
    return epoch


def validate_rows(rows: list[Any]) -> list[dict[str, Any]]:
    seen: set[tuple[str, int]] = set()
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        label = f"rows[{index}]"
        if not isinstance(raw, dict):
            raise InputError(f"{label} must be an object")
        epoch = row_epoch(raw)
        object_id = raw.get("object_id")
        if isinstance(object_id, bool) or not isinstance(object_id, int):
            raise InputError(f"{label}.object_id must be an integer")
        key = (epoch, object_id)
        if key in seen:
            raise InputError(f"duplicate object_id {object_id} in score epoch {epoch}")
        seen.add(key)
        if not isinstance(raw.get("required_reject"), bool):
            raise InputError(f"{label}.required_reject must be true or false")
        outcome = raw.get("outcome")
        if outcome not in VALID_OUTCOMES:
            raise InputError(f"{label}.outcome must be accept, reject, spilled, or null")
        manual_injection = raw.get("manual_injection", False)
        if not isinstance(manual_injection, bool):
            raise InputError(f"{label}.manual_injection must be true or false when present")
        result.append({
            "object_id": object_id,
            "spawn_time_s": number(raw.get("spawn_time_s"), f"{label}.spawn_time_s"),
            "required_reject": raw["required_reject"],
            "outcome": outcome,
            "manual_injection": manual_injection,
            "score_epoch_id": epoch,
        })
    return result


def validate_engine_scores(scores: Any, label: str) -> dict[str, Any]:
    if not isinstance(scores, dict):
        raise InputError(f"{label} must be an object")
    for key in (
        "schema_version", "clock", "score_epoch_id", "as_of_sim_time_s",
        "window_seconds", "settling_seconds", "window_start_exclusive_s",
        "window_end_inclusive_s", "available_seconds", "warming_up",
        "manual_injections_excluded", "settling_objects", "eligible_objects", "versions",
    ):
        if key not in scores:
            raise InputError(f"{label}.{key} is required")
    for key in (
        "as_of_sim_time_s", "window_seconds", "settling_seconds",
        "window_start_exclusive_s", "window_end_inclusive_s", "available_seconds",
    ):
        number(scores[key], f"{label}.{key}")
    for key in ("settling_objects", "eligible_objects"):
        if isinstance(scores[key], bool) or not isinstance(scores[key], int) or scores[key] < 0:
            raise InputError(f"{label}.{key} must be a non-negative integer")
    for key in ("warming_up", "manual_injections_excluded"):
        if not isinstance(scores[key], bool):
            raise InputError(f"{label}.{key} must be true or false")
    versions = scores["versions"]
    if not isinstance(versions, dict):
        raise InputError(f"{label}.versions must be an object")
    for key in ("model", "policy", "source_revision"):
        if not isinstance(versions.get(key), str) or not versions[key]:
            raise InputError(f"{label}.versions.{key} must be non-empty")
    for name in METRICS:
        value = scores.get(name)
        if not isinstance(value, dict):
            raise InputError(f"{label}.{name} must be an object")
        for key in ("numerator", "denominator", "value"):
            if key not in value:
                raise InputError(f"{label}.{name}.{key} is required")
        for key in ("numerator", "denominator"):
            if isinstance(value[key], bool) or not isinstance(value[key], int) or value[key] < 0:
                raise InputError(f"{label}.{name}.{key} must be a non-negative integer")
        if value["value"] is not None:
            number(value["value"], f"{label}.{name}.value")
    return scores


def snapshot_context(capture: dict[str, Any]) -> list[dict[str, Any]]:
    snapshots = capture.get("snapshots")
    if snapshots is None:
        snapshots = [capture]
    if not isinstance(snapshots, list) or not snapshots:
        raise InputError("capture.snapshots must be a non-empty list when present")
    contexts = []
    epoch_versions: dict[str, tuple[str, str, str | None]] = {}
    for index, raw in enumerate(snapshots):
        if not isinstance(raw, dict):
            raise InputError(f"snapshots[{index}] must be an object")
        merged = {**capture, **raw}
        epoch = merged.get("score_epoch_id")
        if not isinstance(epoch, str) or not epoch:
            raise InputError(f"snapshots[{index}].score_epoch_id is required")
        window = number(merged.get("window_seconds", 60.0), "window_seconds")
        settling = number(merged.get("settling_seconds", 0.6), "settling_seconds")
        if window <= 0 or settling < 0:
            raise InputError("window_seconds must be positive and settling_seconds must be non-negative")
        versions = merged.get("versions")
        if not isinstance(versions, dict):
            raise InputError(f"snapshots[{index}].versions is required")
        model = versions.get("model")
        policy = versions.get("policy")
        source = versions.get("source_revision")
        if not isinstance(model, str) or not model or not isinstance(policy, str) or not policy:
            raise InputError(f"snapshots[{index}].versions needs non-empty model and policy values")
        if not isinstance(source, str) or not source:
            raise InputError(f"snapshots[{index}].versions.source_revision must be non-empty")
        version_key = (model, policy, source)
        prior_versions = epoch_versions.setdefault(epoch, version_key)
        if prior_versions != version_key:
            raise InputError(f"score epoch {epoch} spans different model or policy versions")
        engine_scores = validate_engine_scores(raw.get("rolling_scores"), f"snapshots[{index}].rolling_scores")
        contexts.append({
            "snapshot_index": index,
            "score_epoch_id": epoch,
            "as_of_sim_time_s": number(merged.get("as_of_sim_time_s"), "as_of_sim_time_s"),
            "window_seconds": window,
            "settling_seconds": settling,
            "versions": {"model": model, "policy": policy, "source_revision": source},
            "engine_scores": engine_scores,
        })
    return contexts


def calculate(rows: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    now = context["as_of_sim_time_s"]
    end = now - context["settling_seconds"]
    start = end - context["window_seconds"]
    epoch_rows = [row for row in rows if row["score_epoch_id"] == context["score_epoch_id"]]
    manual = [row for row in epoch_rows if row["manual_injection"]]
    cohort = [
        row for row in epoch_rows
        if not row["manual_injection"] and start < row["spawn_time_s"] <= end
    ]
    required = [row for row in cohort if row["required_reject"]]
    keep = [row for row in cohort if not row["required_reject"]]
    captured = sum(row["outcome"] == "reject" for row in required)
    accepted_keep = sum(row["outcome"] == "accept" for row in keep)
    good_lost = sum(row["outcome"] in {"reject", "spilled"} for row in keep)
    unresolved = sum(row["outcome"] is None for row in cohort)
    return {
        "schema_version": 1,
        "clock": "simulation",
        "score_epoch_id": context["score_epoch_id"],
        "as_of_sim_time_s": now,
        "window_seconds": context["window_seconds"],
        "settling_seconds": context["settling_seconds"],
        "window_start_exclusive_s": start,
        "window_end_inclusive_s": end,
        "available_seconds": max(0.0, min(context["window_seconds"], now - context["settling_seconds"])),
        "warming_up": max(0.0, min(context["window_seconds"], now - context["settling_seconds"])) < context["window_seconds"],
        "manual_injections_excluded": True,
        "source_rows_in_epoch": len(epoch_rows),
        "manual_rows_excluded": len(manual),
        "eligible_objects": len(cohort),
        "settling_objects": sum(end < row["spawn_time_s"] <= now for row in epoch_rows if not row["manual_injection"]),
        "sorting_accuracy": metric(captured + accepted_keep, len(cohort)),
        "defect_capture": metric(captured, len(required)),
        "good_loss": metric(good_lost, len(keep)),
        "unresolved": metric(unresolved, len(cohort)),
    }


def compare(expected: dict[str, Any], observed: dict[str, Any]) -> dict[str, Any]:
    differences = []
    for key in (
        "schema_version", "clock", "score_epoch_id", "as_of_sim_time_s",
        "window_seconds", "settling_seconds", "window_start_exclusive_s",
        "window_end_inclusive_s", "available_seconds", "warming_up",
        "manual_injections_excluded", "eligible_objects", "settling_objects",
    ):
        if key not in observed:
            differences.append(f"{key} missing")
        elif observed[key] != expected[key]:
            differences.append(f"{key}: expected {expected.get(key)!r}, engine {observed.get(key)!r}")
    for name in METRICS:
        actual = observed.get(name)
        if not isinstance(actual, dict):
            differences.append(f"{name} missing")
            continue
        for key in ("numerator", "denominator", "value"):
            if actual.get(key) != expected[name][key]:
                differences.append(f"{name}.{key}: expected {expected[name][key]!r}, engine {actual.get(key)!r}")
    observed_versions = observed.get("versions")
    if not isinstance(observed_versions, dict):
        differences.append("versions missing")
    else:
        for key, value in expected["versions"].items():
            if key not in observed_versions:
                differences.append(f"versions.{key} missing")
            elif observed_versions[key] != value:
                differences.append(
                    f"versions.{key}: expected {value!r}, engine {observed_versions.get(key)!r}"
                )
    return {"pass": not differences, "differences": differences}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", help="capture JSON file, or - for stdin")
    parser.add_argument("--indent", type=int, default=2)
    args = parser.parse_args()
    try:
        capture = read_capture(args.capture)
        rows = validate_rows(capture["rows"])
        results = []
        for context in snapshot_context(capture):
            expected = calculate(rows, context)
            expected["versions"] = context["versions"]
            expected["engine_comparison"] = compare(expected, context["engine_scores"])
            results.append(expected)
    except InputError as error:
        print(f"audit input error: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"audit_version": 1, "snapshots": results}, indent=args.indent, sort_keys=True))
    return 1 if any(not result["engine_comparison"]["pass"] for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
