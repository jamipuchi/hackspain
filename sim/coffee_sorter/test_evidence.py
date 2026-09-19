import json
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np
from types import SimpleNamespace

from evidence import save_samples, valve_status
from vision import Blobs


class ValveEvidenceTest(unittest.TestCase):
    def test_late_reject_is_not_claimed_as_fired(self):
        decision = SimpleNamespace(reject=True, late=True)
        self.assertEqual(valve_status(decision, [], 2, .002), 'LATE: NO FIRE')

    def test_queued_pulse_needs_a_physics_step(self):
        decision = SimpleNamespace(reject=True, late=False)
        fire = SimpleNamespace(t_on=1.001, t_off=1.005)
        self.assertEqual(valve_status(decision, [fire], 1.002, .002), 'QUEUED')
        self.assertEqual(valve_status(decision, [fire], 1.004, .002), 'FIRED')
        self.assertEqual(valve_status(decision, [], 2, .002), 'NO VALID VALVE')

    def test_explicit_activation_flag_overrides_window_inference(self):
        decision = SimpleNamespace(reject=True, late=False)
        fire = SimpleNamespace(t_on=1.001, t_off=1.005, activated=False)
        self.assertEqual(valve_status(decision, [fire], 2, .002), 'QUEUED')
        fire.activated = True
        self.assertEqual(valve_status(decision, [fire], 1.002, .002), 'FIRED')

    def test_keep_and_unclassified_have_no_fire(self):
        self.assertEqual(valve_status(None, [], 2, .002), 'UNDECIDED')
        self.assertEqual(valve_status(SimpleNamespace(reject=False), [], 2, .002), 'KEEP')

    def test_saved_evidence_distinguishes_prediction_action_and_collateral_hit(self):
        frame = np.zeros((192, 2080, 3), np.uint8)
        blobs = Blobs(1., 2, np.zeros(2), np.zeros(2), np.zeros(2), np.zeros(2),
                      np.array([[10, 10, 20, 20], [40, 10, 20, 20]]),
                      np.array([False, True]), np.zeros((2, 1)))
        decision = SimpleNamespace(tid=7, reject=True, late=False, cls='black')
        ctrl = SimpleNamespace(decisions=[decision], classes=['black', 'good'])
        bean = SimpleNamespace(uid=4, cls='good', jet_hits=2, outcome='reject')
        sim = SimpleNamespace(beans=[bean], data=SimpleNamespace(time=2.), dt=.002,
                              fire_hits={(7, 4)})
        fire = SimpleNamespace(t_on=1.001, t_off=1.005, activated=True, nozzle=3)
        samples = [(frame, blobs, np.array([[.8, .2]]), np.array([7, -1]),
                    [np.array([4]), np.zeros(0, int)])]
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            save_samples(samples, ctrl, sim, out, {7: [fire]})
            rows = json.loads((out / 'inspection_evidence.json').read_text())[0]['blobs']
            self.assertEqual(rows[0]['predicted'], 'black')
            self.assertEqual(rows[0]['decision_class'], 'black')
            self.assertEqual(rows[0]['valve'], 'FIRED')
            self.assertTrue(rows[0]['any_jet_hit'])
            self.assertEqual(rows[0]['own_pulse_hit_uids'], [4])
            self.assertEqual(rows[0]['outcomes'], ['reject'])
            self.assertEqual(rows[1]['predicted'], 'partial')
            self.assertFalse(rows[1]['any_jet_hit'])
            self.assertEqual(rows[1]['valve'], 'UNDECIDED')
            image = cv2.imread(str(out / 'inspection_0.png'))
            self.assertEqual(image.shape, (430, 2080, 3))
