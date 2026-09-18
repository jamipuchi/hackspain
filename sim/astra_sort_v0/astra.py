"""GPT-6 Astra as the robot's brain: camera frame in, movement program out.

The model receives the overhead camera image (with a labelled pixel grid), a description
of the arm's primitive operations and the outcome of previous rounds. It returns strict
JSON: the pieces it sees (with material guess) and a program of ops for the arm.
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

MODEL = os.environ.get("ASTRA_MODEL", "gpt-6-astra")
LEADS_GPT_ENV = Path.home() / "Documents" / "leads-gpt" / "api" / ".env"

SYSTEM = """You control a 3-axis SCARA robot arm with an ELECTROMAGNET on its tip, seen from a fixed
overhead camera. Your job: sort the metal pieces on the table. Only IRON (ferrous) pieces
stick to the electromagnet. Pick every iron piece and drop it in the blue bin. Leave
aluminum, brass and plastic pieces where they are: the magnet cannot lift them.

Visual cues: iron pieces are dark matte grey. Aluminum is bright silvery and shiny. Brass is
yellow-gold. Plastic is saturated red or blue. The bin is the blue open box; the arm is the
light grey two-link arm with a red magnet at its tip. Ignore the arm and the bin when
listing pieces.

Coordinates: give the pixel (u, v) of each piece's CENTER in the image you are shown. The
image has a labelled grid every 100 px to help you read coordinates precisely. u grows to
the right, v grows downward.

Available program ops (executed in order):
  {"op":"pick","u":U,"v":V,"piece":"p1"}  moves above the pixel, lowers the magnet onto the
                                           piece, switches the magnet on and lifts.
  {"op":"place","target":"iron_bin"}      moves over the bin and switches the magnet off.
  {"op":"home"}                            parks the arm.
  {"op":"wait","seconds":S}
  Low-level (rarely needed): {"op":"move_xy","u":U,"v":V}, {"op":"move_z","z":Z},
  {"op":"magnet_on"}, {"op":"magnet_off"}.

Rules:
- Every pick must be followed by a place. Finish the program with home.
- Pick at most 3 pieces per program; you will get a new image afterwards.
- Use the feedback from earlier rounds: if a pick found nothing sticking to the magnet, that
  piece is NOT iron. Do not try it again. If a pick missed slightly, correct the pixel.
- When no iron pieces remain on the table (outside the bin), return an empty program and
  done=true.
- Keep "message" to one short sentence describing what you are doing."""

PLAN_SCHEMA = {
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
                    "material": {"type": "string", "enum": ["iron", "aluminum", "brass", "plastic", "unknown"]},
                    "ferrous": {"type": "boolean"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "u", "v", "material", "ferrous", "confidence", "reason"],
                "additionalProperties": False,
            },
        },
        "program": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["pick", "place", "home", "wait", "move_xy", "move_z", "magnet_on", "magnet_off"]},
                    "u": {"type": ["integer", "null"]},
                    "v": {"type": ["integer", "null"]},
                    "z": {"type": ["number", "null"]},
                    "target": {"type": ["string", "null"]},
                    "seconds": {"type": ["number", "null"]},
                    "piece": {"type": ["string", "null"]},
                },
                "required": ["op", "u", "v", "z", "target", "seconds", "piece"],
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


def encode_jpeg(frame_bgr: np.ndarray, quality: int = 88) -> str:
    ok, buf = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("jpeg encode failed")
    return base64.b64encode(buf.tobytes()).decode()


class AstraPlanner:
    name = "gpt-6-astra"

    def __init__(self, model: str = MODEL, verbose: bool = True):
        self.client = OpenAI(api_key=load_api_key())
        self.model = model
        self.verbose = verbose
        self.usage: list[dict] = []

    def plan(self, frame_bgr: np.ndarray, history: list[str], round_no: int) -> dict:
        h, w = frame_bgr.shape[:2]
        feedback = "\n".join(f"- {line}" for line in history[-12:]) or "- (first round, no feedback yet)"
        text = (
            f"Round {round_no}. Image size {w}x{h} px.\n"
            f"Feedback from the arm so far:\n{feedback}\n\n"
            "List every piece you see with its material, then write the program."
        )
        response = self.client.responses.create(
            model=self.model,
            instructions=SYSTEM,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": text},
                        {"type": "input_image", "image_url": f"data:image/jpeg;base64,{encode_jpeg(frame_bgr)}", "detail": "high"},
                    ],
                }
            ],
            text={"format": {"type": "json_schema", "name": "sort_plan", "schema": PLAN_SCHEMA, "strict": True}},
        )
        if response.usage is not None:
            self.usage.append({"input": response.usage.input_tokens, "output": response.usage.output_tokens})
        plan = json.loads(response.output_text)
        for i, piece in enumerate(plan.get("pieces", []), 1):
            piece.setdefault("id", f"p{i}")
        return plan
