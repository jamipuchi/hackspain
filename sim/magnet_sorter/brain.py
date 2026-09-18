"""GPT-6 as the sorter's brain: phone photo in, servo program out.

Nothing here knows the ground truth. GPT-6 sees a photo of real-looking hardware, decides what
each part is from appearance and learns from the arm's feedback: the only truth signal is whether
a piece left its spot after the magnet tried it. The task text comes from the active build in
scene_def (sort by type, pick ferrous only, assemble kits, grade by length, …).
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

import cv2
import numpy as np
from openai import OpenAI

import scene_def as sd

MODEL = os.environ.get("GPT6_MODEL", "gpt-6-astra")
LEADS_GPT_ENV = Path.home() / "Documents" / "leads-gpt" / "api" / ".env"

APPEARANCE = """Appearance cues (unreliable, use them as priors):
- zinc-plated steel: bluish/greyish silver, slightly dull, sometimes with a yellowish tint
- black-oxide steel: dark grey to black, matte;  rusty steel: brown/orange, rough
- stainless: bright neutral silver, often shinier and cleaner than zinc — hard to tell apart
- aluminium: light matte silver, lighter/whiter than steel, no colour cast
- brass: yellow-gold; copper: reddish-orange
Screws have a round pan head with a cross slot and a threaded shaft; nuts are hexagonal with a
hole; washers are flat rings."""


def system_prompt() -> str:
    t = sd.TASK
    targets = "\n".join(f'  "{name}": {spec["label"]}' for name, spec in sd.TARGETS.items())
    return f"""You are the brain of a small hobby robot: an Arduino-driven 3-servo arm with an ELECTROMAGNET
hanging from its tip. A phone camera looks down at the work area.

TASK: {t["goal"]}

Targets you can place into:
{targets}

Scene: {t["scene"]}

{APPEARANCE}

The only ground truth is the arm's FEEDBACK: after each attempt you are told whether the piece
left its spot (lifted), stayed (not ferrous or missed) or was only nudged (new pixel given). Do not
try the same piece more than twice. The magnet lifts any steel part within about 1 cm of its
20 mm face, so two parts lying close together may come up as one.

Coordinates: give the pixel (u, v) of each piece's CENTRE in the image you are shown (grid lines
every 100 px are labelled). u grows to the right, v grows downward.

Program ops, executed in order by the arm:
  {{"op":"pick","u":U,"v":V,"piece":"p1"}}          move above, lower the magnet onto the piece, energise, lift
  {{"op":"place","target":"<target name>"}}        move over that container, verify by camera, release
  {{"op":"home"}}                                    park the arm
  {{"op":"wait","seconds":S}}
Rules: every pick is followed by a place; end with home; at most 3 picks per program (you get a
new photo afterwards); when nothing worth trying remains, return an empty program with done=true.
Set "kind", "material", "ferrous" (your belief) and "confidence" 0-1 per piece; "target" is where
you would send it. Keep "message" to one sentence."""


def plan_schema() -> dict:
    t = sd.TASK
    return {
        "type": "object",
        "properties": {
            "pieces": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "u": {"type": "integer"},
                        "v": {"type": "integer"},
                        "kind": {"type": "string", "enum": t["kinds"]},
                        "material": {"type": "string", "enum": t["materials"]},
                        "ferrous": {"type": "boolean"},
                        "target": {"type": "string", "enum": [*sd.TARGETS, "leave"]},
                        "confidence": {"type": "number"},
                        "reason": {"type": "string"},
                    },
                    "required": ["id", "u", "v", "kind", "material", "ferrous", "target", "confidence", "reason"],
                    "additionalProperties": False,
                },
            },
            "program": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {"type": "string", "enum": ["pick", "place", "home", "wait"]},
                        "u": {"type": ["integer", "null"]},
                        "v": {"type": ["integer", "null"]},
                        "target": {"type": ["string", "null"], "enum": [*sd.TARGETS, None]},
                        "seconds": {"type": ["number", "null"]},
                        "piece": {"type": ["string", "null"]},
                    },
                    "required": ["op", "u", "v", "target", "seconds", "piece"],
                    "additionalProperties": False,
                },
            },
            "done": {"type": "boolean"},
            "message": {"type": "string"},
        },
        "required": ["pieces", "program", "done", "message"],
        "additionalProperties": False,
    }


def load_api_key() -> str:
    for var in ("OPENAI_API_KEY", "OPEN_AI_KEY"):
        if os.environ.get(var):
            return os.environ[var]
    if LEADS_GPT_ENV.exists():
        m = re.search(r'^OPEN_AI_KEY=["\']?([^"\'\n]+)', LEADS_GPT_ENV.read_text(), re.M)
        if m:
            return m.group(1)
    raise RuntimeError("no OpenAI key: set OPENAI_API_KEY or keep OPEN_AI_KEY in leads-gpt/api/.env")


def encode_jpeg(frame_bgr: np.ndarray, quality: int = 90) -> str:
    ok, buf = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    return base64.b64encode(buf.tobytes()).decode()


class GPT6Planner:
    name = MODEL

    def __init__(self, model: str = MODEL):
        self.client = OpenAI(api_key=load_api_key())
        self.model = model
        self.usage: list[dict] = []
        self.last_input_text = ""
        self.system = system_prompt()
        self.schema = plan_schema()

    def plan(self, frame_bgr: np.ndarray, history: list[str], round_no: int) -> dict:
        h, w = frame_bgr.shape[:2]
        feedback = "\n".join(f"- {line}" for line in history[-16:]) or "- (first round, nothing tried yet)"
        text = (
            f"Round {round_no}. Photo is {w}x{h} px.\n"
            f"Feedback from the arm so far:\n{feedback}\n\n"
            "List every loose part with your kind/material guess and intended target, then write the program."
        )
        self.last_input_text = text
        response = self.client.responses.create(
            model=self.model,
            instructions=self.system,
            input=[{"role": "user", "content": [{"type": "input_text", "text": text}, {"type": "input_image", "image_url": f"data:image/jpeg;base64,{encode_jpeg(frame_bgr)}", "detail": "high"}]}],
            text={"format": {"type": "json_schema", "name": "sort_plan", "schema": self.schema, "strict": True}},
        )
        if response.usage is not None:
            self.usage.append({"input": response.usage.input_tokens, "output": response.usage.output_tokens})
        plan = json.loads(response.output_text)
        for i, piece in enumerate(plan.get("pieces", []), 1):
            piece.setdefault("id", f"p{i}")
        return plan

    def cost_usd(self) -> float:
        return sum(u["input"] * 10e-6 + u["output"] * 50e-6 for u in self.usage)


class MockPlanner:
    """No-API stand-in: tries every grey-ish blob once, sends it to the first target, learns from feedback."""

    name = "mock-grey-blobs"

    def __init__(self, pixel_to_table=None):
        self.pixel_to_table = pixel_to_table
        self.usage: list[dict] = []
        self.last_input_text = ""

    def plan(self, frame_bgr, history, round_no):
        from controller import in_workspace

        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
        grey = cv2.inRange(hsv, (0, 0, 40), (180, 70, 235))
        grey = cv2.morphologyEx(grey, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        contours, _ = cv2.findContours(grey, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        failed = [(int(a), int(b)) for a, b in re.findall(r"pixel \((\d+),(\d+)\) FAILED", " ".join(history))]
        first_target = next(iter(sd.TARGETS))
        pieces, program = [], []
        for c in contours:
            area = cv2.contourArea(c)
            if not 40 < area < 3500:
                continue
            m = cv2.moments(c)
            u, v = int(m["m10"] / m["m00"]), int(m["m01"] / m["m00"])
            if self.pixel_to_table and not in_workspace(*self.pixel_to_table(u, v)):
                continue
            pid = f"p{len(pieces) + 1}"
            tried_fail = any(abs(fu - u) < 25 and abs(fv - v) < 25 for fu, fv in failed)
            pieces.append({"id": pid, "u": u, "v": v, "kind": "other", "material": "unknown", "ferrous": not tried_fail, "target": first_target, "confidence": 0.4, "reason": "grey blob"})
            if not tried_fail and len(program) < 6:
                program += [{"op": "pick", "u": u, "v": v, "piece": pid}, {"op": "place", "target": first_target}]
        if program:
            program.append({"op": "home"})
        return {"pieces": pieces, "program": program, "done": not program, "message": f"mock: {len(pieces)} grey blobs, trying {len(program) // 2}"}

    def cost_usd(self) -> float:
        return 0.0


class OraclePlanner:
    """Ground-truth vision (for mechanics debugging and 'perfect perception' demos): reads the sim
    state, sends every part with a target to that target, three per round, honours feedback."""

    name = "oracle-ground-truth"

    def __init__(self, model, data, pieces, table_to_pixel):
        self.model, self.data, self.pieces = model, data, pieces
        self.table_to_pixel = table_to_pixel
        self.usage: list[dict] = []
        self.last_input_text = ""
        self.tried: dict[str, int] = {}

    def plan(self, frame_bgr, history, round_no):
        pieces, program = [], []
        for b in self.pieces:
            want = b.piece.get("target")
            p = self.data.body(b.name).xpos
            if p[2] < -0.05:
                continue
            u, v = self.table_to_pixel(float(p[0]), float(p[1]))
            pid = f"p{len(pieces) + 1}"
            pieces.append({"id": pid, "u": int(u), "v": int(v), "kind": b.piece["kind"], "material": b.piece["material"], "ferrous": b.piece["ferrous"], "target": want or "leave", "confidence": 1.0, "reason": "oracle"})
            in_target = False
            if want:
                tx, ty = sd.TARGETS[want]["pos"]
                in_target = abs(p[0] - tx) < sd.TARGETS[want]["size"][0] + 0.01 and abs(p[1] - ty) < sd.TARGETS[want]["size"][1] + 0.01
            if want and not in_target and self.tried.get(b.name, 0) < 3 and len(program) < 6:
                from controller import in_workspace

                if in_workspace(float(p[0]), float(p[1])):
                    self.tried[b.name] = self.tried.get(b.name, 0) + 1
                    program += [{"op": "pick", "u": int(u), "v": int(v), "piece": pid}, {"op": "place", "target": want}]
        if program:
            program.append({"op": "home"})
        return {"pieces": pieces, "program": program, "done": not program, "message": f"oracle: {len(program) // 2} parts to move"}

    def cost_usd(self) -> float:
        return 0.0
