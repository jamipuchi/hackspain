"""Camera sources without hardware. RealCamera on the phone runs only with LINE_HW=1."""
import os

import cv2
import numpy as np
import pytest

from line import config
from line.camera_source import FileCamera, RealCamera, SyntheticCamera, diagnose_missing, resolve_camera
from line.contracts import Frame, FrameSource

STATUS_KEYS = {"name", "resolution", "fps_measured", "grab_latency_ms", "frames", "dropped"}


def test_resolve_camera_by_name_skips_desk_view():
    devices = [(0, "Jaume's iPhone Camera"), (1, "MacBook Pro Camera"), (2, "Jaume's iPhone Desk View Camera")]
    assert resolve_camera("iphone", devices) == (0, "Jaume's iPhone Camera")
    assert resolve_camera("macbook", devices) == (1, "MacBook Pro Camera")
    with pytest.raises(LookupError):
        resolve_camera("webcam", devices)


def test_diagnose_missing_says_why(monkeypatch):
    monkeypatch.setattr("line.camera_source.iphone_on_usb", lambda: True)
    assert "unlock" in diagnose_missing("iphone")
    monkeypatch.setattr("line.camera_source.iphone_on_usb", lambda: False)
    assert "plug it in" in diagnose_missing("iphone")
    assert "webcam" in diagnose_missing("webcam")


def test_real_camera_reports_error_instead_of_raising_when_absent(monkeypatch):
    monkeypatch.setattr("line.camera_source.list_cameras", lambda: [(0, "MacBook Pro Camera")])
    monkeypatch.setattr("line.camera_source.iphone_on_usb", lambda: False)
    cam = RealCamera(config.LineConfig(), match="iphone")
    assert not cam.ok and "plug it in" in cam.error and "MacBook Pro Camera" in cam.detail
    st = cam.status()
    assert STATUS_KEYS <= set(st) and st["ok"] is False
    checks = cam.selftest()
    assert checks and not checks[0].ok and "plug it in" in checks[0].detail
    with pytest.raises(RuntimeError):
        cam.grab()
    cam.close()


def test_synthetic_is_deterministic_and_moves_beans():
    cfg = config.LineConfig()
    a, b = SyntheticCamera(cfg, seed=3), SyntheticCamera(cfg, seed=3)
    fa = [a.grab() for _ in range(20)]
    fb = [b.grab() for _ in range(20)]
    assert all(np.array_equal(x.bgr, y.bgr) for x, y in zip(fa, fb))
    assert isinstance(fa[0], Frame) and fa[0].bgr.shape == (cfg.camera.height, cfg.camera.width, 3)
    assert [f.seq for f in fa] == list(range(20))
    assert fa[1].t - fa[0].t == pytest.approx(1.0 / cfg.camera.fps)
    assert a.truth(), "beans should have spawned within 20 frames"
    # something dark is in the frame once a bean is in view, and it moves along +x between frames
    us = [t["u"] for t in a.truth()]
    a.grab()
    us2 = [t["u"] for t in a.truth() if t["id"] in {t2["id"] for t2 in a.truth()}]
    assert us2 and max(us2) > max(us)
    assert STATUS_KEYS <= set(a.status())
    assert isinstance(a, FrameSource)


def test_synthetic_different_seed_differs():
    cfg = config.LineConfig()
    a, b = SyntheticCamera(cfg, seed=1), SyntheticCamera(cfg, seed=2)
    for _ in range(15):
        fa, fb = a.grab(), b.grab()
    assert not np.array_equal(fa.bgr, fb.bgr)


def test_synthetic_flow_axis_y():
    cfg = config.LineConfig()
    cfg.camera.flow_axis = "-y"
    cam = SyntheticCamera(cfg, seed=0)
    for _ in range(12):
        cam.grab()
    vs = [t["v"] for t in cam.truth()]
    cam.grab()
    vs2 = [t["v"] for t in cam.truth()]
    assert vs and vs2 and min(vs2) < min(vs)


def test_file_camera_replays_folder_and_loops(tmp_path):
    for i in range(3):
        img = np.full((40, 60, 3), i * 40, np.uint8)
        cv2.imwrite(str(tmp_path / f"f{i}.png"), img)
    cam = FileCamera(tmp_path, fps=10)
    assert len(cam) == 3
    frames = [cam.grab() for _ in range(7)]
    assert [int(f.bgr[0, 0, 0]) for f in frames] == [0, 40, 80, 0, 40, 80, 0]
    assert frames[1].t - frames[0].t == pytest.approx(0.1)
    assert cam.loops == 2
    st = cam.status()
    assert STATUS_KEYS <= set(st) and st["resolution"] == [60, 40]
    assert cam.selftest()[0].ok
    cam.close()


def test_file_camera_no_loop_stops(tmp_path):
    cv2.imwrite(str(tmp_path / "a.png"), np.zeros((8, 8, 3), np.uint8))
    cam = FileCamera([tmp_path / "a.png"], fps=5, loop=False)
    cam.grab()
    with pytest.raises(StopIteration):
        cam.grab()


def test_file_camera_missing_reports_error(tmp_path):
    cam = FileCamera(tmp_path / "nothing", fps=5)
    assert cam.error and not cam.selftest()[0].ok


def test_file_camera_realtime_paces(tmp_path):
    import time

    cv2.imwrite(str(tmp_path / "a.png"), np.zeros((8, 8, 3), np.uint8))
    cam = FileCamera(tmp_path, fps=50, realtime=True)
    t0 = time.monotonic()
    for _ in range(6):
        cam.grab()
    assert time.monotonic() - t0 >= 5 * 0.02 * 0.8


@pytest.mark.skipif(os.environ.get("LINE_HW") != "1", reason="needs the iPhone; run with LINE_HW=1")
def test_real_iphone_selftest():
    cam = RealCamera(config.load())
    try:
        checks = cam.selftest()
        for c in checks:
            print(c)
        assert all(c.ok for c in checks)
        st = cam.status()
        assert st["fps_measured"] > 20 and st["grab_latency_ms"] < 100
    finally:
        cam.close()
