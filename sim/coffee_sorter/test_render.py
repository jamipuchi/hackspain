import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import render
import vision


class FakeRenderer:
    def __init__(self, model, height, width):
        self._scene_option = SimpleNamespace(
            geomgroup=np.array([1, 1, 1, 0, 0, 0], dtype=np.uint8)
        )


class RenderGroupsTest(unittest.TestCase):
    def test_overview_shows_inspection_culled_skins(self):
        sim = SimpleNamespace(model=object())

        with patch.object(render.mujoco, "Renderer", FakeRenderer):
            overview = render.Overview(sim)

        self.assertEqual(overview.r._scene_option.geomgroup[3], 0)
        self.assertEqual(overview.r._scene_option.geomgroup[4], 1)

    def test_inspector_keeps_off_strip_skins_culled(self):
        layout = SimpleNamespace(cam_h=192, cam_w=2080, px_per_m=4000)
        model = SimpleNamespace(geom_group=np.array([0], dtype=np.uint8))
        sim = SimpleNamespace(
            L=layout,
            model=model,
            geom_of=np.array([0]),
            P=SimpleNamespace(belt_rgb=(0.1, 0.2, 0.3)),
        )

        with patch.object(vision.mujoco, "Renderer", FakeRenderer):
            inspector = vision.Inspector(sim)

        self.assertEqual(inspector.r._scene_option.geomgroup[4], 0)


class HudTest(unittest.TestCase):
    def test_hud_uses_a_dark_panel_and_separates_rows(self):
        frame = np.full((720, 1280, 3), 180, dtype=np.uint8)

        render.hud(frame, ["status", "camera", "metrics"])

        self.assertTrue(np.array_equal(frame[12, 20], (20, 20, 20)))
        white_rows = np.where(np.all(frame == (255, 255, 255), axis=2).any(axis=1))[0]
        groups = np.split(white_rows, np.where(np.diff(white_rows) > 1)[0] + 1)
        self.assertEqual(len(groups), 3)

    def test_hud_scales_long_lines_inside_the_frame(self):
        frame = np.full((720, 1280, 3), 180, dtype=np.uint8)

        render.hud(frame, ["x" * 300])

        self.assertTrue(np.array_equal(frame[:, -1], np.full((720, 3), 180, dtype=np.uint8)))


if __name__ == "__main__":
    unittest.main()
