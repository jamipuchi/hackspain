"""CLI: preview | train | run | bench | viewer.

  ../.venv/bin/python run.py preview                       # PNG renders of the plant
  ../.venv/bin/python run.py train --profile green_arabica  # learn the blob classifier from sim ground truth
  ../.venv/bin/python run.py run --rate 2000 --seconds 8 --video
  ../.venv/bin/python run.py bench --rates 500,1000,2000,3000
  ../.venv/bin/mjpython run.py viewer --rate 1500          # live MuJoCo window (macOS needs mjpython)
"""
from __future__ import annotations

import argparse, json, time, datetime, hashlib
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path
import numpy as np
import cv2

import assets
from profiles import PROFILES
from scene import Layout
from rolling_scores import SETTLING_SECONDS
from sim import SorterSim, JET_FORCE
from vision import Inspector, draw_blobs
from classifier import Model, collect, train, label_blobs, MODELS
from controller import Controller, SPECIALTY, COMMERCIAL
from render import Overview, Video, hud
from evidence import save_samples

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
def _wilson(successes, total):
    if total == 0:
        return None
    z = 1.96
    p = successes / total
    centre = (p + z * z / (2 * total)) / (1 + z * z / total)
    half = z * np.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return [max(0.0, centre - half), min(1.0, centre + half)]


def metrics(sim, ctrl, warmup, t_end):
    P = sim.P
    bean_by_uid = getattr(sim, "bean_by_uid", {b.uid: b for b in sim.beans})
    fired_targets = getattr(sim, "fired_targets", set())
    fire_hits = getattr(sim, "fire_hits", set())
    cohort_end = t_end - SETTLING_SECONDS
    beans = [b for b in sim.beans if warmup <= b.spawn_t <= cohort_end]
    resolved = [b for b in beans if b.outcome is not None]
    per = {}
    for c in P.classes:
        bs = [b for b in beans if b.cls == c.name]
        n = len(bs)
        rej = sum(b.outcome == "reject" for b in bs)
        spill = sum(b.outcome == "spilled" for b in bs)
        tgt = sum(b.targeted for b in bs)
        hit = sum(b.jet_hits > 0 for b in bs)
        per[c.name] = dict(n=n, resolved=sum(b.outcome is not None for b in bs),
                           unresolved=sum(b.outcome is None for b in bs), rejected=rej, spilled=spill,
                           targeted=tgt, fired_target=sum(b.fired_target for b in bs), defect=c.defect, severity=c.severity,
                           jet_hit=hit, targeted_rejected=sum(b.targeted and b.outcome == "reject" for b in bs),
                           jet_hit_rejected=sum(b.jet_hits > 0 and b.outcome == "reject" for b in bs),
                           reject_rate=rej / n if n else None, target_rate=tgt / n if n else None)
    defects = [b for b in beans if b.defect and P.by_name(b.cls).severity in ctrl.pol.reject_severities]
    defect_uids = {b.uid for b in defects}
    keep = [b for b in beans if not (b.defect and P.by_name(b.cls).severity in ctrl.pol.reject_severities)]
    accepted = [b for b in beans if b.outcome == "accept"]
    rejected = [b for b in beans if b.outcome == "reject"]
    lat = np.array(ctrl.latency_ms) if ctrl.latency_ms else np.zeros(1)
    compute = np.array(getattr(ctrl, "compute_ms", []))
    dec = ctrl.decisions
    reject_decisions = [d for d in dec if d.reject]
    scheduled = [d for d in reject_decisions if d.scheduled]
    activated = [d for d in scheduled if d.tid in fired_targets]
    associated = [d for d in reject_decisions if d.target_uids]
    associated_activated = [d for d in associated if d.tid in fired_targets]
    associated_hit = [d for d in associated_activated
                      if any((d.tid, uid) in fire_hits for uid in d.target_uids)]
    associated_rejected = [d for d in associated_hit
                           if any((d.tid, uid) in fire_hits and
                                  bean_by_uid[uid].outcome == "reject" for uid in d.target_uids)]
    constituent_uids = {uid for d in associated for uid in d.target_uids}
    constituent_hits = {uid for d in associated_activated for uid in d.target_uids
                        if (d.tid, uid) in fire_hits}
    headroom = np.array([1000 * (d.t_fire - d.t_available) for d in reject_decisions])
    sim_span = t_end - warmup
    n_defect_rejected = sum(b.outcome == "reject" for b in defects)
    n_keep_rejected = sum(b.outcome == "reject" for b in keep)
    n_keep_accepted = sum(b.outcome == "accept" for b in keep)
    n_correct_action = n_defect_rejected + n_keep_accepted

    def bean_cohort(selected):
        ds = [b for b in selected if b.defect and P.by_name(b.cls).severity in ctrl.pol.reject_severities]
        defect_ids = {b.uid for b in ds}
        ks = [b for b in selected if b.uid not in defect_ids]
        return dict(n=len(selected), defects_to_remove=len(ds), keep=len(ks),
                    unresolved=sum(b.outcome is None for b in selected),
                    defects_rejected=sum(b.outcome == "reject" for b in ds),
                    keep_rejected=sum(b.outcome == "reject" for b in ks),
                    defect_removal=sum(b.outcome == "reject" for b in ds) / len(ds) if ds else None,
                    good_yield_loss=sum(b.outcome == "reject" for b in ks) / len(ks) if ks else None)

    camera_cohorts = {
        "single_only": bean_cohort([b for b in beans if b.camera_observations and not b.merged_observations]),
        "ever_merged": bean_cohort([b for b in beans if b.merged_observations]),
        "never_observed": bean_cohort([b for b in beans if not b.camera_observations]),
    }
    return dict(
        beans_evaluated=len(beans),
        beans_resolved=len(resolved), beans_unresolved=len(beans) - len(resolved),
        throughput_beans_per_s=len([b for b in sim.beans if warmup <= b.spawn_t <= t_end]) / sim_span,
        feed_kg_per_h=sum(b.mass for b in sim.beans if warmup <= b.spawn_t <= t_end) / sim_span * 3600,
        defect_removal=n_defect_rejected / max(len(defects), 1),
        physical_reject_recall=n_defect_rejected / max(len(defects), 1),
        physical_reject_precision=n_defect_rejected / max(len(rejected), 1),
        good_yield_loss=n_keep_rejected / max(len(keep), 1),
        good_false_eject_rate=n_keep_rejected / max(len(keep), 1),
        physical_reject_accuracy=n_correct_action / max(len(beans), 1),
        accept_purity_defects_per_1000=1000 * sum(b.uid in defect_uids for b in accepted) / max(len(accepted), 1),
        accept_purity_defects_per_1000_incoming=1000 * len(defects) / max(len(beans), 1),
        spilled_rate=sum(b.outcome == "spilled" for b in beans) / max(len(beans), 1),
        decisions=len(dec), reject_decisions=len(reject_decisions),
        scheduled_reject_decisions=len(scheduled), activated_reject_decisions=len(activated),
        associated_reject_decisions=len(associated), associated_activated=len(associated_activated),
        associated_jet_hit=len(associated_hit), associated_rejected=len(associated_rejected),
        associated_constituents=len(constituent_uids), own_pulse_hit_constituents=len(constituent_hits),
        scheduled_valves=sim.n_fired, fired_valves=getattr(sim, "n_activated", 0),
        late_decisions=sum(d.late for d in reject_decisions),
        obs_per_track_mean=float(np.mean([d.n_obs for d in dec])) if dec else 0,
        latency_ms=dict(p50=float(np.percentile(lat, 50)), p99=float(np.percentile(lat, 99)), max=float(lat.max())),
        measured_compute_ms=(dict(p50=float(np.percentile(compute, 50)), p99=float(np.percentile(compute, 99)),
                                  max=float(compute.max())) if len(compute) else None),
        latency_budget_ms=1000 * (sim.L.ej_x - sim.L.cam_x) / sim.L.belt_speed,
        deadline_headroom_ms=(dict(p01=float(np.percentile(headroom, 1)), p50=float(np.percentile(headroom, 50)),
                                   minimum=float(headroom.min())) if len(headroom) else None),
        pool_starved=sim.starved, frames=ctrl.frames,
        denominators=dict(eligible_beans=len(beans), resolved_beans=len(resolved), defects_to_remove=len(defects),
                          keep_beans=len(keep), accepted_beans=len(accepted), rejected_beans=len(rejected),
                          decisions=len(dec), reject_decisions=len(reject_decisions), frames=ctrl.frames,
                          cohort_start_s=warmup, cohort_end_s=cohort_end),
        intervals_95=dict(defect_removal=_wilson(n_defect_rejected, len(defects)),
                          physical_reject_recall=_wilson(n_defect_rejected, len(defects)),
                          physical_reject_precision=_wilson(n_defect_rejected, len(rejected)),
                          good_yield_loss=_wilson(n_keep_rejected, len(keep)),
                          good_false_eject_rate=_wilson(n_keep_rejected, len(keep)),
                          physical_reject_accuracy=_wilson(n_correct_action, len(beans))),
        camera_blobs=dict(getattr(ctrl, "blob_counts", {})),
        camera_bean_cohorts=camera_cohorts,
        keep_jet_outcomes=dict(any_jet_hit=sum(b.jet_hits > 0 for b in keep),
                               all_rejected=n_keep_rejected, denominator=len(keep),
                               note="all_rejected is an outcome count, not causal attribution to a jet"),
        per_class=per,
    )


def cmd_run(a, return_metrics=False):
    P = PROFILES[a.profile]
    L = Layout(n_nozzles=a.nozzles, split_z_drop=a.split_z_drop,
               n_ellipsoid=a.pool_ellipsoid, n_half=a.pool_half,
               n_box=a.pool_box, n_capsule=a.pool_capsule)
    model_path = MODELS / f"{a.profile}.joblib"
    model = Model.load(model_path)
    base_policy = COMMERCIAL if a.policy == "commercial" else SPECIALTY
    pol = replace(base_policy, threshold=a.threshold, anomaly=not a.no_anomaly,
                  base_pulse=a.base_pulse_ms / 1000,
                  induced_delay=a.controller_delay_ms / 1000,
                  fixed_latency=(a.fixed_controller_latency_ms / 1000
                                 if a.fixed_controller_latency_ms is not None else None),
                  target_nozzles=a.target_nozzles)
    sim = SorterSim(P, L, rate=a.rate, seed=a.seed)
    insp = Inspector(sim)
    ctrl = Controller(sim, insp, model, pol, jet_force=a.jet_force)
    ctrl.blob_counts = Counter()
    out = RUNS / (a.name or f"{stamp()}_{a.profile}_{int(a.rate)}")
    out.mkdir(parents=True, exist_ok=not bool(a.name))
    video = Video(out / "overview.mp4", fps=50) if a.video else None
    ov = Overview(sim) if a.video else None
    frame_every = 2                       # camera at 250 Hz sim time
    video_every = int(0.02 / sim.dt)
    n_steps = int(a.seconds / sim.dt)
    warmup = 0.8
    t0 = time.perf_counter(); n_dec_seen = 0; samples_saved = 0
    last_overlay = None
    samples = []
    sample_times = np.linspace(warmup + 0.2, max(warmup + 0.2, a.seconds - 0.8), 6)
    fires_by_track = {}
    for i in range(n_steps):
        sim.step()
        if i % frame_every == 0:
            frame, t = insp.capture()
            blobs, Pr, A, full, blob_tracks = ctrl.on_frame(frame, t)
            blobs.member_uids = insp.component_members(blobs)
            prediction = {}
            k = 0
            for b in range(blobs.n):
                if not full[b]:
                    continue
                pred_class = ctrl.classes[int(np.argmax(Pr[k]))]
                pred_reject = bool(Pr[k][ctrl.reject_mask].sum() >= pol.threshold or
                                   (pol.anomaly and A[k] > model.anomaly_thresh))
                prediction[b] = (pred_class, float(Pr[k].max()), pred_reject)
                members = np.unique(blobs.member_uids[b])
                ctrl.blob_counts["full_observations"] += 1
                ctrl.blob_counts["constituent_observations"] += len(members)
                if len(members) == 0:
                    ctrl.blob_counts["unmatched_observations"] += 1
                else:
                    group = "single" if len(members) == 1 else "merged"
                    ctrl.blob_counts[f"{group}_observations"] += 1
                    truth_reject = any(sim.bean_by_uid[int(uid)].defect and
                                       P.by_name(sim.bean_by_uid[int(uid)].cls).severity in pol.reject_severities
                                       for uid in members)
                    action = ("tp" if truth_reject else "fp") if pred_reject else \
                             ("fn" if truth_reject else "tn")
                    ctrl.blob_counts[f"{group}_action_{action}"] += 1
                    if len(members) == 1:
                        ctrl.blob_counts["single_class_correct"] += int(
                            pred_class == sim.bean_by_uid[int(members[0])].cls)
                    for uid in members:
                        bean = sim.bean_by_uid[int(uid)]
                        bean.camera_observations += 1
                        bean.merged_observations += int(len(members) >= 2)
                k += 1
            # evaluation hook: link fresh decisions to ground-truth beans (metrics only)
            new = ctrl.decisions[n_dec_seen:]; n_dec_seen = len(ctrl.decisions)
            if new:
                new_tids = {d.tid for d in new}
                for fire in sim.fires:
                    if fire.uid in new_tids:
                        fires_by_track.setdefault(fire.uid, []).append(fire)
                bodies, pos, _ = sim.active_state(rendered=True)
                for d in new:
                    if not d.reject:
                        continue
                    current_blob = np.where(blob_tracks == d.tid)[0]
                    if len(current_blob):
                        d.target_uids = tuple(int(uid) for uid in
                                              np.unique(blobs.member_uids[current_blob[0]]))
                    if not d.target_uids and len(bodies):
                        xp = d.x + d.v * (t - d.t_decided)
                        dd = (pos[:, 0] - xp) ** 2 + (pos[:, 1] - d.y) ** 2
                        j = int(np.argmin(dd))
                        if dd[j] < 0.008 ** 2:
                            d.target_uids = (sim.bean_of[bodies[j]].uid,)
                    if d.target_uids:
                        if d.scheduled:
                            for uid in d.target_uids:
                                sim.bean_by_uid[uid].targeted = True
            if (video or samples_saved < 6) and blobs.n and i % (frame_every * 5) == 0:
                labels = []; colors = []
                for b in range(blobs.n):
                    if full[b]:
                        c, confidence, rej = prediction[b]
                        d = next((x for x in new if x.tid == blob_tracks[b]), None)
                        state = " L" if d and d.late else (" S" if d and d.scheduled else "")
                        labels.append(f"{c} {confidence:.2f}{state}")
                        colors.append((255, 60, 60) if rej else (60, 255, 90))
                    else:
                        labels.append(""); colors.append((160, 160, 160))
                last_overlay = draw_blobs(frame, blobs, labels, colors)
                if samples_saved < 6 and t >= sample_times[samples_saved]:
                    members = [uids.copy() for uids in blobs.member_uids]
                    samples.append((frame.copy(), blobs, Pr.copy(), blob_tracks.copy(), members))
                    samples_saved += 1
        if video and i % video_every == 0:
            img = ov.frame("overview" if (sim.data.time % 8) < 5 else "discharge")
            if last_overlay is not None:
                strip = cv2.resize(last_overlay, (1240, int(1240 * last_overlay.shape[0] / last_overlay.shape[1])))
                img[720 - strip.shape[0] - 10:720 - 10, 20:20 + strip.shape[1]] = strip
            mres = metrics(sim, ctrl, warmup, sim.data.time) if sim.data.time > warmup + 0.7 else None
            lines = [f"t = {sim.data.time:5.2f} s   feed {a.rate:.0f} beans/s   belt {L.belt_speed:.1f} m/s   in transit {sim.n_active()}",
                     f"camera 250 fps  {L.cam_w}x{L.cam_h}  latency p99 {np.percentile(ctrl.latency_ms, 99) if ctrl.latency_ms else 0:.1f} ms / budget {1000 * (L.ej_x - L.cam_x) / L.belt_speed:.0f} ms   valves queued {sim.n_fired}"]
            if mres:
                lines.append(f"defect removal {100 * mres['defect_removal']:.1f}%   good yield loss {100 * mres['good_yield_loss']:.2f}%   "
                             f"accept purity {mres['accept_purity_defects_per_1000']:.1f} defects/1000 (in: {mres['accept_purity_defects_per_1000_incoming']:.0f})")
            hud(img, lines)
            video.add(img)
        if i % (n_steps // 10 or 1) == 0 and i:
            print(f"  t={sim.data.time:5.2f}s  wall={time.perf_counter() - t0:5.1f}s  active={sim.n_active()}  queued={sim.n_fired}  decisions={len(ctrl.decisions)}")
    wall = time.perf_counter() - t0
    for d in ctrl.decisions:
        if d.tid in sim.fired_targets:
            for uid in d.target_uids:
                sim.bean_by_uid[uid].fired_target = True
    res = metrics(sim, ctrl, warmup, sim.data.time)
    source_files = ("run.py", "controller.py", "sim.py", "vision.py", "evidence.py", "render.py", "scene.py")
    res.update(profile=a.profile, policy=pol.name, threshold=pol.threshold, rate=a.rate, seconds=a.seconds,
               simulation_seconds=float(sim.data.time), simulation_end_s=float(sim.data.time), seed=a.seed,
               wall_seconds=wall, wall_per_sim_second=wall / a.seconds,
               classifier_sha256=hashlib.sha256(model_path.read_bytes()).hexdigest(),
               source_sha256={name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
                              for name in source_files},
               config=dict(layout=asdict(L), policy=asdict(pol), jet_force_n=a.jet_force,
                           requested_rate_beans_per_s=a.rate),
               timing_semantics=dict(
                   availability="fixed minimum total latency" if pol.fixed_latency is not None else
                                "4 ms exposure/transfer + measured controller CPU + induced delay",
                   pulse_queue="t_on=max(nominal_on,t_available)", camera_backlog_modeled=False,
                   late_definition="reject result available more than 2 ms after predicted t_fire",
                   rendering_in_measured_latency=False, ground_truth_evaluation_in_measured_latency=False),
               decision_scope="all camera frames; bean outcome metrics use the explicit eligible cohort")
    (out / "metrics.json").write_text(json.dumps(res, indent=1, default=float))
    # decisions log
    with open(out / "decisions.csv", "w") as f:
        f.write("tid,t,x,y,v,cls,p_reject,anomaly,reject,scheduled,activated,late,headroom_ms,n_obs,pulse_ms,nozzles,target_uids,own_hit_uids\n")
        for d in ctrl.decisions:
            hit_uids = [uid for uid in d.target_uids if (d.tid, uid) in sim.fire_hits]
            f.write(f"{d.tid},{d.t_decided:.4f},{d.x:.4f},{d.y:.4f},{d.v:.3f},{d.cls},"
                    f"{d.probs[ctrl.reject_mask].sum():.3f},{d.anomaly:.2f},{int(d.reject)},"
                    f"{int(d.scheduled)},{int(d.tid in sim.fired_targets)},{int(d.late)},"
                    f"{1000 * (d.t_fire - d.t_available):.2f},{d.n_obs},{1000 * d.pulse:.1f},"
                    f"{'|'.join(map(str, d.nozzles))},{'|'.join(map(str, d.target_uids))},"
                    f"{'|'.join(map(str, hit_uids))}\n")
    if video:
        video.close(); ov.close()
    save_samples(samples, ctrl, sim, out, fires_by_track)
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
    name = a.name or f"rate-sweep-{stamp()}"
    for r in [float(x) for x in a.rates.split(",")]:
        a2 = argparse.Namespace(**vars(a)); a2.rate = r; a2.video = False
        a2.name = f"{name}/rate-{int(r)}"
        res = cmd_run(a2, return_metrics=True)
        rows.append(res)
    out = RUNS / name; out.mkdir(parents=True, exist_ok=True)
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

    def experiment_args(p):
        p.add_argument("--policy", default="specialty", choices=["specialty", "commercial"])
        p.add_argument("--threshold", type=float, default=0.5)
        p.add_argument("--no-anomaly", action="store_true")
        p.add_argument("--name", help="exact runs/ output directory name")
        p.add_argument("--controller-delay-ms", type=float, default=0,
                       help="extra simulated result-availability delay; does not sleep or model frame backlog")
        p.add_argument("--fixed-controller-latency-ms", type=float,
                       help="minimum total exposure-to-availability latency (real measured compute still wins)")
        p.add_argument("--jet-force", type=float, default=JET_FORCE)
        p.add_argument("--base-pulse-ms", type=float, default=3.0)
        p.add_argument("--target-nozzles", type=int, choices=(1, 2, 3),
                       help="override adaptive 1-3 valves opened per reject target")
        p.add_argument("--split-z-drop", type=float, default=Layout.split_z_drop)
        p.add_argument("--nozzles", type=int, default=Layout.n_nozzles)
        p.add_argument("--pool-ellipsoid", type=int, default=Layout.n_ellipsoid)
        p.add_argument("--pool-half", type=int, default=Layout.n_half)
        p.add_argument("--pool-box", type=int, default=Layout.n_box)
        p.add_argument("--pool-capsule", type=int, default=Layout.n_capsule)

    p = sub.add_parser("run"); common(p); experiment_args(p)
    p.add_argument("--video", action="store_true"); p.set_defaults(fn=cmd_run)
    p = sub.add_parser("bench"); common(p); experiment_args(p)
    p.add_argument("--rates", default="500,1000,2000,3000"); p.set_defaults(fn=cmd_bench)
    p = sub.add_parser("viewer"); common(p)
    p.add_argument("--policy", default="specialty", choices=["specialty", "commercial"])
    p.set_defaults(fn=cmd_viewer)
    a = ap.parse_args()
    assets.build()
    a.fn(a)


if __name__ == "__main__":
    main()
