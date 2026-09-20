"""Product profiles: what travels on the belt and how each class looks.

A profile is *data*, not code. The values live in `object_catalog/` as validated JSON.
Adding a new product (roasted coffee, chickpeas, ...) or a new defect class means adding
a definition file plus a catalog manifest entry, then re-running `train`. Nothing here
needs editing. `ClassSpec` and `Profile` stay engine adapters derived from that data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np

from object_catalog import load_catalog, profile_from_catalog

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


# Sizes are semi-axes in mm. A screen-16 bean is ~10 x 7 x 5 mm.
BEAN = ((4.2, 5.6), (3.1, 4.0), (2.2, 2.9))

# ---------------------------------------------------------------- built-in catalogs
# green arabica is the active catalog. roasted is the static generalisation demo:
# good = medium roast brown, quakers = pale under-developed beans, burnt = charcoal.
GREEN_ARABICA = profile_from_catalog(load_catalog())
ROASTED = profile_from_catalog(load_catalog(manifest="builtin/roasted.catalog.json"))

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
