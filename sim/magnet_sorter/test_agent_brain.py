import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import agent_brain as ab
import scene_def as sd
from robot_api import ToolResult


class FakeRobot:
    def __init__(self):
        self.magnet_on = False
        self.budget_actions = 0
        self.camera_names = []
        self.actions = []

    def state(self):
        return {"magnet_on": self.magnet_on, "actions_so_far": len(self.actions)}

    def _fmt_state(self):
        return "fake robot"

    def describe(self):
        return "fake robot"

    def pick_at(self, x_cm, y_cm):
        self.actions.append(("pick_at", x_cm, y_cm))
        self.magnet_on = True
        return ToolResult(True, "picked", photo=True)

    def place_in(self, target):
        self.actions.append(("place_in", target))
        self.magnet_on = False
        return ToolResult(True, "placed", photo=True)

    def home(self):
        self.actions.append(("home",))
        return ToolResult(True, "home", photo=True)

    def belt_advance(self, cm):
        self.actions.append(("belt_advance", cm))
        return ToolResult(True, "belt", photo=True)


class FakeAstra:
    def __init__(self, observations, usage=(10, 2)):
        self.observations = iter(observations)
        self.calls = []
        self.usage = usage
        self.responses = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        observation = next(self.observations)
        return SimpleNamespace(
            output=[],
            output_text=json.dumps(observation),
            usage=SimpleNamespace(input_tokens=self.usage[0], output_tokens=self.usage[1]),
            model="gpt-6-astra-2026-09-01",
        )


class FakeOpenRouter(FakeAstra):
    def __init__(self, observations, cost=0.01):
        super().__init__(observations)
        self.cost = cost

    def create(self, **kwargs):
        self.calls.append(kwargs)
        observation = next(self.observations)
        return SimpleNamespace(
            output=[],
            output_text=json.dumps(observation),
            usage=SimpleNamespace(input_tokens=7, output_tokens=3, cost=self.cost),
            model="deepseek/deepseek-v4.1-flash",
            id="openrouter-response",
            status="completed",
            error=None,
        )


class FakeJev:
    model = "jev-2026-09-01"

    def __init__(self, selector=None, usage=(20, 0)):
        self.selector = selector or (lambda candidates: next(iter(candidates)))
        self.usage = []
        self.states = []
        self.questions = []
        self._usage = usage

    def system_one(self, state, questions):
        self.states.append(state)
        self.questions.append(questions)
        candidates = questions["action"]["criteria"]
        selected = self.selector(candidates)
        probabilities = {key: (1.0 if key == selected else 0.0) for key in candidates}
        self.usage.append({"input": self._usage[0], "output": self._usage[1]})
        return {"model": "jev-2026-09-01", "usage": {"input_tokens": self._usage[0], "output_tokens": self._usage[1]}, "answers": {"action": {"type": "choice", "choice": selected, "probabilities": probabilities, "confidence": 0.9}}}


class FakeFunctionAstra:
    def __init__(self, calls):
        self.calls = iter(calls)
        self.responses = self

    def create(self, **_kwargs):
        call = next(self.calls)
        return SimpleNamespace(output=[call], usage=SimpleNamespace(input_tokens=1, output_tokens=1))


class FakeNoToolAstra:
    def __init__(self):
        self.calls = 0
        self.responses = self

    def create(self, **_kwargs):
        self.calls += 1
        return SimpleNamespace(output=[], usage=SimpleNamespace(input_tokens=1, output_tokens=1))


class AgentBrainTests(unittest.TestCase):
    def setUp(self):
        radius = sum(sd.WORKSPACE["r"]) / 2
        yaw = sum(sd.WORKSPACE["yaw"]) / 2
        self.x_cm = 100 * radius * math.cos(yaw)
        self.y_cm = 100 * radius * math.sin(yaw)
        self.containers = {name: "appears empty" for name in sd.TARGETS}
        self.observation = {
            "scene_summary": "one loose part",
            "parts": [{"id": "p1", "x_cm": self.x_cm, "y_cm": self.y_cm, "kind": sd.TASK["kinds"][0], "material": sd.TASK["materials"][0], "description": "small visible part"}],
            "containers": self.containers,
        }

    def _observation(self, summary, parts):
        return {"scene_summary": summary, "parts": parts, "containers": self.containers.copy()}

    def _jev_agent(self, astra, jev):
        with patch.object(ab, "OpenAI", return_value=astra), patch.object(ab, "load_api_key", return_value="key"), patch.object(ab, "JevClient", return_value=jev):
            agent = ab.JevAgent(FakeRobot(), {"A": lambda: np.zeros((2, 2, 3), np.uint8)}, {}, Path(tempfile.mkdtemp()), max_steps=8)
        agent.photo = lambda *_args, **_kwargs: ([{"type": "input_image", "image_url": "data:image/jpeg;base64,image-secret", "detail": "high"}], ["photo.png"])
        return agent

    def _openrouter_agent(self, router, jev, **kwargs):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "router-key"}, clear=False), patch.object(ab, "OpenAI", return_value=router), patch.object(ab, "JevClient", return_value=jev):
            agent = ab.JevAgent(FakeRobot(), {"A": lambda: np.zeros((2, 2, 3), np.uint8)}, {}, Path(tempfile.mkdtemp()), model="deepseek/deepseek-v4.1-flash", max_steps=8, vision_provider="openrouter", **kwargs)
        agent.photo = lambda *_args, **_kwargs: ([{"type": "input_image", "image_url": "data:image/jpeg;base64,image-secret", "detail": "high"}], ["photo.png"])
        return agent

    def test_jev_keeps_images_with_astra_and_maps_selected_candidate(self):
        astra = FakeAstra([self.observation])
        jev = FakeJev(selector=lambda candidates: "pick:p1")
        agent = self._jev_agent(astra, jev)

        decision = agent._decide([{"role": "user", "content": [{"type": "input_image", "image_url": "data:image/jpeg;base64,image-secret", "detail": "high"}]}])

        self.assertEqual(decision.calls[0].name, "pick_at")
        self.assertEqual(json.loads(decision.calls[0].arguments), {"x_cm": self.x_cm, "y_cm": self.y_cm})
        self.assertIn("image-secret", json.dumps(astra.calls[0]["input"]))
        self.assertNotIn("image-secret", json.dumps(jev.states))
        self.assertIn("Containers to inspect", astra.calls[0]["input"][0]["content"][0]["text"])
        traces = [json.loads(line) for line in agent.trace_path.read_text().splitlines()]
        self.assertIn("latency_s", traces[0])
        self.assertEqual(agent.vision_model, agent.model)
        self.assertEqual(agent.decision_model, "jev-2026-09-01")

    def test_invalid_jev_choice_and_probabilities_fail_before_execution(self):
        candidates = {"done": {"name": "done", "args": {}}}
        with self.assertRaisesRegex(RuntimeError, "invalid action probabilities"):
            ab.JevAgent._chosen_candidate({"answers": {"action": {"type": "choice", "choice": "other", "probabilities": {"done": 1.0}, "confidence": 0.5}}}, candidates)
        with self.assertRaisesRegex(RuntimeError, "invalid action probabilities"):
            ab.JevAgent._chosen_candidate({"answers": {"action": {"type": "choice", "choice": "done", "probabilities": {"done": 0.8}, "confidence": 0.5}}}, candidates)

    def test_malformed_answer_containers_and_confidence_range_fail(self):
        candidates = {"done": {"name": "done", "args": {}}}
        with self.assertRaisesRegex(RuntimeError, "invalid action answer"):
            ab.JevAgent._chosen_candidate({"answers": []}, candidates)
        with self.assertRaisesRegex(RuntimeError, "confidence"):
            ab.JevAgent._chosen_candidate({"answers": {"action": {"type": "choice", "choice": "done", "probabilities": {"done": 1.0}, "confidence": 1.1}}}, candidates)

    def test_rounded_probabilities_allow_only_the_expected_rounding_error(self):
        for values, accepted in [
            ([0.33, 0.33, 0.33], True),
            ([0.24] + [0.08] * 9, True),
            ([0.23] + [0.09] * 9, True),
            ([0.18] + [0.08] * 9, False),
            ([0.2, 0.8], False),
        ]:
            with self.subTest(values=values):
                probabilities = {str(i): value for i, value in enumerate(values)}
                body = {"answers": {"action": {"type": "choice", "choice": "0", "probabilities": probabilities, "confidence": 0.5}}}
                if accepted:
                    self.assertEqual(ab.JevAgent._chosen_candidate(body, probabilities)[0], "0")
                else:
                    with self.assertRaisesRegex(RuntimeError, "invalid action probabilities"):
                        ab.JevAgent._chosen_candidate(body, probabilities)

    def test_cost_records_astra_and_jev_usage_separately(self):
        astra = FakeAstra([self.observation], usage=(10, 2))
        agent = self._jev_agent(astra, FakeJev())
        agent._decide([{"role": "user", "content": [{"type": "input_image", "image_url": "data:image/jpeg;base64,image-secret"}]}])

        self.assertEqual((agent.run.calls, agent.run.astra_calls, agent.run.jev_calls), (2, 1, 1))
        self.assertEqual((agent.run.usage_in, agent.run.usage_out, agent.run.jev_usage_in, agent.run.jev_usage_out), (10, 2, 20, 0))
        self.assertAlmostEqual(agent.run.cost_usd(), 10 * 10e-6 + 2 * 50e-6 + 20 * ab.USD_PER_INPUT_TOKEN)

    def test_pick_cache_skips_only_the_next_placement_observation(self):
        choices = iter(["pick:p1", "place", "done"])

        def choose(candidates):
            wanted = next(choices)
            return next(key for key in candidates if key.startswith("place:")) if wanted == "place" else wanted

        astra = FakeAstra([self.observation, self._observation("after place", [])])
        agent = self._jev_agent(astra, FakeJev(selector=choose))
        agent.reuse_pick_observation = True
        image = [{"role": "user", "content": [{"type": "input_image", "image_url": "data:image/jpeg;base64,image-secret"}]}]
        part = self.observation["parts"][0]

        agent._decide(image)
        agent.robot.magnet_on = True
        agent._after_action("pick_at", {"x_cm": part["x_cm"], "y_cm": part["y_cm"]}, ToolResult(True, "picked"))
        place = agent._decide(image)
        self.assertEqual(len(astra.calls), 1)
        agent.robot.magnet_on = False
        agent._after_action("place_in", {"target": next(iter(sd.TARGETS))}, ToolResult(True, "placed"))
        agent._decide(image)

        self.assertEqual(place.calls[0].name, "place_in")
        self.assertEqual(len(astra.calls), 2)
        self.assertTrue(any(json.loads(line)["kind"] == "vision_reused" for line in agent.trace_path.read_text().splitlines()))

    def test_failed_pick_does_not_reuse_vision(self):
        astra = FakeAstra([self.observation, self._observation("failed pick", [self.observation["parts"][0]])])
        choices = iter(["pick:p1", "done"])
        agent = self._jev_agent(astra, FakeJev(selector=lambda _candidates: next(choices)))
        agent.reuse_pick_observation = True
        image = [{"role": "user", "content": [{"type": "input_image", "image_url": "data:image/jpeg;base64,image-secret"}]}]
        part = self.observation["parts"][0]

        agent._decide(image)
        agent._after_action("pick_at", {"x_cm": part["x_cm"], "y_cm": part["y_cm"]}, ToolResult(False, "missed"))
        agent._decide(image)

        self.assertEqual(len(astra.calls), 2)

    def test_photo_after_cached_pick_forces_the_following_vision_request(self):
        choices = iter(["pick:p1", "photo", "place"])

        def choose(candidates):
            wanted = next(choices)
            if wanted == "place":
                return next(key for key in candidates if key.startswith("place:"))
            return wanted

        astra = FakeAstra([self.observation, self._observation("after photo", [])])
        agent = self._jev_agent(astra, FakeJev(selector=choose))
        agent.reuse_pick_observation = True
        image = [{"role": "user", "content": [{"type": "input_image", "image_url": "data:image/jpeg;base64,image-secret"}]}]
        part = self.observation["parts"][0]

        agent._decide(image)
        agent.robot.magnet_on = True
        agent._after_action("pick_at", {"x_cm": part["x_cm"], "y_cm": part["y_cm"]}, ToolResult(True, "picked"))
        photo = agent._decide(image)
        agent._after_action("take_photo", {"camera": "all", "zoom_x_cm": None, "zoom_y_cm": None}, ToolResult(True, "photo", photo=False))
        place = agent._decide(image)

        self.assertEqual(photo.calls[0].name, "take_photo")
        self.assertEqual(place.calls[0].name, "place_in")
        self.assertEqual(len(astra.calls), 2)

    def test_openrouter_uses_its_client_payload_and_provider_cost(self):
        router = FakeOpenRouter([self.observation], cost=0.25)
        jev = FakeJev()
        agent = self._openrouter_agent(router, jev)
        image = [{"role": "user", "content": [{"type": "input_image", "image_url": "data:image/jpeg;base64,image-secret"}]}]

        agent._decide(image)

        request = router.calls[0]
        self.assertEqual(request["model"], "deepseek/deepseek-v4.1-flash")
        self.assertEqual(request["text"]["format"]["schema"], ab.vision_schema())
        self.assertEqual(request["extra_body"], {"provider": {"require_parameters": True}})
        self.assertIn("image-secret", json.dumps(request["input"]))
        self.assertEqual((agent.run.astra_calls, agent.run.openrouter_calls, agent.run.openrouter_usage_in, agent.run.openrouter_usage_out), (0, 1, 7, 3))
        self.assertAlmostEqual(agent.run.openrouter_cost_usd, 0.25)
        self.assertAlmostEqual(agent.run.cost_usd(), 0.25 + 20 * ab.USD_PER_INPUT_TOKEN)

    def test_openrouter_needs_no_openai_key_and_budget_uses_cost(self):
        router = FakeOpenRouter([self.observation], cost=2.0)
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "router-key"}, clear=True), patch.object(ab, "OpenAI", return_value=router) as openai, patch.object(ab, "load_api_key", side_effect=AssertionError("OpenAI key was requested")), patch.object(ab, "JevClient", return_value=FakeJev()):
            agent = ab.JevAgent(FakeRobot(), {"A": lambda: np.zeros((2, 2, 3), np.uint8)}, {}, Path(tempfile.mkdtemp()), model="deepseek/deepseek-v4.1-flash", budget_usd=1.0, vision_provider="openrouter")
        image = [{"role": "user", "content": [{"type": "input_image", "image_url": "data:image/jpeg;base64,image-secret"}]}]

        decision = agent._decide(image)

        self.assertEqual(openai.call_args.kwargs, {"api_key": "router-key", "base_url": "https://openrouter.ai/api/v1", "timeout": 90, "max_retries": 0})
        self.assertEqual(decision.calls, [])
        self.assertEqual(agent.run.jev_calls, 0)
        self.assertEqual(agent.run.openrouter_cost_usd, 2.0)

    def test_openrouter_rejects_missing_provider_cost(self):
        agent = self._openrouter_agent(FakeOpenRouter([self.observation], cost=None), FakeJev())
        image = [{"role": "user", "content": [{"type": "input_image", "image_url": "data:image/jpeg;base64,image-secret"}]}]

        with self.assertRaisesRegex(RuntimeError, "OpenRouter usage cost"):
            agent._decide(image)
        trace = json.loads(agent.trace_path.read_text())
        self.assertEqual(trace["kind"], "invalid_vision_accounting")
        self.assertEqual(trace["response_id"], "openrouter-response")
        self.assertEqual(trace["status"], "completed")
        self.assertIsNone(trace["usage"]["cost"])
        self.assertFalse(agent.run.cost_complete)

    def test_openrouter_interruption_and_request_failure_mark_cost_incomplete(self):
        image = [{"role": "user", "content": [{"type": "input_image", "image_url": "data:image/jpeg;base64,image-secret"}]}]
        for error in (RuntimeError("request failed"), KeyboardInterrupt(), SystemExit()):
            with self.subTest(error=type(error).__name__):
                router = FakeOpenRouter([])
                agent = self._openrouter_agent(router, FakeJev())

                def raise_error(**_kwargs):
                    raise error

                router.create = raise_error
                with self.assertRaises(type(error)):
                    agent._decide(image)
                self.assertFalse(agent.run.cost_complete)

    def test_pending_pick_is_available_for_the_place_decision(self):
        jev = FakeJev(selector=lambda candidates: next(key for key in candidates if key.startswith("place:")))
        agent = self._jev_agent(FakeAstra([]), jev)
        agent.observation = self.observation
        part = self.observation["parts"][0]
        agent.robot.magnet_on = True
        agent._after_action("pick_at", {"x_cm": part["x_cm"], "y_cm": part["y_cm"]}, ToolResult(True, "pick motion complete"))

        decision = agent._decide([])

        self.assertEqual(decision.calls[0].name, "place_in")
        self.assertEqual(jev.states[0]["pending_pick"]["id"], "p1")
        agent._after_batch_refill()
        self.assertEqual(agent.pick_attempts, [])
        self.assertIsNone(agent.pending_pick)

    def test_attempts_and_failed_place_feedback_persist(self):
        agent = self._jev_agent(FakeAstra([]), FakeJev())
        agent.observation = self.observation
        part = self.observation["parts"][0]
        args = {"x_cm": part["x_cm"], "y_cm": part["y_cm"]}
        agent._after_action("pick_at", args, ToolResult(True, "pick motion complete"))
        agent._after_action("place_in", {"target": next(iter(sd.TARGETS))}, ToolResult(False, "part remained on the magnet"))
        agent._after_action("pick_at", args, ToolResult(True, "second pick motion complete"))

        self.assertEqual(agent.pick_attempts[0]["attempts"], 2)
        self.assertEqual(agent.pick_attempts[0]["failed_attempts"], 1)
        self.assertEqual(agent.last_place_feedback["part"]["id"], "p1")
        self.assertFalse(agent.last_place_feedback["ok"])

    def test_vision_budget_exhaustion_prevents_jev_request(self):
        astra = FakeAstra([self.observation], usage=(10, 2))
        jev = FakeJev()
        agent = self._jev_agent(astra, jev)
        agent.budget_usd = 0.00001

        decision = agent._decide([{"role": "user", "content": [{"type": "input_image", "image_url": "data:image/jpeg;base64,image-secret"}]}])

        self.assertEqual(decision.calls, [])
        self.assertEqual(jev.states, [])
        self.assertEqual(agent.run.jev_calls, 0)

    def test_renamed_part_at_the_same_position_cannot_bypass_failure_limit(self):
        agent = self._jev_agent(FakeAstra([]), FakeJev())
        agent.observation = self.observation
        part = self.observation["parts"][0]
        args = {"x_cm": part["x_cm"], "y_cm": part["y_cm"]}
        for _ in range(sd.TASK.get("max_failed_pick_attempts", 3)):
            agent._after_action("pick_at", args, ToolResult(True, "pick motion complete"))
            agent._after_action("place_in", {"target": next(iter(sd.TARGETS))}, ToolResult(False, "part remained on the magnet"))
        renamed = dict(part, id="renamed-part")
        agent.observation = self._observation("renamed observation", [renamed])

        candidates = agent._candidates()

        self.assertNotIn("pick:renamed-part", candidates)
        self.assertEqual(sd.TASK.get("max_failed_pick_attempts", 3), 1 if sd.BUILD == "theker_v1" else 3)

    def test_one_failure_policy_is_declared_for_each_matching_task(self):
        for build in (sd.TARAS_V1, sd.THEKER_V1, sd.TARAS_CONVEYOR, sd.TARAS_KITTING):
            self.assertEqual(build.task["max_failed_pick_attempts"], 1)

    def test_unreachable_pick_does_not_create_a_pending_part(self):
        agent = self._jev_agent(FakeAstra([]), FakeJev())
        agent.observation = self.observation
        part = self.observation["parts"][0]

        agent._after_action("pick_at", {"x_cm": part["x_cm"], "y_cm": part["y_cm"]}, ToolResult(False, "unreachable", photo=False))

        self.assertIsNone(agent.pending_pick)
        self.assertEqual(agent.pick_attempts[0]["failed_attempts"], 1)

    def test_gpt6_no_tool_responses_stop_at_request_cap(self):
        astra = FakeNoToolAstra()
        with patch.object(ab, "OpenAI", return_value=astra), patch.object(ab, "load_api_key", return_value="key"):
            agent = ab.GPT6Agent(FakeRobot(), {"A": lambda: np.zeros((2, 2, 3), np.uint8)}, {}, Path(tempfile.mkdtemp()), max_steps=1)
        agent.photo = lambda *_args, **_kwargs: ([{"type": "input_image", "image_url": "data:image/jpeg;base64,photo"}], ["photo.png"])

        result = agent.run_loop()

        self.assertEqual(astra.calls, agent.max_requests)
        self.assertFalse(result.finished)
        self.assertIn("request budget", result.summary)

    def test_jev_run_loop_refill_clears_part_state(self):
        choices = iter(["pick:p1", "place", "done", "done", "done", "done"])

        def choose(candidates):
            wanted = next(choices)
            if wanted == "place":
                return next(key for key in candidates if key.startswith("place:"))
            return wanted

        final_containers = {name: "contains a visible wrong screw" for name in sd.TARGETS}
        astra = FakeAstra([self.observation, self._observation("placed", []), {"scene_summary": "final", "parts": [], "containers": final_containers}, self._observation("refill", []), self._observation("confirmed", [])])
        jev = FakeJev(selector=choose)
        agent = self._jev_agent(astra, jev)
        agent.reuse_pick_observation = True
        refills = []
        agent.refill = lambda: (refills.append(True) or "new batch") if len(refills) == 0 else None

        result = agent.run_loop()

        self.assertTrue(result.finished)
        self.assertEqual(len(refills), 1)
        self.assertEqual(agent.batches_done, 1)
        self.assertEqual(jev.states[3]["observation"]["containers"], final_containers)
        self.assertTrue(jev.states[3]["observation_fresh"])
        self.assertEqual(len(astra.calls), 5)
        self.assertEqual(jev.states[4]["pick_attempts"], [])
        self.assertIsNone(jev.states[4]["pending_pick"])
        self.assertIsNone(jev.states[4]["last_place_feedback"])

    def test_existing_done_confirmation_and_refill_still_work(self):
        done = lambda ident: SimpleNamespace(type="function_call", call_id=ident, name="done", arguments=json.dumps({"summary": "complete"}))
        astra = FakeFunctionAstra([done("1"), done("2"), done("3"), done("4")])
        refills = []

        with patch.object(ab, "OpenAI", return_value=astra), patch.object(ab, "load_api_key", return_value="key"):
            agent = ab.GPT6Agent(FakeRobot(), {"A": lambda: np.zeros((2, 2, 3), np.uint8)}, {}, Path(tempfile.mkdtemp()), max_steps=8, refill=lambda: (refills.append(True) or "new batch") if len(refills) == 0 else None)
        agent.photo = lambda *_args, **_kwargs: ([{"type": "input_image", "image_url": "data:image/jpeg;base64,photo"}], ["photo.png"])

        result = agent.run_loop()

        self.assertTrue(result.finished)
        self.assertEqual(result.steps, 4)
        self.assertEqual(agent.batches_done, 1)
        self.assertEqual(len(refills), 1)


if __name__ == "__main__":
    unittest.main()
