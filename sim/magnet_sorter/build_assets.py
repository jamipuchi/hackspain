"""Generate meshes and textures used by both renderers into assets/."""

from __future__ import annotations

import math
import struct
from pathlib import Path

import cv2
import numpy as np

from scene_def import ASSETS, MARKERS


def write_stl(path: Path, tris: np.ndarray) -> None:
    with open(path, "wb") as f:
        f.write(b"\0" * 80)
        f.write(struct.pack("<I", len(tris)))
        for a, b, c in tris:
            n = np.cross(b - a, c - a)
            n = n / (np.linalg.norm(n) + 1e-12)
            f.write(struct.pack("<3f", *n))
            for v in (a, b, c):
                f.write(struct.pack("<3f", *v))
            f.write(b"\0\0")


def hexprism(circumradius: float = 1.0, half_h: float = 1.0) -> np.ndarray:
    """Unit hex prism (scaled per geom at load time); axis along z, flat sides on ±y."""
    ang = [math.radians(30 + 60 * i) for i in range(6)]
    top = np.array([[circumradius * math.cos(a), circumradius * math.sin(a), half_h] for a in ang])
    bot = top.copy()
    bot[:, 2] = -half_h
    tris = []
    ct, cb = np.array([0, 0, half_h]), np.array([0, 0, -half_h])
    for i in range(6):
        j = (i + 1) % 6
        tris.append((ct, top[i], top[j]))
        tris.append((cb, bot[j], bot[i]))
        tris.append((top[i], bot[i], bot[j]))
        tris.append((top[i], bot[j], top[j]))
    return np.asarray(tris)


def mdf_texture(size: int = 1024) -> np.ndarray:
    rng = np.random.default_rng(3)
    base = np.array([0.40, 0.29, 0.18])
    noise = rng.normal(0, 1, (size, size))
    noise = cv2.GaussianBlur(noise, (0, 0), 1.2) * 0.06 + cv2.GaussianBlur(rng.normal(0, 1, (size, size)), (0, 0), 6) * 0.05
    img = np.clip(base[None, None, :] * (1 + noise[:, :, None]), 0, 1)
    # pressed-fibre speckle
    speck = rng.random((size, size)) < 0.004
    img[speck] *= 0.75
    return (img[:, :, ::-1] ** (1 / 2.2) * 255).astype(np.uint8)


def wood_texture(size: int = 1024) -> np.ndarray:
    """Pine plywood: warm base, long grain streaks, faint knots."""
    rng = np.random.default_rng(11)
    y = np.linspace(0, 1, size)[:, None]
    x = np.linspace(0, 1, size)[None, :]
    grain = np.zeros((size, size))
    for k in range(1, 7):
        grain += np.sin(2 * np.pi * (x * 0 + y) * (14 * k) + 3.0 * np.sin(2 * np.pi * x * (1.3 * k) + k)) / k
    grain = (grain - grain.min()) / (grain.max() - grain.min())
    noise = cv2.GaussianBlur(rng.normal(0, 1, (size, size)), (0, 0), 2.0)
    base = np.array([0.66, 0.49, 0.30])
    dark = np.array([0.50, 0.35, 0.19])
    img = base[None, None, :] * (1 - 0.55 * grain[:, :, None]) + dark[None, None, :] * (0.55 * grain[:, :, None])
    img *= 1 + 0.05 * noise[:, :, None]
    for _ in range(3):  # knots
        cx, cy = rng.integers(100, size - 100, 2)
        r = np.sqrt((np.arange(size)[None, :] - cx) ** 2 + (np.arange(size)[:, None] - cy) ** 2)
        img *= (1 - 0.35 * np.exp(-(r / 28.0) ** 2))[:, :, None]
    img = np.clip(img, 0, 1)
    return (img[:, :, ::-1] ** (1 / 2.2) * 255).astype(np.uint8)


def pcb_texture(size: int = 512) -> np.ndarray:
    img = np.zeros((size, size, 3), np.uint8)
    img[:] = (85, 45, 10)  # BGR of a dark blue PCB
    rng = np.random.default_rng(5)
    for _ in range(70):  # traces
        x0, y0 = rng.integers(0, size, 2)
        L = rng.integers(20, 140)
        if rng.random() < 0.5:
            cv2.line(img, (int(x0), int(y0)), (int(min(size - 1, x0 + L)), int(y0)), (120, 80, 40), 2)
        else:
            cv2.line(img, (int(x0), int(y0)), (int(x0), int(min(size - 1, y0 + L))), (120, 80, 40), 2)
    for _ in range(90):  # pads
        x, y = rng.integers(6, size - 6, 2)
        cv2.circle(img, (int(x), int(y)), 4, (200, 200, 205), -1)
        cv2.circle(img, (int(x), int(y)), 2, (60, 40, 20), -1)
    cv2.putText(img, "UNO", (size // 2 - 40, size // 2), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (230, 230, 230), 3, cv2.LINE_AA)
    return img


def breadboard_texture(w: int = 1024, h: int = 342) -> np.ndarray:
    img = np.full((h, w, 3), 232, np.uint8)
    pitch = w / 63
    for col in range(63):
        for row in range(10):
            y = int(h * (0.16 + row * 0.055 + (0.12 if row >= 5 else 0)))
            x = int((col + 0.5) * pitch)
            cv2.rectangle(img, (x - 3, y - 3), (x + 3, y + 3), (40, 40, 40), -1)
    cv2.line(img, (0, h // 2), (w, h // 2), (200, 200, 200), 3)
    for y in (int(h * 0.05), int(h * 0.95)):
        for col in range(50):
            x = int((col + 0.5) * pitch * 1.25)
            cv2.rectangle(img, (x - 3, y - 3), (x + 3, y + 3), (40, 40, 40), -1)
    cv2.line(img, (0, int(h * 0.02)), (w, int(h * 0.02)), (40, 40, 220), 3)
    cv2.line(img, (0, int(h * 0.98)), (w, int(h * 0.98)), (220, 40, 40), 3)
    return img


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    write_stl(ASSETS / "hexprism.stl", hexprism())
    cv2.imwrite(str(ASSETS / "mdf.png"), mdf_texture())
    cv2.imwrite(str(ASSETS / "wood.png"), wood_texture())
    cv2.imwrite(str(ASSETS / "pcb.png"), pcb_texture())
    cv2.imwrite(str(ASSETS / "breadboard.png"), breadboard_texture())
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    for mid in MARKERS:
        m = cv2.aruco.generateImageMarker(d, mid, 6 * 40)
        m = cv2.copyMakeBorder(m, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=255)  # white quiet zone
        cv2.imwrite(str(ASSETS / f"aruco_{mid}.png"), m)
    print("assets:", sorted(p.name for p in ASSETS.iterdir()))


if __name__ == "__main__":
    main()
