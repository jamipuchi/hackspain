"""Reproduce isolated physics and frozen-model outcomes from the repository root."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
    os.environ[key] = '1'

parser = argparse.ArgumentParser()
parser.add_argument('--sim-source', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--model', type=Path, required=True)
parser.add_argument('--all-classes', action='store_true')
parser.add_argument('--full-pool', action='store_true')
args = parser.parse_args()
sys.path.insert(0, str(Path.cwd() / 'sim/coffee_sorter'))
spec = importlib.util.spec_from_file_location('sim', args.sim_source)
module = importlib.util.module_from_spec(spec)
sys.modules['sim'] = module
spec.loader.exec_module(module)
import mujoco
import numpy as np
from classifier import Model
from controller import Controller, Policy
from profiles import GREEN_ARABICA
from scene import Layout
from vision import Inspector

model_path = args.model
preset = json.loads(Path('sim/coffee_sorter/configs/default_demo.json').read_text())
layout = Layout(**(preset['layout'] if args.full_pool else {**preset['layout'], 'n_ellipsoid': 2, 'n_half': 1, 'n_box': 1, 'n_capsule': 1}))
output = {'pool_size': layout.n_ellipsoid + layout.n_half + layout.n_box + layout.n_capsule, 'mujoco': mujoco.__version__, 'source_sha256': hashlib.sha256(args.sim_source.read_bytes()).hexdigest(),
          'model_sha256': hashlib.sha256(model_path.read_bytes()).hexdigest(), 'free_fall': [], 'routes': []}
with open('/private/tmp/hackspain-coffee-runtime.lock', 'a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    sim = module.SorterSim(GREEN_ARABICA, layout, rate=0, seed=7)
    for cls in ('husk', 'good', 'stone', 'stick', 'broken'):
        for reuse in range(2):
            bean = sim.spawn(GREEN_ARABICA.by_name(cls))
            qa, va = sim.body_qpos[bean.body], sim.body_qvel[bean.body]
            sim.data.qpos[qa:qa + 3] = [0, 0, 3]
            sim.data.qpos[qa + 3:qa + 7] = [1, 0, 0, 0]
            sim.data.qvel[va:va + 6] = 0
            mujoco.mj_forward(sim.model, sim.data)
            output['free_fall'].append({'class': cls, 'reuse': reuse, 'mass_kg': bean.mass,
                'az': float(sim.data.qacc[va + 2]), 'body_simple': int(sim.model.body_simple[bean.body])})
            sim._park(bean.body)
    frozen = Model.load(model_path)
    cases = [('default', name) for name in GREEN_ARABICA.names] if args.all_classes else [
        (mode, 'stone') for mode in ('no_air', 'keep', 'reject', 'keep_without_anomaly')]
    for mode, class_name in cases:
        for seed in (7, 8, 9, 42):
            sim = module.SorterSim(GREEN_ARABICA, layout, rate=0, seed=seed)
            inspector = Inspector(sim) if mode != 'no_air' else None
            policy = Policy(threshold=.8, anomaly=mode != 'keep_without_anomaly')
            controller = Controller(sim, inspector, frozen, policy, jet_force=.06) if inspector else None
            if controller:
                controller.set_reject_classes(
                    [item.name for item in GREEN_ARABICA.classes if item.defect] if mode == 'default'
                    else ['stone'] if mode == 'reject' else [])
            try:
                for reuse in range(2):
                    bean = sim.spawn(GREEN_ARABICA.by_name(class_name))
                    start_commands, start_activated = sim.n_fired, sim.n_activated
                    start_decisions = len(controller.decisions) if controller else 0
                    commands = []
                    original_fire = sim.fire
                    def record_fire(*args, **kwargs):
                        fire = original_fire(*args, **kwargs)
                        commands.append(fire)
                        return fire
                    sim.fire = record_fire
                    flight = {}
                    jet_impulse = 0.0
                    start = sim.data.time
                    split_pos = None
                    step = 0
                    while sim.data.time - start < 1.5 and bean.body in sim.bean_of:
                        sim.step()
                        qa, va = sim.body_qpos[bean.body], sim.body_qvel[bean.body]
                        position = sim.data.qpos[qa:qa + 3]
                        velocity = sim.data.qvel[va:va + 3]
                        if bean.body in sim.bean_of:
                            jet_impulse -= float(sim.data.xfrc_applied[bean.body, 2]) * sim.dt
                            for label, crossed in [('edge', position[0] >= 0), ('jet_entry', position[0] >= sim.L.ej_x - .01), ('jet_exit', position[0] >= sim.L.ej_x + .01)]:
                                if crossed and label not in flight:
                                    flight[label] = {'t': sim.data.time, 'position': position.tolist(), 'velocity': velocity.tolist()}
                        if bean.outcome is not None and split_pos is None:
                            qa = sim.body_qpos[bean.body]
                            split_pos = sim.data.qpos[qa:qa + 3].tolist()
                        if inspector and step % 4 == 0:
                            frame, exposure = inspector.capture()
                            controller.on_frame(frame, exposure)
                        step += 1
                    row = {'mode': mode, 'class': class_name, 'seed': seed, 'reuse': reuse, 'body': int(bean.body),
                           'mass_kg': bean.mass, 'outcome': bean.outcome, 'split_position': split_pos,
                           'commands': sim.n_fired - start_commands,
                           'activated': sim.n_activated - start_activated, 'jet_hit_steps': bean.jet_hits,
                           'decisions': []}
                    if controller:
                        for decision in controller.decisions[start_decisions:]:
                            row['decisions'].append({'predicted': decision.cls,
                                'stone_probability': float(decision.probs[frozen.classes.index('stone')]),
                                'class_reject_probability': float(decision.probs[controller.reject_mask].sum()),
                                'anomaly': float(decision.anomaly), 'anomaly_threshold': float(frozen.anomaly_thresh),
                                'anomaly_reject': bool(policy.anomaly and decision.anomaly > frozen.anomaly_thresh),
                                'reject': bool(decision.reject), 'scheduled': bool(decision.scheduled),
                                'late': bool(decision.late), 'pulse_s': float(decision.pulse),
                                'own_contact': (decision.tid, bean.uid) in sim.fire_hits})
                    row['flight'] = flight
                    row['jet_impulse_ns'] = jet_impulse
                    row['pulse_windows'] = [{'nozzle': f.nozzle, 't_on': f.t_on, 't_off': f.t_off, 'force_n': f.force} for f in commands]
                    sim.fire = original_fire
                    output['routes'].append(row)
                    print(json.dumps(row), flush=True)
            finally:
                if inspector:
                    inspector.close()
            args.output.write_text(json.dumps(output, indent=2))
print(args.output)
