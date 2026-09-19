"""Bounded evaluator-truth rows for continuous rolling scores."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass


SETTLING_SECONDS = 0.6


@dataclass
class ScoreRow:
    object_id: int
    spawn_time_s: float
    required_reject: bool
    outcome: str | None = None


def _score(numerator: int, denominator: int) -> dict:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


class RollingScoreLedger:
    """Retain only feed truth that can enter the current score window."""

    def __init__(self, window_seconds: float):
        if window_seconds <= 0:
            raise ValueError("score_window_seconds must be greater than zero")
        self.window_seconds = float(window_seconds)
        self._rows: deque[ScoreRow] = deque()
        self._by_id: dict[int, ScoreRow] = {}

    def add(self, object_id: int, spawn_time_s: float, required_reject: bool) -> None:
        row = ScoreRow(int(object_id), float(spawn_time_s), bool(required_reject))
        self._rows.append(row)
        self._by_id[row.object_id] = row

    def resolve(self, object_id: int, outcome: str) -> None:
        row = self._by_id.get(object_id)
        if row is not None:
            row.outcome = outcome

    def prune(self, as_of_sim_time_s: float) -> None:
        window_start = float(as_of_sim_time_s) - SETTLING_SECONDS - self.window_seconds
        while self._rows and self._rows[0].spawn_time_s <= window_start:
            row = self._rows.popleft()
            self._by_id.pop(row.object_id, None)

    def __len__(self) -> int:
        return len(self._rows)

    def scores(self, *, as_of_sim_time_s: float, score_epoch_id: str,
               model_version: str, policy_version: str,
               source_revision: str | None) -> dict:
        now = float(as_of_sim_time_s)
        self.prune(now)
        window_end = now - SETTLING_SECONDS
        window_start = window_end - self.window_seconds
        eligible = [row for row in self._rows
                    if window_start < row.spawn_time_s <= window_end]
        settling = sum(window_end < row.spawn_time_s <= now for row in self._rows)
        required = [row for row in eligible if row.required_reject]
        keep = [row for row in eligible if not row.required_reject]
        captured = sum(row.outcome == "reject" for row in required)
        accepted_keep = sum(row.outcome == "accept" for row in keep)
        good_lost = sum(row.outcome in ("reject", "spilled") for row in keep)
        unresolved = sum(row.outcome is None for row in eligible)
        available = max(0.0, min(self.window_seconds, now - SETTLING_SECONDS))
        return {
            "schema_version": 1,
            "clock": "simulation",
            "score_epoch_id": score_epoch_id,
            "as_of_sim_time_s": now,
            "window_seconds": self.window_seconds,
            "settling_seconds": SETTLING_SECONDS,
            "window_start_exclusive_s": window_start,
            "window_end_inclusive_s": window_end,
            "available_seconds": available,
            "warming_up": available < self.window_seconds,
            "manual_injections_excluded": True,
            "settling_objects": settling,
            "eligible_objects": len(eligible),
            "sorting_accuracy": _score(captured + accepted_keep, len(eligible)),
            "defect_capture": _score(captured, len(required)),
            "good_loss": _score(good_lost, len(keep)),
            "unresolved": _score(unresolved, len(eligible)),
            "versions": {
                "model": model_version,
                "policy": policy_version,
                "source_revision": source_revision,
            },
        }
