"""Offline stand-in for Astra: classic colour thresholding. Lets you run the loop with no API.

Iron pieces are dark, low-saturation blobs. Everything else is left alone.
"""

from __future__ import annotations

import cv2
import numpy as np


class MockPlanner:
    name = "mock-colour-threshold"

    def __init__(self, pixel_to_table=None, workspace=None):
        self.pixel_to_table = pixel_to_table
        self.workspace = workspace

    def _in_workspace(self, u: int, v: int) -> bool:
        if self.pixel_to_table is None or self.workspace is None:
            return True
        x, y = self.pixel_to_table(u, v)
        return self.workspace["x"][0] <= x <= self.workspace["x"][1] and self.workspace["y"][0] <= y <= self.workspace["y"][1]

    def plan(self, frame_bgr: np.ndarray, history: list[str], round_no: int) -> dict:
        # iron renders as a neutral dark grey: small channel spread, mid-low brightness
        f = frame_bgr.astype(np.int16)
        spread = f.max(axis=2) - f.min(axis=2)
        value = f.max(axis=2)
        dark = ((spread < 14) & (value > 35) & (value < 120)).astype(np.uint8) * 255
        dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        # remove the arm column / magnet shadow etc. by size filtering
        contours, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        tried = {line.split("pick at ")[1].split(":")[0] for line in history if "pick at " in line and "nothing stuck" in line}

        pieces, program = [], []
        for c in contours:
            area = cv2.contourArea(c)
            if not 150 < area < 4000:
                continue
            m = cv2.moments(c)
            u, v = int(m["m10"] / m["m00"]), int(m["m01"] / m["m00"])
            x, y, w, h = cv2.boundingRect(c)
            if max(w, h) / max(1, min(w, h)) > 2.5:  # arm links are elongated
                continue
            if not self._in_workspace(u, v):
                continue
            pid = f"p{len(pieces) + 1}"
            pieces.append({"id": pid, "u": u, "v": v, "material": "iron", "ferrous": True, "confidence": 0.6, "reason": "dark low-saturation blob"})
            key = "({:.3f},{:.3f})".format(*self.pixel_to_table(u, v)) if self.pixel_to_table else f"({u},{v})"
            if len(program) < 6 and key not in tried:
                program.append({"op": "pick", "u": u, "v": v, "piece": pid})
                program.append({"op": "place", "target": "iron_bin"})
        if program:
            program.append({"op": "home"})
        return {"pieces": pieces, "program": program, "done": not program, "message": f"mock: {len(pieces)} dark blobs, picking {len(program) // 2}"}
