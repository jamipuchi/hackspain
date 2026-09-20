"""Measure one existing feed seed with frozen inputs and complete collection rows."""
from __future__ import annotations

import argparse
from collections import Counter
import fcntl
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time

for variable in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
    os.environ[variable] = '1'

LOCK = Path('/private/tmp/hackspain-coffee-runtime.lock')
MODEL_HASH = 'f5b26f26aed62db18fbef23b4df712b96b2946b1ae2fce699e1a2e85a20844a3'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--preset', type=Path, required=True)
    parser.add_argument('--seed', type=int, choices=(11, 17, 23, 31), required=True)
    parser.add_argument('--seconds', type=float, default=4.0)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    if args.seconds != 4.0:
        parser.error('This validation fixes feed duration at four simulated seconds.')
    repo = args.repo.resolve()
    coffee = repo / 'sim/coffee_sorter'
    sys.path.insert(0, str(coffee))
    from engine import Engine
    from rolling_scores import SETTLING_SECONDS
    from profiles import GREEN_ARABICA

    model = args.model.resolve()
    preset_path = args.preset.resolve()
    preset = json.loads(preset_path.read_text())
    assert sha(model) == MODEL_HASH
    assert preset['jet_force_n'] == .06 and preset['policy']['lead_s'] == .0015
    assert preset['layout']['timestep'] == .001 and preset['requested_rate'] == 500.0
    assert sum(preset['layout'][k] for k in ('n_ellipsoid', 'n_half', 'n_box', 'n_capsule')) == 488
    preset['seed'] = args.seed
    rows, decisions, pool_peak = {}, [], {}
    started = time.perf_counter()
    with LOCK.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        waited = time.perf_counter() - started
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory) / 'feed.json'
            runtime.write_text(json.dumps(preset))
            engine = Engine(runtime)
            try:
                assert engine.model_path.resolve() == model
                assert engine.model.classes == GREEN_ARABICA.names
                original_register = engine._register_bean
                original_evaluate = engine._evaluate_frame
                original_park = engine.sim._park

                def register(bean, injected=False):
                    original_register(bean, injected)
                    rows.setdefault(int(bean.uid), {
                        'uid': int(bean.uid), 'class': bean.cls, 'spawn_s': float(bean.spawn_t),
                        'mass_kg': float(bean.mass), 'axes_m': bean.axes.tolist(),
                        'required_reject': bean.cls in engine.reject_classes,
                        'outcome': None, 'resolved_s': None, 'retired_s': None,
                    })

                def park(body):
                    bean = engine.sim.bean_of[body]
                    if bean.uid in rows:
                        row = rows[bean.uid]
                        row.update(outcome=bean.outcome, resolved_s=bean.resolved_t,
                                   retired_s=float(engine.sim.data.time), jet_hits=int(bean.jet_hits),
                                   last_pos=bean.last_pos)
                    original_park(body)

                def evaluate(blobs, full, tracks, captured):
                    for decision in list(engine.controller.decisions):
                        decisions.append({'track': int(decision.tid), 'class': decision.cls,
                                          'reject': bool(decision.reject), 'scheduled': bool(decision.scheduled),
                                          'late': bool(decision.late), 'pulse_s': float(decision.pulse)})
                    return original_evaluate(blobs, full, tracks, captured)

                engine._register_bean = register
                engine._evaluate_frame = evaluate
                engine.sim._park = park
                run_started, cpu_started = time.perf_counter(), time.process_time()
                load_start = os.getloadavg()
                while engine.sim.data.time < args.seconds - engine.sim.dt / 2:
                    engine.step()
                    for shape, slots in engine.sim.pools.items():
                        active = len(slots) - len(engine.sim.free[shape])
                        pool_peak[shape] = max(pool_peak.get(shape, 0), active)
                run_wall = time.perf_counter() - run_started
                run_cpu = time.process_time() - cpu_started
                now = float(engine.sim.data.time)
                for bean in engine.sim.bean_of.values():
                    rows[bean.uid].update(outcome=bean.outcome, resolved_s=bean.resolved_t,
                                         jet_hits=int(bean.jet_hits))
                # Use the same 1.1-second horizon for both branches in paired quality evidence.
                cohort = [row for row in rows.values() if .8 <= row['spawn_s'] <= now - 1.1]
                reject_rows = [r for r in cohort if r['required_reject']]
                keep_rows = [r for r in cohort if not r['required_reject']]
                resolved = [r for r in rows.values() if r['resolved_s'] is not None]
                class_metrics = {}
                for name in GREEN_ARABICA.names:
                    group = [r for r in rows.values() if r['class'] == name]
                    ages = [r['resolved_s'] - r['spawn_s'] for r in group if r['resolved_s'] is not None]
                    class_metrics[name] = {
                        'spawned': len(group), 'resolved': len(ages),
                        'max_resolution_age_s': max(ages, default=None),
                        'resolved_after_deadline': sum(age > SETTLING_SECONDS for age in ages),
                        'active_past_deadline': sum(r['resolved_s'] is None and now-r['spawn_s'] > SETTLING_SECONDS for r in group),
                    }
                physics_ms = list(engine._timings['physics_ms'])
                result = {
                    'seed': args.seed, 'source_revision': subprocess.check_output(
                        ['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip(),
                    'source_sha256': {name: sha(coffee/name) for name in (
                        'sim.py', 'engine.py', 'rolling_scores.py', 'controller.py', 'scene.py', 'profiles.py')},
                    'model_sha256': sha(model), 'preset_sha256': sha(preset_path), 'runner_sha256': sha(__file__),
                    'settling_seconds': SETTLING_SECONDS,
                    'execution': {'lock_wait_s': waited, 'run_wall_s': run_wall, 'run_cpu_s': run_cpu,
                                  'startup_s': engine.startup_seconds, 'load_start': load_start,
                                  'load_end': os.getloadavg(), 'peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss},
                    'capacity': {'spawned': engine.sim.n_spawned, 'starved': engine.sim.starved,
                                 'admitted_rate': engine.sim.n_spawned/now, 'peak_active': engine._peak_active,
                                 'peak_active_by_pool': pool_peak, 'active_at_end': engine.sim.n_active()},
                    'timing': {'physics_steps': len(physics_ms),
                               'physics_mean_ms': sum(physics_ms)/len(physics_ms),
                               'maximum_resolution_age_s': max((r['resolved_s']-r['spawn_s'] for r in resolved), default=None)},
                    'cohort': {'start_s': .8, 'end_s': now-1.1, 'objects': len(cohort),
                               'outcomes': dict(Counter(r['outcome'] or 'unresolved' for r in cohort)),
                               'required_reject': len(reject_rows),
                               'captured_reject': sum(r['outcome']=='reject' for r in reject_rows),
                               'required_keep': len(keep_rows),
                               'keep_lost': sum(r['outcome'] in ('reject', 'spilled') for r in keep_rows)},
                    'rolling_scores': engine.rolling_scores(), 'classes': class_metrics,
                    'decisions': decisions, 'objects': list(rows.values()),
                    'valves': {'commanded': engine.sim.n_fired, 'activated': engine.sim.n_activated},
                }
            finally:
                engine.close()
    result['lock_released'] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    print(json.dumps({key: result[key] for key in ('seed', 'execution', 'capacity', 'timing', 'cohort')}))


if __name__ == '__main__':
    main()
