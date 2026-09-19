import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import sys
import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from adaptive_brain import AdaptiveAgent, Ledger, BudgetStop, CHEAP, STRONG, RESERVE, PRICES, agree, detect, validate_labels
from agent_brain import AgentRun
from evaluate_adaptive import pin_manifest
from camera import MujocoPhoneCamera
import scene_def as sd


class AccountingTests(unittest.TestCase):
    def test_resume_cannot_relabel_old_results_with_new_policy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = dict(seeds=[4, 17, 29], policy_sha256='old')
            pin_manifest(root, manifest, b'old source')
            pin_manifest(root, manifest, b'old source')
            with self.assertRaises(ValueError):
                pin_manifest(root, {**manifest, 'policy_sha256': 'new'}, b'new source')
            self.assertEqual((root / 'policy-source.py').read_bytes(), b'old source')

    def test_ceiling_covers_entire_catalog_context_and_max_output(self):
        for model, context in ((CHEAP, 1310720), (STRONG, 1048576)):
            price_in, price_out = PRICES[model]
            self.assertGreater(RESERVE[model], (context * price_in + 2048 * price_out) / 1e6)

    def test_http_rejection_retains_full_reservation(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = Ledger(Path(folder) / 'ledger.json', 'run', .9)
            row = ledger.begin(CHEAP)
            row.update(status='rejected', http_status=429)
            ledger.save()
            # $0.13 rejection + $0.81 next call does not fit $0.90.
            with self.assertRaises(BudgetStop):
                ledger.begin(STRONG)
            ledger.begin(CHEAP)

    def test_unresolved_request_survives_restart_and_blocks_spend(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'ledger.json'
            Ledger(path, 'run1', 1.5).begin(CHEAP)
            with self.assertRaises(BudgetStop):
                Ledger(path, 'run2', 1.5).begin(STRONG)
            self.assertEqual(json.loads(path.read_text())['requests'][0]['status'], 'pending')

    def test_global_and_per_run_limits_reserve_before_call(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'ledger.json'
            ledger = Ledger(path, 'run', .81)
            row = ledger.begin(CHEAP)
            ledger.finish(row, .01, {}, .5, 'provider_reported')
            with self.assertRaises(BudgetStop):
                ledger.begin(STRONG)
            ledger.data['requests'] = [dict(run='prior', status='accounted', cost_usd=4.9)]
            with self.assertRaises(BudgetStop):
                ledger.begin(CHEAP)

    def test_missing_cost_is_not_zero_and_over_ceiling_blocks_next_call(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = Ledger(Path(folder) / 'ledger.json', 'run', 1.5)
            row = ledger.begin(CHEAP)
            with self.assertRaises(BudgetStop):
                ledger.finish(row, None, {}, 1, 'provider_reported')
            self.assertEqual(row['status'], 'pending')
            with self.assertRaises(BudgetStop):
                ledger.finish(row, .14, {}, 1, 'provider_reported')
            with self.assertRaises(BudgetStop):
                ledger.begin(CHEAP)


class GeometryTests(unittest.TestCase):
    def test_workspace_boundary_does_not_turn_a_fixture_into_a_part(self):
        transform = np.array([[0, -2000., 280], [-2000., 0, 400], [0, 0, 1.]])
        cal = Mock(H=np.linalg.inv(transform))
        frame = np.full((440, 560, 3), (180, 90, 30), dtype=np.uint8)
        # A real small candidate at 80 mm and a large fixture centred outside
        # the 108 mm workspace whose silhouette crosses into it.
        cv2.circle(frame, (280, 240), 8, (220, 220, 220), -1)
        cv2.circle(frame, (280, 176), 40, (220, 220, 220), -1)
        with patch.object(sd, 'WORKSPACE', {'r': (.055, .108), 'yaw': (-.7, .7)}), patch.object(sd, 'TARGETS', {}):
            parts, clear = detect(frame, cal)
        self.assertTrue(clear)
        self.assertEqual(len(parts), 1)
        self.assertAlmostEqual(parts[0]['x'], .08, places=3)

    def test_disagreement_and_ambiguity_cannot_produce_motion_candidates(self):
        a = [{'x': .08, 'y': 0}]
        self.assertEqual(agree(a, [{'x': .10, 'y': 0}]), ([], False))
        self.assertEqual(agree(a, [{'x': .08, 'y': 0}, {'x': .081, 'y': 0}]), ([], False))
        pairs, complete = agree(a, [{'x': .084, 'y': 0}])
        self.assertTrue(complete)
        self.assertEqual(pairs[0]['x'], .08)  # A, not a model or B-parallax average
        self.assertAlmostEqual(pairs[0]['disagreement_mm'], 4)

    def test_coordinate_and_extra_candidate_injection_rejected(self):
        good = {'p0_A': {'kind': 'nut', 'material': 'steel'}}
        self.assertEqual(validate_labels(good, ['p0_A']), good)
        for bad in ({'p0_A': {**good['p0_A'], 'x': 0.1}}, {**good, 'p1_A': good['p0_A']}, {'p0_A': {'kind': 'invented', 'material': 'steel'}}):
            with self.assertRaises(ValueError):
                validate_labels(bad, ['p0_A'])

    def test_supplied_renderer_reused_without_another_gl_context(self):
        cam = sd.CAMERAS['A']
        renderer = Mock(height=cam['height'], width=cam['width'])
        with patch('mujoco.Renderer') as constructor:
            camera = MujocoPhoneCamera(object(), object(), 'A', renderer=renderer)
        self.assertIs(camera.renderer, renderer)
        constructor.assert_not_called()
        with self.assertRaises(ValueError):
            MujocoPhoneCamera(object(), object(), 'A', renderer=Mock(height=1, width=1))


class CompletionTests(unittest.TestCase):
    def agent(self, pick_ok, place_ok):
        agent = AdaptiveAgent.__new__(AdaptiveAgent)
        agent.run = AgentRun()
        agent.max_steps = 12
        agent.routing = 'strong'
        agent.attempts = []
        agent.failed_actions = 0
        agent.refill = None
        agent.trace = Mock()
        agent.robot = Mock()
        agent.robot.pick_at.return_value = Mock(ok=pick_ok, text='motion feedback')
        agent.robot.place_in.return_value = Mock(ok=place_ok, text='camera feedback')
        part = dict(id='p0', x=.08, y=0, kind='nut')
        agent.observe = Mock(return_value=({}, [part], True))
        agent.choose = Mock(return_value=part)
        agent.classify = Mock(side_effect=lambda frames, parts, model: {f'{p["id"]}_{view}': {'kind': 'nut', 'material': 'steel'} for p in parts for view in ('A', 'B')})
        return agent

    def test_visible_failed_pick_or_place_never_claims_completion(self):
        for pick_ok, place_ok in ((False, False), (True, False)):
            with self.subTest(pick_ok=pick_ok):
                agent = self.agent(pick_ok, place_ok)
                result = agent.run_loop()
                self.assertFalse(result.finished)
                self.assertIn('failed actions', result.summary)
                agent.robot.pick_at.assert_called_once()

    def test_unmatched_geometry_cannot_move_or_complete(self):
        agent = self.agent(True, True)
        agent.observe.return_value = ({}, [], False)
        result = agent.run_loop()
        self.assertFalse(result.finished)
        agent.robot.pick_at.assert_not_called()

    def test_displaced_part_after_failure_cannot_evade_retry_limit(self):
        agent = self.agent(True, False)
        original = agent.observe.return_value[1][0]
        moved = {**original, 'id': 'p1', 'x': .097}
        agent.observe.side_effect = [({}, [original], True), ({}, [moved], True)]
        result = agent.run_loop()
        self.assertFalse(result.finished)
        agent.robot.pick_at.assert_called_once()
        self.assertEqual(agent.observe.call_count, 1)

    def test_visible_candidate_after_success_is_not_assumed_handled(self):
        agent = self.agent(True, True)
        result = agent.run_loop()
        self.assertFalse(result.finished)
        self.assertIn('unresolved', result.summary)
        agent.robot.pick_at.assert_called_once()


if __name__ == '__main__':
    unittest.main()
