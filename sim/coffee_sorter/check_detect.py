"""Check the foreground-only detector against the pre-optimization implementation.

Run from the repository root:
  python sim/coffee_sorter/check_detect.py
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import time
import types
from unittest import mock

import cv2
import numpy as np

import vision
from vision import Inspector


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SHAPE = (192, 2080)


def inspector(cls, shape):
    """Build the minimal Inspector state needed by detect without a renderer."""
    H, W = shape
    out = cls.__new__(cls)
    out.L = types.SimpleNamespace(cam_x=-0.12, cam_h=H, cam_w=W)
    out.ppm = 4000
    if cls is Inspector:
        out._grid_shape = shape
        out._xs = np.tile(np.arange(W, dtype=np.float64), H)
        out._ys = np.repeat(np.arange(H, dtype=np.float64), W)
    return out


def old_inspector():
    source = subprocess.run(
        ["git", "show", "cad5f9b:sim/coffee_sorter/vision.py"],
        cwd=REPO,
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    module = types.ModuleType("vision_before_foreground_optimization")
    module.__file__ = "cad5f9b:sim/coffee_sorter/vision.py"
    sys.modules[module.__name__] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module.Inspector


def belt_frame(shape=SHAPE):
    return np.full((*shape, 3), (35, 75, 160), dtype=np.uint8)


def samples():
    blank = belt_frame()

    noise = belt_frame()
    noise[40:49, 300:310] = (110, 160, 65)
    noise[43:46, 304:307] = (30, 30, 30)
    noise[::19, ::31] = (110, 160, 65)  # isolated foreground pixels must be filtered

    touching = belt_frame()
    cv2.ellipse(touching, (700, 90), (31, 12), 20, 0, 360, (105, 155, 65), -1)
    cv2.ellipse(touching, (752, 101), (31, 12), 20, 0, 360, (125, 170, 75), -1)
    cv2.circle(touching, (700, 90), 4, (25, 25, 25), -1)

    edge = belt_frame()
    cv2.ellipse(edge, (100, 1), (25, 14), 0, 0, 360, (105, 155, 65), -1)
    cv2.ellipse(edge, (1980, 190), (25, 14), 0, 0, 360, (105, 155, 65), -1)

    all_edges = belt_frame((97, 321))
    for center in ((0, 0), (320, 0), (0, 96), (320, 96)):
        cv2.circle(all_edges, center, 9, (105, 155, 65), -1)

    padded = belt_frame((83, 642))
    noncontiguous = padded[:, ::2]
    noncontiguous[20:51, 120:151] = (95, 145, 55)
    noncontiguous[32:39, 132:139] = (30, 30, 30)
    return {
        "blank": blank, "noise": noise, "touching": touching, "edge": edge,
        "all_edges": all_edges, "noncontiguous": noncontiguous,
        "fortran": np.asfortranarray(all_edges), "negative_stride": all_edges[:, ::-1],
    }


def assert_constructor_cache():
    class FakeRenderer:
        def __init__(self, model, height, width):
            self._scene_option = types.SimpleNamespace(geomgroup=np.ones(6, dtype=np.uint8))

    shape = (71, 313)
    sim = types.SimpleNamespace(
        L=types.SimpleNamespace(cam_h=shape[0], cam_w=shape[1], px_per_m=4000),
        P=types.SimpleNamespace(belt_rgb=(0.1, 0.2, 0.3)),
        model=types.SimpleNamespace(geom_group=np.array([2], dtype=np.uint8)),
        geom_of=np.array([0]),
    )
    with mock.patch.object(vision.mujoco, "Renderer", FakeRenderer):
        detector = Inspector(sim)
    assert detector._grid_shape == shape
    np.testing.assert_array_equal(detector._xs, np.tile(np.arange(shape[1], dtype=np.float64), shape[0]))
    np.testing.assert_array_equal(detector._ys, np.repeat(np.arange(shape[0], dtype=np.float64), shape[1]))


def assert_same(name, before, after):
    if before.n != after.n:
        raise AssertionError(f"{name}: blob count {before.n} != {after.n}")
    for field in ("x", "y", "u", "v", "bbox", "partial", "X"):
        np.testing.assert_array_equal(getattr(before, field), getattr(after, field),
                                      err_msg=f"{name}: {field}")


def timing(detector, frame, iterations):
    for _ in range(10):
        detector.detect(frame, 0.0)
    elapsed = np.empty(iterations)
    for i in range(iterations):
        start = time.perf_counter_ns()
        detector.detect(frame, 0.0)
        elapsed[i] = (time.perf_counter_ns() - start) / 1e6
    return np.percentile(elapsed, (50, 95))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=50)
    parser.add_argument("--max-p50-ms", type=float,
                        help="fail when the new detector p50 exceeds this limit")
    args = parser.parse_args()

    assert_constructor_cache()
    old_cls = old_inspector()
    cases = samples()
    for name, real in {
        "preview": HERE / "runs/preview/inspection_raw.png",
        "validation": HERE / "runs/validation/inspection_raw.png",
    }.items():
        if real.exists():
            cases[name] = cv2.cvtColor(cv2.imread(str(real)), cv2.COLOR_BGR2RGB)

    benchmark = None
    benchmark_name = None
    reused = inspector(Inspector, SHAPE)
    for name, frame in cases.items():
        before = inspector(old_cls, frame.shape[:2]).detect(frame, 1.25)
        reused.L.cam_h, reused.L.cam_w = frame.shape[:2]
        after = reused.detect(frame, 1.25)
        assert_same(name, before, after)
        print(f"equivalent {name:15s} blobs={after.n} contiguous={frame.flags.c_contiguous}")
        if after.n:
            benchmark = frame
            benchmark_name = name
    if benchmark is None:
        raise AssertionError("no non-empty frame to benchmark")

    new_ms = timing(inspector(Inspector, benchmark.shape[:2]), benchmark, args.iterations)
    old_ms = timing(inspector(old_cls, benchmark.shape[:2]), benchmark, args.iterations)
    f = benchmark.astype(np.int16)
    foreground = ~((f[..., 2] > f[..., 0] + 35) & (f[..., 2] > f[..., 1] + 10))
    print(f"OpenCV threads={cv2.getNumThreads()} OMP_NUM_THREADS={os.environ.get('OMP_NUM_THREADS')} "
          f"OPENBLAS_NUM_THREADS={os.environ.get('OPENBLAS_NUM_THREADS')}")
    print(f"benchmark context={benchmark_name} shape={benchmark.shape[:2]} "
          f"foreground={np.count_nonzero(foreground)}/{foreground.size} "
          f"density={foreground.mean():.3%}")
    print(f"old detect: p50={old_ms[0]:.3f} ms p95={old_ms[1]:.3f} ms")
    print(f"new detect: p50={new_ms[0]:.3f} ms p95={new_ms[1]:.3f} ms")
    print(f"<5 ms target: {'met' if new_ms[0] < 5.0 else 'missed'}")
    if args.max_p50_ms is not None and new_ms[0] > args.max_p50_ms:
        raise AssertionError(f"new p50 {new_ms[0]:.3f} ms exceeds {args.max_p50_ms:.3f} ms")


if __name__ == "__main__":
    main()
