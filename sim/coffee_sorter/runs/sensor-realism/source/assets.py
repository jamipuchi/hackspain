"""Procedural textures and the half-bean mesh. Cheap, deterministic, regenerable."""
from __future__ import annotations

from pathlib import Path
import numpy as np
import cv2

ASSETS = Path(__file__).resolve().parent / "assets"
N_VARIANTS = 6           # textures per family (per-bean colour jitter comes from geom rgba)
TEX_H, TEX_W = 64, 128

# base colour is white-ish: the geom rgba tints it, so one texture family serves many colours.
FAMILIES = {
    # name: (mottle amplitude, crease darkness, n_holes range, speckle)
    "good":   (0.10, 0.60, (0, 0), 0.0),
    "faded":  (0.08, 0.75, (0, 0), 0.0),
    "black":  (0.05, 0.85, (0, 0), 0.0),
    "sour":   (0.14, 0.65, (0, 0), 0.15),
    "insect": (0.10, 0.60, (2, 6), 0.0),
    "roast":  (0.08, 0.45, (0, 0), 0.05),
}


def _texture(rng, mottle, crease, holes, speckle):
    img = np.ones((TEX_H, TEX_W, 3), np.float32)
    noise = cv2.GaussianBlur(rng.normal(0, 1, (TEX_H, TEX_W)).astype(np.float32), (0, 0), 3)
    img *= (1 + mottle * noise)[..., None]
    if speckle > 0:
        sp = cv2.GaussianBlur(rng.normal(0, 1, (TEX_H, TEX_W)).astype(np.float32), (0, 0), 1)
        img *= (1 - speckle * np.clip(sp, 0, None))[..., None]
    # centre crease (the flat face groove) : a dark band around v = 0.5
    v = np.linspace(0, 1, TEX_H)[:, None]
    band = np.exp(-((v - 0.5) / 0.035) ** 2)
    img *= (1 - (1 - crease) * band)[..., None]
    n = rng.integers(holes[0], holes[1] + 1) if holes[1] > 0 else 0
    for _ in range(n):
        cx, cy = int(rng.integers(8, TEX_W - 8)), int(rng.integers(6, TEX_H - 6))
        cv2.circle(img, (cx, cy), int(rng.integers(3, 6)), (0.10, 0.08, 0.06), -1)
        cv2.circle(img, (cx, cy), int(rng.integers(5, 8)), (0.55, 0.50, 0.45), 1)
    return (np.clip(img, 0, 1) * 255).astype(np.uint8)


def half_bean_obj(path: Path, n_u=24, n_v=12):
    """Unit half-ellipsoid (cut along the long axis' mid plane -> a 'broken' bean).
    Semi-axes 1,1,1; the geom's meshscale is fixed so size variety comes from 3 scale variants."""
    verts, uvs, faces = [], [], []
    us = np.linspace(0, np.pi, n_u)            # polar angle
    vs = np.linspace(0, np.pi, n_v)            # half of the azimuth -> half ellipsoid
    for i, u in enumerate(us):
        for j, v in enumerate(vs):
            verts.append((np.cos(u), np.sin(u) * np.cos(v), np.sin(u) * np.sin(v)))
            uvs.append((u / np.pi, v / np.pi))
    idx = lambda i, j: i * n_v + j + 1
    for i in range(n_u - 1):
        for j in range(n_v - 1):
            faces.append((idx(i, j), idx(i + 1, j), idx(i + 1, j + 1)))
            faces.append((idx(i, j), idx(i + 1, j + 1), idx(i, j + 1)))
    # cap (flat cut face) : fan around centre
    c = len(verts) + 1
    verts.append((0.0, 0.0, 0.0)); uvs.append((0.5, 0.5))
    for i in range(n_u - 1):
        faces.append((c, idx(i + 1, 0), idx(i, 0)))
        faces.append((c, idx(i, n_v - 1), idx(i + 1, n_v - 1)))
    with open(path, "w") as f:
        for v in verts: f.write(f"v {v[0]:.5f} {v[1]:.5f} {v[2]:.5f}\n")
        for t in uvs: f.write(f"vt {t[0]:.5f} {t[1]:.5f}\n")
        for a, b, c_ in faces: f.write(f"f {a}/{a} {b}/{b} {c_}/{c_}\n")


def build(force=False):
    ASSETS.mkdir(exist_ok=True)
    rng = np.random.default_rng(1234)
    for fam, (mottle, crease, holes, speckle) in FAMILIES.items():
        for k in range(N_VARIANTS):
            p = ASSETS / f"tex_{fam}_{k}.png"
            if force or not p.exists():
                cv2.imwrite(str(p), cv2.cvtColor(_texture(rng, mottle, crease, holes, speckle), cv2.COLOR_RGB2BGR))
    p = ASSETS / "half_bean.obj"
    if force or not p.exists():
        half_bean_obj(p)
    return ASSETS


if __name__ == "__main__":
    print(build(force=True))
