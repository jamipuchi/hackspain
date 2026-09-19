"""Contracts smoke test: the frozen types import and the config round-trips."""
import json
from pathlib import Path

from line import config, contracts


def test_config_roundtrip(tmp_path: Path):
    cfg = config.LineConfig()
    cfg.gate.open_deg = 115
    p = tmp_path / "c.json"
    config.save(cfg, p)
    again = config.load(p)
    assert again.gate.open_deg == 115
    assert again.gate.hold_deg == (90, 75)
    assert json.loads(p.read_text())["dry_run"] is True


def test_roi_crop():
    import numpy as np

    roi = contracts.ROI(2, 1, 6, 4)
    img = np.zeros((10, 10, 3), np.uint8)
    assert roi.crop(img).shape == (3, 4, 3)
    assert (roi.w, roi.h) == (4, 3)
