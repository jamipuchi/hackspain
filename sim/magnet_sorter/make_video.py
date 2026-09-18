"""Build the dashboard video for a recorded run: Cycles-rendered 3D view + what GPT-6 saw and said.

    cd ~/robotics/magnet_sorter && ../.venv/bin/python make_video.py runs/<timestamp> [--samples 32] [--renderer cycles|mujoco] [--fps-div 1]

Layout 1920x1080:
  left   : cinematic Cycles render of the bench (arm, magnet, pieces) following the trajectory
  right  : the phone photo GPT-6 received (then with its detections), INPUT text, OUTPUT plan with
           the op in progress highlighted
  bottom : phase / score / serial link to the Arduino / feedback lines going back to GPT-6
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))

# the run's meta.json says which build it was recorded with; scene_def must know before import
for _a in sys.argv[1:]:
    _meta = Path(_a) / "meta.json"
    if _meta.exists():
        import json as _json
        import os as _os

        _os.environ["SORTER_BUILD"] = _json.loads(_meta.read_text()).get("build", "hobby_v1")
        break
import scene_def as sd  # noqa: E402

W, H = 1920, 1080
FPS = 25
SCENE_W, SCENE_H = 1200, 900
CAM_W, CAM_H = 640, 480
PANEL_X = SCENE_W + 20
PANEL_W = W - PANEL_X - 20
BAR_Y = SCENE_H + 20

BG = (24, 26, 30)
PANEL = (36, 39, 45)
TXT = (230, 232, 236)
DIM = (150, 155, 165)
ACCENT = (90, 200, 120)
WARN = (235, 120, 90)
BLUE = (90, 160, 240)
FONT = "/System/Library/Fonts/Helvetica.ttc"
MONO = "/System/Library/Fonts/Menlo.ttc"


def font(size, mono=False, bold=False):
    try:
        return ImageFont.truetype(MONO if mono else FONT, size, index=1 if bold and not mono else 0)
    except OSError:
        return ImageFont.load_default()


F_TITLE, F_H, F_T, F_S = font(22, bold=True), font(20, bold=True), font(18), font(15)
TITLE_BY_BUILD = {
    "hobby_v1": "GPT-6 sorts ferrous hardware: servo arm + electromagnet",
    "taras_v1": "GPT-6 sorts screws, nuts, washers · wooden servo arm",
    "theker_v1": "GPT-6 sorts screws, nuts, washers · THEKER build",
    "taras_conveyor": "GPT-6 sorts from a conveyor: wooden servo arm + electromagnet",
    "taras_kitting": "GPT-6 assembles hardware kits: wooden servo arm + electromagnet",
    "taras_grading": "GPT-6 grades screws by length and rust: wooden servo arm + electromagnet",
}
F_M, F_MS = font(15, mono=True), font(13, mono=True)


class State:
    """UI state reconstructed from the event log up to a given frame."""

    def __init__(self, run: Path, events: list[dict], serial_lines: list[str]):
        self.run = run
        self.events = events
        self.serial = serial_lines
        self.photos: dict[str, np.ndarray] = {}

    def photo(self, name: str) -> np.ndarray | None:
        if name not in self.photos:
            img = cv2.imread(str(self.run / name))
            self.photos[name] = None if img is None else cv2.resize(img, (CAM_W, CAM_H), interpolation=cv2.INTER_AREA)
        return self.photos[name]

    def at(self, frame: int) -> dict:
        st = dict(phase="starting", round=0, cam=None, cam_caption="waiting for the first phone photo", input_text="", plan=None, plan_s=None, cost=0.0, current_op=None, op_results={}, feedback=[], score=None, calibration=None)
        for e in self.events:
            if e["frame"] > frame:
                break
            k = e["kind"]
            if k == "phase":
                st["phase"] = e["text"]
            elif k == "calibration":
                st["calibration"] = e
            elif k == "photo":
                st["round"] = e["round"]
                st["cam"] = e["file"]
                st["cam_caption"] = f"round {e['round']}: phone photo as sent to GPT-6 (Cycles, {e.get('render_s', 0):.0f} s render)"
                st["plan"] = None
                st["current_op"] = None
                st["op_results"] = {}
            elif k == "plan":
                st["plan"] = e["plan"]
                st["plan_s"] = e["seconds"]
                st["cost"] = e["cost_usd"]
                st["input_text"] = e.get("input_text", "")
                st["cam"] = e["file"]
                st["cam_caption"] = f"round {e['round']}: GPT-6's detections (red = it thinks ferrous) and pick order"
            elif k == "op_start":
                st["current_op"] = e["index"]
            elif k == "op_done":
                st["current_op"] = None
                st["op_results"][e["index"]] = e["ok"]
                st["feedback"].append(("✓ " if e["ok"] else "✗ ") + e["detail"])
            elif k == "score":
                st["score"] = (e["binned"], e["total"])
            elif k == "agent_photo":
                st["cam"] = e["files"][0] if e.get("files") else st["cam"]
                st["cam2"] = e["files"][1] if len(e.get("files", [])) > 1 else None
                st["cam_caption"] = "initial survey photos sent to GPT-6"
                st["phase"] = "GPT-6 surveys the parts"
                st["photos"] = st.get("photos", 0) + len(e.get("files", []))
            elif k == "tool_call":
                st["phase"] = f"GPT-6 calls {e['name']}({', '.join(f'{k2}={v2}' for k2, v2 in e['args'].items())})"[:84]
                st["agent_calls"] = st.get("agent_calls", []) + [f"{e['step']:>2}. {e['name']}({', '.join(f'{v2}' for v2 in e['args'].values())})"[:76]]
                st["current_op"] = e["step"]
                st["steps"] = e["step"]
                st["latency"] = e.get("latency_s")
                if e["name"] == "note_parts":
                    st["inventory"] = e["args"].get("parts", [])
                counts = st.setdefault("counts", {})
                counts[e["name"]] = counts.get(e["name"], 0) + 1
            elif k == "tool_result":
                if e.get("files"):
                    st["cam"] = e["files"][0]
                    st["cam2"] = e["files"][1] if len(e["files"]) > 1 else st.get("cam2")
                    st["cam_caption"] = f"photo after step {e['step']} ({e['name']}) as sent to GPT-6"
                    st["photos"] = st.get("photos", 0) + len(e["files"])
                st["feedback"].append(("✓ " if e["ok"] else "✗ ") + f"{e['name']}: {e['text']}")
                st["input_text"] = f"tool result of step {e['step']}: {e['text'][:120]}"
                if e["name"] in ("pick_at", "place_in"):
                    st["picks"] = st.get("picks", 0) + (1 if e["name"] == "pick_at" else 0)
                    if e["name"] == "place_in":
                        st["places_ok"] = st.get("places_ok", 0) + (1 if e["ok"] else 0)
                        st["places"] = st.get("places", 0) + 1
            elif k == "agent_text":
                st["feedback"].append("GPT-6: " + e["text"][:110])
        return st


def label(draw, x, y, text, f, fill, shadow=False):
    if shadow:
        draw.text((x + 1, y + 1), text, font=f, fill=(0, 0, 0))
    draw.text((x, y), text, font=f, fill=fill)


def compose(scene_bgr: np.ndarray, st: dict, frame_i: int, serial_upto: int, serial_lines: list[str], magnet_on: bool, planner: str, total_frames: int) -> np.ndarray:
    canvas = np.full((H, W, 3), BG, np.uint8)
    canvas[0:SCENE_H, 0:SCENE_W] = cv2.resize(scene_bgr, (SCENE_W, SCENE_H)) if scene_bgr.shape[:2] != (SCENE_H, SCENE_W) else scene_bgr
    img = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(img)

    label(d, 16, 12, f"BENCH  ·  {sd.CFG.title}"[:96], F_H, TXT, shadow=True)
    label(d, 16, SCENE_H - 34, f"ELECTROMAGNET {'ON' if magnet_on else 'off'}     t = {frame_i / FPS:5.1f} s", F_M, ACCENT if magnet_on else DIM, shadow=True)

    # camera panel
    cam_panel_h = CAM_H + 74
    d.rectangle([PANEL_X, 0, W - 20, cam_panel_h], fill=PANEL)
    label(d, PANEL_X + 14, 12, "WHAT GPT-6 SEES  ·  phone camera(s), ArUco-calibrated", F_H, TXT)
    box = (PANEL_X + (PANEL_W - CAM_W) // 2, 44)
    if st["cam"] is not None:
        photo = st_photos.photo(st["cam"])
        if photo is not None:
            img.paste(Image.fromarray(cv2.cvtColor(photo, cv2.COLOR_BGR2RGB)), box)
    else:
        d.rectangle([box[0], box[1], box[0] + CAM_W, box[1] + CAM_H], fill=(20, 20, 24))
    if st.get("cam2"):
        thumb = st_photos.photo(st["cam2"])
        if thumb is not None:
            tw, th = 200, 150
            small = cv2.resize(thumb, (tw, th), interpolation=cv2.INTER_AREA)
            img.paste(Image.fromarray(cv2.cvtColor(small, cv2.COLOR_BGR2RGB)), (box[0] + CAM_W - tw - 6, box[1] + CAM_H - th - 6))
            d.rectangle([box[0] + CAM_W - tw - 6, box[1] + CAM_H - th - 6, box[0] + CAM_W - 6, box[1] + CAM_H - 6], outline=(255, 255, 255))
    label(d, PANEL_X + 14, 44 + CAM_H + 6, st["cam_caption"][:88], F_S, DIM)

    # input / output
    y = cam_panel_h + 12
    d.rectangle([PANEL_X, y, W - 20, SCENE_H], fill=PANEL)
    label(d, PANEL_X + 14, y + 10, f"INPUT  ->  GPT-6  ({planner})", F_H, BLUE)
    yy = y + 40
    for line in textwrap.wrap(st["input_text"] or "photo + task instructions + feedback so far", 80)[:2]:
        label(d, PANEL_X + 14, yy, line, F_MS, DIM)
        yy += 17
    yy += 8
    label(d, PANEL_X + 14, yy, "OUTPUT  <-  GPT-6", F_H, ACCENT)
    if st["plan_s"] is not None:
        label(d, W - 230, yy + 2, f"{st['plan_s']:.1f} s  ·  ${st['cost']:.2f} total", F_S, DIM)
    yy += 30
    plan = st["plan"]
    if st.get("agent_calls") or st.get("inventory"):
        inv = st.get("inventory", [])
        if inv:
            label(d, PANEL_X + 14, yy, "GPT-6's parts inventory (note_parts):", F_S, DIM)
            yy += 18
            for p in inv[:6]:
                txt = f"{p['id'][:12]:<12} ({p['x_cm']:>4.1f},{p['y_cm']:>5.1f}) {p['kind'][:6]:<6} {p['material'][:10]:<10} → {p['target'][:8]:<8} {p['status']}"
                label(d, PANEL_X + 14, yy, txt[:80], F_MS, TXT)
                yy += 16
            yy += 6
        label(d, PANEL_X + 14, yy, "tool calls (latest last):", F_S, DIM)
        yy += 18
        n = 12 - min(len(inv), 6)
        for line in st.get("agent_calls", [])[-n:]:
            label(d, PANEL_X + 14, yy, line[:78], F_MS, ACCENT if line == st["agent_calls"][-1] else TXT)
            yy += 16
    elif plan is None:
        label(d, PANEL_X + 14, yy, "…" if "looking" in st["phase"] else "(no plan yet)", F_T, DIM)
    else:
        for line in textwrap.wrap(f"“{plan.get('message', '')}”", 78)[:2]:
            label(d, PANEL_X + 14, yy, line, F_T, TXT)
            yy += 22
        yy += 6
        pieces = plan.get("pieces", [])[:12]
        for i, p in enumerate(pieces):
            mark = "●" if p.get("ferrous") else "○"
            colour = WARN if p.get("ferrous") else DIM
            txt = f"{mark} {p['id']:<3} {p.get('kind', '?')[:6]:<6} {p.get('material', '?')[:11]:<11} ({p['u']:>4},{p['v']:>3})"
            label(d, PANEL_X + 14 + (i % 2) * 330, yy + (i // 2) * 17, txt, F_MS, colour)
        yy += ((len(pieces) + 1) // 2) * 17 + 8
        label(d, PANEL_X + 14, yy, "program  (executed in order)", F_S, DIM)
        yy += 20
        for i, op in enumerate(plan.get("program", [])[:8]):
            txt = op["op"] + (f"({op['u']},{op['v']})" if op.get("u") is not None else "") + (f" → {op['target']}" if op.get("target") else "")
            cx, cy = PANEL_X + 14 + (i % 2) * 330, yy + (i // 2) * 17
            if i == st["current_op"]:
                d.rectangle([cx - 6, cy - 2, cx + 318, cy + 15], fill=(60, 70, 60))
                colour, prefix = ACCENT, "▶ "
            elif i in st["op_results"]:
                colour, prefix = (ACCENT if st["op_results"][i] else WARN), ("✓ " if st["op_results"][i] else "✗ ")
            else:
                colour, prefix = DIM, "  "
            label(d, cx, cy, prefix + txt, F_MS, colour)

    # bottom bar
    d.rectangle([0, BAR_Y, W, H], fill=PANEL)
    label(d, 16, BAR_Y + 12, TITLE_BY_BUILD.get(sd.BUILD, "GPT-6 sorts hardware: servo arm + electromagnet")[:60], F_TITLE, TXT)
    label(d, 16, BAR_Y + 48, f"phase: {st['phase']}"[:70], F_T, DIM)
    if st["score"]:
        b, t = st["score"]
        label(d, 16, BAR_Y + 76, f"correctly placed: {b}/{t}", F_T, ACCENT if b == t else TXT)
    if st["calibration"]:
        label(d, 16, BAR_Y + 104, f"pixel->table homography from ArUco markers {st['calibration']['markers']}: {st['calibration']['error_mm']:.1f} mm error", F_S, DIM)
    if st.get("steps") is not None:
        c = st.get("counts", {})
        label(d, 16, BAR_Y + 128, f"steps {st.get('steps', 0)}  ·  photos {st.get('photos', 0)}  ·  picks {st.get('picks', 0)}  ·  places {st.get('places_ok', 0)}/{st.get('places', 0)} verified  ·  last GPT-6 latency {st.get('latency') or 0:.1f} s", F_S, DIM)
    else:
        label(d, 16, BAR_Y + 128, "photo -> GPT-6 -> program -> serial -> Arduino -> servos/magnet -> photo ...", F_S, DIM)

    sx = 700
    label(d, sx, BAR_Y + 12, "USB SERIAL -> ARDUINO", F_H, BLUE)
    label(d, sx, BAR_Y + 36, "115200 baud, S=servos M=magnet", F_S, DIM)
    for i, line in enumerate(serial_lines[max(0, serial_upto - 6) : serial_upto]):
        label(d, sx, BAR_Y + 58 + i * 17, line[:30], F_MS, TXT if line.startswith(">") else DIM)

    fx = 1000
    label(d, fx, BAR_Y + 12, "FEEDBACK TO GPT-6  (the only truth it gets: did the piece leave the table?)", F_H, BLUE)
    for i, line in enumerate(st["feedback"][-7:]):
        colour = WARN if "FAILED" in line else TXT
        label(d, fx, BAR_Y + 40 + i * 17, line[:110], F_MS, colour)

    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)


st_photos: State  # set in main


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run", type=Path)
    ap.add_argument("--renderer", choices=["cycles", "mujoco", "none"], default="cycles", help="3D view: Cycles (photoreal), MuJoCo rasteriser (fast preview, needs qpos in the trajectory), none (panels only)")
    ap.add_argument("--samples", type=int, default=32)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--planner-name", default="gpt-6-astra")
    ap.add_argument("--every", type=int, default=1, help="render every Nth 3D frame and hold it (halves GPU time with 2)")
    args = ap.parse_args()
    run = args.run.resolve()
    traj = np.load(run / "trajectory.npz", allow_pickle=True)
    names = [str(n) for n in traj["names"]]
    poses = traj["poses"]
    serial_idx = traj["serial_idx"]
    magnet = traj["magnet"]
    events = json.loads((run / "events.json").read_text())
    serial_lines = (run / "serial.log").read_text().splitlines()
    n = poses.shape[0]
    print(f"{n} frames ({n / FPS:.0f} s), renderer={args.renderer}")

    frames_dir = run / f"frames_{args.renderer}"
    frames_dir.mkdir(exist_ok=True)
    if args.renderer == "cycles":
        from blender_client import BlenderRenderer

        r = BlenderRenderer(samples=args.samples, log_path=run / "blender_video.log")
        print(f"Blender scene built in {r.build_s:.1f}s; rendering {n} frames at {args.samples} samples (hold frames are reused)…")
        res = r.render_batch("cine", run / "trajectory.npz", frames_dir, sd.CINE_CAM["width"], sd.CINE_CAM["height"], args.samples, every=args.every)
        r.close()
        print(f"rendered {res.get('rendered')} unique frames, mean {res.get('mean_render_s') or 0:.1f} s/frame, total {res.get('total_s', 0) / 60:.1f} min")
    elif args.renderer == "mujoco":
        import mujoco

        from mujoco_model import load

        if "qpos" not in traj:
            raise SystemExit("this trajectory has no qpos; re-run run_demo.py or use --renderer cycles")
        qpos = traj["qpos"]
        model, data, _ = load()
        ren = mujoco.Renderer(model, height=sd.CINE_CAM["height"], width=sd.CINE_CAM["width"])
        for f in range(n):
            out = frames_dir / f"frame_{f:05d}.png"
            if out.exists():
                continue
            data.qpos[:] = qpos[f]
            mujoco.mj_forward(model, data)
            ren.update_scene(data, camera="cine")
            cv2.imwrite(str(out), cv2.cvtColor(ren.render(), cv2.COLOR_RGB2BGR))

    global st_photos
    st_photos = State(run, events, serial_lines)
    out_path = args.out or (run / f"video_{args.renderer}.mp4")
    raw = out_path.with_suffix(".raw.mp4")
    writer = cv2.VideoWriter(str(raw), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    last_scene = np.zeros((SCENE_H, SCENE_W, 3), np.uint8)
    for f in range(n):
        scene = cv2.imread(str(frames_dir / f"frame_{f - (f % args.every):05d}.png")) if args.renderer != "none" else None
        if scene is None:
            scene = last_scene
        last_scene = scene
        st = st_photos.at(f)
        writer.write(compose(scene, st, f, int(serial_idx[f]), serial_lines, bool(magnet[f]), args.planner_name, n))
        if f % 200 == 0:
            print(f"  composed {f}/{n}")
    writer.release()
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "19", "-movflags", "+faststart", str(out_path)], check=True)
    raw.unlink(missing_ok=True)
    print(f"video: {out_path}")


if __name__ == "__main__":
    main()
