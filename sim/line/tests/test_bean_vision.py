"""PaperBeanDetector on synthetic frames: finds beans, features are sane, fast enough, partial flag works."""
import time

import cv2
import numpy as np
import pytest

from line import config
from line.bean_vision import FEATURES, PaperBeanDetector, draw_blobs
from line.camera_source import SyntheticCamera
from line.contracts import BeanDetector, Blob, Frame, ROI


def paper(w=640, h=400, gray=235):
    return np.full((h, w, 3), gray, np.uint8)


def frame(bgr, t=1.0, seq=0):
    return Frame(bgr=bgr, t=t, seq=seq, source="test")


def cfg_small():
    c = config.LineConfig()
    c.camera.width, c.camera.height = 640, 400
    c.camera.zone = [100, 50, 540, 350]
    c.camera.px_per_mm = 6.0
    return c


def test_single_bean_features_and_scale():
    c = cfg_small()
    det = PaperBeanDetector(c)
    img = paper()
    cv2.ellipse(img, (320, 200), (36, 21), 0, 0, 360, (40, 60, 90), -1)  # 72x42 px -> 12x7 mm at 6 px/mm
    blobs = det.detect(frame(img))
    assert len(blobs) == 1
    b = blobs[0]
    assert isinstance(b, Blob) and not b.partial
    assert set(b.features) == set(FEATURES)
    assert b.u == pytest.approx(320, abs=1.5) and b.v == pytest.approx(200, abs=1.5)
    assert b.features["major_mm"] == pytest.approx(12.0, rel=0.08)
    assert b.features["minor_mm"] == pytest.approx(7.0, rel=0.1)
    assert 1.5 < b.features["aspect"] < 1.9
    assert 0.7 < b.features["fill"] < 0.85  # ellipse in its bbox = pi/4
    assert b.features["n_dark_spots"] == 0
    assert b.features["mean_gray"] < 100
    assert b.features["area_mm2"] == pytest.approx(np.pi * 6 * 3.5, rel=0.1)
    assert isinstance(det, BeanDetector)


def test_dark_spots_counted():
    c = cfg_small()
    det = PaperBeanDetector(c)
    img = paper()
    cv2.ellipse(img, (320, 200), (36, 21), 0, 0, 360, (70, 90, 120), -1)
    cv2.circle(img, (305, 198), 4, (10, 10, 10), -1)
    cv2.circle(img, (338, 204), 4, (10, 10, 10), -1)
    b = det.detect(frame(img))[0]
    assert b.features["n_dark_spots"] == 2
    assert 0 < b.features["dark_spot_area_frac"] < 0.1


def test_partial_when_touching_roi_edge_and_full_frame_coords():
    c = cfg_small()
    det = PaperBeanDetector(c)
    img = paper()
    cv2.ellipse(img, (110, 200), (36, 21), 0, 0, 360, (40, 60, 90), -1)  # crosses ROI x0=100
    cv2.ellipse(img, (400, 120), (30, 18), 20, 0, 360, (40, 60, 90), -1)  # fully inside
    blobs = sorted(det.detect(frame(img)), key=lambda b: b.u)
    assert len(blobs) == 2
    assert blobs[0].partial and not blobs[1].partial
    x, y, w, h = blobs[1].bbox
    assert x <= 400 <= x + w and y <= 120 <= y + h  # full-frame pixels, not ROI pixels


def test_explicit_roi_and_empty_paper():
    c = cfg_small()
    det = PaperBeanDetector(c)
    img = paper()
    cv2.ellipse(img, (320, 200), (36, 21), 0, 0, 360, (40, 60, 90), -1)
    assert det.detect(frame(img), ROI(0, 0, 200, 100)) == []
    assert det.detect(frame(paper())) == []
    st = det.status()
    assert st["paper_ok"] and st["n_frames"] == 2 and st["threshold_used"] > 0


def test_size_filters_and_noise():
    c = cfg_small()
    det = PaperBeanDetector(c)
    img = paper()
    cv2.circle(img, (300, 200), 6, (30, 30, 30), -1)  # ~113 px < min_area_px 400
    cv2.rectangle(img, (150, 80), (500, 330), (30, 30, 30), -1)  # 87k px > max_area_px 40000
    assert det.detect(frame(img)) == []
    rng = np.random.default_rng(0)
    noisy = np.clip(paper().astype(int) + rng.normal(0, 8, (400, 640, 3)), 0, 255).astype(np.uint8)
    assert det.detect(frame(noisy)) == []


def test_dim_light_adapts_threshold():
    c = cfg_small()
    det = PaperBeanDetector(c)
    img = paper(gray=140)  # below paper_gray_min: dim room / shadow
    cv2.ellipse(img, (320, 200), (36, 21), 0, 0, 360, (45, 50, 60), -1)
    blobs = det.detect(frame(img))
    assert len(blobs) == 1
    assert not det.paper_ok and det.threshold_used < c.vision.bean_gray_max


def test_synthetic_pipeline_and_speed():
    c = config.LineConfig()  # 1280x720, ROI 480x320
    cam = SyntheticCamera(c, seed=5, spawn_every_s=0.3)
    det = PaperBeanDetector(c)
    roi = det.default_roi((c.camera.height, c.camera.width))
    ms, whole, matched = [], 0, 0
    for _ in range(90):
        f = cam.grab()
        t0 = time.perf_counter()
        blobs = det.detect(f, roi)
        ms.append((time.perf_counter() - t0) * 1000)
        truth = cam.truth()
        for b in blobs:
            if b.partial:
                continue
            whole += 1
            if any(abs(t["u"] - b.u) < 4 and abs(t["v"] - b.v) < 4 for t in truth):
                matched += 1
            assert 7.0 < b.features["major_mm"] < 15.0, b.features
    assert whole > 5 and matched == whole
    assert float(np.median(ms)) < 15.0, f"median {np.median(ms):.2f} ms"


def test_draw_blobs_returns_copy():
    c = cfg_small()
    det = PaperBeanDetector(c)
    img = paper()
    cv2.ellipse(img, (320, 200), (36, 21), 0, 0, 360, (40, 60, 90), -1)
    f = frame(img)
    out = draw_blobs(img, det.detect(f), det.default_roi(img.shape))
    assert out.shape == img.shape and not np.array_equal(out, img)


def test_selftest_passes():
    checks = PaperBeanDetector(config.LineConfig()).selftest()
    assert all(ch.ok for ch in checks), [(ch.name, ch.detail) for ch in checks]


def _blob(u, v, partial=False, **feats):
    return Blob(u=u, v=v, bbox=(int(u) - 10, int(v) - 10, 20, 20), area_px=400.0, partial=partial, features=feats)


def test_is_still_two_frame_trigger():
    from line.bean_vision import is_still

    prev = [_blob(100, 100), _blob(300, 120)]
    cur = [_blob(101, 100), _blob(370, 120), _blob(500, 100, partial=True)]
    still = is_still(prev, cur, tol_px=3)
    assert [b.u for b in still] == [101]  # moved 1 px: still; moved 70 px: rolling; partial: ignored
    assert is_still([], cur) == []


def test_still_tracker_counts_consecutive_frames_and_averages():
    from line.bean_vision import StillTracker

    tr = StillTracker(n_frames=3, tol_px=3)
    assert tr.update([_blob(200, 100, mean_gray=80)]) == []
    assert tr.update([_blob(201, 100, mean_gray=90)]) == []
    still = tr.update([_blob(200, 101, mean_gray=100)])
    assert len(still) == 1
    assert tr.features_mean(still[0])["mean_gray"] == pytest.approx(90.0)
    # the bean rolls away: track breaks, a new one starts
    assert tr.update([_blob(280, 100, mean_gray=100)]) == []
    tr.reset()
    assert tr.update([_blob(200, 100)]) == []
