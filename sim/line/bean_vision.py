"""Dark beans on white paper -> blobs with features. Owner: camera agent. Implements `contracts.BeanDetector`.

`PaperBeanDetector(cfg)` segments dark objects against the bright paper floor of the chute inside the ROI
(cfg.camera.zone unless a ROI is passed), runs one connected-components pass and computes every feature
with bincount statistics over the ROI, like coffee_sorter/vision.py (same feature names where the meaning
is the same). mm scale comes from cfg.camera.px_per_mm. Blob coordinates are FULL-frame pixels.

Segmentation (the only thing that differs from the belt sorter): the paper is bright (median gray >
cfg.vision.paper_gray_min); bean pixels are darker than cfg.vision.bean_gray_max. If the paper looks dim
(a shadow, a dark desk) the threshold adapts to `paper_median - 45`, so a bean stays a bean when the room
light changes; the value actually used is reported in status().
"""
from __future__ import annotations

import time
from collections import deque

import cv2
import numpy as np

from line.config import LineConfig, load as load_config
from line.contracts import Blob, Check, Frame, ROI, now

FEATURES = [
    "area_mm2", "major_mm", "minor_mm", "aspect", "fill", "solidity",
    "mean_r", "mean_g", "mean_b", "mean_gray", "std_gray",
    "mean_h", "mean_s", "mean_v", "std_s",
    "dark_frac", "bright_frac", "n_dark_spots", "dark_spot_area_frac",
    "bbox_w_mm", "bbox_h_mm", "rg_ratio", "gb_ratio",
    "area_px",
]
DARK_T, BRIGHT_T = 60, 175  # same meaning as coffee_sorter.vision: fraction of very dark / very bright pixels


class PaperBeanDetector:
    name = "paper_bean_detector"

    def __init__(self, cfg: LineConfig | None = None):
        self.cfg = cfg or load_config()
        self._times = deque(maxlen=200)
        self.n_frames = 0
        self.n_blobs_last = 0
        self.paper_median = 0.0
        self.threshold_used = 0
        self.paper_ok = True
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))

    # -- geometry
    def default_roi(self, frame_shape) -> ROI:
        H, W = frame_shape[:2]
        x0, y0, x1, y1 = self.cfg.camera.zone
        return ROI(max(0, min(x0, W - 1)), max(0, min(y0, H - 1)), max(1, min(x1, W)), max(1, min(y1, H)))

    def mask(self, bgr_roi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(mask uint8 0/1 of bean pixels, gray float32) for a ROI crop."""
        v = self.cfg.vision
        gray = cv2.cvtColor(bgr_roi, cv2.COLOR_BGR2GRAY)
        med = float(np.median(gray[::4, ::4]))
        self.paper_median = med
        self.paper_ok = med >= v.paper_gray_min
        thr = int(min(v.bean_gray_max, med - 45)) if med > 60 else int(v.bean_gray_max)
        self.threshold_used = max(thr, 10)
        m = (gray < self.threshold_used).astype(np.uint8)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, self._kernel)
        return m, gray

    # -- BeanDetector
    def detect(self, frame: Frame, roi: ROI | None = None) -> list[Blob]:
        t0 = time.perf_counter()
        roi = roi or self.default_roi(frame.bgr.shape)
        crop = roi.crop(frame.bgr)
        blobs = self._detect_crop(crop, roi.x0, roi.y0)
        self._times.append((time.perf_counter() - t0) * 1000.0)
        self.n_frames += 1
        self.n_blobs_last = len(blobs)
        return blobs

    def _detect_crop(self, crop: np.ndarray, ox: int, oy: int) -> list[Blob]:
        v = self.cfg.vision
        H, W = crop.shape[:2]
        if H < 2 or W < 2:
            return []
        mask, gray = self.mask(crop)
        n, labels, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
        if n <= 1:
            return []
        areas = stats[1:, cv2.CC_STAT_AREA]
        keep = np.where((areas >= v.min_area_px) & (areas <= v.max_area_px))[0] + 1
        if len(keep) == 0:
            return []

        # every statistic below runs over FOREGROUND pixels only (a few thousand), not the whole ROI:
        # bincount over the full 150k-px ROI costs ~25 ms per frame, over the foreground < 2 ms.
        fg = np.flatnonzero(mask.ravel())
        lab = labels.ravel()[fg]
        area = np.bincount(lab, minlength=n).astype(np.float64)
        area_safe = np.maximum(area, 1)
        g = gray.ravel()[fg].astype(np.float32)
        bgr_fg = crop.reshape(-1, 3)[fg].astype(np.float32)
        b_, g_, r_ = bgr_fg[:, 0], bgr_fg[:, 1], bgr_fg[:, 2]
        hsv_fg = cv2.cvtColor(crop.reshape(-1, 1, 3)[fg], cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(np.float32)
        hh, ss, vv = hsv_fg[:, 0], hsv_fg[:, 1], hsv_fg[:, 2]

        def bmean(w):
            return np.bincount(lab, weights=w, minlength=n) / area_safe

        mr, mg, mb = bmean(r_), bmean(g_), bmean(b_)
        mgray = bmean(g)
        sgray = np.sqrt(np.maximum(bmean(g * g) - mgray**2, 0))
        mh, ms, mv = bmean(hh), bmean(ss), bmean(vv)
        ssat = np.sqrt(np.maximum(bmean(ss * ss) - ms**2, 0))
        dark_frac = bmean((g < DARK_T).astype(np.float32))
        bright_frac = bmean((g > BRIGHT_T).astype(np.float32))
        # second moments -> equivalent ellipse axes (full lengths)
        ys, xs = np.divmod(fg, W)
        xs = xs.astype(np.float64)
        ys = ys.astype(np.float64)
        mx, my = bmean(xs), bmean(ys)
        cxx = bmean(xs * xs) - mx**2
        cyy = bmean(ys * ys) - my**2
        cxy = bmean(xs * ys) - mx * my
        tr, det = cxx + cyy, cxx * cyy - cxy**2
        disc = np.sqrt(np.maximum(tr * tr / 4 - det, 0))
        l1, l2 = tr / 2 + disc, np.maximum(tr / 2 - disc, 1e-6)
        major, minor = 4 * np.sqrt(l1), 4 * np.sqrt(l2)
        # dark spots inside beans (insect holes, mould, burnt patches)
        spot_mask = np.zeros(H * W, np.uint8)
        spot_mask[fg[g < v.dark_spot_gray]] = 1
        spot_mask = spot_mask.reshape(H, W)
        n_spots = np.zeros(n)
        spot_area = np.zeros(n)
        ns, _slab, sstats, scents = cv2.connectedComponentsWithStats(spot_mask, connectivity=8)
        if ns > 1:
            sc = np.clip(np.round(scents[1:]).astype(int), 0, [W - 1, H - 1])
            owner = labels[sc[:, 1], sc[:, 0]]
            sa = sstats[1:, cv2.CC_STAT_AREA].astype(float)
            ok = (owner > 0) & (sa >= 4) & (sa < area_safe[owner] * 0.6)  # a spot is a fraction of the bean, not the bean
            n_spots = np.bincount(owner[ok], minlength=n).astype(float)
            spot_area = np.bincount(owner[ok], weights=sa[ok], minlength=n)

        mm = 1.0 / self.cfg.camera.px_per_mm
        out: list[Blob] = []
        for k in keep:
            x, y, w, h, a = (int(stats[k, i]) for i in (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP, cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT, cv2.CC_STAT_AREA))
            partial = x <= 0 or y <= 0 or x + w >= W or y + h >= H
            feats = {
                "area_mm2": a * mm * mm, "major_mm": major[k] * mm, "minor_mm": minor[k] * mm,
                "aspect": major[k] / max(minor[k], 1e-6), "fill": a / max(w * h, 1),
                "solidity": a / max(np.pi / 4 * major[k] * minor[k], 1e-6),
                "mean_r": mr[k], "mean_g": mg[k], "mean_b": mb[k], "mean_gray": mgray[k], "std_gray": sgray[k],
                "mean_h": mh[k], "mean_s": ms[k], "mean_v": mv[k], "std_s": ssat[k],
                "dark_frac": dark_frac[k], "bright_frac": bright_frac[k],
                "n_dark_spots": n_spots[k], "dark_spot_area_frac": spot_area[k] / area_safe[k],
                "bbox_w_mm": w * mm, "bbox_h_mm": h * mm,
                "rg_ratio": (mr[k] + 1) / (mg[k] + 1), "gb_ratio": (mg[k] + 1) / (mb[k] + 1),
                "area_px": float(a),
            }
            out.append(Blob(u=float(cents[k, 0] + ox), v=float(cents[k, 1] + oy), bbox=(x + ox, y + oy, w, h),
                            area_px=float(a), partial=bool(partial), features={f: float(feats[f]) for f in FEATURES}))
        return out

    # -- Module
    def status(self) -> dict:
        ts = list(self._times)
        return {"name": self.name, "features": len(FEATURES), "n_frames": self.n_frames, "n_blobs_last": self.n_blobs_last,
                "last_ms": round(ts[-1], 2) if ts else 0.0, "mean_ms": round(float(np.mean(ts)), 2) if ts else 0.0,
                "paper_gray_median": round(self.paper_median, 1), "paper_ok": self.paper_ok, "threshold_used": self.threshold_used,
                "px_per_mm": self.cfg.camera.px_per_mm, "zone": list(self.cfg.camera.zone)}

    def selftest(self) -> list[Check]:
        from line.camera_source import SyntheticCamera

        checks: list[Check] = []
        cam = SyntheticCamera(self.cfg, seed=1, spawn_every_s=0.35)
        roi = self.default_roi((cam.h, cam.w))
        found = 0
        whole = 0
        ms = []
        t0 = now()
        for _ in range(int(cam.fps * 1.5)):
            f = cam.grab()
            t1 = time.perf_counter()
            blobs = self.detect(f, roi)
            ms.append((time.perf_counter() - t1) * 1000)
            found += len(blobs)
            whole += sum(not b.partial for b in blobs)
        med = float(np.median(ms))
        checks.append(Check("detects synthetic beans", found > 0 and whole > 0, f"{found} blobs, {whole} whole, in {len(ms)} frames", (now() - t0) * 1000))
        checks.append(Check("< 15 ms per frame", med < 15.0, f"median {med:.2f} ms, max {max(ms):.2f} ms on {roi.w}x{roi.h} ROI", med))
        checks.append(Check("paper looks white", self.paper_ok, f"median gray {self.paper_median:.0f} (min {self.cfg.vision.paper_gray_min}), threshold {self.threshold_used}"))
        return checks

    def close(self) -> None:
        pass


def draw_blobs(bgr: np.ndarray, blobs: list[Blob], roi: ROI | None = None, labels: list[str] | None = None) -> np.ndarray:
    """Overlay ROI + boxes for humans (capture tool, panel)."""
    out = bgr.copy()
    if roi is not None:
        cv2.rectangle(out, (roi.x0, roi.y0), (roi.x1 - 1, roi.y1 - 1), (0, 200, 255), 2)
    for i, b in enumerate(blobs):
        x, y, w, h = b.bbox
        c = (0, 165, 255) if b.partial else (0, 220, 0)
        cv2.rectangle(out, (x, y), (x + w, y + h), c, 2)
        txt = labels[i] if labels else f"{b.features.get('major_mm', 0):.1f}x{b.features.get('minor_mm', 0):.1f}mm g{b.features.get('mean_gray', 0):.0f}"
        cv2.putText(out, txt, (x, max(y - 4, 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, c, 1, cv2.LINE_AA)
    return out
