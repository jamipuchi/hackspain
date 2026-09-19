"""GPT-6 as a closed-loop agent: one tool call at a time, a fresh photo after every motion.

Build-agnostic: the task text, containers, workspace, cameras and optional conveyor come from
the active scene_def build via RobotAPI.describe(); the tool list is generated from the same
config (a build without a conveyor gets no belt tool, target names become an enum, …). A new
arm or use case therefore needs only a BuildConfig.

Uses the Responses API with function tools, reasoning.effort and previous_response_id chaining
(OpenAI "latest model" guide). Tool results are returned as function_call_output text; the
photo(s) taken after the action are appended as a user message with input_image parts, since
function outputs are text-only.
"""

from __future__ import annotations

import base64
import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Callable

import cv2
import numpy as np
from openai import OpenAI

import scene_def as sd
from brain import MODEL, load_api_key
from camera import TableCalibration, draw_metric_grid
from controller import in_workspace
from jev_brain import JevClient, USD_PER_INPUT_TOKEN
from robot_api import RobotAPI, ToolResult

APPEARANCE = """Appearance cues (unreliable priors): zinc-plated steel = dull bluish/grey silver, sometimes a warm
tint; black-oxide steel = dark matte; rusty steel = brown/orange; stainless = bright neutral silver,
often shinier than zinc; aluminium = light matte silver, no colour cast; brass = yellow-gold;
copper = reddish. Screws: round pan head with a cross slot + threaded shaft. Nuts: hexagonal with a
hole. Washers: flat rings. Only steel (zinc, black, rusty) is lifted by the electromagnet."""


def system_prompt(robot: RobotAPI) -> str:
    t = sd.TASK
    return f"""You are the brain of a small hobby robot arm with an ELECTROMAGNET hanging from its tip. You act
through tools, one step at a time, and you SEE the result of every motion in a fresh photo before
deciding the next step. Bias towards action and persist until the task is verifiably complete.

TASK: {t["goal"]}

{robot.describe()}

Scene notes: {t["scene"]}

{APPEARANCE}

How to work:
- A photo is attached automatically after every motion, so you rarely need take_photo: use it for a zoom
  on a small part or for the other camera. Never take more than two photos without acting.
- Start by surveying the parts in the initial photos and write them down ONCE with note_parts (positions in
  cm, kind, material, target). Then work through the inventory part by part; update it when a part is done or
  fails. Do not re-survey. Read part positions in cm off the grid;
  with two cameras, cross-check a position in both views (the grid is exact on the table plane, and a
  part's base sits on that plane) and use the second view where the arm hides something in the first.
  When unsure about a small part, use take_photo with a zoom centre to inspect it.
- pick_at(x, y) does the whole approach–energise–lift; place_in(target) parks, checks by camera whether
  the part actually left its spot, then releases in the container and tells you what it saw. Aim at
  the CENTRE of the part's footprint on the table (for a screw: the middle of its shaft, not the head).
  Use move_to / nudge / magnet for finer control when a pick fails (e.g. re-centre by 0.5 cm and
  retry, or lower slowly). If a part was nudged, retry at its new position. Give each part up to three
  attempts before leaving it.
- Identify the TYPE carefully before choosing the container: a nut is a thick hexagon with a hole, a
  washer is a thin flat ring, a screw is elongated with a round head. Zoom when the part is small.
- Two parts within ~1 cm of each other may come up together on the magnet; if a container receives a
  wrong part you can pick it back out of the container if it is reachable, otherwise note it.
- A part that does not lift after a centred attempt is not steel: leave it and say so.
- Parts arrive in BATCHES: the operator puts a handful on the card at a time. When your done is accepted and more
  parts exist, the operator adds the next batch and you are told so; rebuild the inventory and continue.
- Before calling done, take a final photo and check every part is where the task wants it.
  done() is only accepted after that check; you will be shown the final photos once more to confirm.
Keep each tool call purposeful; there is a budget of about {robot.budget_actions} actions."""


def tool_defs(robot: RobotAPI) -> list[dict]:
    cams = list(robot.camera_names)
    targets = list(sd.TARGETS)
    num = {"type": "number"}
    tools = [
        dict(type="function", name="take_photo", description="Take a photo from a camera (or all). Optionally centre a 2× zoom crop on a table point (cm) to inspect small parts.", strict=True,
             parameters={"type": "object", "properties": {"camera": {"type": "string", "enum": [*cams, "all"]}, "zoom_x_cm": {"type": ["number", "null"]}, "zoom_y_cm": {"type": ["number", "null"]}}, "required": ["camera", "zoom_x_cm", "zoom_y_cm"], "additionalProperties": False}),
        dict(type="function", name="move_to", description="Move the magnet face to table coordinates (cm). z is height above the work surface; travel at z≈travel height, lower to pick height to touch parts.", strict=True,
             parameters={"type": "object", "properties": {"x_cm": num, "y_cm": num, "z_cm": num}, "required": ["x_cm", "y_cm", "z_cm"], "additionalProperties": False}),
        dict(type="function", name="nudge", description="Move the magnet face by a relative offset in cm.", strict=True,
             parameters={"type": "object", "properties": {"dx_cm": num, "dy_cm": num, "dz_cm": num}, "required": ["dx_cm", "dy_cm", "dz_cm"], "additionalProperties": False}),
        dict(type="function", name="magnet", description="Switch the electromagnet on or off.", strict=True,
             parameters={"type": "object", "properties": {"on": {"type": "boolean"}}, "required": ["on"], "additionalProperties": False}),
        dict(type="function", name="pick_at", description="Full pick: move above (x, y) cm, lower onto the part, energise the magnet, lift to travel height.", strict=True,
             parameters={"type": "object", "properties": {"x_cm": num, "y_cm": num}, "required": ["x_cm", "y_cm"], "additionalProperties": False}),
        dict(type="function", name="place_in", description="Park, verify by camera that the last picked part left its spot, move over the container and release.", strict=True,
             parameters={"type": "object", "properties": {"target": {"type": "string", "enum": targets}}, "required": ["target"], "additionalProperties": False}),
        dict(type="function", name="home", description="Park the arm out of the camera's way.", strict=True, parameters={"type": "object", "properties": {}, "required": [], "additionalProperties": False}),
        dict(type="function", name="note_parts", description="Write down (replace) your inventory of the loose parts: position in cm, kind, material, intended target, status. It is echoed back to you every step, so survey once, note, then act.", strict=True,
             parameters={"type": "object", "properties": {"parts": {"type": "array", "items": {"type": "object", "properties": {
                 "id": {"type": "string"}, "x_cm": num, "y_cm": num, "kind": {"type": "string", "enum": sd.TASK["kinds"]}, "material": {"type": "string", "enum": sd.TASK["materials"]},
                 "target": {"type": "string", "enum": [*targets, "leave"]}, "status": {"type": "string", "enum": ["todo", "done", "failed", "leave"]}, "note": {"type": "string"}},
                 "required": ["id", "x_cm", "y_cm", "kind", "material", "target", "status", "note"], "additionalProperties": False}}}, "required": ["parts"], "additionalProperties": False}),
        dict(type="function", name="done", description="Declare the task complete (call only after a final photo check).", strict=True,
             parameters={"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"], "additionalProperties": False}),
    ]
    if sd.CONVEYOR:
        tools.append(dict(type="function", name="belt_advance", description="Run the conveyor forward by N cm (1–20) and stop; new parts enter the pick zone, unsorted ones leave it.", strict=True,
                          parameters={"type": "object", "properties": {"cm": num}, "required": ["cm"], "additionalProperties": False}))
    return tools


def _jpeg_b64(img: np.ndarray, quality: int = 88) -> str:
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    return base64.b64encode(buf.tobytes()).decode()


@dataclass
class AgentRun:
    steps: int = 0
    calls: int = 0
    usage_in: int = 0
    usage_out: int = 0
    astra_calls: int = 0
    openrouter_calls: int = 0
    openrouter_usage_in: int = 0
    openrouter_usage_out: int = 0
    openrouter_cost_usd: float = 0.0
    cost_complete: bool = True
    jev_calls: int = 0
    jev_usage_in: int = 0
    jev_usage_out: int = 0
    log: list[dict] = field(default_factory=list)
    finished: bool = False
    summary: str = ""

    def cost_usd(self) -> float:
        return self.usage_in * 10e-6 + self.usage_out * 50e-6 + self.openrouter_cost_usd + self.jev_usage_in * USD_PER_INPUT_TOKEN


@dataclass
class Decision:
    calls: list
    text: str = ""


@dataclass
class CandidateCall:
    call_id: str
    name: str
    arguments: str


def vision_schema() -> dict:
    """The vision model describes the scene. It does not plan robot actions."""
    part = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "x_cm": {"type": "number"},
            "y_cm": {"type": "number"},
            "kind": {"type": "string", "enum": sd.TASK["kinds"]},
            "material": {"type": "string", "enum": sd.TASK["materials"]},
            "description": {"type": "string"},
        },
        "required": ["id", "x_cm", "y_cm", "kind", "material", "description"],
        "additionalProperties": False,
    }
    containers = {name: {"type": "string"} for name in sd.TARGETS}
    return {
        "type": "object",
        "properties": {"scene_summary": {"type": "string"}, "parts": {"type": "array", "items": part}, "containers": {"type": "object", "properties": containers, "required": list(containers), "additionalProperties": False}},
        "required": ["scene_summary", "parts", "containers"],
        "additionalProperties": False,
    }


class GPT6Agent:
    def __init__(self, robot: RobotAPI, cameras: dict[str, Callable[[], np.ndarray]], calibrations: dict[str, TableCalibration], run_dir, on_event: Callable[[str, dict], None] | None = None, effort: str = "low", max_steps: int = 60, budget_usd: float = 4.0, model: str = MODEL, refill=None, client=None):
        self.robot = robot
        self.cameras = cameras
        self.cal = calibrations
        self.run_dir = run_dir
        self.on_event = on_event or (lambda kind, payload: None)
        self.effort = effort
        self.max_steps = max_steps
        self.budget_usd = budget_usd
        self.model = model
        self.client = client or OpenAI(api_key=load_api_key())
        self.robot.budget_actions = max_steps
        self.robot.camera_names = list(cameras)
        self.system = system_prompt(robot)
        self.tools = tool_defs(robot)
        self.photo_n = 0
        self.run = AgentRun()
        self.awaiting_done_confirm = False
        self.refill = refill  # optional: called on confirmed done; returns a message if new parts arrived (then we continue)
        self.batches_done = 0
        self.history: list[str] = []  # compact text log replaces chained context (keeps cost linear)
        self.consecutive_photos = 0
        self.unproductive = 0  # consecutive take_photo / home / note_parts calls
        self.inventory: list[dict] = []
        self.post_action_size = (1024, 768)
        self.requests = 0
        self.max_requests = max(1, max_steps * 3)

    # ------------------------------------------------------------ photos
    def photo(self, camera: str = "A", zoom: tuple[float, float] | None = None, small: bool = False) -> tuple[list[dict], list[str]]:
        """Grab, overlay the metric grid, save; return input_image parts and file names."""
        parts, files = [], []
        names = list(self.cameras) if camera == "all" else [camera]
        for name in names:
            frame = self.cameras[name]()
            cal = self.cal[name]
            if cal.H is None:
                cal.fit(frame)
            img = draw_metric_grid(frame, cal) if cal.H is not None else frame
            if small and zoom is None:
                img = cv2.resize(img, self.post_action_size, interpolation=cv2.INTER_AREA)
            if zoom is not None and cal.H is not None:
                u, v = cal.table_to_pixel(zoom[0] / 100, zoom[1] / 100)
                h, w = img.shape[:2]
                r = 160
                x0, y0 = max(0, min(w - 2 * r, u - r)), max(0, min(h - 2 * r, v - r))
                img = cv2.resize(img[y0 : y0 + 2 * r, x0 : x0 + 2 * r], (4 * r, 4 * r), interpolation=cv2.INTER_CUBIC)
                cv2.putText(img, f"camera {name} zoom around ({zoom[0]:.1f},{zoom[1]:.1f}) cm", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3, cv2.LINE_AA)
                cv2.putText(img, f"camera {name} zoom around ({zoom[0]:.1f},{zoom[1]:.1f}) cm", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
            self.photo_n += 1
            fname = f"agent_{self.photo_n:03d}_cam{name}{'_zoom' if zoom else ''}.png"
            cv2.imwrite(str(self.run_dir / fname), img)
            files.append(fname)
            parts.append({"type": "input_text", "text": f"camera {name}{' (zoom)' if zoom else ''}:"})
            parts.append({"type": "input_image", "image_url": f"data:image/jpeg;base64,{_jpeg_b64(img)}", "detail": "high"})
        return parts, files

    def mosaic(self) -> tuple[list[dict], list[str]]:
        """Zoom crops (3×) of every inventory part from every camera, tiled with labels."""
        todo = [p for p in self.inventory if p.get("status") in ("todo", "failed")][:12]
        if not todo:
            return [], []
        tiles = []
        for name, grab in self.cameras.items():
            frame = grab()
            cal = self.cal[name]
            if cal.H is None:
                continue
            for p in todo:
                u, v = cal.table_to_pixel(p["x_cm"] / 100, p["y_cm"] / 100)
                h, w = frame.shape[:2]
                r = 60
                x0, y0 = max(0, min(w - 2 * r, u - r)), max(0, min(h - 2 * r, v - r))
                crop = cv2.resize(frame[y0 : y0 + 2 * r, x0 : x0 + 2 * r], (3 * 2 * r, 3 * 2 * r), interpolation=cv2.INTER_CUBIC)
                cv2.rectangle(crop, (0, 0), (crop.shape[1] - 1, crop.shape[0] - 1), (255, 255, 255), 2)
                cv2.putText(crop, f"{p['id']} cam{name} ({p['x_cm']:.1f},{p['y_cm']:.1f})", (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3, cv2.LINE_AA)
                cv2.putText(crop, f"{p['id']} cam{name} ({p['x_cm']:.1f},{p['y_cm']:.1f})", (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 1, cv2.LINE_AA)
                tiles.append(crop)
        if not tiles:
            return [], []
        cols = min(4, len(tiles))
        rows = (len(tiles) + cols - 1) // cols
        th, tw = tiles[0].shape[:2]
        canvas = np.full((rows * th, cols * tw, 3), 30, np.uint8)
        for i, tile in enumerate(tiles):
            rr, cc = divmod(i, cols)
            canvas[rr * th : (rr + 1) * th, cc * tw : (cc + 1) * tw] = tile
        self.photo_n += 1
        fname = f"agent_{self.photo_n:03d}_mosaic.png"
        cv2.imwrite(str(self.run_dir / fname), canvas)
        return [{"type": "input_text", "text": "zoom mosaic of your noted parts (3×, each camera):"}, {"type": "input_image", "image_url": f"data:image/jpeg;base64,{_jpeg_b64(canvas)}", "detail": "high"}], [fname]

    # ------------------------------------------------------------ tool dispatch
    def _execute(self, name: str, args: dict) -> tuple[ToolResult, list[dict], list[str]]:
        r = self.robot
        if name in ("take_photo", "home", "note_parts"):
            self.unproductive += 1
        else:
            self.unproductive = 0
        if name == "note_parts":
            self.inventory = args.get("parts", [])
            parts, files = self.mosaic()
            return ToolResult(True, f"inventory noted: {len(self.inventory)} parts. A zoom mosaic of each noted part from every camera is attached: confirm kinds/materials, fix the inventory if needed, then act on the first 'todo' part", photo=False), parts, files
        if name == "take_photo":
            self.consecutive_photos += 1
            if self.consecutive_photos > 3 or self.unproductive > 5:
                return ToolResult(False, "refused: too many looks/parks without acting. Photos are attached automatically after every action. Pick the first 'todo' part from your inventory now with pick_at, or call done after the final check.", photo=False), [], []
            zoom = (args["zoom_x_cm"], args["zoom_y_cm"]) if args.get("zoom_x_cm") is not None and args.get("zoom_y_cm") is not None else None
            parts, files = self.photo(args.get("camera", list(self.cameras)[0]), zoom)
            return ToolResult(True, f"photo taken; {r._fmt_state()}", photo=False), parts, files
        self.consecutive_photos = 0
        if name == "home" and self.unproductive > 5:
            return ToolResult(False, "refused: act on a part (pick_at) instead of parking again.", photo=False), [], []
        if name == "move_to":
            res = r.move_to(args["x_cm"], args["y_cm"], args["z_cm"])
        elif name == "nudge":
            res = r.nudge(args["dx_cm"], args["dy_cm"], args["dz_cm"])
        elif name == "magnet":
            res = r.magnet(bool(args["on"]))
        elif name == "pick_at":
            res = r.pick_at(args["x_cm"], args["y_cm"])
        elif name == "place_in":
            res = r.place_in(args["target"])
        elif name == "home":
            res = r.home()
        elif name == "belt_advance":
            res = r.belt_advance(float(args["cm"]))
        elif name == "done":
            return ToolResult(True, "", photo=False), [], []
        else:
            return ToolResult(False, f"unknown tool {name}", photo=False), [], []
        parts, files = self.photo(list(self.cameras)[0], small=True) if res.photo else ([], [])
        return res, parts, files

    # ------------------------------------------------------------ main loop
    def _context_message(self, note: str, parts: list[dict]) -> dict:
        log = "\n".join(self.history[-40:]) or "(nothing done yet)"
        inv = "\n".join(f"  {p['id']}: ({p['x_cm']:.1f}, {p['y_cm']:.1f}) cm {p['kind']} {p['material']} → {p['target']} [{p['status']}] {p.get('note', '')}" for p in self.inventory) or "  (empty — call note_parts after surveying)"
        text = f"{note}\n\nYour parts inventory (update it with note_parts when something changes):\n{inv}\n\nProgress log (oldest first):\n{log}\n\nCurrent robot state: {self.robot._fmt_state()}."
        return {"role": "user", "content": [{"type": "input_text", "text": text}, *parts]}

    def _record_astra(self, resp) -> None:
        self.requests += 1
        self.run.calls += 1
        self.run.astra_calls += 1
        if resp.usage is not None:
            self.run.usage_in += resp.usage.input_tokens
            self.run.usage_out += resp.usage.output_tokens

    def _decide(self, inputs: list[dict]) -> Decision:
        resp = self.client.responses.create(
            model=self.model,
            instructions=self.system,
            input=inputs,
            tools=self.tools,
            reasoning={"effort": self.effort},
            store=False,
        )
        self._record_astra(resp)
        calls = [it for it in resp.output if getattr(it, "type", "") == "function_call"]
        text = "".join(
            c.text for it in resp.output if getattr(it, "type", "") == "message"
            for c in getattr(it, "content", []) if getattr(c, "type", "") == "output_text"
        )
        return Decision(calls, text)

    def run_loop(self) -> AgentRun:
        first_parts, first_files = self.photo("all")
        self.on_event("agent_photo", {"files": first_files, "note": "initial survey"})
        inputs = [self._context_message("Begin. Initial view from every camera. Survey the parts, then act.", first_parts)]
        while self.run.steps < self.max_steps and self.run.cost_usd() < self.budget_usd and self.requests < self.max_requests:
            t0 = time.time()
            decision = self._decide(inputs)
            calls = decision.calls
            if not calls:
                self.on_event("agent_text", {"text": decision.text})
                self.history.append(f"(you wrote: {decision.text[:200]})")
                inputs = [self._context_message("Continue with a tool call (or call done after your final photo check).", [])]
                continue
            # Sliding window: echo the function_call items back with their outputs, then the compact log + latest photos.
            inputs = []
            photo_parts: list[dict] = []
            note = "Result of your last action(s) above; latest photo attached."
            for call in calls:
                args = json.loads(call.arguments or "{}")
                self.run.steps += 1
                self.on_event("tool_call", {"step": self.run.steps, "name": call.name, "args": args, "latency_s": round(time.time() - t0, 1)})
                inputs.append({"type": "function_call", "call_id": call.call_id, "name": call.name, "arguments": call.arguments or "{}"})
                if call.name == "done":
                    if not self.awaiting_done_confirm:
                        parts, files = self.photo("all")
                        self.awaiting_done_confirm = True
                        self.on_event("tool_result", {"step": self.run.steps, "name": "done", "ok": True, "text": "confirmation requested", "files": files})
                        self.history.append(f"step {self.run.steps}: done requested → final check photos shown, awaiting confirmation")
                        inputs.append({"type": "function_call_output", "call_id": call.call_id, "output": json.dumps({"ok": False, "message": "Not accepted yet: inspect the final photos from every camera. If every part is where the task wants it, call done again; otherwise continue working."})})
                        photo_parts += parts
                        note = "Final check photos from every camera:"
                        continue
                    more = self.refill() if self.refill else None
                    if more:
                        self.batches_done += 1
                        self.awaiting_done_confirm = False
                        self.inventory = []
                        self.consecutive_photos = 0
                        self.unproductive = 0
                        self._after_batch_refill()
                        self.on_event("tool_result", {"step": self.run.steps, "name": "done", "ok": True, "text": f"batch {self.batches_done} accepted: {args.get('summary', '')[:120]} | " + more, "files": []})
                        self.history.append(f"step {self.run.steps}: batch {self.batches_done} accepted ({args.get('summary', '')[:100]}). New parts arrived on the card; inventory reset.")
                        inputs.append({"type": "function_call_output", "call_id": call.call_id, "output": json.dumps({"ok": True, "message": more, "state": self.robot.state()})})
                        parts, files = self.photo("all")
                        photo_parts += parts
                        note = "New parts on the card (photos from every camera):"
                        continue
                    self.run.finished = True
                    self.run.summary = args.get("summary", "")
                    self.on_event("tool_result", {"step": self.run.steps, "name": "done", "ok": True, "text": self.run.summary, "files": []})
                    return self.run
                self.awaiting_done_confirm = False
                res, parts, files = self._execute(call.name, args)
                self._after_action(call.name, args, res)
                self.on_event("tool_result", {"step": self.run.steps, "name": call.name, "ok": res.ok, "text": res.text, "files": files})
                self.history.append(f"step {self.run.steps}: {call.name}({json.dumps(args)}) → {'OK' if res.ok else 'FAILED'}: {res.text}")
                inputs.append({"type": "function_call_output", "call_id": call.call_id, "output": json.dumps({"ok": res.ok, "message": res.text, "state": self.robot.state()})})
                photo_parts += parts
            inputs.append(self._context_message(note, photo_parts))
        self.run.summary = "stopped: step, cost, or request budget exhausted"
        return self.run

    def _after_action(self, name: str, args: dict, result: ToolResult) -> None:
        """Subclasses can retain feedback that affects their next bounded choice."""

    def _after_batch_refill(self) -> None:
        """Subclasses can clear state that only applies to the completed batch."""


class JevAgent(GPT6Agent):
    """A vision model describes photos. Jev chooses one validated high-level action."""

    def __init__(self, robot: RobotAPI, cameras: dict[str, Callable[[], np.ndarray]], calibrations: dict[str, TableCalibration], run_dir, on_event: Callable[[str, dict], None] | None = None, effort: str = "low", max_steps: int = 60, budget_usd: float = 4.0, model: str = MODEL, refill=None, *, vision_provider: str = "openai", reuse_pick_observation: bool = False):
        if vision_provider not in ("openai", "openrouter"):
            raise ValueError("vision_provider must be openai or openrouter")
        vision_client = None
        if vision_provider == "openrouter":
            key = os.environ.get("OPENROUTER_API_KEY")
            if not key:
                raise RuntimeError("Vision requires OPENROUTER_API_KEY")
            vision_client = OpenAI(api_key=key, base_url="https://openrouter.ai/api/v1", timeout=90, max_retries=0)
        super().__init__(robot, cameras, calibrations, run_dir, on_event, effort, max_steps, budget_usd, model, refill, client=vision_client)
        self.vision_model = model
        self.vision_provider = vision_provider
        self.reuse_pick_observation = reuse_pick_observation
        self.observation_fresh = False
        self._reuse_next_vision = False
        self.jev_client = JevClient()
        self.decision_model = self.jev_client.model
        self.observation: dict = {"scene_summary": "No photo has been described yet.", "parts": [], "containers": {name: "not observed yet" for name in sd.TARGETS}}
        self.pick_attempts: list[dict] = []
        self.pending_pick: dict | None = None
        self.last_place_feedback: dict | None = None
        self.trace_path = self.run_dir / "jev_decisions.jsonl"

    def _trace(self, event: dict) -> None:
        """Persist text-only model state for an experiment replay."""
        with self.trace_path.open("a") as f:
            f.write(json.dumps(event, sort_keys=True) + "\n")

    @staticmethod
    def _fresh_images(inputs: list[dict]) -> list[dict]:
        images = []
        for item in inputs:
            content = item.get("content", [])
            for i, part in enumerate(content):
                if part.get("type") != "input_image":
                    continue
                if i and content[i - 1].get("type") == "input_text":
                    images.append(content[i - 1])
                images.append(part)
        return images

    def _record_vision(self, resp) -> None:
        self.requests += 1
        self.run.calls += 1
        usage = getattr(resp, "usage", None)
        if self.vision_provider == "openai":
            self.run.astra_calls += 1
            if usage is not None:
                self.run.usage_in += usage.input_tokens
                self.run.usage_out += usage.output_tokens
            return
        self.run.openrouter_calls += 1
        if usage is not None:
            self.run.openrouter_usage_in += usage.input_tokens
            self.run.openrouter_usage_out += usage.output_tokens
            cost = getattr(usage, "cost", None)
        else:
            cost = None
        if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
            self.run.cost_complete = False
            self._trace({
                "kind": "invalid_vision_accounting", "provider": self.vision_provider, "model": self.vision_model,
                "response_id": getattr(resp, "id", None), "status": getattr(resp, "status", None), "error": str(getattr(resp, "error", None)) if getattr(resp, "error", None) is not None else None,
                "usage": {"input_tokens": getattr(usage, "input_tokens", None), "output_tokens": getattr(usage, "output_tokens", None), "cost": cost},
            })
            raise RuntimeError("Vision response is missing a finite OpenRouter usage cost")
        self.run.openrouter_cost_usd += float(cost)

    def _observe(self, inputs: list[dict], images: list[dict]) -> None:
        state = {
            "robot_state": self.robot.state(),
            "coordinate_frame": (
                "Table-frame centimetres. The origin is the arm base. Positive X points forward from the arm. "
                "Positive Y points to the arm's left when facing forward, not the camera's left. "
                "Read signed X and Y labels on the grid. Both cameras share this frame. "
                "Do not infer coordinate signs from image-left or image-right."
            ),
            "previous_observation": self.observation,
            "history": self.history[-16:],
        }
        container_positions = {name: {"position_cm": [round(value * 100, 1) for value in target["pos"]], "description": target["label"]} for name, target in sd.TARGETS.items()}
        prompt = (
            "Describe visible loose hardware parts only in parts. Return a stable id, table coordinates in cm, kind, material, and a short visual description for each part. "
            "Return coordinates at the centre of each part's footprint on the table. For screws, use the shaft midpoint, not the head. "
            "For every named container, describe its visible contents, including wrong kinds, or say it is obscured. Return a scene summary. Do not assign destinations, recommend actions, or mention tools.\n"
            + json.dumps(state)
            + "\nContainers to inspect: " + json.dumps(container_positions)
        )
        t0 = time.time()
        try:
            resp = self.client.responses.create(
                model=self.vision_model,
                instructions="You are a careful visual observer for a robot experiment. Report only what the photos show.",
                input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}, *images]}],
                text={"format": {"type": "json_schema", "name": "loose_parts_observation", "schema": vision_schema(), "strict": True}},
                reasoning={"effort": self.effort},
                store=False,
                extra_body={"provider": {"require_parameters": True}} if self.vision_provider == "openrouter" else None,
            )
        except (Exception, KeyboardInterrupt, SystemExit):
            if self.vision_provider == "openrouter":
                self.run.cost_complete = False
            raise
        self._record_vision(resp)
        try:
            observation = json.loads(resp.output_text)
        except (AttributeError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Vision returned an invalid observation") from exc
        self._validate_observation(observation)
        self.observation = observation
        self.on_event("vision_observation", observation)
        usage = getattr(resp, "usage", None)
        self.observation_fresh = True
        self._trace({
            "kind": "vision", "provider": self.vision_provider, "model": getattr(resp, "model", self.vision_model), "observation": observation, "latency_s": round(time.time() - t0, 3),
            "usage": {"input_tokens": getattr(usage, "input_tokens", 0), "output_tokens": getattr(usage, "output_tokens", 0)},
        })

    @staticmethod
    def _validate_observation(observation: dict) -> None:
        if not isinstance(observation, dict) or set(observation) != {"scene_summary", "parts", "containers"} or not isinstance(observation["scene_summary"], str) or not isinstance(observation["parts"], list) or not isinstance(observation["containers"], dict):
            raise RuntimeError("Vision returned an invalid observation")
        if set(observation["containers"]) != set(sd.TARGETS) or not all(isinstance(value, str) for value in observation["containers"].values()):
            raise RuntimeError("Vision returned invalid container observations")
        seen = set()
        for part in observation["parts"]:
            if not isinstance(part, dict) or set(part) != {"id", "x_cm", "y_cm", "kind", "material", "description"}:
                raise RuntimeError("Vision returned an invalid part")
            if not isinstance(part["id"], str) or not part["id"] or part["id"] in seen:
                raise RuntimeError("Vision returned duplicate or missing part ids")
            if part["kind"] not in sd.TASK["kinds"] or part["material"] not in sd.TASK["materials"] or not isinstance(part["description"], str):
                raise RuntimeError("Vision returned an invalid part label")
            if any(isinstance(part[key], bool) or not isinstance(part[key], (int, float)) or not math.isfinite(part[key]) for key in ("x_cm", "y_cm")):
                raise RuntimeError("Vision returned non-finite part coordinates")
            seen.add(part["id"])

    def _can_take_photo(self) -> bool:
        return self.consecutive_photos < 3 and self.unproductive <= 5

    def _attempt_record(self, part: dict, create: bool = False) -> dict | None:
        for record in self.pick_attempts:
            if record["id"] == part["id"] or math.hypot(record["x_cm"] - part["x_cm"], record["y_cm"] - part["y_cm"]) <= 1:
                return record
        if create:
            record = {"id": part["id"], "x_cm": part["x_cm"], "y_cm": part["y_cm"], "attempts": 0, "failed_attempts": 0}
            self.pick_attempts.append(record)
            return record
        return None

    def _candidates(self) -> dict[str, dict]:
        candidates: dict[str, dict] = {}
        if self.robot.magnet_on:
            for target in sd.TARGETS:
                candidates[f"place:{target}"] = {"name": "place_in", "args": {"target": target}, "description": f"place the attached part in {target}: {sd.TARGETS[target]['label']}"}
            if self._can_take_photo():
                candidates["photo"] = {"name": "take_photo", "args": {"camera": "all", "zoom_x_cm": None, "zoom_y_cm": None}, "description": "take a new photo before placing"}
            return candidates
        for part in self.observation["parts"]:
            x_cm, y_cm = float(part["x_cm"]), float(part["y_cm"])
            record = self._attempt_record(part)
            attempts = record["attempts"] if record else 0
            failures = record["failed_attempts"] if record else 0
            if in_workspace(x_cm / 100, y_cm / 100) and attempts < 3 and failures < sd.TASK.get("max_failed_pick_attempts", 3):
                candidates[f"pick:{part['id']}"] = {"name": "pick_at", "args": {"x_cm": x_cm, "y_cm": y_cm}, "description": f"pick observed {part['id']} at ({x_cm:.1f}, {y_cm:.1f}) cm: {part['kind']} {part['material']}"}
        if self.unproductive < 5:
            candidates["home"] = {"name": "home", "args": {}, "description": "park the arm"}
        if self._can_take_photo():
            candidates["photo"] = {"name": "take_photo", "args": {"camera": "all", "zoom_x_cm": None, "zoom_y_cm": None}, "description": "take a new photo"}
        candidates["done"] = {"name": "done", "args": {"summary": "Jev selected done after the current observation."}, "description": "request completion. A final photo confirmation is still required."}
        if sd.CONVEYOR:
            candidates["belt"] = {"name": "belt_advance", "args": {"cm": 5.0}, "description": "advance the conveyor by 5 cm"}
        return candidates

    def _record_jev(self, body: dict, before_usage: int) -> None:
        self.requests += 1
        self.run.calls += 1
        self.run.jev_calls += 1
        usage = None
        if len(self.jev_client.usage) > before_usage:
            usage = self.jev_client.usage[-1]
        elif isinstance(body, dict) and isinstance(body.get("usage"), dict):
            usage = body["usage"]
        if usage is not None:
            self.run.jev_usage_in += int(usage.get("input", usage.get("input_tokens", 0)))
            self.run.jev_usage_out += int(usage.get("output", usage.get("output_tokens", 0)))

    @staticmethod
    def _chosen_candidate(body: dict, candidates: dict[str, dict]) -> tuple[str, float, float]:
        answers = body.get("answers") if isinstance(body, dict) else None
        answer = answers.get("action") if isinstance(answers, dict) else None
        if not isinstance(answer, dict) or answer.get("type") != "choice":
            raise RuntimeError("Jev returned an invalid action answer")
        selected, probabilities, confidence = answer.get("choice"), answer.get("probabilities"), answer.get("confidence")
        if not isinstance(selected, str) or selected not in candidates or not isinstance(probabilities, dict) or set(probabilities) != set(candidates):
            raise RuntimeError("Jev returned invalid action probabilities")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise RuntimeError("Jev returned an invalid action confidence")
        values = []
        for key in candidates:
            value = probabilities[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                raise RuntimeError("Jev returned invalid action probabilities")
            values.append(float(value))
        # Live Jev responses round each probability to two decimal places.
        total = math.fsum(values)
        rounding_error = 0.005 * len(values) + 1e-12
        if total <= 0 or not math.isclose(total, 1.0, rel_tol=0, abs_tol=rounding_error) or float(probabilities[selected]) < max(values):
            raise RuntimeError("Jev returned invalid action probabilities")
        return selected, float(probabilities[selected]), float(confidence)

    def _decide(self, inputs: list[dict]) -> Decision:
        if self.run.cost_usd() >= self.budget_usd or self.requests >= self.max_requests:
            return Decision([])
        images = self._fresh_images(inputs)
        if images:
            if self._reuse_next_vision:
                self._reuse_next_vision = False
                self.observation_fresh = False
                self._trace({"kind": "vision_reused", "provider": self.vision_provider, "model": self.vision_model, "pending_pick": self.pending_pick})
            else:
                self._observe(inputs, images)
        if self.run.cost_usd() >= self.budget_usd or self.requests >= self.max_requests:
            return Decision([])
        candidates = self._candidates()
        state = {
            "task": sd.TASK["goal"],
            "containers": {name: spec["label"] for name, spec in sd.TARGETS.items()},
            "observation": self.observation,
            "observation_fresh": self.observation_fresh,
            "robot_state": self.robot.state(),
            "history": self.history[-24:],
            "pending_pick": self.pending_pick,
            "last_place_feedback": self.last_place_feedback,
            "pick_attempts": self.pick_attempts,
            "remaining_steps": self.max_steps - self.run.steps,
            "awaiting_done_confirmation": self.awaiting_done_confirm,
        }
        criteria = {key: value["description"] for key, value in candidates.items()}
        question = {
            "action": {
                "type": "choice",
                "instructions": "Choose one useful action. Pick motion does not prove attachment until place feedback. Leave nonferrous parts. Choose done only after the final observation confirms completion.",
                "criteria": criteria,
            }
        }
        before_usage = len(self.jev_client.usage)
        t0 = time.time()
        body = self.jev_client.system_one(state, question)
        self._record_jev(body, before_usage)
        try:
            selected, probability, confidence = self._chosen_candidate(body, candidates)
        except RuntimeError:
            self._trace({"kind": "invalid_decision", "state": state, "candidates": candidates, "response": body})
            raise
        candidate = candidates[selected]
        resolved = body.get("model", self.decision_model)
        self.decision_model = resolved
        self._trace({
            "kind": "decision", "model": resolved, "candidates": {key: {"name": value["name"], "args": value["args"]} for key, value in candidates.items()},
            "selected": selected, "selected_probability": probability, "confidence": confidence,
            "probabilities": body["answers"]["action"]["probabilities"], "state": state,
            "latency_s": round(time.time() - t0, 3), "usage": body.get("usage", {}),
        })
        return Decision([CandidateCall(f"jev-{self.requests}", candidate["name"], json.dumps(candidate["args"]))])

    def _after_action(self, name: str, args: dict, result: ToolResult) -> None:
        if name == "pick_at":
            for part in self.observation["parts"]:
                if float(part["x_cm"]) == float(args["x_cm"]) and float(part["y_cm"]) == float(args["y_cm"]):
                    record = self._attempt_record(part, create=True)
                    record["attempts"] += 1
                    if result.ok:
                        self.pending_pick = part.copy()
                        self._reuse_next_vision = self.reuse_pick_observation
                    else:
                        record["failed_attempts"] += 1
                        self.pending_pick = None
                    return
        if name == "place_in":
            if self.pending_pick and not result.ok:
                self._attempt_record(self.pending_pick)["failed_attempts"] += 1
            self.last_place_feedback = {"part": self.pending_pick, "ok": result.ok, "message": result.text}
            self.pending_pick = None

    def _after_batch_refill(self) -> None:
        self.pick_attempts = []
        self.pending_pick = None
        self.last_place_feedback = None
        self._reuse_next_vision = False
        self.observation_fresh = False
