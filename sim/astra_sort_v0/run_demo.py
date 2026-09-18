"""Astra sorts iron pieces with a 3-axis electromagnet arm (MuJoCo simulation).

Loop:  overhead camera frame  ->  GPT-6 Astra  ->  movement program  ->  arm executes
       ->  outcome feedback  ->  next frame ...   until no iron is left on the table.

Run (viewer needs mjpython on macOS):
    cd ~/robotics && .venv/bin/mjpython astra_sort/run_demo.py                 # Astra + viewer
    cd ~/robotics && .venv/bin/python  astra_sort/run_demo.py --headless       # Astra, no window
    cd ~/robotics && .venv/bin/python  astra_sort/run_demo.py --planner mock --headless   # no API

Each round's camera frame, annotated with Astra's detections and the plan, is saved under
astra_sort/runs/<timestamp>/.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from arm import Arm  # noqa: E402
from sim_camera import SimCamera, annotate, draw_grid  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--planner", choices=["astra", "mock"], default="astra")
    parser.add_argument("--rounds", type=int, default=5, help="max camera→plan→execute rounds")
    parser.add_argument("--seed", type=int, default=None, help="piece layout seed (default: random)")
    parser.add_argument("--headless", action="store_true", help="no MuJoCo viewer")
    parser.add_argument("--realtime", action="store_true", help="slow the sim to wall-clock speed in the viewer")
    parser.add_argument("--dry-run", action="store_true", help="ask the planner once, print the program, do not move")
    parser.add_argument("--video", type=Path, default=None, help="record a 1920x1080 dashboard video to this .mp4")
    args = parser.parse_args()

    run_dir = Path(__file__).resolve().parent / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    viewer = None
    arm = Arm.load()
    if not args.headless:
        import mujoco.viewer

        viewer = mujoco.viewer.launch_passive(arm.model, arm.data, show_left_ui=False, show_right_ui=False)
        viewer.cam.azimuth, viewer.cam.elevation, viewer.cam.distance = 150, -35, 1.4
        viewer.cam.lookat[:] = [0.25, 0.0, 0.05]
        counter = {"n": 0}

        def on_step() -> None:
            counter["n"] += 1
            if counter["n"] % 8 == 0 and viewer.is_running():
                viewer.sync()
                if args.realtime:
                    time.sleep(8 * arm.model.opt.timestep * 0.9)

        arm.on_step = on_step

    seed = args.seed if args.seed is not None else int(time.time()) % 10_000
    rng = np.random.default_rng(seed)
    arm.scatter_pieces(rng)
    arm.home()
    camera = SimCamera(arm)

    rec = None
    if args.video is not None:
        from video import Recorder

        rec = Recorder(arm, camera, args.video, args.planner, seed)
        prev_on_step = arm.on_step

        def on_step_rec() -> None:
            if prev_on_step is not None:
                prev_on_step()
            rec.on_step()

        arm.on_step = on_step_rec
        rec.phase = "scene ready"
        rec.hold(1.5)

    if args.planner == "astra":
        from astra import AstraPlanner

        planner = AstraPlanner()
    else:
        from arm import WORKSPACE
        from mock_planner import MockPlanner

        planner = MockPlanner(camera.pixel_to_table, WORKSPACE)

    truth = {p: ("iron" if p.startswith("piece_iron_") else p.split("_")[1]) for p in arm.pieces}
    print(f"planner={planner.name}  seed={seed}  run_dir={run_dir}")
    print("ground truth:", ", ".join(f"{p.removeprefix('piece_')}" for p in arm.pieces))

    history: list[str] = []
    t0 = time.time()
    for round_no in range(1, args.rounds + 1):
        frame = draw_grid(camera.grab())
        cv2.imwrite(str(run_dir / f"round{round_no}_camera.png"), frame)
        if rec:
            rec.round_no = round_no
            rec.phase = "capturing camera frame"
            rec.cam_frame = frame
            rec.cam_caption = f"round {round_no}: raw frame sent to GPT-6"
            rec.plan = None
            rec.current_op = None
            rec.op_results = {}
            rec.input_text = f"[image 960×720] Round {round_no}. Feedback so far: " + ("; ".join(history[-4:]) if history else "(none, first round)") + ". List every piece with its material, then write the program."
            rec.hold(1.5)
            rec.phase = "GPT-6 thinking"
            rec.hold(1.0)

        t_plan = time.time()
        plan = planner.plan(frame, history, round_no)
        plan_s = time.time() - t_plan
        annotated = annotate(frame, plan)
        cv2.imwrite(str(run_dir / f"round{round_no}_plan.png"), annotated)
        (run_dir / f"round{round_no}_plan.json").write_text(json.dumps(plan, indent=2))
        if rec:
            rec.plan = plan
            rec.plan_seconds = plan_s
            rec.calls += 1
            if getattr(planner, "usage", None):
                u = planner.usage[-1]
                rec.cost_usd += u["input"] * 10e-6 + u["output"] * 50e-6
            rec.cam_frame = annotated
            rec.cam_caption = f"round {round_no}: GPT-6's detections (red = iron, grey = other) and pick order 1-2-3"
            rec.phase = f"plan received in {plan_s:.1f} s"
            rec.hold(3.0)

        print(f"\n=== round {round_no}  ({plan_s:.1f}s planning)  {plan.get('message', '')}")
        for piece in plan.get("pieces", []):
            # grade the material guess against the sim's ground truth
            x, y = camera.pixel_to_table(piece["u"], piece["v"])
            nearest = min(arm.pieces, key=lambda p: np.hypot(*(arm.data.body(p).xpos[:2] - [x, y])))
            dist = float(np.hypot(*(arm.data.body(nearest).xpos[:2] - [x, y])))
            verdict = truth[nearest] if dist < 0.03 else "nothing there"
            mark = "ok" if verdict == piece["material"] else f"actually {verdict}"
            print(f"  {piece['id']:>3} ({piece['u']:4d},{piece['v']:4d}) {piece['material']:<9} conf={piece.get('confidence', 0):.2f}  [{mark}]  {piece.get('reason', '')[:60]}")
        print("  program:", " → ".join(op["op"] + (f"({op['u']},{op['v']})" if op.get("u") is not None else "") for op in plan.get("program", [])) or "(empty)")

        if args.dry_run:
            break
        if plan.get("done") and not plan.get("program"):
            print("planner says done.")
            break

        program = plan.get("program", [])
        results = []
        for i, op in enumerate(program):
            if rec:
                rec.phase = f"executing {op['op']}"
                rec.current_op = i
            r = arm.run_program([op], camera.pixel_to_table)[0]
            results.append(r)
            print(f"    {'✓' if r.ok else '✗'} {r.detail}")
            history.append(f"round {round_no}: {r.detail}")
            if rec:
                rec.op_results[i] = r.ok
                rec.current_op = None
                rec.feedback.append(("✓ " if r.ok else "✗ ") + r.detail)
                rec.hold(0.4)

        left = arm.ferrous_left_on_table()
        status = f"round {round_no}: {len(left)} iron piece(s) still on the table" if left else f"round {round_no}: table has no iron left"
        history.append(status)
        if rec:
            rec.feedback.append(status)
        if not left:
            print("\nall iron pieces are in the bin.")
            if rec:
                rec.phase = "done: all iron in the bin"
                rec.hold(3.0)
            break
    else:
        print("\nround limit reached.")

    left = arm.ferrous_left_on_table()
    print(f"\nresult: {3 - len(left)}/3 iron pieces binned in {time.time() - t0:.0f}s; frames in {run_dir}")
    if getattr(planner, "usage", None):
        tin = sum(u["input"] for u in planner.usage)
        tout = sum(u["output"] for u in planner.usage)
        print(f"astra usage: {len(planner.usage)} calls, {tin} in / {tout} out tokens ≈ ${tin * 10e-6 + tout * 50e-6:.2f}")

    if rec is not None:
        rec.phase = "finished"
        rec.hold(1.0)
        out = rec.close()
        print(f"video: {out}  ({rec.frames} frames, {rec.frames / 25:.0f} s)")

    if viewer is not None:
        print("viewer stays open; close the window to exit.")
        while viewer.is_running():
            arm.step()
            time.sleep(arm.model.opt.timestep)
        viewer.close()


if __name__ == "__main__":
    main()
