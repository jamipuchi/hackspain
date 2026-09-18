"""Blender render server: builds the scene once, then serves render requests from a queue dir.

Run:  Blender -b --python render_server.py -- <queue_dir> [--samples N] [--engine CYCLES|BLENDER_EEVEE_NEXT]

Request files <queue_dir>/<id>.json:
  {"type":"frame","camera":"phone","poses":{body:[x,y,z,w,qx,qy,qz]},"out":"/abs/path.png","samples":64,"width":1280,"height":960}
  {"type":"batch","camera":"cine","npz":"/abs/traj.npz","out_dir":"/abs/dir","samples":48,"width":1200,"height":900,"start":0,"stop":null}
  {"type":"quit"}
Reply: <queue_dir>/<id>.done (JSON with timings) or <id>.err (message). Batch requests also write
<id>.progress with the last frame index rendered.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
import traceback
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scene_builder as sb  # noqa: E402


def parse_args() -> tuple[Path, int, str]:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    queue = Path(argv[0]) if argv else Path("/tmp/magnet_sorter_render_queue")
    samples = 96
    engine = "CYCLES"
    if "--samples" in argv:
        samples = int(argv[argv.index("--samples") + 1])
    if "--engine" in argv:
        engine = argv[argv.index("--engine") + 1]
    return queue, samples, engine


def main() -> None:
    queue, samples, engine = parse_args()
    queue.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    empties, cams = sb.build_everything(samples=samples, engine=engine)
    (queue / "READY").write_text(json.dumps({"build_s": time.time() - t0, "engine": engine, "bodies": sorted(empties)}))
    print(f"[render_server] scene built in {time.time() - t0:.1f}s, engine={engine}, watching {queue}", flush=True)

    while True:
        reqs = sorted(p for p in queue.glob("*.json") if not p.with_suffix(".done").exists() and not p.with_suffix(".err").exists())
        if not reqs:
            time.sleep(0.15)
            continue
        req_path = reqs[0]
        try:
            req = json.loads(req_path.read_text())
        except json.JSONDecodeError:
            time.sleep(0.1)  # still being written
            continue
        try:
            t1 = time.time()
            if req["type"] == "quit":
                req_path.with_suffix(".done").write_text("{}")
                print("[render_server] quit", flush=True)
                return
            cam = cams[req.get("camera", "cine")]
            if req["type"] == "frame":
                sb.set_poses(empties, req["poses"])
                sb.render(cam, req["out"], req["width"], req["height"], req.get("samples"))
                req_path.with_suffix(".done").write_text(json.dumps({"render_s": time.time() - t1}))
            elif req["type"] == "batch":
                data = np.load(req["npz"], allow_pickle=True)
                names = [str(n) for n in data["names"]]
                poses = data["poses"]  # (F, nbody, 7)
                out_dir = Path(req["out_dir"])
                out_dir.mkdir(parents=True, exist_ok=True)
                start = int(req.get("start") or 0)
                stop = int(req["stop"]) if req.get("stop") is not None else poses.shape[0]
                every = int(req.get("every") or 1)
                prev = None
                prev_path = None
                times = []
                for f in range(start, stop, every):
                    out = out_dir / f"frame_{f:05d}.png"
                    if out.exists():
                        continue
                    if prev is not None and np.allclose(poses[f], prev, atol=1e-6) and prev_path is not None:
                        shutil.copyfile(prev_path, out)  # identical pose (hold frame): reuse the render
                    else:
                        tf = time.time()
                        sb.set_poses(empties, {n: poses[f, i].tolist() for i, n in enumerate(names)})
                        sb.render(cam, str(out), req["width"], req["height"], req.get("samples"))
                        times.append(time.time() - tf)
                        prev = poses[f].copy()
                        prev_path = out
                    req_path.with_suffix(".progress").write_text(json.dumps({"frame": f, "stop": stop, "mean_render_s": float(np.mean(times)) if times else None}))
                req_path.with_suffix(".done").write_text(json.dumps({"frames": stop - start, "rendered": len(times), "mean_render_s": float(np.mean(times)) if times else None, "total_s": time.time() - t1}))
            else:
                req_path.with_suffix(".err").write_text(f"unknown request type {req.get('type')!r}")
        except Exception:  # noqa: BLE001
            req_path.with_suffix(".err").write_text(traceback.format_exc())
            print(traceback.format_exc(), flush=True)


if __name__ == "__main__":
    main()
