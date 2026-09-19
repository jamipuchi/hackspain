"""Export actual MuJoCo poses, camera decisions and valve events for the web demo.

Use --sim-dir to replay a pinned closed-loop checkout; no physics is implemented here.
The trained model is trusted local input (joblib), never a downloaded user payload.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

import numpy as np


def quantize(values, scale):
    return np.rint(np.asarray(values) * scale).astype(int).tolist()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sim-dir', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--model', type=Path)
    parser.add_argument('--seconds', type=float, default=4)
    parser.add_argument('--fps', type=int, default=30)
    parser.add_argument('--rate', type=float, default=1000)
    parser.add_argument('--jet-force', type=float, default=.06)
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / 'web/replay.json')
    args = parser.parse_args()
    if not (args.seconds > 0 and 1 <= args.fps <= 60 and args.rate > 0 and args.jet_force >= 0):
        parser.error('require seconds/rate > 0, fps in 1..60, jet-force >= 0')
    source = args.sim_dir.resolve()
    sys.path.insert(0, str(source))
    import mujoco
    from profiles import PROFILES
    from scene import Layout
    from sim import SorterSim
    from vision import Inspector
    from classifier import Model
    from controller import Controller, Policy

    model_path = args.model or source / 'models/green_arabica.joblib'
    profile = PROFILES['green_arabica']
    sim = SorterSim(profile, Layout(), rate=args.rate, seed=args.seed)
    inspector = Inspector(sim)
    if not hasattr(inspector, 'component_members'):
        raise SystemExit('Use the coffee-sorter-closed-loop simulator: component membership is required for decision provenance.')
    controller = Controller(sim, inspector, Model.load(model_path), Policy(), jet_force=args.jet_force)
    m, d = sim.model, sim.data
    # Export compiled machine transforms so the web scene uses the same dimensions.
    machine = []
    for g in range(m.ngeom):
        if int(m.geom_bodyid[g]) in sim.body_index:
            continue
        material = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_MATERIAL, int(m.geom_matid[g])) if m.geom_matid[g] >= 0 else ''
        quat = np.zeros(4)
        mujoco.mju_mat2Quat(quat, d.geom_xmat[g])
        machine.append(dict(name=mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, g) or '',
                            type=int(m.geom_type[g]), size=m.geom_size[g].tolist(),
                            pos=d.geom_xpos[g].tolist(), quat=quat.tolist(),
                            material=material, rgba=m.geom_rgba[g].tolist()))
    fires = []
    original_fire = sim.fire

    def record_fire(nozzle, t_on, duration, force, uid=None):
        result = original_fire(nozzle, t_on, duration, force, uid)
        if 0 <= nozzle < sim.L.n_nozzles:
            fires.append([round(t_on, 6), round(t_on + duration, 6), nozzle, force])
        return result

    sim.fire = record_fire
    decisions = {}  # UID -> unknown=0 / accept=1 / reject=2, last observed controller action
    decision_events = []
    frames = []
    next_frame = 0
    seen_decisions = 0
    clock_start = time.perf_counter()
    capture_every = max(1, round(.004 / sim.dt))
    for step in range(int(round(args.seconds / sim.dt))):
        sim.step()
        if step % capture_every == 0:
            rgb, t = inspector.capture()
            blobs, _, _, _, tracks = controller.on_frame(rgb, t)
            members = inspector.component_members(blobs)
            for decision in controller.decisions[seen_decisions:]:
                blob_indices = np.flatnonzero(tracks == decision.tid)
                uids = sorted({int(uid) for i in blob_indices for uid in members[i]})
                # No guessed ground-truth nearest-neighbour links: unmatched stays unknown.
                state = 2 if decision.reject else 1
                for uid in uids:
                    decisions[uid] = state
                decision_events.append([round(float(d.time), 6), state, uids, bool(decision.late)])
            seen_decisions = len(controller.decisions)
        if d.time + 1e-9 >= next_frame:
            records = []
            for body, bean in sorted(sim.bean_of.items(), key=lambda pair: pair[1].uid):
                qa = sim.body_qpos[body]
                records.extend([bean.uid, *quantize(d.qpos[qa:qa+3], 10000),
                                *quantize(d.qpos[qa+3:qa+7], 10000), decisions.get(bean.uid, 0)])
            resolved = [bean for bean in sim.beans if bean.outcome]
            counters = [len(resolved), sum(b.defect and b.outcome == 'reject' for b in resolved),
                        sum(not b.defect and b.outcome in ('reject', 'spilled') for b in resolved)]
            frames.append(dict(t=round(float(d.time), 6), beans=records, counters=counters))
            next_frame += 1 / args.fps
        if step % 500 == 0:
            print(f't={d.time:.3f}s active={sim.n_active()} frames={len(frames)} wall={time.perf_counter()-clock_start:.1f}s', flush=True)
    inspector.r.close()
    metadata = []
    class_ids = {c.name: i for i, c in enumerate(profile.classes)}
    for bean in sim.beans:
        metadata.append([bean.uid, class_ids[bean.cls], *quantize(bean.axes, 100000),
                         round(bean.spawn_t, 6), bean.outcome,
                         None if bean.resolved_t is None else round(bean.resolved_t, 6)])
    revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    hashes = {name: hashlib.sha256((source / name).read_bytes()).hexdigest()
              for name in ('sim.py', 'scene.py', 'profiles.py', 'controller.py', 'vision.py', 'classifier.py')}
    payload = dict(version=1, source=dict(revision=revision, files=hashes,
                   modelSha256=hashlib.sha256(model_path.read_bytes()).hexdigest(), mujoco=mujoco.__version__),
                   config=dict(seconds=float(d.time), fps=args.fps, rate=args.rate, jetForce=args.jet_force,
                               seed=args.seed, policy=asdict(controller.pol)),
                   layout=asdict(sim.L), machine=machine,
                   classes=[dict(name=c.name, defect=c.defect, rgb=c.rgb, shape=c.shape) for c in profile.classes],
                   schema=dict(stride=9, row='uid,x,y,z,qw,qx,qy,qz,decision', positionScale=10000,
                               quaternionScale=10000, axesScale=100000,
                               bean='uid,class,ax,ay,az,spawnTime,outcome,resolvedTime',
                               decision='0 unknown; 1 accept; 2 reject',
                               counters='resolved beans; defects in reject; good in reject or spilled'),
                   beans=metadata, frames=frames, fires=fires, decisions=decision_events)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, separators=(',', ':'), allow_nan=False))
    print(json.dumps(dict(path=str(args.output), bytes=args.output.stat().st_size, frames=len(frames),
                          beans=len(metadata), peakActive=max(len(f['beans']) // 9 for f in frames),
                          fires=len(fires), counters=frames[-1]['counters'], wallSeconds=time.perf_counter()-clock_start)))


if __name__ == '__main__':
    main()
