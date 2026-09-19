"""Reproducible open-set product-transfer experiment.

The green-arabica model is loaded unchanged.  This harness defines three local,
never-trained classes, measures the anomaly threshold tradeoff on camera data,
then runs the same controller and plant to separate decisions from ejections.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import time
from collections import Counter, defaultdict
from dataclasses import asdict, replace
from pathlib import Path

import cv2
import numpy as np

import assets
from classifier import MODELS, Model
from controller import Controller, SPECIALTY
from profiles import BEAN, BOX, ELLIPSOID, ClassSpec, GREEN_ARABICA, Profile
from render import Overview, Video, hud
from run import metrics
from scene import Layout
from sim import SorterSim
from vision import Inspector, draw_blobs


HERE = Path(__file__).resolve().parent
DEFAULT_OUTPUT = HERE / "runs" / "generalization" / "openset"
MODEL_PATH = MODELS / "green_arabica.joblib"
UNKNOWN_NAMES = ("plastic_chip", "odd_colour", "wrong_size")
SOURCE_FILES = (
    "openset.py",
    "classifier.py",
    "controller.py",
    "profiles.py",
    "render.py",
    "run.py",
    "scene.py",
    "sim.py",
    "vision.py",
)


def openset_profile(good_prior: float = 0.70) -> Profile:
    """Copy the trained product profile and add local, never-trained objects."""
    classes = [replace(c, prior=good_prior if c.name == "good" else 0.0)
               for c in GREEN_ARABICA.classes]
    classes.extend([
        ClassSpec(
            "plastic_chip", 0.10, BOX,
            ((6.0, 8.0), (4.0, 6.0), (0.8, 1.4)),
            (0.05, 0.82, 0.95), 0.02, density=1050,
            defect=True, severity="foreign",
        ),
        ClassSpec(
            "odd_colour", 0.10, ELLIPSOID, BEAN,
            (0.92, 0.06, 0.68), 0.015, density=1150,
            defect=True, severity="foreign",
        ),
        ClassSpec(
            "wrong_size", 0.10, ELLIPSOID,
            ((7.2, 8.8), (5.0, 6.2), (3.4, 4.4)),
            (0.50, 0.60, 0.46), 0.04, density=1150, texture="good",
            defect=True, severity="foreign",
        ),
    ])
    return Profile("openset_generalization", GREEN_ARABICA.belt_rgb, classes)


def _wilson(successes: int, total: int) -> list[float] | None:
    if not total:
        return None
    z = 1.96
    p = successes / total
    centre = (p + z * z / (2 * total)) / (1 + z * z / total)
    half = z * np.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return [float(max(0.0, centre - half)), float(min(1.0, centre + half))]


def threshold_result(records: list[dict], threshold: float) -> dict:
    """Evaluate independent objects at one anomaly threshold."""
    good = [r for r in records if r["class"] == "good"]
    unknown = [r for r in records if r["class"] in UNKNOWN_NAMES]

    def count(rows, criterion):
        return sum(bool(criterion(r)) for r in rows)

    anomaly = lambda r: r["anomaly_max"] > threshold
    classifier = lambda r: r["p_reject_mean"] >= 0.5
    combined = lambda r: anomaly(r) or classifier(r)
    a_unknown, a_good = count(unknown, anomaly), count(good, anomaly)
    c_unknown, c_good = count(unknown, classifier), count(good, classifier)
    both_unknown, both_good = count(unknown, combined), count(good, combined)
    per_class = {}
    for name in UNKNOWN_NAMES:
        rows = [r for r in records if r["class"] == name]
        per_class[name] = {
            "n": len(rows),
            "anomaly_rejected": count(rows, anomaly),
            "anomaly_rejection_rate": count(rows, anomaly) / len(rows) if rows else None,
            "combined_rejected": count(rows, combined),
            "combined_rejection_rate": count(rows, combined) / len(rows) if rows else None,
        }
    return {
        "threshold": float(threshold),
        "denominators": {"good_objects": len(good), "unknown_objects": len(unknown)},
        "anomaly_detector_alone": {
            "unknown_rejected": a_unknown,
            "unknown_rejection_rate": a_unknown / len(unknown) if unknown else None,
            "unknown_rejection_interval_95": _wilson(a_unknown, len(unknown)),
            "good_false_ejects": a_good,
            "good_false_eject_rate": a_good / len(good) if good else None,
            "good_false_eject_interval_95": _wilson(a_good, len(good)),
        },
        "classifier_alone": {
            "unknown_rejected": c_unknown,
            "unknown_rejection_rate": c_unknown / len(unknown) if unknown else None,
            "good_false_ejects": c_good,
            "good_false_eject_rate": c_good / len(good) if good else None,
        },
        "combined_classifier_or_anomaly": {
            "unknown_rejected": both_unknown,
            "unknown_rejection_rate": both_unknown / len(unknown) if unknown else None,
            "good_false_ejects": both_good,
            "good_false_eject_rate": both_good / len(good) if good else None,
        },
        "per_unknown_class": per_class,
    }


def collect_camera_objects(profile: Profile, model: Model, seconds: float, rate: float,
                           seed: int) -> tuple[list[dict], list[dict], dict]:
    """Collect camera observations and aggregate the offline selected views by UID."""
    sim = SorterSim(profile, rate=rate, seed=seed)
    inspector = Inspector(sim)
    reject_mask = np.array([GREEN_ARABICA.by_name(c).defect for c in model.classes])
    observations = defaultdict(list)
    labels = {}
    coverage = defaultdict(set)
    raw_observations = []
    counts = Counter()
    n_steps = int(seconds / sim.dt)
    started = time.perf_counter()
    for i in range(n_steps):
        sim.step()
        if i % 2:
            continue
        frame, captured_t = inspector.capture()
        blobs = inspector.detect(frame, captured_t)
        members = inspector.component_members(blobs)
        full_indices = np.where(~blobs.partial)[0]
        probs, anomaly = model.predict(blobs.X[full_indices])
        counts["frames"] += 1
        counts["full_blob_observations"] += len(full_indices)
        for blob_index in range(blobs.n):
            uids = np.unique(members[blob_index])
            for uid_value in uids:
                uid = int(uid_value)
                coverage[uid].add("partial" if blobs.partial[blob_index] else
                                  "full_single" if len(uids) == 1 else "full_merged")
        for k, blob_index in enumerate(full_indices):
            uids = np.unique(members[blob_index])
            if len(uids) != 1:
                counts["merged_or_unmatched_observations"] += 1
                continue
            uid = int(uids[0])
            bean = sim.bean_by_uid[uid]
            if bean.cls != "good" and bean.cls not in UNKNOWN_NAMES:
                continue
            observations[uid].append((float(anomaly[k]), float(probs[k][reject_mask].sum())))
            labels[uid] = bean.cls
            raw_observations.append({
                "captured_t": float(captured_t),
                "blob_index": int(blob_index),
                "uid": uid,
                "class": bean.cls,
                "anomaly": float(anomaly[k]),
                "p_reject": float(probs[k][reject_mask].sum()),
                "selected_for_offline_sweep": True,
            })
            counts["single_object_observations"] += 1
    inspector.close()
    eligible = [bean for bean in sim.beans if bean.spawn_t <= sim.data.time - 0.6 and
                (bean.cls == "good" or bean.cls in UNKNOWN_NAMES)]
    eligible_uids = {bean.uid for bean in eligible}
    raw_observations = [row for row in raw_observations if row["uid"] in eligible_uids]
    records = []
    for uid, values in sorted(observations.items()):
        if uid not in eligible_uids:
            continue
        scores = np.asarray(values)
        records.append({
            "uid": uid,
            "class": labels[uid],
            "observations": len(values),
            "anomaly_max": float(scores[:, 0].max()),
            "p_reject_mean": float(scores[:, 1].mean()),
        })
    counts["independent_objects"] = len(records)
    counts["objects_by_class"] = dict(Counter(r["class"] for r in records))
    coverage_by_class = {}
    for name in ("good", *UNKNOWN_NAMES):
        beans = [bean for bean in eligible if bean.cls == name]
        selected = sum("full_single" in coverage[bean.uid] for bean in beans)
        merged_only = sum("full_single" not in coverage[bean.uid] and
                          "full_merged" in coverage[bean.uid] for bean in beans)
        partial_only = sum("full_single" not in coverage[bean.uid] and
                           "full_merged" not in coverage[bean.uid] and
                           "partial" in coverage[bean.uid] for bean in beans)
        unseen = sum(not coverage[bean.uid] for bean in beans)
        coverage_by_class[name] = {
            "eligible_spawned_objects": len(beans),
            "selected_full_single": selected,
            "merged_full_only": merged_only,
            "partial_only": partial_only,
            "never_in_any_detected_component": unseen,
            "selected_rate": selected / len(beans) if beans else None,
        }
    counts["eligible_population_coverage_by_class"] = coverage_by_class
    counts["wall_seconds"] = time.perf_counter() - started
    counts["simulation_seconds"] = float(sim.data.time)
    counts["rate_beans_per_s"] = rate
    counts["seed"] = seed
    counts["selection"] = (
        "Offline whole-passage curve: full, single-constituent blobs only; all views "
        "aggregated by physical UID using max anomaly and mean classifier probability. "
        "This is not the controller's camera-centre finalization rule."
    )
    return records, raw_observations, dict(counts)


def _write_object_csv(records: list[dict], path: Path) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=(
            "uid", "class", "observations", "anomaly_max", "p_reject_mean"))
        writer.writeheader()
        writer.writerows(records)


def _write_observation_csv(records: list[dict], path: Path) -> None:
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=(
            "captured_t", "blob_index", "uid", "class", "anomaly", "p_reject",
            "selected_for_offline_sweep"))
        writer.writeheader()
        writer.writerows(records)


def _plot_sweep(rows: list[dict], default_threshold: float, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    nearest = min(rows, key=lambda row: abs(row["threshold"] - default_threshold))
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    ax = axes[0]
    ax.plot(
        [100 * row["anomaly_detector_alone"]["good_false_eject_rate"] for row in rows],
        [100 * row["anomaly_detector_alone"]["unknown_rejection_rate"] for row in rows],
        "o-", ms=2.5, lw=1.4, label="anomaly detector alone",
    )
    ax.plot(
        [100 * row["combined_classifier_or_anomaly"]["good_false_eject_rate"] for row in rows],
        [100 * row["combined_classifier_or_anomaly"]["unknown_rejection_rate"] for row in rows],
        "-", lw=1.4, label="classifier OR anomaly",
    )
    ax.scatter(
        100 * nearest["classifier_alone"]["good_false_eject_rate"],
        100 * nearest["classifier_alone"]["unknown_rejection_rate"],
        marker="x", s=70, color="black", label="classifier alone",
    )
    ax.scatter(
        100 * nearest["anomaly_detector_alone"]["good_false_eject_rate"],
        100 * nearest["anomaly_detector_alone"]["unknown_rejection_rate"],
        s=55, color="C3", zorder=5, label=f"trained threshold {default_threshold:.2f}",
    )
    ax.set(xlabel="good objects flagged (%)", ylabel="unknown objects flagged (%)",
           title="Offline whole-passage camera tradeoff")
    ax.set_xscale("symlog", linthresh=0.5)
    ax.set_xticks((0, 0.5, 1, 2, 5, 10, 25, 50, 100))
    ax.get_xaxis().set_major_formatter("{x:g}")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8)

    ax = axes[1]
    thresholds = [row["threshold"] for row in rows]
    for name in UNKNOWN_NAMES:
        ax.plot(thresholds,
                [100 * rate if (rate := row["per_unknown_class"][name]["anomaly_rejection_rate"])
                 is not None else np.nan for row in rows],
                label=name.replace("_", " "))
    ax.plot(thresholds,
            [100 * row["anomaly_detector_alone"]["good_false_eject_rate"] for row in rows],
            color="black", ls="--", label="good false eject")
    ax.axvline(default_threshold, color="C3", ls=":", label="trained threshold")
    ax.set_xscale("symlog", linthresh=5)
    ax.set(xlabel="Mahalanobis threshold", ylabel="objects flagged (%)",
           title="Offline detector score by unseen type")
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8)
    fig.suptitle("Green-arabica model unchanged; selected singleton objects, not controller decisions")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _validate_sweep_records(records: list[dict]) -> None:
    if not any(record["class"] == "good" for record in records):
        raise RuntimeError("camera sweep produced no eligible selected good objects")
    if not any(record["class"] in UNKNOWN_NAMES for record in records):
        raise RuntimeError("camera sweep produced no eligible selected unknown objects")


def run_sweep(args, profile: Profile, model: Model) -> dict:
    print(f"camera sweep: {args.sweep_seconds:g}s at {args.sweep_rate:g} beans/s")
    records, observations, collection = collect_camera_objects(
        profile, model, args.sweep_seconds, args.sweep_rate, args.seed)
    _validate_sweep_records(records)
    scores = np.array([r["anomaly_max"] for r in records])
    thresholds = np.unique(np.r_[
        np.quantile(scores, np.linspace(0.0, 1.0, 101)),
        model.anomaly_thresh,
    ])
    rows = [threshold_result(records, threshold) for threshold in thresholds]
    default = min(rows, key=lambda row: abs(row["threshold"] - model.anomaly_thresh))
    result = {
        "model_anomaly_threshold": float(model.anomaly_thresh),
        "collection": collection,
        "default_threshold_result": default,
        "threshold_rows": rows,
        "curve_semantics": (
            "Offline whole-passage criterion over selected full singleton objects. "
            "Max anomaly and mean classifier probability use all recorded views, unlike "
            "the controller, which finalizes around camera centre or after misses."
        ),
        "caveat": (
            "Simulated camera observations, not real-camera validation. Conditional curve "
            "excludes merged-only, partial-only, and never-detected objects; eligible population "
            "coverage is reported separately."
        ),
    }
    _write_object_csv(records, args.output / "camera_objects.csv")
    _write_observation_csv(observations, args.output / "camera_observations.csv")
    (args.output / "threshold_sweep.json").write_text(json.dumps(result, indent=1))
    _plot_sweep(rows, model.anomaly_thresh, args.output / "threshold_sweep.png")
    print("camera objects:", collection["objects_by_class"])
    return result


def _link_decisions(sim, blobs, blob_tracks, decisions, captured_t):
    bodies, positions, _ = sim.active_state(rendered=True)
    for decision in decisions:
        current = np.where(blob_tracks == decision.tid)[0]
        if len(current):
            decision.target_uids = tuple(int(uid) for uid in
                                         np.unique(blobs.member_uids[current[0]]))
        if not decision.target_uids and len(bodies):
            predicted_x = decision.x + decision.v * (captured_t - decision.t_decided)
            distance = ((positions[:, 0] - predicted_x) ** 2 +
                        (positions[:, 1] - decision.y) ** 2)
            nearest = int(np.argmin(distance))
            if distance[nearest] < 0.008 ** 2:
                decision.target_uids = (sim.bean_of[bodies[nearest]].uid,)
        if decision.reject and decision.scheduled:
            for uid in decision.target_uids:
                sim.bean_by_uid[uid].targeted = True


def _decision_summary(sim, ctrl, warmup: float, end_t: float, threshold: float) -> dict:
    eligible = {b.uid: b for b in sim.beans if warmup <= b.spawn_t <= end_t - 0.6}
    by_uid = defaultdict(list)
    for decision in ctrl.decisions:
        for uid in decision.target_uids:
            if uid in eligible:
                by_uid[uid].append(decision)

    result = {}
    for group, names in (("good", {"good"}), ("unknown", set(UNKNOWN_NAMES))):
        beans = [bean for bean in eligible.values() if bean.cls in names]
        anomaly_uids, classifier_uids, combined_uids = set(), set(), set()
        overlap_uids, exclusive_anomaly_uids = set(), set()
        for bean in beans:
            decisions = by_uid.get(bean.uid, [])
            anomaly = any(d.anomaly > threshold for d in decisions)
            classifier = any(float(d.probs[ctrl.reject_mask].sum()) >= ctrl.pol.threshold
                             for d in decisions)
            if anomaly:
                anomaly_uids.add(bean.uid)
            if classifier:
                classifier_uids.add(bean.uid)
            if anomaly or classifier:
                combined_uids.add(bean.uid)
            if anomaly and classifier:
                overlap_uids.add(bean.uid)
            if anomaly and not classifier:
                exclusive_anomaly_uids.add(bean.uid)
        n = len(beans)
        result[group] = {
            "eligible_physical_objects": n,
            "objects_with_linked_decision": sum(bean.uid in by_uid for bean in beans),
            "anomaly_detector_alone_flagged": len(anomaly_uids),
            "anomaly_detector_alone_rate": len(anomaly_uids) / n if n else None,
            "classifier_alone_flagged": len(classifier_uids),
            "classifier_alone_rate": len(classifier_uids) / n if n else None,
            "classifier_and_anomaly_overlap": len(overlap_uids),
            "anomaly_exclusive": len(exclusive_anomaly_uids),
            "combined_command_criterion": len(combined_uids),
            "combined_command_rate": len(combined_uids) / n if n else None,
            "physically_targeted": sum(bean.targeted for bean in beans),
            "own_or_collateral_jet_hit": sum(bean.jet_hits > 0 for bean in beans),
            "actual_rejected": sum(bean.outcome == "reject" for bean in beans),
            "actual_rejection_rate": (sum(bean.outcome == "reject" for bean in beans) / n
                                      if n else None),
            "spilled": sum(bean.outcome == "spilled" for bean in beans),
            "unresolved": sum(bean.outcome is None for bean in beans),
        }
    result["semantics"] = {
        "anomaly_detector_alone": "Mahalanobis criterion evaluated without classifier probability",
        "combined_command_criterion": "classifier probability >= 0.5 OR Mahalanobis score above threshold",
        "actual_rejected": "ground-truth object crossed the physical reject side of the splitter",
        "linkage_limit": "criterion rates use objects linked to decisions; unlinked eligible objects count as unflagged",
    }
    return result


def _transcode_h264(source: Path, destination: Path) -> dict:
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(source),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(destination),
    ], check=True)
    capture = cv2.VideoCapture(str(destination))
    if not capture.isOpened():
        raise RuntimeError(f"could not open transcoded video: {destination}")
    fourcc_value = int(capture.get(cv2.CAP_PROP_FOURCC))
    probe = {
        "codec_tag": "".join(chr((fourcc_value >> (8 * i)) & 0xFF) for i in range(4)),
        "width": int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        "fps": float(capture.get(cv2.CAP_PROP_FPS)),
        "frames": int(capture.get(cv2.CAP_PROP_FRAME_COUNT)),
    }
    capture.release()
    if probe["codec_tag"].lower() not in {"avc1", "h264"}:
        raise RuntimeError(f"expected H.264 video, got codec tag {probe['codec_tag']!r}")
    source.unlink()
    return probe


def _demo_unknown_record(bean, decision, reject_mask, anomaly_threshold, fired_targets,
                         fire_hits) -> dict:
    """Build JSON-native evidence for the physically rejected object shown in the demo."""
    return {
        "uid": int(bean.uid),
        "class": bean.cls,
        "anomaly_score": float(decision.anomaly),
        "anomaly_threshold": float(anomaly_threshold),
        "classifier_reject_probability": float(decision.probs[reject_mask].sum()),
        "combined_command": bool(decision.reject),
        "scheduled": bool(decision.scheduled),
        "activated": bool(decision.tid in fired_targets),
        "own_pulse_hit": bool((decision.tid, bean.uid) in fire_hits),
        "any_jet_hit": bool(bean.jet_hits > 0),
        "physical_outcome": bean.outcome,
        "selection": "HUD-tracked odd-colour unknown with own controller-pulse hit and physical reject",
    }


def run_physics(args, profile: Profile, source_model: Model) -> dict:
    model = replace(source_model, anomaly_thresh=args.anomaly_threshold)
    layout = Layout()
    policy = replace(SPECIALTY, threshold=0.5, fixed_latency=args.fixed_latency_ms / 1000)
    sim = SorterSim(profile, layout, rate=args.physics_rate, seed=args.seed + 1)
    inspector = Inspector(sim)
    controller = Controller(sim, inspector, model, policy, jet_force=args.jet_force)
    controller.blob_counts = Counter()
    overview = Overview(sim) if not args.no_video else None
    intermediate = args.output / "overview_mpeg4.mp4"
    video = Video(intermediate, fps=50) if not args.no_video else None
    n_steps = int(args.physics_seconds / sim.dt)
    video_every = int(0.02 / sim.dt)
    warmup = 0.8
    n_decisions_seen = 0
    unknown_decisions = []
    unknown_trajectories = defaultdict(list)
    demo_candidate = None
    last_overlay = None
    started = time.perf_counter()

    print(f"physics video: {args.physics_seconds:g}s at {args.physics_rate:g} beans/s")
    for i in range(n_steps):
        sim.step()
        if i % 2 == 0:
            frame, captured_t = inspector.capture()
            blobs, probabilities, anomaly, full, blob_tracks = controller.on_frame(frame, captured_t)
            blobs.member_uids = inspector.component_members(blobs)
            labels, colors = [], []
            probability_index = 0
            for blob_index in range(blobs.n):
                if not full[blob_index]:
                    labels.append("")
                    colors.append((160, 160, 160))
                    continue
                members = np.unique(blobs.member_uids[blob_index])
                unknown = [int(uid) for uid in members
                           if sim.bean_by_uid[int(uid)].cls in UNKNOWN_NAMES]
                if unknown:
                    name = sim.bean_by_uid[unknown[0]].cls.replace("_", " ")
                    labels.append(f"UNKNOWN {name} A={anomaly[probability_index]:.1f}")
                    colors.append((255, 60, 255))
                else:
                    labels.append(f"A={anomaly[probability_index]:.1f}")
                    colors.append((60, 255, 90))
                for uid in members:
                    bean = sim.bean_by_uid[int(uid)]
                    bean.camera_observations += 1
                    bean.merged_observations += int(len(members) >= 2)
                probability_index += 1
            if video and blobs.n:
                last_overlay = draw_blobs(frame, blobs, labels, colors)
            new_decisions = controller.decisions[n_decisions_seen:]
            n_decisions_seen = len(controller.decisions)
            _link_decisions(sim, blobs, blob_tracks, new_decisions, captured_t)
            for decision in new_decisions:
                unknown_uids = [uid for uid in decision.target_uids
                                if sim.bean_by_uid[uid].cls in UNKNOWN_NAMES]
                if unknown_uids:
                    unknown_decisions.append((decision, unknown_uids[0]))
                    demo_uid = next((uid for uid in unknown_uids
                                     if sim.bean_by_uid[uid].cls == "odd_colour"), None)
                    if demo_candidate:
                        old_decision, old_uid = demo_candidate
                        old_bean = sim.bean_by_uid[old_uid]
                        old_succeeded = (old_bean.outcome == "reject" and
                                         (old_decision.tid, old_uid) in sim.fire_hits)
                        old_pending = old_bean.outcome is None
                    else:
                        old_succeeded = old_pending = False
                    if decision.reject and demo_uid is not None and not old_succeeded and not old_pending:
                        demo_candidate = (decision, demo_uid)
        if video:
            for _, uid in unknown_decisions:
                bean = sim.bean_by_uid[uid]
                if sim.bean_of.get(bean.body) is bean:
                    position = sim.data.qpos[sim.body_qpos[bean.body]:sim.body_qpos[bean.body] + 3]
                    unknown_trajectories[uid].append((float(sim.data.time), *map(float, position)))
        if video and i % video_every == 0:
            featured = demo_candidate
            highlighted_geom = None
            original_rgba = None
            if featured:
                featured_bean = sim.bean_by_uid[featured[1]]
                if sim.bean_of.get(featured_bean.body) is featured_bean:
                    highlighted_geom = sim.body_geom[featured_bean.body]
                    original_rgba = sim.model.geom_rgba[highlighted_geom].copy()
                    sim.model.geom_rgba[highlighted_geom] = (1.0, 1.0, 0.0, 1.0)
            camera = "overview" if sim.data.time % 4.0 < 2.8 else "discharge"
            image = overview.frame(camera)
            if highlighted_geom is not None:
                sim.model.geom_rgba[highlighted_geom] = original_rgba
            if featured:
                decision, uid = featured
                bean = sim.bean_by_uid[uid]
                classifier_score = float(decision.probs[controller.reject_mask].sum())
                anomaly_flag = decision.anomaly > model.anomaly_thresh
                classifier_flag = classifier_score >= policy.threshold
                status = (
                    f"UNKNOWN uid {uid} {bean.cls.replace('_', ' ')} | score {decision.anomaly:.2f} "
                    f"> {model.anomaly_thresh:.2f}: {'YES' if anomaly_flag else 'NO'}"
                )
                action = (
                    f"anomaly-only {'flag' if anomaly_flag else 'pass'} | classifier "
                    f"p(reject) {classifier_score:.2f} | combined "
                    f"{'REJECT' if decision.reject else 'KEEP'} | own jet hit "
                    f"{'YES' if (decision.tid, uid) in sim.fire_hits else 'pending/no'} | "
                    f"physics {bean.outcome or 'pending'}"
                )
            else:
                status = "UNKNOWN score: waiting for first camera decision"
                action = "anomaly-only | classifier | combined command | physical outcome"
            known_unknown = [b for b in sim.beans if b.cls in UNKNOWN_NAMES and b.outcome is not None]
            rejected = sum(b.outcome == "reject" for b in known_unknown)
            lines = [
                "OPEN-SET TEST | unchanged green-arabica model | simulated evaluation truth",
                f"t {sim.data.time:5.2f}s | feed {args.physics_rate:.0f}/s | belt {layout.belt_speed:.1f} m/s | fixed latency floor {args.fixed_latency_ms:.0f} ms",
                status,
                action,
                f"resolved unknown physics: {rejected}/{len(known_unknown)} rejected | valves activated {sim.n_activated}",
                "HUD tracks one reject-command unknown until outcome; yellow highlight is render-only",
            ]
            hud(image, lines, scale=0.62)
            if last_overlay is not None:
                strip = cv2.resize(last_overlay, (1240, int(1240 * last_overlay.shape[0] /
                                                           last_overlay.shape[1])))
                image[720 - strip.shape[0] - 10:720 - 10, 20:20 + strip.shape[1]] = strip
            video.add(image)
        if i and i % max(n_steps // 10, 1) == 0:
            print(f"  t={sim.data.time:5.2f}s decisions={len(controller.decisions)} active={sim.n_active()}")

    wall_seconds = time.perf_counter() - started
    for decision in controller.decisions:
        if decision.tid in sim.fired_targets:
            for uid in decision.target_uids:
                sim.bean_by_uid[uid].fired_target = True
    physical = metrics(sim, controller, warmup, sim.data.time)
    attribution = _decision_summary(sim, controller, warmup, sim.data.time,
                                    model.anomaly_thresh)
    inspector.close()
    video_probe = None
    demo_uid = None
    if video:
        video.close()
        overview.close()
        video_probe = _transcode_h264(intermediate, args.output / "overview_h264.mp4")
        rejected_examples = [(decision, uid) for decision, uid in unknown_decisions
                             if sim.bean_by_uid[uid].cls == "odd_colour" and
                             sim.bean_by_uid[uid].outcome == "reject" and
                             (decision.tid, uid) in sim.fire_hits]
        displayed_reject = (demo_candidate if demo_candidate and
                            sim.bean_by_uid[demo_candidate[1]].outcome == "reject" and
                            (demo_candidate[0].tid, demo_candidate[1]) in sim.fire_hits else None)
        selected_demo = displayed_reject or (rejected_examples[0] if rejected_examples else None)
        if selected_demo:
            demo_decision, demo_uid = selected_demo
            with (args.output / "demo_unknown_trajectory.csv").open("w", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(("t", "x", "y", "z"))
                writer.writerows(unknown_trajectories[demo_uid])
            demo_record = _demo_unknown_record(
                sim.bean_by_uid[demo_uid], demo_decision, controller.reject_mask,
                model.anomaly_thresh, sim.fired_targets, sim.fire_hits)
            (args.output / "demo_unknown.json").write_text(json.dumps(demo_record, indent=1))
    result = {
        "profile": profile.name,
        "model_profile": source_model.meta.get("profile"),
        "anomaly_threshold": model.anomaly_thresh,
        "rate_beans_per_s": args.physics_rate,
        "simulation_seconds": float(sim.data.time),
        "seed": args.seed + 1,
        "wall_seconds": wall_seconds,
        "wall_per_sim_second": wall_seconds / args.physics_seconds,
        "controller_policy": asdict(policy),
        "jet_force_n": args.jet_force,
        "decision_attribution": attribution,
        "physical_metrics": physical,
        "video_probe": video_probe,
        "video_demo_uid": demo_uid,
        "timing_semantics": {
            "availability": (
                f"{args.fixed_latency_ms:g} ms minimum total exposure-to-availability; "
                "slower measured compute wins"
            ),
            "camera_backlog_modeled": False,
            "rendering_in_measured_latency": False,
            "ground_truth_linkage_in_measured_latency": False,
        },
        "caveat": "All objects and pixels are simulated. Physical rejection is MuJoCo outcome capture, not hardware validation.",
    }
    (args.output / "physics_metrics.json").write_text(json.dumps(result, indent=1, default=float))
    return result


def _hashes() -> dict:
    return {
        "model": {str(MODEL_PATH.relative_to(HERE)): hashlib.sha256(MODEL_PATH.read_bytes()).hexdigest()},
        "sources": {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
                    for name in SOURCE_FILES},
    }


def _profile_manifest(profile: Profile) -> dict:
    return {
        "name": profile.name,
        "trained_model_classes": GREEN_ARABICA.names,
        "never_trained_classes": list(UNKNOWN_NAMES),
        "classes": [asdict(spec) for spec in profile.classes],
        "note": "Profile objects are constructed locally; profiles.py and the trained joblib are not modified.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("sweep", "physics", "all"), nargs="?", default="all")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--seed", type=int, default=41)
    parser.add_argument("--sweep-seconds", type=float, default=8.0)
    parser.add_argument("--sweep-rate", type=float, default=300.0)
    parser.add_argument("--physics-seconds", type=float, default=8.0)
    parser.add_argument("--physics-rate", type=float, default=100.0)
    parser.add_argument("--fixed-latency-ms", type=float, default=60.0)
    parser.add_argument("--jet-force", type=float, default=0.06,
                        help="Explicit demo candidate; plant default remains 0.09 N")
    parser.add_argument("--no-video", action="store_true",
                        help="Skip overview rendering and H.264 output for numerical threshold runs")
    parser.add_argument("--anomaly-threshold", type=float,
                        help="Physics threshold; defaults to the unchanged model threshold")
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    assets.build()
    experiment_hashes = _hashes()
    model = Model.load(MODEL_PATH)
    if args.anomaly_threshold is None:
        args.anomaly_threshold = float(model.anomaly_thresh)
    profile = openset_profile()
    sweep = run_sweep(args, profile, model) if args.command in ("sweep", "all") else None
    physics = run_physics(args, profile, model) if args.command in ("physics", "all") else None
    ending_hashes = _hashes()
    manifest = {
        "command": " ".join(__import__("sys").argv),
        "profile": _profile_manifest(profile),
        "hashes": experiment_hashes,
        "ending_hashes": ending_hashes,
        "source_changed_during_run": experiment_hashes != ending_hashes,
        "sweep_summary": sweep["default_threshold_result"] if sweep else None,
        "physics_summary": physics["decision_attribution"] if physics else None,
        "limitations": [
            "Synthetic camera and MuJoCo physics only; no real-camera or hardware claim.",
            "Sweep excludes merged and partial blobs and aggregates repeated views by physical object UID.",
            "Sweep curve is an offline whole-passage aggregation, not the controller decision rule; population coverage is separate.",
            "The threshold was trained from green good objects; no open-set examples retrain or alter the model.",
            (f"The physics video uses {args.jet_force:g} N jet force and a "
             f"{args.fixed_latency_ms:g} ms latency floor; plant defaults are unchanged."),
        ],
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=1, default=float))
    if experiment_hashes != ending_hashes:
        raise RuntimeError("source or model changed during experiment; outputs are marked invalid")
    print("wrote", args.output)


if __name__ == "__main__":
    main()
