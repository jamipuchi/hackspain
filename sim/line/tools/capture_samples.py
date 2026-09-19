"""Shoot labelled bean samples from the live camera for the classifier. Owner: camera agent.

    cd ~/robotics && .venv/bin/python line/tools/capture_samples.py                # iPhone, ROI from config.json
    .venv/bin/python line/tools/capture_samples.py --backend synthetic             # no hardware
    .venv/bin/python line/tools/capture_samples.py --roi 400,200,880,520 --save-roi  # try a ROI, write it to config

Window keys:  g = save as good   d = save as defect   f = save as foreign   a = save every whole blob as <auto label>
              arrows / WASD move the ROI, +/- grow/shrink it, r = write ROI to config.json, p = pause, q = quit
Each save writes datasets/beans/<label>/<timestamp>.png (crop with 8 px margin) and .json (a contracts.Sample:
path, label, features, verdict=None, t). If no whole blob is in the ROI the ROI crop itself is saved with
empty features and a warning, so a hand-held shot still yields an image.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from line import config as line_config  # noqa: E402
from line.bean_vision import PaperBeanDetector, draw_blobs  # noqa: E402
from line.camera_source import FileCamera, RealCamera, SyntheticCamera  # noqa: E402
from line.contracts import ROI, Sample, now  # noqa: E402

LABEL_KEYS = {ord("g"): "good", ord("d"): "defect", ord("f"): "foreign"}
DEFAULT_DATASET = ROOT / "line" / "datasets" / "beans"


def save_sample(bgr, blob, label: str, out_dir: Path, margin: int = 8) -> Path:
    out_dir = out_dir / label
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S") + f"-{int((time.time() % 1) * 1000):03d}"
    if blob is not None:
        x, y, w, h = blob.bbox
        H, W = bgr.shape[:2]
        crop = bgr[max(y - margin, 0): min(y + h + margin, H), max(x - margin, 0): min(x + w + margin, W)]
        feats = blob.features
    else:
        crop, feats = bgr, {}
    png = out_dir / f"{ts}.png"
    cv2.imwrite(str(png), crop)
    sample = Sample(path=str(png), label=label, features=feats, verdict=None, t=now())
    (out_dir / f"{ts}.json").write_text(json.dumps(asdict(sample), indent=1))
    return png


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", choices=["real", "synthetic", "file"], default=None, help="default: config camera.backend")
    ap.add_argument("--file", nargs="*", help="images / folder / video for --backend file")
    ap.add_argument("--roi", help="x0,y0,x1,y1 in pixels (default: config camera.zone)")
    ap.add_argument("--save-roi", action="store_true", help="write --roi to config.json at start")
    ap.add_argument("--out", type=Path, default=DEFAULT_DATASET, help=f"dataset root (default {DEFAULT_DATASET})")
    ap.add_argument("--auto-label", default="good", help="label used by the 'a' key")
    ap.add_argument("--scale", type=float, default=0.6, help="display scale")
    args = ap.parse_args()

    cfg = line_config.load()
    if args.roi:
        cfg.camera.zone = [int(v) for v in args.roi.split(",")]
        if args.save_roi:
            line_config.save(cfg)
            print("wrote camera.zone", cfg.camera.zone, "to", line_config.CONFIG_PATH)
    backend = args.backend or cfg.camera.backend
    if backend == "real":
        cam = RealCamera(cfg)
        if not cam.ok:
            sys.exit(f"camera: {cam.error}")
    elif backend == "synthetic":
        cam = SyntheticCamera(cfg, realtime=True)
    else:
        cam = FileCamera(args.file or cfg.camera.file_paths, fps=cfg.camera.fps, realtime=True)
    det = PaperBeanDetector(cfg)
    print("camera:", cam.status())
    print("keys: g good, d defect, f foreign, a auto, arrows/WASD move ROI, +/- resize, r save ROI, p pause, q quit")
    print("saving to", args.out)

    roi = det.default_roi(cam.grab().bgr.shape)
    paused = False
    frame = None
    blobs = []
    counts: dict[str, int] = {}
    while True:
        if not paused or frame is None:
            frame = cam.grab()
            blobs = det.detect(frame, roi)
        whole = [b for b in blobs if not b.partial]
        view = draw_blobs(frame.bgr, blobs, roi)
        st = cam.status()
        hud = f"{st.get('fps_measured', 0):.0f} fps  grab {st.get('grab_latency_ms', 0):.0f} ms  detect {det.status()['mean_ms']:.1f} ms  paper {det.paper_median:.0f} thr {det.threshold_used}  ROI {roi.x0},{roi.y0},{roi.x1},{roi.y1}  saved {counts}"
        cv2.putText(view, hud, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(view, hud, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1, cv2.LINE_AA)
        if args.scale != 1.0:
            view = cv2.resize(view, None, fx=args.scale, fy=args.scale, interpolation=cv2.INTER_AREA)
        cv2.imshow("capture_samples", view)
        key = cv2.waitKeyEx(1)
        if key == -1:
            continue
        k = key & 0xFF
        if k in (ord("q"), 27):
            break
        if k == ord("p"):
            paused = not paused
        elif k in LABEL_KEYS or k == ord("a"):
            label = LABEL_KEYS.get(k, args.auto_label)
            targets = whole if k == ord("a") else whole[:1]
            if not targets:
                p = save_sample(roi.crop(frame.bgr), None, label, args.out)
                print(f"WARNING no whole blob in ROI; saved ROI crop {p.name} with empty features")
                counts[label] = counts.get(label, 0) + 1
            for b in targets:
                p = save_sample(frame.bgr, b, label, args.out)
                counts[label] = counts.get(label, 0) + 1
                print(f"saved {label}: {p.name}  major {b.features['major_mm']:.1f} mm  gray {b.features['mean_gray']:.0f}  spots {b.features['n_dark_spots']:.0f}")
        elif k == ord("r"):
            cfg.camera.zone = [roi.x0, roi.y0, roi.x1, roi.y1]
            line_config.save(cfg)
            print("wrote camera.zone", cfg.camera.zone)
        else:  # ROI editing
            step = 10
            H, W = frame.bgr.shape[:2]
            dx = dy = 0
            if key in (2424832, 65361) or k == ord("a") and False or k == ord("A"):
                dx = -step
            if key in (2555904, 65363) or k == ord("D"):
                dx = step
            if key in (2490368, 65362) or k == ord("W"):
                dy = -step
            if key in (2621440, 65364) or k == ord("S"):
                dy = step
            if k == ord("h"): dx = -step
            if k == ord("l"): dx = step
            if k == ord("k"): dy = -step
            if k == ord("j"): dy = step
            grow = step if k in (ord("+"), ord("=")) else -step if k in (ord("-"), ord("_")) else 0
            x0 = min(max(roi.x0 + dx - grow, 0), W - 2)
            y0 = min(max(roi.y0 + dy - grow, 0), H - 2)
            x1 = max(min(roi.x1 + dx + grow, W), x0 + 2)
            y1 = max(min(roi.y1 + dy + grow, H), y0 + 2)
            roi = ROI(x0, y0, x1, y1)
    cam.close()
    cv2.destroyAllWindows()
    print("saved:", counts, "->", args.out)


if __name__ == "__main__":
    main()
