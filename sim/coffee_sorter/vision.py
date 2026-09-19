"""Inspection camera + fast blob detector + feature extraction.

Everything the sorter *knows* about a bean comes through here: an RGB frame of the belt strip,
segmented against the blue belt, one feature vector per blob. No ground truth leaks in.
Designed for throughput: one connected-components pass + bincount statistics, no per-blob loops
except the final packing.
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import cv2
import mujoco

FEATURES = [
    "area_mm2", "major_mm", "minor_mm", "aspect", "fill", "solidity",
    "mean_r", "mean_g", "mean_b", "mean_gray", "std_gray",
    "mean_h", "mean_s", "mean_v", "std_s",
    "dark_frac", "bright_frac", "n_dark_spots", "dark_spot_area_frac",
    "bbox_w_mm", "bbox_h_mm", "rg_ratio", "gb_ratio",
]
DARK_T, BRIGHT_T, SPOT_T = 60, 175, 75
MIN_AREA_PX = 30


@dataclass
class Blobs:
    t: float
    n: int
    x: np.ndarray          # world x (m) of centroid
    y: np.ndarray          # world y (m)
    u: np.ndarray          # pixel col
    v: np.ndarray          # pixel row
    bbox: np.ndarray       # (n,4) x,y,w,h px
    partial: np.ndarray    # touching top/bottom edge -> not fully visible yet
    X: np.ndarray          # (n, F) features


class Inspector:
    def __init__(self, sim, cull_margin=0.06):
        self.sim, L = sim, sim.L
        self.L = L
        self.r = mujoco.Renderer(sim.model, L.cam_h, L.cam_w)
        self.r._scene_option.geomgroup[3] = 0      # collision geoms never drawn
        self.r._scene_option.geomgroup[4] = 0      # culled (off-strip) skins
        self.r._scene_option.geomgroup[1] = 0      # machine frame not needed in the strip (belt is group 2)
        self.base_group = sim.model.geom_group[sim.geom_of].copy()
        self.cull_margin = cull_margin
        self.ppm = L.px_per_m
        self.bg = np.array(sim.P.belt_rgb) * 255
        self._grid_shape = (L.cam_h, L.cam_w)
        self._xs = np.tile(np.arange(L.cam_w, dtype=np.float64), L.cam_h)
        self._ys = np.repeat(np.arange(L.cam_h, dtype=np.float64), L.cam_w)

    # -------- geometry: image rows run along +x (belt travel), columns along +y
    def pixel_to_world(self, u, v):
        x = self.L.cam_x + (v - self.L.cam_h / 2 + 0.5) / self.ppm
        y = (u - self.L.cam_w / 2 + 0.5) / self.ppm
        return x, y

    def world_to_pixel(self, x, y):
        v = (x - self.L.cam_x) * self.ppm + self.L.cam_h / 2 - 0.5
        u = y * self.ppm + self.L.cam_w / 2 - 0.5
        return u, v

    def capture(self):
        sim, m = self.sim, self.sim.model
        qa = sim.qpos_adr
        near = np.abs(m.body_pos[0, 0] * 0 + sim.data.qpos[qa] - self.L.cam_x) < (self.L.cam_fov / 2 + self.cull_margin)
        near &= sim.active
        m.geom_group[sim.geom_of] = np.where(near, self.base_group, 4)
        self.r.update_scene(sim.data, "inspect")
        # the renderer draws the kinematics computed at the start of the last step -> that is the exposure time
        return self.r.render(), sim.data.time - m.opt.timestep

    # -------- detection + features
    def detect(self, frame, t) -> Blobs:
        H, W, _ = frame.shape
        r, g, b = cv2.split(frame)
        belt = cv2.bitwise_and(cv2.compare(cv2.subtract(b, r), 35, cv2.CMP_GT),
                               cv2.compare(cv2.subtract(b, g), 10, cv2.CMP_GT))
        mask = cv2.bitwise_not(belt)
        n, labels, stats, cents = cv2.connectedComponentsWithStatsWithAlgorithm(
            mask, 8, cv2.CV_32S, cv2.CCL_DEFAULT)
        if n <= 1:
            return Blobs(t, 0, *[np.zeros(0)] * 4, np.zeros((0, 4), int), np.zeros(0, bool), np.zeros((0, len(FEATURES))))
        keep = np.where(stats[1:, cv2.CC_STAT_AREA] >= MIN_AREA_PX)[0] + 1
        if len(keep) == 0:
            return Blobs(t, 0, *[np.zeros(0)] * 4, np.zeros((0, 4), int), np.zeros(0, bool), np.zeros((0, len(FEATURES))))
        if self._grid_shape != (H, W):
            self._grid_shape = (H, W)
            self._xs = np.tile(np.arange(W, dtype=np.float64), H)
            self._ys = np.repeat(np.arange(H, dtype=np.float64), W)
        idx = np.flatnonzero(mask)
        lab = labels.ravel()[idx]
        area = stats[:, cv2.CC_STAT_AREA].astype(np.float64)
        area_safe = np.maximum(area, 1)
        pixels = frame.reshape(-1, 3)[idx]
        rgb = pixels.astype(np.int16)
        gray = (0.299 * rgb[:, 0] + 0.587 * rgb[:, 1] + 0.114 * rgb[:, 2]).astype(np.float32)
        hsv = cv2.cvtColor(pixels.reshape(-1, 1, 3), cv2.COLOR_RGB2HSV).reshape(-1, 3)
        hh, ss, vv = hsv[:, 0], hsv[:, 1].astype(np.float32), hsv[:, 2]

        def bmean(w): return np.bincount(lab, weights=w, minlength=n) / area_safe
        mr, mg, mb = bmean(rgb[:, 0]), bmean(rgb[:, 1]), bmean(rgb[:, 2])
        mgray = bmean(gray); sgray = np.sqrt(np.maximum(bmean(gray * gray) - mgray ** 2, 0))
        mh, ms, mv = bmean(hh), bmean(ss), bmean(vv)
        ssat = np.sqrt(np.maximum(bmean(ss * ss) - ms ** 2, 0))
        dark_frac = bmean(gray < DARK_T)
        bright_frac = bmean(gray > BRIGHT_T)
        # second moments -> equivalent ellipse
        xs, ys = self._xs[idx], self._ys[idx]
        mx, my = cents[:, 0], cents[:, 1]
        cxx = bmean(xs * xs) - mx ** 2; cyy = bmean(ys * ys) - my ** 2; cxy = bmean(xs * ys) - mx * my
        tr, det = cxx + cyy, cxx * cyy - cxy ** 2
        disc = np.sqrt(np.maximum(tr * tr / 4 - det, 0))
        l1, l2 = tr / 2 + disc, np.maximum(tr / 2 - disc, 1e-6)
        major, minor = 4 * np.sqrt(l1), 4 * np.sqrt(l2)
        # dark spots (insect holes, mould) inside blobs: one extra components pass on the whole strip
        spot_mask = np.zeros(H * W, np.uint8)
        spot_mask[idx] = gray < SPOT_T
        spot_mask = spot_mask.reshape(H, W)
        ns, slab, sstats, scents = cv2.connectedComponentsWithStatsWithAlgorithm(
            spot_mask, 8, cv2.CV_32S, cv2.CCL_DEFAULT)
        n_spots = np.zeros(n); spot_area = np.zeros(n)
        if ns > 1:
            sc = np.clip(np.round(scents[1:]).astype(int), 0, [W - 1, H - 1])
            owner = labels[sc[:, 1], sc[:, 0]]
            sa = sstats[1:, cv2.CC_STAT_AREA]
            ok = (owner > 0) & (sa >= 4)
            n_spots = np.bincount(owner[ok], minlength=n).astype(float)
            spot_area = np.bincount(owner[ok], weights=sa[ok], minlength=n)
        st = stats[keep]
        w_px, h_px = st[:, cv2.CC_STAT_WIDTH].astype(float), st[:, cv2.CC_STAT_HEIGHT].astype(float)
        k = keep
        mm = 1000.0 / self.ppm
        X = np.stack([
            area[k] * mm * mm, major[k] * mm, minor[k] * mm, major[k] / np.maximum(minor[k], 1e-6),
            area[k] / np.maximum(w_px * h_px, 1), area[k] / np.maximum(np.pi / 4 * major[k] * minor[k], 1e-6),
            mr[k], mg[k], mb[k], mgray[k], sgray[k], mh[k], ms[k], mv[k], ssat[k],
            dark_frac[k], bright_frac[k], n_spots[k], spot_area[k] / area_safe[k],
            w_px * mm, h_px * mm, (mr[k] + 1) / (mg[k] + 1), (mg[k] + 1) / (mb[k] + 1),
        ], 1)
        top, bottom = st[:, cv2.CC_STAT_TOP], st[:, cv2.CC_STAT_TOP] + h_px
        partial = (top <= 0) | (bottom >= H) | (st[:, cv2.CC_STAT_LEFT] <= 0) | (st[:, cv2.CC_STAT_LEFT] + w_px >= W)
        u, v = cents[k, 0], cents[k, 1]
        x, y = self.pixel_to_world(u, v)
        return Blobs(t, len(k), x, y, u, v, st[:, :4], partial, X)

    def close(self): self.r.close()


def draw_blobs(frame, blobs: Blobs, labels=None, colors=None, scale=1.0):
    """Overlay boxes on an inspection frame (for humans)."""
    out = frame.copy()
    for i in range(blobs.n):
        x, y, w, h = blobs.bbox[i]
        c = (200, 200, 200) if colors is None else colors[i]
        cv2.rectangle(out, (int(x), int(y)), (int(x + w), int(y + h)), c, 1)
        if labels is not None:
            cv2.putText(out, labels[i], (int(x), max(int(y) - 2, 8)), cv2.FONT_HERSHEY_PLAIN, 0.9, c, 1, cv2.LINE_AA)
    if scale != 1.0:
        out = cv2.resize(out, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return out
