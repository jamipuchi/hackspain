"""Human-facing renders: overview frames, inspection strip, MP4 writer."""
from __future__ import annotations

import numpy as np
import cv2
import mujoco


class Overview:
    def __init__(self, sim, width=1280, height=720, camera="overview"):
        self.sim = sim
        self.r = mujoco.Renderer(sim.model, height, width)
        self.r._scene_option.geomgroup[3] = 0
        self.camera = camera

    def frame(self, camera=None):
        self.r.update_scene(self.sim.data, camera or self.camera)
        return self.r.render()

    def close(self): self.r.close()


class Video:
    def __init__(self, path, fps=50, size=(1280, 720)):
        self.w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
        self.size = size

    def add(self, rgb):
        if (rgb.shape[1], rgb.shape[0]) != self.size:
            rgb = cv2.resize(rgb, self.size)
        self.w.write(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))

    def close(self): self.w.release()


def hud(frame, lines, org=(20, 36), scale=0.7, color=(255, 255, 255)):
    y = org[1]
    for ln in lines:
        cv2.putText(frame, ln, (org[0], y), cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(frame, ln, (org[0], y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)
        y += int(34 * scale)
    return frame
