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
        self.r._scene_option.geomgroup[4] = 1
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
    lines = [str(line) for line in lines if line]
    if not lines:
        return frame

    font = cv2.FONT_HERSHEY_SIMPLEX
    padding = 8
    available_width = frame.shape[1] - org[0] - 2 * padding
    widest = max(cv2.getTextSize(line, font, scale, 1)[0][0] for line in lines)
    if widest > available_width:
        scale *= available_width / widest

    sizes = [cv2.getTextSize(line, font, scale, 1) for line in lines]
    line_height = max(height + baseline for (width, height), baseline in sizes) + 5
    top = org[1] - max(height for (width, height), baseline in sizes) - padding
    bottom = org[1] + (len(lines) - 1) * line_height + max(baseline for (size, baseline) in sizes) + padding
    right = min(frame.shape[1] - 1, org[0] + max(width for (width, height), baseline in sizes) + padding)
    cv2.rectangle(frame, (org[0] - padding, top), (right, bottom), (20, 20, 20), -1)

    for i, line in enumerate(lines):
        cv2.putText(frame, line, (org[0], org[1] + i * line_height), font, scale, color, 1, cv2.LINE_AA)
    return frame
