"""Product profiles: what travels on the belt and how each class looks.

A profile is *data*, not code. Adding a new product (roasted coffee, chickpeas, ...)
or a new defect class means adding an entry here and re-running `train`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np

# Shape families. Each maps to a body pool in the MJCF (see scene.py).
ELLIPSOID, HALF, BOX, CAPSULE = "ellipsoid", "half", "box", "capsule"


@dataclass
class ClassSpec:
    name: str
    prior: float                 # fraction of the feed
    shape: str                   # ELLIPSOID | HALF | BOX | CAPSULE
    size_mm: tuple               # (lo, hi) per semi-axis, 3x2  -> sampled uniformly
    rgb: tuple                   # base colour (0-1), tinted with `rgb_jitter`
    rgb_jitter: float = 0.05
    density: float = 1150.0      # kg/m^3 -> mass from sampled volume
    texture: str | None = None   # texture family name in assets.py (None = flat colour)
    defect: bool = True          # False = product we want to keep
    severity: str = "major"      # 'none' | 'minor' | 'major' | 'foreign'


@dataclass
class Profile:
    name: str
    belt_rgb: tuple
    classes: list = field(default_factory=list)

    def by_name(self, n): return next(c for c in self.classes if c.name == n)
    @property
    def names(self): return [c.name for c in self.classes]
    def priors(self, defect_boost: float = 1.0):
        p = np.array([c.prior * (defect_boost if c.defect else 1.0) for c in self.classes])
        return p / p.sum()


# ---------------------------------------------------------------- green arabica
# Sizes are semi-axes in mm. A screen-16 bean is ~10 x 7 x 5 mm.
BEAN = ((4.2, 5.6), (3.1, 4.0), (2.2, 2.9))
GREEN_ARABICA = Profile(
    name="green_arabica",
    belt_rgb=(0.10, 0.22, 0.62),
    classes=[
        ClassSpec("good",   0.86,  ELLIPSOID, BEAN, (0.50, 0.60, 0.46), 0.06, texture="good",   defect=False, severity="none"),
        ClassSpec("faded",  0.03,  ELLIPSOID, BEAN, (0.70, 0.66, 0.42), 0.05, texture="faded",  severity="minor"),
        ClassSpec("black",  0.025, ELLIPSOID, BEAN, (0.13, 0.11, 0.09), 0.03, texture="black"),
        ClassSpec("sour",   0.025, ELLIPSOID, BEAN, (0.50, 0.32, 0.18), 0.05, texture="sour"),
        ClassSpec("insect", 0.02,  ELLIPSOID, BEAN, (0.50, 0.60, 0.46), 0.06, texture="insect"),
        ClassSpec("broken", 0.02,  HALF,      BEAN, (0.50, 0.60, 0.46), 0.06, texture="good"),
        ClassSpec("shell",  0.01,  ELLIPSOID, ((4.0, 5.2), (2.8, 3.6), (0.6, 1.0)), (0.62, 0.70, 0.55), 0.05, density=600, texture="good"),
        ClassSpec("husk",   0.01,  ELLIPSOID, ((4.5, 7.0), (3.0, 5.0), (0.3, 0.6)), (0.85, 0.78, 0.60), 0.05, density=300, severity="foreign"),
        ClassSpec("stone",  0.005, BOX,       ((2.5, 5.0), (2.0, 4.0), (1.5, 3.5)), (0.45, 0.44, 0.42), 0.08, density=2600, severity="foreign"),
        ClassSpec("stick",  0.005, CAPSULE,   ((8.0, 16.0), (0.9, 1.4), (0.9, 1.4)), (0.42, 0.30, 0.16), 0.06, density=700, severity="foreign"),
    ],
)

# ---------------------------------------------------------------- roasted (generalisation demo)
# Good = medium roast brown. Quakers = pale, under-developed beans. Burnt = charcoal.
ROASTED = Profile(
    name="roasted",
    belt_rgb=(0.10, 0.22, 0.62),
    classes=[
        ClassSpec("good",   0.90, ELLIPSOID, ((4.6, 6.0), (3.5, 4.4), (2.6, 3.2)), (0.36, 0.22, 0.13), 0.05, density=650, texture="roast", defect=False, severity="none"),
        ClassSpec("quaker", 0.04, ELLIPSOID, ((4.6, 6.0), (3.5, 4.4), (2.6, 3.2)), (0.72, 0.56, 0.36), 0.05, density=600, texture="faded"),
        ClassSpec("burnt",  0.02, ELLIPSOID, ((4.6, 6.0), (3.5, 4.4), (2.6, 3.2)), (0.08, 0.07, 0.06), 0.02, density=550, texture="black"),
        ClassSpec("broken", 0.03, HALF,      ((4.6, 6.0), (3.5, 4.4), (2.6, 3.2)), (0.36, 0.22, 0.13), 0.05, density=650, texture="roast"),
        ClassSpec("stone",  0.005, BOX,      ((2.5, 5.0), (2.0, 4.0), (1.5, 3.5)), (0.45, 0.44, 0.42), 0.08, density=2600, severity="foreign"),
        ClassSpec("stick",  0.005, CAPSULE,  ((8.0, 16.0), (0.9, 1.4), (0.9, 1.4)), (0.42, 0.30, 0.16), 0.06, density=700, severity="foreign"),
    ],
)

PROFILES = {p.name: p for p in (GREEN_ARABICA, ROASTED)}


def sample_instance(spec: ClassSpec, rng: np.random.Generator):
    """Sample one physical instance: semi-axes (m), rgba, mass (kg)."""
    ax = np.array([rng.uniform(lo, hi) for lo, hi in spec.size_mm]) * 1e-3
    rgb = np.clip(np.array(spec.rgb) + rng.normal(0, spec.rgb_jitter, 3) * np.array([1.0, 1.0, 0.8]), 0.02, 1.0)
    if spec.shape == ELLIPSOID:
        vol = 4 / 3 * np.pi * ax.prod()
    elif spec.shape == HALF:
        vol = 0.5 * 4 / 3 * np.pi * ax.prod()
    elif spec.shape == BOX:
        vol = 8 * ax.prod()
    else:  # capsule: half-length ax[0], radius ax[1]
        vol = np.pi * ax[1] ** 2 * (2 * ax[0]) + 4 / 3 * np.pi * ax[1] ** 3
    return ax, np.array([*rgb, 1.0]), spec.density * vol
