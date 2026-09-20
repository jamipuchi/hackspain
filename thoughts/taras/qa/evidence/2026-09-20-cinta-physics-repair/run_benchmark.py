"""Compare pooled spawn costs and constant refreshes from the repository root."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import sys
import time

for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
    os.environ[key] = '1'

import mujoco
import numpy as np

sys.path.insert(0, str(Path.cwd() / 'sim/coffee_sorter'))
from profiles import GREEN_ARABICA
from scene import Layout

parser = argparse.ArgumentParser()
parser.add_argument('--before-source', type=Path, required=True)
parser.add_argument('--after-source', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
results = {'python': platform.python_version(), 'platform': platform.platform(),
           'mujoco': mujoco.__version__, 'rows': []}
for count in (400, 1150):
    for version, path in [('before', args.before_source), ('after', args.after_source)]:
        with open('/private/tmp/hackspain-coffee-runtime.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            spec = importlib.util.spec_from_file_location('sim_' + version, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            started = time.perf_counter()
            sim = module.SorterSim(GREEN_ARABICA, Layout(
                n_ellipsoid=count, n_half=48, n_box=20, n_capsule=20, timestep=.001),
                rate=0, seed=7)
            startup = time.perf_counter() - started
            elapsed = []
            for index in range(400):
                name = ('good', 'broken', 'stone', 'stick')[index % 4]
                started = time.perf_counter()
                bean = sim.spawn(GREEN_ARABICA.by_name(name))
                elapsed.append((time.perf_counter() - started) * 1e6)
                sim._park(bean.body)
            row = {'version': version, 'pool': count + 88,
                   'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                   'init_s': startup, 'spawn_mean_us': float(np.mean(elapsed)),
                   'spawn_p50_us': float(np.median(elapsed)),
                   'spawn_p95_us': float(np.percentile(elapsed, 95))}
            if version == 'after':
                expected = {field: getattr(sim.model, field).copy() for field in (
                    'dof_M0', 'dof_invweight0', 'body_invweight0', 'body_subtreemass', 'dof_length')}
                scratch = mujoco.MjData(sim.model)
                started = time.perf_counter()
                mujoco.mj_setConst(sim.model, scratch)
                row['full_refresh_ms'] = (time.perf_counter() - started) * 1000
                row['oracle_max_abs'] = {}
                for field, value in expected.items():
                    reference = getattr(sim.model, field)
                    row['oracle_max_abs'][field] = float(np.max(np.abs(value - reference)))
                    np.testing.assert_allclose(value, reference, rtol=1e-10, atol=1e-12)
            sim.continuous = True
            sim.rate = 500
            started = time.perf_counter()
            for _ in range(2000):
                sim.step()
                sim.drain_continuous_events()
            row['physics_2_sim_seconds_wall_s'] = time.perf_counter() - started
            row['physics_sim_seconds_per_wall_second'] = 2 / row['physics_2_sim_seconds_wall_s']
            row['spawned_during_feed'] = sim.n_spawned - 400
            row['starved'] = sim.starved
            results['rows'].append(row)
            print(json.dumps(row), flush=True)
        args.output.write_text(json.dumps(results, indent=2) + '\n')
