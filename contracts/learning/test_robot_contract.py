"""Tests for the learned sorting contract."""

from __future__ import annotations

import json
import math
import unittest

from robot_contract import RecordingRobot, SortDecision, execute_sort


class ExplodingRobot(RecordingRobot):
    def __init__(self, explode_on: str) -> None:
        super().__init__()
        self.explode_on = explode_on

    def pick_at(self, x_cm: float, y_cm: float):
        if self.explode_on == "pick_at":
            raise RuntimeError("pick adapter error")
        return super().pick_at(x_cm, y_cm)

    def place_in(self, target: str):
        if self.explode_on == "place_in":
            raise RuntimeError("place adapter error")
        return super().place_in(target)


class RobotContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.decision = SortDecision("bolt-1", 12.5, -4.0, "metal")

    def test_invalid_inputs_have_no_side_effects(self) -> None:
        cases = (
            SortDecision("", 12.5, -4.0, "metal"),
            SortDecision("bolt-1", True, -4.0, "metal"),
            SortDecision("bolt-1", math.inf, -4.0, "metal"),
            SortDecision("bolt-1", 12.5, math.nan, "metal"),
            SortDecision("bolt-1", 12.5, -4.0, "unknown"),
            SortDecision("bolt-1", 12.5, -4.0, []),
        )

        for decision in cases:
            with self.subTest(decision=decision):
                robot = RecordingRobot()
                receipts = execute_sort(robot, decision, {"metal"})
                self.assertEqual(robot.calls, [])
                self.assertEqual(receipts[0]["action"], "validation")
                self.assertTrue(receipts[0]["error"])

    def test_pick_failure_suppresses_place(self) -> None:
        robot = RecordingRobot(pick_ok=False)

        receipts = execute_sort(robot, self.decision, {"metal"})

        self.assertEqual(robot.calls, [("pick_at", 12.5, -4.0)])
        self.assertEqual(receipts[0]["reported_ok"], False)
        self.assertEqual(len(receipts), 1)

    def test_adapter_exception_stops_execution(self) -> None:
        robot = ExplodingRobot("place_in")

        receipts = execute_sort(robot, self.decision, {"metal"})

        self.assertEqual(robot.calls, [("pick_at", 12.5, -4.0)])
        self.assertEqual(receipts[-1]["action"], "place_in")
        self.assertTrue(receipts[-1]["error"])
        self.assertEqual(receipts[-1]["text"], "place adapter error")

    def test_success_calls_pick_then_place(self) -> None:
        robot = RecordingRobot()

        receipts = execute_sort(robot, self.decision, {"metal"})

        self.assertEqual(
            robot.calls,
            [("pick_at", 12.5, -4.0), ("place_in", "metal")],
        )
        self.assertEqual([receipt["text"] for receipt in receipts], ["recorded pick", "recorded place"])
        json.dumps(receipts)

    def test_legacy_results_are_not_physically_verified(self) -> None:
        receipts = execute_sort(RecordingRobot(), self.decision, {"metal"})

        self.assertEqual([receipt["reported_ok"] for receipt in receipts], [True, True])
        self.assertEqual([receipt["task_verified"] for receipt in receipts], [None, None])

    def test_malformed_pick_result_does_not_trigger_place(self) -> None:
        robot = RecordingRobot(pick_ok="yes")
        receipts = execute_sort(robot, self.decision, {"metal"})
        self.assertEqual(robot.calls, [("pick_at", 12.5, -4.0)])
        self.assertTrue(receipts[0]["error"])


if __name__ == "__main__":
    unittest.main()
