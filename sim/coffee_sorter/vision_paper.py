"""White-paper variant of the inspection camera: dark beans on a bright background, like the physical chute
in ~/robotics/line. Additive: nothing in vision.py changes behaviour. Used to bootstrap a classifier for the
real line from simulated roasted beans before real labelled samples exist (line/classifier.py:train_from_sim).
"""
from __future__ import annotations

import dataclasses
import numpy as np

from vision import Inspector
from profiles import Profile

PAPER_RGB = (0.93, 0.93, 0.90)


def paper_profile(profile: Profile) -> Profile:
    """Same product, white paper instead of the blue belt."""
    return dataclasses.replace(profile, belt_rgb=PAPER_RGB)


class PaperInspector(Inspector):
    """Segments anything darker than `bean_gray_max` (paper renders at ~235)."""

    def __init__(self, sim, bean_gray_max: int = 190, **kw):
        super().__init__(sim, **kw)
        self.bean_gray_max = bean_gray_max

    def segment(self, r, g, b) -> np.ndarray:
        gray = (77 * r.astype(np.int32) + 150 * g.astype(np.int32) + 29 * b.astype(np.int32)) >> 8   # luma (int32: 150*255 overflows int16)
        return (gray < self.bean_gray_max).astype(np.uint8)
