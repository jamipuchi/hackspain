"""Small contract between a learned sorting policy and Jaume's RobotAPI."""

from __future__ import annotations

import math
from dataclasses import dataclass
from numbers import Real
from typing import Iterable, Protocol


@dataclass(frozen=True)
class SortDecision:
    object_id: str
    x_cm: float
    y_cm: float
    target: str


class RobotResult(Protocol):
    ok: bool
    text: str


class RobotAPI(Protocol):
    def pick_at(self, x_cm: float, y_cm: float) -> RobotResult: ...

    def place_in(self, target: str) -> RobotResult: ...


def execute_sort(
    robot: RobotAPI,
    decision: SortDecision,
    allowed_targets: Iterable[str],
) -> list[dict[str, object]]:
    """Execute one decision and return receipts without claiming physical proof."""
    allowed = set(allowed_targets)
    error = _validation_error(decision, allowed)
    if error:
        return [_error_receipt("validation", error)]

    receipts: list[dict[str, object]] = []
    try:
        pick = robot.pick_at(decision.x_cm, decision.y_cm)
        pick_receipt = _result_receipt("pick_at", pick)
    except Exception as exc:
        return [_error_receipt("pick_at", str(exc))]

    receipts.append(pick_receipt)
    if pick.ok is not True:
        return receipts

    try:
        place = robot.place_in(decision.target)
        place_receipt = _result_receipt("place_in", place)
    except Exception as exc:
        receipts.append(_error_receipt("place_in", str(exc)))
        return receipts

    receipts.append(place_receipt)
    return receipts


def _validation_error(decision: SortDecision, allowed_targets: set[str]) -> str | None:
    if not isinstance(decision.object_id, str) or not decision.object_id.strip():
        return "object_id must be a nonempty string"
    if not _is_finite_coordinate(decision.x_cm):
        return "x_cm must be a finite number"
    if not _is_finite_coordinate(decision.y_cm):
        return "y_cm must be a finite number"
    if not isinstance(decision.target, str) or decision.target not in allowed_targets:
        return "target is not allowed"
    return None


def _is_finite_coordinate(value: object) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def _result_receipt(action: str, result: RobotResult) -> dict[str, object]:
    if not isinstance(result.ok, bool) or not isinstance(result.text, str):
        raise ValueError("robot result requires a boolean ok and string text")
    return {
        "action": action,
        "reported_ok": result.ok,
        "task_verified": None,
        "text": result.text,
    }


def _error_receipt(action: str, text: str) -> dict[str, object]:
    return {
        "action": action,
        "reported_ok": None,
        "task_verified": None,
        "text": text,
        "error": True,
    }


@dataclass(frozen=True)
class RecordingResult:
    """Legacy-shaped result from the RecordingRobot test double."""

    ok: bool
    text: str


class RecordingRobot:
    """Deterministic test double for dry runs. It does not verify physical work."""

    def __init__(self, pick_ok: bool = True, place_ok: bool = True) -> None:
        self.pick_ok = pick_ok
        self.place_ok = place_ok
        self.calls: list[tuple[object, ...]] = []

    def pick_at(self, x_cm: float, y_cm: float) -> RecordingResult:
        self.calls.append(("pick_at", x_cm, y_cm))
        return RecordingResult(self.pick_ok, "recorded pick")

    def place_in(self, target: str) -> RecordingResult:
        self.calls.append(("place_in", target))
        return RecordingResult(self.place_ok, "recorded place")
