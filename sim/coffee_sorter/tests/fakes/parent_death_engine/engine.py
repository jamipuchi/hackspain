"""Fake engine for the parent-death regression. No mujoco, no model, no catalog.

Its snapshots are deliberately larger than a pipe buffer, so the states queue feeder
thread blocks once the service-like parent stops draining. That is the condition that
can keep a worker alive after its parent dies.
"""
import os
import time
from pathlib import Path
from types import SimpleNamespace

# One packet is far above the 64 KB pipe buffer, so two packets block the feeder.
FILLER = 'x' * 300_000
FILL_AFTER = 6


class Engine:
    def __init__(self, preset):
        self.continuous = True
        self.preset = {'limits': {'max_sim_seconds': None, 'max_wall_seconds': None}}
        self.session_id = 'parent-death-session'
        self.sim = SimpleNamespace(data=SimpleNamespace(time=0.0))
        self.source_revision = 'parent-death'
        self.source_hashes = {}
        self.policy_version = 'policy-1'
        self.snapshots = 0
        self.out = Path(os.environ['CINTA_PARENT_DEATH_OUT'])

    def snapshot(self):
        self.snapshots += 1
        if self.snapshots == 1:
            (self.out / 'engine-started').write_text('1')
        if self.snapshots == FILL_AFTER:
            (self.out / 'engine-filled').write_text('1')
        self.sim.data.time += 0.01
        return {'protocol_version': 2, 'mode': 'continuous', 'session_id': self.session_id,
                'sim_time_s': self.sim.data.time, 'filler': FILLER}

    def rolling_scores(self):
        return {'schema_version': 1, 'as_of_sim_time_s': self.sim.data.time}

    def reject_policy(self):
        return {'policy_version': self.policy_version, 'score_epoch_id': 'epoch-1'}

    def inject(self, class_name):
        return 1

    def injection_position(self, object_id):
        return [0.0, 0.0, 0.0]

    def injection_expectation(self, object_id):
        return {'expected_outcome': 'accept', 'expectation_policy_version': self.policy_version}

    def step(self):
        time.sleep(0.005)

    def report(self):
        return {}

    def close(self):
        pass
