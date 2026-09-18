"""GPT-6 sorts real-looking hardware with an Arduino servo arm and an electromagnet.

    phone camera (Cycles render)  ->  GPT-6  ->  program  ->  serial  ->  Arduino firmware
        ^                                                                  |  servos + magnet
        +------------- "piece is gone from the table" / "still there" <----+  (MuJoCo physics)

Run:
    cd ~/robotics/magnet_sorter && ../.venv/bin/python run_demo.py                 # GPT-6, Cycles phone frames, trajectory saved
    cd ~/robotics/magnet_sorter && ../.venv/bin/python run_demo.py --planner mock   # no API
    cd ~/robotics/magnet_sorter && ../.venv/bin/python run_demo.py --phone mujoco   # fast rasterised phone frames (no Blender)
    ... --port /dev/tty.usbmodem14101                                              # drive a REAL Arduino instead of the sim

Every run writes runs/<timestamp>/ with the phone frames GPT-6 saw, its JSON answers, the serial
log, and trajectory.npz + events.json that make_video.py turns into the dashboard video.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import mujoco
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

_pre = argparse.ArgumentParser(add_help=False)
_pre.add_argument("--build", default=os.environ.get("SORTER_BUILD", "hobby_v1"))
_build = _pre.parse_known_args()[0].build
os.environ["SORTER_BUILD"] = _build  # inherited by the Blender render server

import scene_def as sd  # noqa: E402
from camera import MujocoPhoneCamera, TableCalibration, annotate, draw_grid  # noqa: E402
from controller import ArmController  # noqa: E402
from hardware import SerialLog, SimArduino  # noqa: E402
from mujoco_model import load  # noqa: E402

FRAME_STEPS = 20  # 40 ms of sim per recorded frame → 25 fps video


class Recorder:
    """Logs body poses every 40 ms plus timestamped UI events for the offline video."""

    def __init__(self, model, data, run_dir: Path):
        self.model, self.data = model, data
        self.names = [model.body(i).name for i in range(1, model.nbody)]
        self.ids = [model.body(n).id for n in self.names]
        self.poses: list[np.ndarray] = []
        self.events: list[dict] = []
        self.serial_idx: list[int] = []
        self.run_dir = run_dir
        self.step_count = 0
        self.serial_log: SerialLog | None = None
        self.magnet_flags: list[int] = []
        self.qpos: list[np.ndarray] = []
        self.ard = None

    def on_step(self) -> None:
        self.step_count += 1
        if self.step_count % FRAME_STEPS == 0:
            self.frame()

    def frame(self) -> None:
        arr = np.zeros((len(self.ids), 7))
        arr[:, :3] = self.data.xpos[self.ids]
        arr[:, 3:] = self.data.xquat[self.ids]
        self.poses.append(arr)
        self.qpos.append(self.data.qpos.copy())
        self.serial_idx.append(len(self.serial_log.lines) if self.serial_log else 0)
        self.magnet_flags.append(int(self.ard.magnet) if self.ard is not None else 0)

    def hold(self, seconds: float) -> None:
        for _ in range(int(seconds * 25)):
            self.frame()

    def event(self, kind: str, **payload) -> None:
        self.events.append({"frame": len(self.poses), "kind": kind, **payload})

    def save(self) -> None:
        np.savez_compressed(self.run_dir / "trajectory.npz", names=np.array(self.names), poses=np.stack(self.poses), qpos=np.stack(self.qpos), serial_idx=np.array(self.serial_idx), magnet=np.array(self.magnet_flags))
        (self.run_dir / "events.json").write_text(json.dumps(self.events, indent=1))
        (self.run_dir / "serial.log").write_text("\n".join(self.serial_log.lines if self.serial_log else []))


def park(model, data, b, k: int) -> None:
    """Keep a piece off-stage (under the board) until it is needed."""
    adr = model.jnt_qposadr[model.joint(f"{b.name}_free").id]
    data.qpos[adr : adr + 7] = [-0.6 - 0.03 * k, 0.0, -0.74, 1, 0, 0, 0]
    data.qvel[model.jnt_dofadr[model.joint(f"{b.name}_free").id] :][:6] = 0


def scatter(model, data, pieces, rng: np.random.Generator, region: str = "workspace", y_center: float | None = None) -> list:
    """Random non-touching layout. region: 'workspace' (arm sector), 'belt' (conveyor pick zone / spawn).
    Guarantees a minimum centre distance between parts (relaxing from the sheet's 3.8 cm down to 2.8 cm) and keeps
    parts out of the container footprints; parts that still do not fit are parked off-stage for the next wave.
    Returns the pieces actually placed."""
    placed, done = [], []
    want = 0.042 if sd.BUILD == "hobby_v1" else 0.038  # the shopping sheet: "spread parts in one layer with gaps"
    floor = 0.028  # a Ø20 magnet reaches ~1.2 cm sideways: 2.8 cm between centres is the physical minimum
    keep_out = [(t["pos"][0], t["pos"][1], max(t["size"][0], t["size"][1]) + 0.015) for t in sd.TARGETS.values()] if region == "workspace" else []
    for b in pieces:
        found = None
        spacing = want
        while found is None and spacing >= floor - 1e-9:
            for _ in range(300):
                if region == "belt":
                    c = sd.CONVEYOR
                    x = rng.uniform(c["x"] - c["width"] / 2 + 0.012, c["x"] + c["width"] / 2 - 0.012)
                    y = y_center + rng.uniform(-0.035, 0.035)
                else:
                    r = rng.uniform(sd.WORKSPACE["r"][0] + 0.008, sd.WORKSPACE["r"][1] - 0.010)
                    yaw = rng.uniform(sd.WORKSPACE["yaw"][0] + 0.10, sd.WORKSPACE["yaw"][1] - 0.10)
                    x, y = r * np.cos(yaw), r * np.sin(yaw)
                if any(np.hypot(x - kx, y - ky) < kr for kx, ky, kr in keep_out):
                    continue
                if all(np.hypot(x - px, y - py) > spacing for px, py in placed):
                    found = (x, y)
                    break
            spacing -= 0.002
        if found is None:
            continue
        x, y = found
        placed.append((x, y))
        done.append(b)
        adr = model.jnt_qposadr[model.joint(f"{b.name}_free").id]
        yaw_q = rng.uniform(0, np.pi)
        data.qpos[adr : adr + 7] = [x, y, sd.SURFACE_Z + sd.piece_half_height(b) + 0.004, np.cos(yaw_q / 2), 0, 0, np.sin(yaw_q / 2)]
    mujoco.mj_forward(model, data)
    if len(placed) > 1:
        nn = min(np.hypot(px - qx, py - qy) for i, (px, py) in enumerate(placed) for j, (qx, qy) in enumerate(placed) if i != j)
        print(f"layout: {len(done)}/{len(pieces)} parts on the {region}, closest pair {nn * 100:.1f} cm" + (f" ({len(pieces) - len(done)} wait for the next wave)" if len(done) < len(pieces) else ""))
    return done


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", default=_build, choices=sorted(sd.BUILDS), help="which physical build / task to simulate")
    ap.add_argument("--planner", choices=["gpt6", "jev", "mock", "oracle"], default="gpt6", help="gpt6: OpenAI GPT-6 (vision); jev: TypeSafe Jev decides per part, code does the vision (program brain only)")
    ap.add_argument("--viewer", action="store_true", help="open the MuJoCo viewer and run at wall-clock speed (needs mjpython on macOS)")
    ap.add_argument("--brain", choices=["program", "agent"], default="agent", help="program: one batch program per photo (legacy); agent: GPT-6 tool calls step by step with a photo after each motion")
    ap.add_argument("--cameras", default="A,B", help="which cameras of the build to use, e.g. A or A,B")
    ap.add_argument("--effort", default="medium", help="GPT-6 reasoning.effort for agent mode")
    ap.add_argument("--max-steps", type=int, default=60)
    ap.add_argument("--budget", type=float, default=4.0, help="USD cap for agent mode")
    ap.add_argument("--phone", choices=["cycles", "mujoco"], default="cycles", help="how the phone frames are rendered")
    ap.add_argument("--samples", type=int, default=64, help="Cycles samples for phone frames")
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--port", default=None, help="serial port of a real Arduino (sim arm is still rendered)")
    ap.add_argument("--no-record", action="store_true")
    ap.add_argument("--cycles", type=int, default=3, help="conveyor builds: number of belt batches")
    args = ap.parse_args()

    run_dir = sd.ROOT / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True)
    seed = args.seed if args.seed is not None else int(time.time()) % 10_000
    rng = np.random.default_rng(seed)

    model, data, bodies = load()
    pieces = [b for b in bodies if b.piece]
    log = SerialLog()
    rec = None if args.no_record else Recorder(model, data, run_dir)
    sim_ard = SimArduino(model, data, pieces, log)
    if rec:
        rec.serial_log = log
        rec.ard = sim_ard
    if args.port:
        from hardware import SerialArduino

        ard = SerialArduino(args.port, log=log)
        print(f"real Arduino on {args.port}; the simulated arm mirrors the commands")
    else:
        ard = sim_ard

    viewer = None
    if args.viewer:
        from mujoco import viewer as mjviewer

        viewer = mjviewer.launch_passive(model, data, show_left_ui=False, show_right_ui=False)
        cx, cy, cz = sd.CINE_CAM["pos"]
        lx, ly, lz = sd.CINE_CAM["lookat"]
        viewer.cam.lookat[:] = [lx, ly, lz]
        viewer.cam.distance = float(np.linalg.norm(np.array([cx - lx, cy - ly, cz - lz])))
        viewer.cam.azimuth = float(np.degrees(np.arctan2(cy - ly, cx - lx)))
        viewer.cam.elevation = -float(np.degrees(np.arcsin((cz - lz) / viewer.cam.distance)))
        step_counter = {"n": 0}

    def step(n: int) -> None:
        for _ in range(n):
            mujoco.mj_step(model, data)
            sim_ard.tick()
            if rec:
                rec.on_step()
            if viewer is not None:
                step_counter["n"] += 1
                if step_counter["n"] % 10 == 0:
                    if not viewer.is_running():
                        raise SystemExit("viewer closed")
                    viewer.sync()
                    time.sleep(10 * model.opt.timestep * 0.9)
        if args.port:
            time.sleep(n * model.opt.timestep)

    if args.port:
        # mirror every command to the sim twin as well
        class Twin:
            def write(self, line):
                sim_ard.write(line)
                return ard.write(line)

            def busy(self):
                return ard.busy() or sim_ard.busy()

            def tick(self):
                return None

        ard = Twin()

    (run_dir / "meta.json").write_text(json.dumps({"build": sd.BUILD, "title": sd.CFG.title, "seed": seed, "planner": args.planner}))
    hopper = list(pieces)
    rng.shuffle(hopper)
    if sd.CONVEYOR:
        batch = sd.CONVEYOR.get("batch", 4)
        for k, b in enumerate(hopper):
            park(model, data, b, k)
        onstage = hopper[:batch]
        hopper = hopper[batch:]
        scatter(model, data, onstage, rng, region="belt", y_center=0.0)
    else:
        wave = max(1, int(getattr(sd.CFG, "wave_size", 6)))
        for k, b in enumerate(hopper):
            park(model, data, b, k)
        onstage = scatter(model, data, hopper[:wave], rng)
        hopper = [b for b in hopper if b not in onstage]
        if hopper:
            print(f"waves: {len(onstage)} parts on the card now, {len(hopper)} more arrive when GPT-6 finishes this batch")
    step(400)
    ard.write("H")
    step(600)

    blender = None
    cam_names = [c.strip() for c in args.cameras.split(",") if c.strip() in sd.CAMERAS] or list(sd.CAMERAS)[:1]
    cams: dict = {}
    if args.phone == "cycles":
        from blender_client import BlenderPhoneCamera, BlenderRenderer

        blender = BlenderRenderer(samples=args.samples, log_path=run_dir / "blender_phone.log")
        for cn in cam_names:
            cams[cn] = BlenderPhoneCamera(blender, model, data, run_dir / "phone", samples=args.samples, name=cn)
        print(f"Blender scene built in {blender.build_s:.1f}s (Cycles, Metal GPU); cameras {cam_names}")
    else:
        for cn in cam_names:
            cams[cn] = MujocoPhoneCamera(model, data, cn)
    phone = cams[cam_names[0]]

    ctrl = ArmController(ard, step, frame_grab=phone.grab)
    cals: dict = {}
    if rec:
        rec.event("phase", text="calibrating cameras from the ArUco markers")
    for cn, cam in cams.items():
        cal_c = TableCalibration()
        if not cal_c.fit(cam.grab()):
            raise SystemExit(f"ArUco calibration failed for camera {cn}: markers not visible")
        cals[cn] = cal_c
        print(f"camera {cn} calibrated from markers {sorted(cal_c.found)}: {cal_c.reprojection_error_mm():.2f} mm reprojection error")
    cal = cals[cam_names[0]]
    ctrl.pixel_to_table = cal.pixel_to_table
    ctrl.frame_grabs = {cn: cams[cn].grab for cn in cams}
    ctrl.calibrations = cals
    if rec:
        rec.event("calibration", markers=sorted(cal.found), error_mm=cal.reprojection_error_mm(), cameras=cam_names)

    if args.planner == "gpt6":
        from brain import GPT6Planner

        planner = GPT6Planner()
    elif args.planner == "jev":
        from jev_brain import JevPlanner

        planner = JevPlanner(cal)
        if args.brain == "agent":
            print("planner jev: Jev is text-only and has no tool calling, using the program brain (one plan per photo)")
    elif args.planner == "oracle":
        from brain import OraclePlanner

        planner = OraclePlanner(model, data, pieces, cal.table_to_pixel)
    else:
        from brain import MockPlanner

        planner = MockPlanner(cal.pixel_to_table)

    truth = {b.name: b.piece for b in pieces}

    def container_of(name: str) -> str | None:
        p = data.body(name).xpos
        for tname, t in sd.TARGETS.items():
            tx, ty = t["pos"]
            hw, hd = (t["size"][0] + 0.01, t["size"][1] + 0.01)
            if abs(p[0] - tx) < hw and abs(p[1] - ty) < hd and p[2] > -0.05:
                return tname
        return None

    def score() -> tuple[int, int, list[str], list[str]]:
        """(correct, expected, still-to-do names, wrongly placed names) against each piece's intended target."""
        expected = [b for b in pieces if b.piece.get("target")]
        correct, todo, wrong = 0, [], []
        for b in pieces:
            c = container_of(b.name)
            want = b.piece.get("target")
            if want:
                if c == want:
                    correct += 1
                else:
                    todo.append(b.name)
            elif c is not None:
                wrong.append(f"{b.name}→{c}")
        if sd.BUILD == "taras_kitting":
            return kitting_score()
        return correct, len(expected), todo, wrong

    def kitting_score() -> tuple[int, int, list[str], list[str]]:
        kits = {k: {"screw": 0, "nut": 0, "washer": 0} for k in ("kit_A", "kit_B", "kit_C")}
        for b in pieces:
            c = container_of(b.name)
            if c in kits:
                kits[c][b.piece["kit_part"]] += 1
        complete = sum(1 for k in kits.values() if all(v == 1 for v in k.values()))
        wrong = [f"{k}:{v}" for k, v in kits.items() if any(n > 1 for n in v.values())]
        return complete, 3, [k for k, v in kits.items() if not all(n == 1 for n in v.values())], wrong

    def ferrous_left() -> list[str]:
        return score()[2]

    print(f"build={sd.BUILD} planner={planner.name} seed={seed} run={run_dir}")
    print("ground truth (hidden from GPT-6):", ", ".join(f"{b.name}→{b.piece.get('target') or 'leave'}" for b in pieces))

    t_start = time.time()
    if viewer is not None:
        print("viewer open: watch the run in the MuJoCo window")
    if args.brain == "agent" and args.planner == "gpt6":
        from agent_brain import GPT6Agent
        from robot_api import RobotAPI

        robot = RobotAPI(ctrl, get_face_pos=lambda: data.site("magnet_face").xpos.copy())
        # conveyor: feed new parts when the agent advances the belt
        if sd.CONVEYOR:
            orig_belt = robot.belt_advance

            def belt_with_feed(cm):
                nonlocal hopper
                res = orig_belt(cm)
                if hopper:
                    batch = sd.CONVEYOR.get("batch", 4)
                    newp, hopper = hopper[:batch], hopper[batch:]
                    scatter(model, data, newp, rng, region="belt", y_center=sd.CONVEYOR["spawn_y"])
                    step(300)
                return res

            robot.belt_advance = belt_with_feed

        def on_event(kind, payload):
            if rec:
                rec.event(kind, **payload)
                rec.hold(0.4)
            if kind == "tool_call":
                print(f"  → {payload['name']}({json.dumps(payload['args'])})  [{payload['latency_s']}s]")
            elif kind == "tool_result":
                print(f"     {'✓' if payload['ok'] else '✗'} {payload['text'][:150]}")
            elif kind == "agent_text":
                print(f"  GPT-6: {payload['text'][:200]}")

        def refill() -> str | None:
            """Called when GPT-6 confirms done. Static builds: the operator puts the next wave of parts on the card."""
            nonlocal hopper
            if sd.CONVEYOR or not hopper:
                return None
            ctrl.home()
            wave = max(1, int(getattr(sd.CFG, "wave_size", 6)))
            newp = scatter(model, data, hopper[:wave], rng)
            hopper = [b for b in hopper if b not in newp]
            step(400)
            if rec:
                rec.event("phase", text=f"batch done: the operator puts {len(newp)} more parts on the card")
                rec.hold(1.5)
            print(f"  ↻ refill: {len(newp)} new parts on the card, {len(hopper)} still waiting")
            return f"This batch is accepted. The operator has put {len(newp)} NEW parts on the card ({len(hopper)} more will follow later). Take a photo, rebuild your inventory with note_parts and continue the same task with the new parts."

        agent = GPT6Agent(robot, {cn: cams[cn].grab for cn in cams}, cals, run_dir, on_event=on_event, effort=args.effort, max_steps=args.max_steps, budget_usd=args.budget, refill=refill)
        print(f"agent mode: {len(cams)} camera(s) {list(cams)}, effort={args.effort}, max {args.max_steps} steps, budget ${args.budget:.2f}")
        result = agent.run_loop()
        correct, expected, todo, wrong = score()
        print(f"\nagent {'finished' if result.finished else 'stopped'}: {result.summary}")
        print(f"result: {correct}/{expected} correctly placed after {result.steps} tool calls, {result.calls} API calls, {time.time() - t_start:.0f}s wall, {data.time:.0f}s sim")
        if wrong:
            print("misplaced (should be none):", wrong)
        for name in todo:
            if name in {b.name for b in pieces}:
                print(f"  not done: {name} at {np.round(data.body(name).xpos, 3)} (in {container_of(name)})")
            else:
                print(f"  not complete: {name}")
        print(f"GPT-6 usage: {result.usage_in} in / {result.usage_out} out tokens ≈ ${result.cost_usd():.2f}")
        if rec:
            rec.event("score", binned=correct, total=expected)
            rec.event("phase", text=f"done: {correct}/{expected} correctly placed")
            rec.hold(3.0)
            rec.save()
        if blender is not None:
            blender.close()
        return

    history: list[str] = []
    belt_cycles_left = args.cycles - 1
    for round_no in range(1, args.rounds + 1):
        if rec:
            rec.event("phase", text=f"round {round_no}: taking phone photo")
            rec.hold(0.8)
        raw = phone.grab()
        frame = draw_grid(raw)
        cv2.imwrite(str(run_dir / f"round{round_no}_photo.png"), frame)
        if rec:
            rec.event("photo", round=round_no, file=f"round{round_no}_photo.png", render_s=getattr(phone, "last_render_s", 0.0))
            rec.event("phase", text=f"round {round_no}: GPT-6 is looking at the photo")
            rec.hold(1.0)

        t_plan = time.time()
        plan = planner.plan(frame, history, round_no)
        plan_s = time.time() - t_plan
        cv2.imwrite(str(run_dir / f"round{round_no}_plan.png"), annotate(frame, plan))
        (run_dir / f"round{round_no}_plan.json").write_text(json.dumps(plan, indent=2))
        if rec:
            rec.event("plan", round=round_no, plan=plan, seconds=plan_s, input_text=getattr(planner, "last_input_text", ""), cost_usd=planner.cost_usd(), file=f"round{round_no}_plan.png")
            rec.event("phase", text=f"round {round_no}: plan received ({plan_s:.1f} s)")
            rec.hold(3.0)

        print(f"\n=== round {round_no} ({plan_s:.1f}s) {plan.get('message', '')}")
        for p in plan.get("pieces", []):
            x, y = cal.pixel_to_table(p["u"], p["v"])
            nearest = min(pieces, key=lambda b: np.hypot(*(data.body(b.name).xpos[:2] - [x, y])))
            dist = float(np.hypot(*(data.body(nearest.name).xpos[:2] - [x, y])))
            if dist < 0.02:
                tp = truth[nearest.name]
                want = tp.get("target") or "leave"
                ok_kind = tp["kind"] == p["kind"]
                ok_tgt = p.get("target", "?") == want
                mark = f"{nearest.name} ({tp['material']}, {tp['kind']}) → {'kind ok' if ok_kind else 'kind WRONG'}, {'target ok' if ok_tgt else 'target WRONG (' + want + ')'}"
            else:
                mark = "nothing within 2 cm"
            print(f"  {p['id']:>3} ({p['u']:4d},{p['v']:4d}) {p['kind']:<7} {p['material']:<11} fe={str(p['ferrous'])[0]} → {p.get('target', '?'):<8} conf={p['confidence']:.2f} | {mark}")
        print("  program:", " → ".join(op["op"] + (f"({op['u']},{op['v']})" if op.get("u") is not None else "") for op in plan.get("program", [])) or "(empty)")

        if plan.get("done") and not plan.get("program"):
            if sd.CONVEYOR and (hopper or belt_cycles_left > 0):
                print("GPT-6 is done with this view → advancing the belt")
                if rec:
                    rec.event("phase", text="view sorted: belt advances, new parts arrive")
                res = ctrl.belt_advance(9.0)
                history.append(f"round {round_no}: belt advanced, new parts in the pick zone")
                if rec:
                    rec.event("op_done", round=round_no, index=0, ok=res.ok, detail=res.detail)
                batch = sd.CONVEYOR.get("batch", 4)
                newp, hopper = hopper[:batch], hopper[batch:]
                if newp:
                    scatter(model, data, newp, rng, region="belt", y_center=sd.CONVEYOR["spawn_y"])
                    step(300)
                    res = ctrl.belt_advance((0.0 - sd.CONVEYOR["spawn_y"]) * 100)
                    if rec:
                        rec.event("op_done", round=round_no, index=1, ok=res.ok, detail=res.detail)
                belt_cycles_left -= 1
                continue
            if hopper and not sd.CONVEYOR:
                ctrl.home()
                wave = max(1, int(getattr(sd.CFG, "wave_size", 6)))
                newp = scatter(model, data, hopper[:wave], rng)
                hopper = [b for b in hopper if b not in newp]
                step(400)
                history.append(f"round {round_no}: batch finished, the operator put {len(newp)} new parts on the card")
                print(f"GPT-6 is done with this batch → {len(newp)} new parts on the card")
                if rec:
                    rec.event("phase", text=f"batch done: the operator puts {len(newp)} more parts on the card")
                continue
            print("GPT-6 says it is done.")
            if rec:
                rec.event("phase", text="GPT-6 reports done")
                rec.hold(1.5)
            break

        program = plan.get("program", [])
        for i, op in enumerate(program):
            if rec:
                rec.event("op_start", round=round_no, index=i, op=op)
            res = ctrl.run([op])[0]
            print(f"    {'✓' if res.ok else '✗'} {res.detail}")
            history.append(f"round {round_no}: {res.detail}")
            if rec:
                rec.event("op_done", round=round_no, index=i, ok=res.ok, detail=res.detail)
                rec.hold(0.3)

        correct, expected, todo, wrong = score()
        print(f"  (evaluator) correctly placed: {correct}/{expected}" + (f", misplaced: {wrong}" if wrong else ""))
        if rec:
            rec.event("score", binned=correct, total=expected)

    correct, expected, todo, wrong = score()
    total_s = time.time() - t_start
    print(f"\nresult: {correct}/{expected} correctly placed, {len(history)} feedback lines, {total_s:.0f}s wall, {data.time:.0f}s sim")
    if wrong:
        print("misplaced (should be none):", wrong)
    for name in todo:
        if name in {b.name for b in pieces}:
            print(f"  not done: {name} at {np.round(data.body(name).xpos, 3)} (in {container_of(name)})")
        else:
            print(f"  not complete: {name}")
    if sim_ard.velocity_clamps:
        print(f"physics guard clamped a piece velocity {sim_ard.velocity_clamps} times")
    if planner.usage:
        tin = sum(u["input"] for u in planner.usage)
        tout = sum(u["output"] for u in planner.usage)
        print(f"GPT-6 usage: {len(planner.usage)} calls, {tin} in / {tout} out tokens ≈ ${planner.cost_usd():.2f}")
    if rec:
        rec.event("phase", text=f"done: {correct}/{expected} correctly placed")
        rec.hold(3.0)
        rec.save()
        print(f"trajectory: {len(rec.poses)} frames ({len(rec.poses) / 25:.0f} s) → {run_dir}")
    if blender is not None:
        blender.close()


if __name__ == "__main__":
    main()
