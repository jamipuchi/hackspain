"""paddle_angle tool: mark detection, angle maths, circle fit, plateau segmentation — on synthetic images/series."""
import math
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import paddle_angle as pa  # noqa: E402


def scene(angle_deg, two_marks=True, w=400, h=300, pivot=(200, 150), arm=110):
    img = np.full((h, w, 3), 236, np.uint8)
    cv2.rectangle(img, (pivot[0] - 30, pivot[1] - 20), (pivot[0] + 30, pivot[1] + 20), (200, 120, 30), -1)  # blue servo case (BGR)
    a = math.radians(angle_deg)
    tip = (int(pivot[0] + arm * math.cos(a)), int(pivot[1] + arm * math.sin(a)))
    base = (int(pivot[0] + 40 * math.cos(a)), int(pivot[1] + 40 * math.sin(a)))
    cv2.line(img, pivot, tip, (250, 250, 250), 9)  # white paddle on white paper (invisible to the tracker on purpose)
    cv2.circle(img, tip, 6, (15, 15, 15), -1)
    if two_marks:
        cv2.circle(img, base, 5, (15, 15, 15), -1)
    # an orange wire that must NOT count as a mark
    cv2.line(img, (20, 280), (380, 260), (0, 90, 230), 3)
    return img, tip, base


def test_find_servo_body_and_auto_roi():
    img, *_ = scene(30)
    x, y, w, h = pa.find_servo_body(img)
    assert abs(x + w / 2 - 200) < 3 and abs(y + h / 2 - 150) < 3
    roi = pa.auto_roi(img, arm_px=120)
    assert roi.x0 == max(x - 120, 0) and roi.x1 == min(x + w + 120, 400)


@pytest.mark.parametrize("deg", [0, 30, -45, 120, 179])
def test_two_marks_angle(deg):
    img, tip, base = scene(deg)
    marks = pa.find_marks(img)
    assert len(marks) == 2, marks
    m = sorted(marks, key=lambda m: -math.hypot(m[0] - 200, m[1] - 150))
    got = pa.angle_two_marks(m[0], m[1])
    diff = (got - deg + 180) % 360 - 180
    assert abs(diff) < 2.0


def test_one_mark_pivot_from_circle_fit():
    pts = []
    for deg in (20, 45, 70, 95):
        img, tip, _ = scene(deg, two_marks=False)
        marks = pa.find_marks(img)
        assert len(marks) == 1
        pts.append(marks[0][:2])
    cx, cy, r = pa.fit_circle(np.array(pts))
    assert abs(cx - 200) < 3 and abs(cy - 150) < 3 and abs(r - 110) < 4
    got = pa.angle_about_pivot(pts[1], (cx, cy))
    assert abs(got - 45) < 2.5


def test_marks_ignore_blue_case_and_orange_wire():
    img, *_ = scene(60, two_marks=False)
    marks = pa.find_marks(img, k=5)
    assert len(marks) == 1  # only the tip dot; case and wire are saturated colours


def test_unwrap():
    assert pa.unwrap_deg(170, -175) == pytest.approx(185)
    assert pa.unwrap_deg(-170, 175) == pytest.approx(-185)
    assert pa.unwrap_deg(None, 33) == 33


def test_plateaus_and_settling():
    ts, ang = [], []
    t = 0.0
    def hold(a, secs, noise=0.3):
        nonlocal t
        rng = np.random.default_rng(int(a))
        for _ in range(int(secs * 30)):
            ts.append(t); ang.append(a + rng.normal(0, noise)); t += 1 / 30
    def move(a0, a1, secs):
        nonlocal t
        for k in range(int(secs * 30)):
            ts.append(t); ang.append(a0 + (a1 - a0) * (k + 1) / int(secs * 30)); t += 1 / 30
    hold(90, 1.0); move(90, 115, 0.3); hold(115, 1.0); move(115, 90, 0.3); hold(90.8, 1.0)
    pl = pa.plateaus(ts, ang, tol_deg=1.5, hold_s=0.4)
    assert len(pl) == 3
    assert [round(p.mean) for p in pl] == [90, 115, 91]
    assert pl[1].settle_s == pytest.approx(0.3, abs=0.12)
    txt = pa.format_plateaus(pl, ts[0])
    assert "spread of plateau angles" in txt and "plateau" in txt
    assert pa.plateaus(ts[:5], ang[:5]) == []
