"""Validate the shipped recording independently of the rendering implementation."""
import base64
import gzip
import json
import math
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).parent
DATA = json.loads((ROOT / 'replay.json').read_text())


class ReplayTests(unittest.TestCase):
    def test_timeline_and_pose_integrity(self):
        metadata = {b[0]: b for b in DATA['beans']}
        self.assertEqual(len(metadata), len(DATA['beans']))
        self.assertGreater(len(DATA['frames']), 90)
        previous = -1
        for frame in DATA['frames']:
            self.assertGreater(frame['t'], previous)
            previous = frame['t']
            rows = frame['beans']
            self.assertEqual(len(rows) % 9, 0)
            self.assertEqual(len(set(rows[::9])), len(rows) // 9)
            for i in range(0, len(rows), 9):
                uid, x, y, z, *rest = rows[i:i+9]
                qw, qx, qy, qz, decision = rest
                self.assertIn(uid, metadata)
                self.assertIn(decision, (0, 1, 2))
                self.assertTrue(all(isinstance(v, int) for v in rows[i:i+9]))
                self.assertLess(abs(math.sqrt(qw*qw+qx*qx+qy*qy+qz*qz)/10000-1), .00012)
                self.assertLess(x/10000, 2, 'parked body leaked into replay')
                self.assertLess(abs(y/10000), 2)
                self.assertGreater(z/10000, -.5)
                self.assertLessEqual(metadata[uid][5], frame['t'])
                # Resolved beans remain visible until the simulator recycles them downstream.

    def test_counters_equal_recorded_outcomes_at_every_frame(self):
        for frame in DATA['frames']:
            resolved = [b for b in DATA['beans'] if b[7] is not None and b[7] <= frame['t'] - DATA['layout']['timestep'] + 1e-6]
            expected = [len(resolved),
                        sum(DATA['classes'][b[1]]['defect'] and b[6] == 'reject' for b in resolved),
                        sum(not DATA['classes'][b[1]]['defect'] and b[6] in ('reject', 'spilled') for b in resolved)]
            self.assertEqual(frame['counters'], expected)
        self.assertGreater(DATA['frames'][-1]['counters'][1], 0)

    def test_decisions_only_appear_after_recorded_controller_event(self):
        events = sorted(DATA['decisions'], key=lambda event: event[0])
        state = {}
        e = 0
        for frame in DATA['frames']:
            while e < len(events) and events[e][0] <= frame['t']:
                for uid in events[e][2]:
                    state[uid] = events[e][1]
                e += 1
            for i in range(0, len(frame['beans']), 9):
                self.assertEqual(frame['beans'][i+8], state.get(frame['beans'][i], 0))

    def test_geometry_and_real_valve_events(self):
        L = DATA['layout']
        names = {g['name']: g for g in DATA['machine'] if g['name']}
        self.assertAlmostEqual(names['belt_top']['pos'][2] + names['belt_top']['size'][2], L['belt_z'])
        self.assertAlmostEqual(names['splitter']['pos'][0], L['split_x'] + .14)
        self.assertIn('bin_accept', names)
        self.assertIn('bin_reject', names)
        self.assertEqual(L['n_nozzles'], 64)
        self.assertGreater(len(DATA['fires']), 100)
        for start, stop, nozzle, force in DATA['fires']:
            self.assertGreater(stop, start)
            self.assertTrue(0 <= nozzle < L['n_nozzles'])
            self.assertEqual(force, DATA['config']['jetForce'])
        self.assertEqual(len(DATA['source']['modelSha256']), 64)
        self.assertEqual(len(DATA['source']['files']), 6)

    def test_standalone_page_embeds_exact_dataset(self):
        html = (ROOT / 'index.html').read_text()
        encoded = re.search(r'<script id="replay-data"[^>]*>(.*?)</script>', html, re.S).group(1)
        self.assertEqual(gzip.decompress(base64.b64decode(encoded)), (ROOT / 'replay.json').read_bytes())
        self.assertNotRegex(html, r'<(?:script|link)[^>]+(?:src|href)="https?://')
        self.assertLess(len(html.encode()), 5*1024*1024)
        self.assertLess((ROOT / 'replay.json').stat().st_size, 3*1024*1024)

# Opt-in physical provenance check, using the pinned simulation checkout and its Python env.
# Kept separate from the zero-dependency artifact checks above.
import os
import sys


@unittest.skipUnless(os.environ.get('COFFEE_SIM_DIR'), 'set COFFEE_SIM_DIR for the physical provenance check')
class PhysicsProvenanceTest(unittest.TestCase):
    def test_first_three_frames_match_fresh_mujoco_run(self):
        import hashlib
        source = Path(os.environ['COFFEE_SIM_DIR'])
        for name, digest in DATA['source']['files'].items():
            self.assertEqual(hashlib.sha256((source / name).read_bytes()).hexdigest(), digest)
        sys.path.insert(0, str(source))
        from sim import SorterSim
        from profiles import PROFILES
        from scene import Layout
        import numpy as np
        simulator = SorterSim(PROFILES['green_arabica'], Layout(), rate=DATA['config']['rate'], seed=DATA['config']['seed'])
        # These frames precede the camera and jets: camera readout cannot change these poses.
        self.assertLess(DATA['frames'][2]['t'], min(event[0] for event in DATA['fires']))
        for frame in DATA['frames'][:3]:
            while simulator.data.time + 1e-9 < frame['t']:
                simulator.step()
            by_uid = {b.uid: b for b in simulator.bean_of.values()}
            rows = frame['beans']
            self.assertEqual(len(by_uid), len(rows)//9)
            for i in range(0, len(rows), 9):
                bean = by_uid[rows[i]]
                q = simulator.body_qpos[bean.body]
                actual = simulator.data.qpos[q:q+7]
                self.assertTrue(np.all(np.abs(actual-np.array(rows[i+1:i+8])/10000) <= .000050001))


if __name__ == '__main__':
    unittest.main()
