"""CLI: preview | train | run | bench | viewer.

  ../.venv/bin/python run.py preview                       # PNG renders of the plant
  ../.venv/bin/python run.py train --profile green_arabica  # learn the blob classifier from sim ground truth
  ../.venv/bin/python run.py run --rate 2000 --seconds 8 --video
  ../.venv/bin/python run.py bench --rates 500,1000,2000,3000
  ../.venv/bin/mjpython run.py viewer --rate 1500          # live MuJoCo window (macOS needs mjpython)
"""
from __future__ import annotations

import argparse, json, time, datetime
from collections import Counter, defaultdict
from pathlib import Path
import numpy as np
import cv2

import assets
from profiles import PROFILES
from scene import Layout
from sim import SorterSim
from vision import Inspector, draw_blobs
from classifier import Model, collect, train, label_blobs, MODELS
from controller import Controller, SPECIALTY, COMMERCIAL, Policy
from render import Overview, Video, hud

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"


def stamp(): return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


# ------------------------------------------------------------------------------------ preview
def cmd_preview(a):
    sim = SorterSim(PROFILES[a.profile], rate=a.rate, seed=a.seed)
    for _ in range(int(a.seconds / sim.dt)):
        sim.step()
    ov = Overview(sim, 1600, 900)
    out = RUNS / "preview"; out.mkdir(parents=True, exist_ok=True)
    for cam in ("overview", "discharge", "topdown"):
        cv2.imwrite(str(out / f"{cam}.png"), cv2.cvtColor(ov.frame(cam), cv2.COLOR_RGB2BGR))
    insp = Inspector(sim)
    frame, t = insp.capture()
    blobs = insp.detect(frame, t)
    labels, _ = label_blobs(blobs, sim)
    cv2.imwrite(str(out / "inspection_raw.png"), cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
    cv2.imwrite(str(out / "inspection_labeled.png"), cv2.cvtColor(draw_blobs(frame, blobs, [str(l) for l in labels]), cv2.COLOR_RGB2BGR))
    print(f"active beans {sim.n_active()}, blobs in strip {blobs.n}, labels {Counter(labels)}")
    print("wrote", out)


# ------------------------------------------------------------------------------------ train
def cmd_train(a):
    P = PROFILES[a.profile]
    out = RUNS / f"train_{a.profile}"
    print(f"collecting {a.seconds}s of camera data at {a.rate} beans/s, defects x{a.boost} ...")
    X, y = collect(P, seconds=a.seconds, rate=a.rate, defect_boost=a.boost, seed=a.seed)
    print("samples:", len(y), dict(Counter(y)))
    model = train(P, X, y, out, seed=a.seed)
    MODELS.mkdir(exist_ok=True)
    model.save(MODELS / f"{a.profile}.joblib")
    m = model.meta
    print(f"accuracy {m['accuracy']:.4f}  defect recall {m['defect_recall']:.4f}  good false-reject {m['good_false_reject']:.4f}  "
          f"predict {m['predict_ms_per_40']:.2f} ms/40 blobs  iters {m['iters']}")
    for c, r in m["per_class"].items():
        print(f"  {c:8s} precision {r['precision']:.3f} recall {r['recall']:.3f} n={r['n']}")
    print("saved", MODELS / f"{a.profile}.joblib", "and", out)


# ------------------------------------------------------------------------------------ run
def metrics(sim, ctrl, warmup, t_end):
    P = sim.P
    beans = [b for b in sim.beans if b.outcome and b.spawn_t >= warmup and b.spawn_t <= t_end - 0.6]
    per = {}
    for c in P.classes:
        bs = [b for b in beans if b.cls == c.name]
        n = len(bs)
        rej = sum(b.outcome == "reject" for b in bs)
        spill = sum(b.outcome == "spilled" for b in bs)
        tgt = sum(b.targeted for b in bs)
        hit = sum(b.jet_hits > 0 for b in bs)
        per[c.name] = dict(n=n, rejected=rej, spilled=spill, targeted=tgt, defect=c.defect, severity=c.severity,
                           jet_hit=hit, targeted_rejected=sum(b.targeted and b.outcome == "reject" for b in bs),
                           jet_hit_rejected=sum(b.jet_hits > 0 and b.outcome == "reject" for b in bs),
                           reject_rate=rej / n if n else None, target_rate=tgt / n if n else None)
    defects = [b for b in beans if b.defect and P.by_name(b.cls).severity in ctrl.pol.reject_severities]
    defect_uids = {b.uid for b in defects}
    keep = [b for b in beans if not (b.defect and P.by_name(b.cls).severity in ctrl.pol.reject_severities)]
    accepted = [b for b in beans if b.outcome == "accept"]
    lat = np.array(ctrl.latency_ms) if ctrl.latency_ms else np.zeros(1)
    dec = ctrl.decisions
    sim_span = t_end - warmup
    return dict(
        beans_evaluated=len(beans),
        throughput_beans_per_s=len([b for b in sim.beans if warmup <= b.spawn_t <= t_end]) / sim_span,
        feed_kg_per_h=sum(b.mass for b in sim.beans if warmup <= b.spawn_t <= t_end) / sim_span * 3600,
        defect_removal=sum(b.outcome == "reject" for b in defects) / max(len(defects), 1),
        good_yield_loss=sum(b.outcome == "reject" for b in keep) / max(len(keep), 1),
        accept_purity_defects_per_1000=1000 * sum(b.uid in defect_uids for b in accepted) / max(len(accepted), 1),
        accept_purity_defects_per_1000_incoming=1000 * len(defects) / max(len(beans), 1),
        spilled_rate=sum(b.outcome == "spilled" for b in beans) / max(len(beans), 1),
        decisions=len(dec), fired_valves=sim.n_fired, late_decisions=sum(d.late for d in dec),
        obs_per_track_mean=float(np.mean([d.n_obs for d in dec])) if dec else 0,
        latency_ms=dict(p50=float(np.percentile(lat, 50)), p99=float(np.percentile(lat, 99)), max=float(lat.max())),
        latency_budget_ms=1000 * (sim.L.ej_x - sim.L.cam_x) / sim.L.belt_speed,
        pool_starved=sim.starved, frames=ctrl.frames,
        per_class=per,
    )


def cmd_run(a, return_metrics=False):
    P = PROFILES[a.profile]
    L = Layout()
    model = Model.load(MODELS / f"{a.profile}.joblib")
    pol = COMMERCIAL if a.policy == "commercial" else SPECIALTY
    pol = Policy(pol.name, pol.reject_severities, threshold=a.threshold, anomaly=not a.no_anomaly)
    sim = SorterSim(P, L, rate=a.rate, seed=a.seed)
    insp = Inspector(sim)
    ctrl = Controller(sim, insp, model, pol)
    out = RUNS / f"{stamp()}_{a.profile}_{int(a.rate)}"; out.mkdir(parents=True, exist_ok=True)
    video = Video(out / "overview.mp4", fps=50) if a.video else None
    ov = Overview(sim) if a.video else None
    frame_every = 2                       # camera at 250 Hz sim time
    video_every = int(0.02 / sim.dt)
    n_steps = int(a.seconds / sim.dt)
    warmup = 0.8
    t0 = time.perf_counter(); n_dec_seen = 0; samples_saved = 0
    last_overlay = None
    for i in range(n_steps):
        sim.step()
        if i % frame_every == 0:
            frame, t = insp.capture()
            blobs, Pr, A, full = ctrl.on_frame(frame, t)
            # evaluation hook: link fresh decisions to ground-truth beans (metrics only)
            new = ctrl.decisions[n_dec_seen:]; n_dec_seen = len(ctrl.decisions)
            if new:
                bodies, pos, _ = sim.active_state(rendered=True)
                for d in new:
                    if not d.reject:
                        continue
                    xp = d.x + d.v * (t - d.t_decided)
                    dd = (pos[:, 0] - xp) ** 2 + (pos[:, 1] - d.y) ** 2
                    j = int(np.argmin(dd))
                    if dd[j] < 0.008 ** 2:
                        sim.bean_of[bodies[j]].targeted = True
            if (video or samples_saved < 6) and blobs.n and i % (frame_every * 5) == 0:
                labels = []; colors = []
                k = 0
                for b in range(blobs.n):
                    if full[b]:
                        c = ctrl.classes[int(np.argmax(Pr[k]))]; rej = Pr[k][ctrl.reject_mask].sum() >= pol.threshold or A[k] > model.anomaly_thresh
                        labels.append(f"{c} {Pr[k].max():.2f}" + (" !" if A[k] > model.anomaly_thresh else "")); colors.append((255, 60, 60) if rej else (60, 255, 90)); k += 1
                    else:
                        labels.append(""); colors.append((160, 160, 160))
                last_overlay = draw_blobs(frame, blobs, labels, colors)
                if samples_saved < 6 and sim.data.time > warmup:
                    cv2.imwrite(str(out / f"inspection_{samples_saved}.png"), cv2.cvtColor(last_overlay, cv2.COLOR_RGB2BGR)); samples_saved += 1
        if video and i % video_every == 0:
            img = ov.frame("overview" if (sim.data.time % 8) < 5 else "discharge")
            if last_overlay is not None:
                strip = cv2.resize(last_overlay, (1240, int(1240 * last_overlay.shape[0] / last_overlay.shape[1])))
                img[720 - strip.shape[0] - 10:720 - 10, 20:20 + strip.shape[1]] = strip
            mres = metrics(sim, ctrl, warmup, sim.data.time) if sim.data.time > warmup + 0.7 else None
            lines = [f"t = {sim.data.time:5.2f} s   feed {a.rate:.0f} beans/s   belt {L.belt_speed:.1f} m/s   in transit {sim.n_active()}",
                     f"camera 250 fps  {L.cam_w}x{L.cam_h}  latency p99 {np.percentile(ctrl.latency_ms, 99) if ctrl.latency_ms else 0:.1f} ms / budget {1000 * (L.ej_x - L.cam_x) / L.belt_speed:.0f} ms   valves fired {sim.n_fired}"]
            if mres:
                lines.append(f"defect removal {100 * mres['defect_removal']:.1f}%   good yield loss {100 * mres['good_yield_loss']:.2f}%   "
                             f"accept purity {mres['accept_purity_defects_per_1000']:.1f} defects/1000 (in: {mres['accept_purity_defects_per_1000_incoming']:.0f})")
            hud(img, lines)
            video.add(img)
        if i % (n_steps // 10 or 1) == 0 and i:
            print(f"  t={sim.data.time:5.2f}s  wall={time.perf_counter() - t0:5.1f}s  active={sim.n_active()}  fired={sim.n_fired}  decisions={len(ctrl.decisions)}")
    wall = time.perf_counter() - t0
    res = metrics(sim, ctrl, warmup, sim.data.time)
    res.update(profile=a.profile, policy=pol.name, threshold=pol.threshold, rate=a.rate, seconds=a.seconds, seed=a.seed,
               wall_seconds=wall, wall_per_sim_second=wall / a.seconds)
    (out / "metrics.json").write_text(json.dumps(res, indent=1, default=float))
    # decisions log
    with open(out / "decisions.csv", "w") as f:
        f.write("tid,t,x,y,v,cls,p_reject,anomaly,reject,late,n_obs,pulse_ms,nozzles\n")
        for d in ctrl.decisions:
            f.write(f"{d.tid},{d.t_decided:.4f},{d.x:.4f},{d.y:.4f},{d.v:.3f},{d.cls},{d.probs[ctrl.reject_mask].sum():.3f},{d.anomaly:.2f},{int(d.reject)},{int(d.late)},{d.n_obs},{1000 * d.pulse:.1f},{'|'.join(map(str, d.nozzles))}\n")
    if video:
        video.close(); ov.close()
    insp.close()
    print_metrics(res)
    print("wrote", out)
    return res if return_metrics else None


def print_metrics(r):
    print(f"\n== {r.get('profile', '')} @ {r.get('rate', 0):.0f} beans/s, policy {r.get('policy', '')} ==")
    print(f"throughput {r['throughput_beans_per_s']:.0f} beans/s  ({r['feed_kg_per_h']:.0f} kg/h)   evaluated {r['beans_evaluated']}   wall {r.get('wall_per_sim_second', 0):.1f} s per sim s")
    print(f"defect removal {100 * r['defect_removal']:.1f}%   good yield loss {100 * r['good_yield_loss']:.2f}%   "
          f"accept stream {r['accept_purity_defects_per_1000']:.1f} defects/1000 (incoming {r['accept_purity_defects_per_1000_incoming']:.0f}/1000)   spilled {100 * r['spilled_rate']:.2f}%")
    print(f"decisions {r['decisions']}  valves {r['fired_valves']}  late {r['late_decisions']}  obs/track {r['obs_per_track_mean']:.2f}  "
          f"latency p50 {r['latency_ms']['p50']:.1f} ms p99 {r['latency_ms']['p99']:.1f} ms (budget {r['latency_budget_ms']:.0f} ms)  starved {r['pool_starved']}")
    print(f"{'class':8s} {'n':>6s} {'rejected':>9s} {'targeted':>9s} {'jet hit':>8s} {'hit/rej':>8s} {'spilled':>8s}")
    for c, v in r["per_class"].items():
        print(f"{c:8s} {v['n']:6d} {v['rejected']:9d} {v['targeted']:9d} {v['jet_hit']:8d} "
              f"{v['jet_hit_rejected']:8d} {v['spilled']:8d}   {'DEFECT' if v['defect'] else 'keep'} {v['severity']}")


# ------------------------------------------------------------------------------------ bench
def cmd_bench(a):
    rows = []
    for r in [float(x) for x in a.rates.split(",")]:
        a2 = argparse.Namespace(**vars(a)); a2.rate = r; a2.video = False
        res = cmd_run(a2, return_metrics=True)
        rows.append(res)
    out = RUNS / f"bench_{stamp()}"; out.mkdir(parents=True, exist_ok=True)
    (out / "bench.json").write_text(json.dumps(rows, indent=1, default=float))
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    rates = [x["throughput_beans_per_s"] for x in rows]
    fig, ax = plt.subplots(1, 3, figsize=(13, 3.8))
    ax[0].plot(rates, [100 * x["defect_removal"] for x in rows], "o-"); ax[0].set_title("defect removal %"); ax[0].set_ylim(0, 101)
    ax[1].plot(rates, [100 * x["good_yield_loss"] for x in rows], "o-", color="C3"); ax[1].set_title("good beans lost %")
    ax[2].plot(rates, [x["latency_ms"]["p99"] for x in rows], "o-", color="C2"); ax[2].axhline(rows[0]["latency_budget_ms"], ls="--", color="k"); ax[2].set_title("decision latency p99 (ms) vs budget")
    for x in ax: x.set_xlabel("beans / s"); x.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(out / "bench.png", dpi=130)
    print("wrote", out)


# ------------------------------------------------------------------------------------ viewer
def cmd_viewer(a):
    import mujoco.viewer
    P = PROFILES[a.profile]
    model = Model.load(MODELS / f"{a.profile}.joblib")
    sim = SorterSim(P, rate=a.rate, seed=a.seed)
    insp = Inspector(sim)
    ctrl = Controller(sim, insp, model, COMMERCIAL if a.policy == "commercial" else SPECIALTY)
    with mujoco.viewer.launch_passive(sim.model, sim.data) as v:
        v.opt.geomgroup[3] = 0
        v.cam.type = mujoco.mjtCamera.mjCAMERA_FIXED
        v.cam.fixedcamid = mujoco.mj_name2id(sim.model, mujoco.mjtObj.mjOBJ_CAMERA, "overview")
        i = 0
        while v.is_running():
            sim.step()
            if i % 2 == 0:
                frame, t = insp.capture()
                ctrl.on_frame(frame, t)
            if i % 10 == 0:
                v.sync()
            i += 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    def common(p):
        p.add_argument("--profile", default="green_arabica", choices=list(PROFILES))
        p.add_argument("--rate", type=float, default=2000)
        p.add_argument("--seed", type=int, default=0)
        p.add_argument("--seconds", type=float, default=6)
    p = sub.add_parser("preview"); common(p); p.set_defaults(fn=cmd_preview, seconds=1.5)
    p = sub.add_parser("train"); common(p); p.add_argument("--boost", type=float, default=5); p.set_defaults(fn=cmd_train, seconds=20, rate=900)
    for name, fn in (("run", cmd_run), ("bench", cmd_bench), ("viewer", cmd_viewer)):
        p = sub.add_parser(name); common(p)
        p.add_argument("--policy", default="specialty", choices=["specialty", "commercial"])
        p.add_argument("--threshold", type=float, default=0.5)
        p.add_argument("--no-anomaly", action="store_true")
        p.add_argument("--video", action="store_true")
        p.add_argument("--rates", default="500,1000,2000,3000")
        p.set_defaults(fn=fn)
    a = ap.parse_args()
    assets.build()
    a.fn(a)


if __name__ == "__main__":
    main()
