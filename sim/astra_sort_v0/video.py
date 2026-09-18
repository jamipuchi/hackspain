"""Dashboard video recorder for the GPT-6 sorting demo.

Layout (1920x1080):
  ┌──────────────────────────────┬────────────────────────────┐
  │ 3D scene (arm executing)     │ Astra's camera input       │
  │                              ├────────────────────────────┤
  │                              │ INPUT → Astra  (text)      │
  │                              │ OUTPUT ← Astra (plan)      │
  ├──────────────────────────────┴────────────────────────────┤
  │ status bar: phase · round · magnet · feedback log         │
  └───────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import cv2
import mujoco
import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 1080
FPS = 25
SIM_STEPS_PER_FRAME = 20  # 0.002 s * 20 = 0.04 s = one frame at 25 fps → real-time playback

SCENE_W, SCENE_H = 1200, 900
CAM_W, CAM_H = 640, 480
PANEL_X = SCENE_W + 20
PANEL_W = W - PANEL_X - 20
BAR_Y = SCENE_H + 20
BAR_H = H - BAR_Y

BG = (24, 26, 30)
PANEL = (36, 39, 45)
TXT = (230, 232, 236)
DIM = (150, 155, 165)
ACCENT = (90, 200, 120)
WARN = (235, 120, 90)
BLUE = (90, 160, 240)

FONT_PATH = "/System/Library/Fonts/Helvetica.ttc"
MONO_PATH = "/System/Library/Fonts/Menlo.ttc"


def _font(size: int, mono: bool = False, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = MONO_PATH if mono else FONT_PATH
    try:
        return ImageFont.truetype(path, size, index=1 if bold and not mono else 0)
    except OSError:
        return ImageFont.load_default()


F_TITLE = _font(30, bold=True)
F_H = _font(20, bold=True)
F_T = _font(18)
F_S = _font(15)
F_M = _font(15, mono=True)
F_MS = _font(13, mono=True)


class Recorder:
    def __init__(self, arm, camera, out_path: Path, planner_name: str, seed: int):
        self.arm = arm
        self.camera = camera
        self.out_path = Path(out_path)
        self.planner_name = planner_name
        self.seed = seed
        self.tmp_path = self.out_path.with_suffix(".raw.mp4")
        self.writer = cv2.VideoWriter(str(self.tmp_path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
        self.renderer = mujoco.Renderer(arm.model, height=SCENE_H, width=SCENE_W)
        self.cam3d = mujoco.MjvCamera()
        self.cam3d.type = mujoco.mjtCamera.mjCAMERA_FREE
        self.cam3d.lookat[:] = [0.22, 0.03, 0.05]
        self.cam3d.distance = 1.1
        self.cam3d.azimuth = 205
        self.cam3d.elevation = -30

        self.phase = "starting"
        self.round_no = 0
        self.cam_frame: np.ndarray | None = None
        self.cam_caption = "waiting for first frame"
        self.input_text = ""
        self.plan: dict | None = None
        self.current_op: int | None = None
        self.op_results: dict[int, bool] = {}
        self.feedback: list[str] = []
        self.plan_seconds: float | None = None
        self.cost_usd = 0.0
        self.calls = 0
        self.step_count = 0
        self.frames = 0

    # ------------------------------------------------------------------ hooks
    def on_step(self) -> None:
        self.step_count += 1
        if self.step_count % SIM_STEPS_PER_FRAME == 0:
            self.capture()

    def hold(self, seconds: float) -> None:
        for _ in range(int(seconds * FPS)):
            self.capture()

    def close(self) -> Path:
        self.writer.release()
        # re-encode to H.264 so it plays everywhere (QuickTime, Slack, browsers)
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(self.tmp_path), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart", str(self.out_path)],
                check=True,
            )
            self.tmp_path.unlink(missing_ok=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.tmp_path.rename(self.out_path)
        return self.out_path

    # ------------------------------------------------------------------ frame
    def capture(self) -> None:
        self.renderer.update_scene(self.arm.data, camera=self.cam3d)
        scene = cv2.cvtColor(self.renderer.render(), cv2.COLOR_RGB2BGR)

        canvas = np.full((H, W, 3), BG, np.uint8)
        canvas[0:SCENE_H, 0:SCENE_W] = scene

        img = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(img)

        # scene overlay
        draw.rectangle([0, 0, SCENE_W, 50], fill=(0, 0, 0, 0))
        self._label(draw, 16, 12, "SIMULATION  ·  3-axis SCARA arm + electromagnet  ·  MuJoCo", F_H, TXT, shadow=True)
        tip = self.arm.tip()
        magnet = f"MAGNET {'ON' if self.arm.magnet_on else 'off'}"
        if self.arm.attached:
            magnet += f"  ·  holding {self.arm.attached.removeprefix('piece_')}"
        self._label(draw, 16, SCENE_H - 34, f"tip x={tip[0]:.3f} y={tip[1]:.3f} z={tip[2]:.3f}    {magnet}", F_M, ACCENT if self.arm.magnet_on else DIM, shadow=True)

        # right column: camera input
        y = 0
        cam_panel_h = CAM_H + 74
        draw.rectangle([PANEL_X, y, W - 20, y + cam_panel_h], fill=PANEL)
        self._label(draw, PANEL_X + 14, y + 12, "WHAT GPT-6 SEES  ·  overhead camera 960×720 + pixel grid", F_H, TXT)
        cam_box = (PANEL_X + (PANEL_W - CAM_W) // 2, y + 44)
        if self.cam_frame is not None:
            small = cv2.resize(self.cam_frame, (CAM_W, CAM_H), interpolation=cv2.INTER_AREA)
            img.paste(Image.fromarray(cv2.cvtColor(small, cv2.COLOR_BGR2RGB)), cam_box)
        else:
            draw.rectangle([cam_box[0], cam_box[1], cam_box[0] + CAM_W, cam_box[1] + CAM_H], fill=(20, 20, 24))
        self._label(draw, PANEL_X + 14, y + 44 + CAM_H + 6, self.cam_caption, F_S, DIM)

        # right column: input / output text panels
        y = cam_panel_h + 12
        draw.rectangle([PANEL_X, y, W - 20, SCENE_H], fill=PANEL)
        self._label(draw, PANEL_X + 14, y + 10, "INPUT  ->  GPT-6  (model gpt-6-astra)", F_H, BLUE)
        yy = y + 40
        for line in textwrap.wrap(self.input_text or "image + task instructions", 80)[:2]:
            self._label(draw, PANEL_X + 14, yy, line, F_MS, DIM)
            yy += 17

        yy += 8
        self._label(draw, PANEL_X + 14, yy, "OUTPUT  <-  GPT-6", F_H, ACCENT)
        if self.plan_seconds is not None:
            self._label(draw, W - 220, yy + 2, f"{self.plan_seconds:.1f} s  ·  ${self.cost_usd:.2f} total", F_S, DIM)
        yy += 30
        if self.plan is None:
            self._label(draw, PANEL_X + 14, yy, "…" if self.phase == "thinking" else "(no plan yet)", F_T, DIM)
        else:
            for line in textwrap.wrap(f"“{self.plan.get('message', '')}”", 78)[:2]:
                self._label(draw, PANEL_X + 14, yy, line, F_T, TXT)
                yy += 22
            yy += 6
            pieces = self.plan.get("pieces", [])
            col_x = PANEL_X + 14
            for i, p in enumerate(pieces[:8]):
                mark = "●" if p.get("ferrous") else "○"
                colour = WARN if p.get("ferrous") else DIM
                txt = f"{mark} {p['id']:<3} {p.get('material', '?'):<8} ({p['u']:>3},{p['v']:>3})"
                self._label(draw, col_x + (i % 2) * 330, yy + (i // 2) * 17, txt, F_MS, colour)
            yy += ((min(len(pieces), 8) + 1) // 2) * 17 + 8
            self._label(draw, PANEL_X + 14, yy, "program  (executed in order, two columns)", F_S, DIM)
            yy += 20
            ops = self.plan.get("program", [])[:8]
            for i, op in enumerate(ops):
                txt = op["op"] + (f"({op['u']},{op['v']})" if op.get("u") is not None else "") + (f" → {op['target']}" if op.get("target") else "")
                cx = PANEL_X + 14 + (i % 2) * 330
                cy = yy + (i // 2) * 17
                if i == self.current_op:
                    draw.rectangle([cx - 6, cy - 2, cx + 318, cy + 15], fill=(60, 70, 60))
                    colour = ACCENT
                    prefix = "▶ "
                elif i in self.op_results:
                    colour = ACCENT if self.op_results[i] else WARN
                    prefix = "✓ " if self.op_results[i] else "✗ "
                else:
                    colour = DIM
                    prefix = "  "
                self._label(draw, cx, cy, prefix + txt, F_MS, colour)

        # bottom bar
        draw.rectangle([0, BAR_Y, W, H], fill=PANEL)
        self._label(draw, 16, BAR_Y + 12, f"GPT-6 sorts iron pieces with an electromagnet arm", F_TITLE, TXT)
        self._label(draw, 16, BAR_Y + 52, f"phase: {self.phase}     round {self.round_no}     planner {self.planner_name}     layout seed {self.seed}     sim t = {self.arm.data.time:5.1f} s", F_T, DIM)
        left = len(self.arm.ferrous_left_on_table())
        self._label(draw, 16, BAR_Y + 80, f"iron on table: {left}    iron in bin: {3 - left}", F_T, ACCENT if left == 0 else TXT)
        self._label(draw, 16, BAR_Y + 108, "camera -> GPT-6 -> JSON program -> arm -> outcome -> GPT-6 -> ...", F_S, DIM)

        fx = 900
        self._label(draw, fx, BAR_Y + 12, "FEEDBACK TO GPT-6  (arm outcomes, appended to the next input)", F_H, BLUE)
        for i, line in enumerate(self.feedback[-6:]):
            colour = WARN if ("✗" in line or "nothing" in line or "did not" in line) else TXT
            self._label(draw, fx, BAR_Y + 40 + i * 19, line[:120], F_MS, colour)

        frame = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        self.writer.write(frame)
        self.frames += 1

    @staticmethod
    def _label(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, font, fill, shadow: bool = False) -> None:
        if shadow:
            draw.text((x + 1, y + 1), text, font=font, fill=(0, 0, 0))
        draw.text((x, y), text, font=font, fill=fill)
