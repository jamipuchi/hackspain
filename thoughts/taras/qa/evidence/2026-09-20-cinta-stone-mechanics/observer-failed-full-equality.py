"""Observe the approved Stone cases without changing their original engine runs."""
from __future__ import annotations

import fcntl
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import mujoco
import numpy as np

REPO = Path('/private/tmp/cinta-final-initial-qa')
OUT = Path('/private/tmp/cinta-stone-mechanics')
COFFEE = REPO / 'sim/coffee_sorter'
LOCK = Path('/private/tmp/hackspain-coffee-runtime.lock')
MODEL = COFFEE / 'models/live_green_arabica.joblib'
PRESET = COFFEE / 'configs/continuous_demo.json'
EXPECTED_REVISION = '289a362c50ba94dc35080369e0083536a30f686a'
EXPECTED_MODEL = 'f5b26f26aed62db18fbef23b4df712b96b2946b1ae2fce699e1a2e85a20844a3'
EXPECTED_SIM = '9382e659b308eae550822d1638cc2e7a1db81f9e52b1897ff0eaad12138bd86d'
EXPECTED_PRESET = '5fc5033ea62ceb4f640b4e642990d014d6de09ce706c20501eecd6c30d5c428b'
CONTINUATION_SECONDS = 1.5
REFERENCE_HASHES = {
    'keep': 'f9ecc27a03d94b44faffe5edfc5475649de710567fb9d0310d8cde04c5d7e661',
    'reject': 'ade79d19ecb0672dac4da5850e53e6c0fd524e3b1589d153678e2f498c33d4c0',
}
RUNNER = Path('/private/tmp/cinta-generated-items-final-e2e/run_stone_routes.py')
RUNNER_HASH = '7be38f6d1a7a887ed37833eca3bc534c8600f3c917c3805aa2dd1dd13e901736'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


sys.path.insert(0, str(COFFEE))
from engine import Engine

assert digest(RUNNER) == RUNNER_HASH
spec = importlib.util.spec_from_file_location(
    'original_routes', RUNNER)
original_routes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(original_routes)


def state(sim, data, body):
    qa, va = sim.body_qpos[body], sim.body_qvel[body]
    return {'time_s': float(data.time), 'pose': data.qpos[qa:qa + 7].tolist(),
            'velocity': data.qvel[va:va + 6].tolist()}


def contacts(sim, data, body, solve_time):
    # implicitfast solves contacts before integration advances the data time.
    result = []
    for index in range(data.ncon):
        contact = data.contact[index]
        geoms = (int(contact.geom1), int(contact.geom2))
        if body not in [int(sim.model.geom_bodyid[g]) for g in geoms]:
            continue
        other = next(g for g in geoms if int(sim.model.geom_bodyid[g]) != body)
        force = np.zeros(6)
        mujoco.mj_contactForce(sim.model, data, index, force)
        result.append({'time_s': solve_time, 'observed_after_step_s': float(data.time), 'geom_id': other,
                       'geom_name': mujoco.mj_id2name(sim.model, mujoco.mjtObj.mjOBJ_GEOM, other),
                       'position_m': contact.pos.tolist(), 'distance_m': float(contact.dist),
                       'normal_force_n': float(force[0])})
    return result


def install_observer(engine):
    sim = engine.sim
    rows = {}
    original_step, original_park = sim.step, sim._park

    def observed_park(body):
        bean = sim.bean_of[body]
        row = rows[bean.uid]
        row['original_retirement'] = state(sim, sim.data, body)
        assert sim.n_active() == 1
        assert all(fire.t_off <= sim.data.time for fire in sim.fires)
        assert np.count_nonzero(sim.data.xfrc_applied) == 0
        assert np.count_nonzero(sim.data.qfrc_applied) == 0
        row['continuation_force_checks'] = {
            'active_objects': sim.n_active(), 'feed_rate': sim.rate,
            'all_valve_pulses_expired': True, 'xfrc_applied_all_zero': True,
            'qfrc_applied_all_zero': True, 'conveyor_and_rollers_updated_each_step': True,
        }
        assert sim.rate == 0

        # Advance copied data while the original engine remains at its retirement boundary.
        # This preserves original injection times, RNG state, and pooled slot reuse.
        saved_qpos = sim.data.qpos.copy()
        saved_qvel = sim.data.qvel.copy()
        saved_warmstart = sim.data.qacc_warmstart.copy()
        saved_time = float(sim.data.time)
        probe = mujoco.MjData(sim.model)
        mujoco.mj_copyData(probe, sim.model, sim.data)
        row['continuation'] = []
        row['continuation_contacts'] = []
        for _ in range(round(CONTINUATION_SECONDS / sim.dt)):
            before = state(sim, probe, body)
            probe.xfrc_applied[:] = 0
            probe.qpos[sim.belt_qpos] = 0.0
            probe.qvel[sim.belt_qvel] = sim.L.belt_speed
            angle = sim.L.belt_speed / 0.03 * sim.dt
            probe.qpos[sim.roller_head] += angle
            probe.qpos[sim.roller_tail] += angle
            mujoco.mj_step(sim.model, probe)
            row['continuation'].append(before)
            row['continuation_contacts'].extend(contacts(sim, probe, body, before['time_s']))
        row['continuation_final'] = state(sim, probe, body)
        assert np.array_equal(saved_qpos, sim.data.qpos)
        assert np.array_equal(saved_qvel, sim.data.qvel)
        assert np.array_equal(saved_warmstart, sim.data.qacc_warmstart)
        assert saved_time == sim.data.time
        row['original_data_unchanged'] = True
        original_park(body)

    def observed_step():
        before = {body: (bean.uid, state(sim, sim.data, body), bean.outcome)
                  for body, bean in sim.bean_of.items()}
        original_step()
        for body, (uid, pose, previous_outcome) in before.items():
            row = rows[uid]
            row['trajectory'].append(pose)
            row['contacts'].extend(contacts(sim, sim.data, body, pose['time_s']))
            bean = sim.bean_by_uid.get(uid)
            if bean is not None:
                pose['applied_force_n'] = sim.data.xfrc_applied[body, :3].tolist()
                if previous_outcome is None and bean.outcome is not None:
                    row['early_score'] = {**pose, 'outcome': bean.outcome}

    sim._park = observed_park
    sim.step = observed_step
    return rows


def summarize(row):
    all_contacts = row['contacts'] + row['continuation_contacts']
    positive = [c for c in all_contacts if c['normal_force_n'] > 1e-9]
    names = sorted({c['geom_name'] or f"geom_{c['geom_id']}" for c in positive})
    bins = [c for c in positive if c['geom_name'] in ('bin_accept', 'bin_reject')]
    final_t = row['continuation_final']['time_s']
    final_bins = sorted({c['geom_name'] for c in bins if c['time_s'] >= final_t - .1})
    tail = [s for s in row['continuation'] if s['time_s'] >= final_t - .1]
    final_position = np.array(row['continuation_final']['pose'][:3])
    displacement = max(float(np.linalg.norm(np.array(s['pose'][:3]) - final_position)) for s in tail)
    speed = float(np.linalg.norm(row['continuation_final']['velocity'][:3]))
    stable_bins = []
    support_fractions = {}
    for name in ('bin_accept', 'bin_reject'):
        supported_steps = {c['time_s'] for c in bins
                           if c['geom_name'] == name and c['time_s'] >= final_t - .1}
        support_fractions[name] = len(supported_steps) / len(tail)
        if support_fractions[name] >= .9 and speed <= .01 and displacement <= .001:
            stable_bins.append(name)
    return {'seed': row['seed'], 'reuse': row['reuse'], 'mode': row['mode'],
            'body_id': row['body_id'], 'mass_kg': row['mass_kg'],
            'original_outcome': row['original']['physical']['outcome'],
            'original_matches_reference': row['original_matches_reference'],
            'early_score': row['early_score'], 'original_retirement': row['original_retirement'],
            'first_bin_contact': bins[0] if bins else None,
            'bin_contacts': sorted({c['geom_name'] for c in bins}),
            'final_100ms_bin_contacts': final_bins,
            'stable_bin_support': stable_bins,
            'final_100ms_support_fractions': support_fractions,
            'final_100ms_max_displacement_m': displacement, 'terminal_speed_m_s': speed,
            'positive_contact_geometries': names,
            'continuation_final': row['continuation_final']}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    revision = subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip()
    assert revision == EXPECTED_REVISION
    assert digest(MODEL) == EXPECTED_MODEL
    assert digest(COFFEE / 'sim.py') == EXPECTED_SIM
    assert digest(PRESET) == EXPECTED_PRESET
    assert mujoco.__version__ == '3.13.0'
    source_preset = json.loads(PRESET.read_text())
    assert source_preset['jet_force_n'] == .06
    assert source_preset['policy']['lead_s'] == .0015
    assert source_preset['layout']['timestep'] == .001
    summaries, execution = [], []
    geometry = None
    for mode in ('reject', 'keep'):
        reference_path = Path('/private/tmp/cinta-generated-items-final-e2e/results-final-initial') / f'stone-{mode}.json'
        assert digest(reference_path) == REFERENCE_HASHES[mode]
        reference = json.loads(reference_path.read_text())
        for seed in (7, 8, 9, 42):
            started = time.perf_counter()
            with LOCK.open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                wait = time.perf_counter() - started
                preset = json.loads(json.dumps(source_preset))
                preset.update(mode='continuous', requested_rate=0.0, seed=seed)
                with tempfile.TemporaryDirectory() as directory:
                    runtime = Path(directory) / 'observer.preset.json'
                    runtime.write_text(json.dumps(preset))
                    engine = Engine(runtime)
                    try:
                        rejects = set(engine.reject_classes)
                        rejects.add('stone') if mode == 'reject' else rejects.discard('stone')
                        engine.set_reject_classes(sorted(rejects))
                        observations = install_observer(engine)
                        if geometry is None:
                            model = engine.sim.model
                            geometry = [{'id': g, 'name': mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, g),
                                         'type': int(model.geom_type[g]), 'size': model.geom_size[g].tolist(),
                                         'position': model.geom_pos[g].tolist(), 'quaternion': model.geom_quat[g].tolist(),
                                         'contype': int(model.geom_contype[g]), 'conaffinity': int(model.geom_conaffinity[g])}
                                        for g in range(model.ngeom) if model.geom_bodyid[g] == 0]
                        for reuse in range(2):
                            uid = engine.inject('stone')
                            bean = engine.sim.bean_by_uid[uid]
                            row = {'mode': mode, 'seed': seed, 'reuse': reuse, 'object_id': uid,
                                   'body_id': bean.body, 'mass_kg': bean.mass, 'axes_m': bean.axes.tolist(),
                                   'trajectory': [], 'contacts': []}
                            observations[uid] = row
                            started_sim = float(engine.sim.data.time)
                            while float(engine.sim.data.time) - started_sim < 2.0:
                                engine.step()
                                if uid not in engine.sim.bean_by_uid:
                                    break
                            assert uid not in engine.sim.bean_by_uid
                            row['original'] = original_routes.object_row(engine, uid, seed, reuse)
                            expected = next(r for r in reference['objects'] if r['seed'] == seed and r['reuse'] == reuse)
                            row['original_matches_reference'] = row['original'] == expected
                            assert row['original_matches_reference'], (mode, seed, reuse)
                            summary = summarize(row)
                            summaries.append(summary)
                            output = OUT / f'{mode}-seed-{seed}-reuse-{reuse}.json.gz'
                            with gzip.open(output, 'wt') as stream:
                                json.dump(row, stream, allow_nan=False)
                            print(json.dumps(summary), flush=True)
                    finally:
                        engine.close()
            execution.append({'mode': mode, 'seed': seed, 'lock_wait_s': wait,
                              'wall_s': time.perf_counter() - started, 'lock_released': True})
            print(json.dumps({'lock_released': True, 'mode': mode, 'seed': seed}), flush=True)
    result = {'source_revision': revision, 'model_sha256': EXPECTED_MODEL,
              'sim_sha256': EXPECTED_SIM, 'preset_sha256': EXPECTED_PRESET,
              'observer_sha256': digest(__file__), 'continuation_seconds': CONTINUATION_SECONDS,
              'reference_sha256': REFERENCE_HASHES, 'runner_sha256': RUNNER_HASH,
              'stable_support_criteria': {'window_s': .1, 'minimum_contact_fraction': .9,
                                          'maximum_speed_m_s': .01, 'maximum_displacement_m': .001,
                                          'minimum_contact_force_n': 1e-9},
              'lock': str(LOCK), 'execution': execution, 'static_geometry': geometry, 'objects': summaries}
    (OUT / 'summary.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')


if __name__ == '__main__':
    main()
