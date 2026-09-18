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
import time
from dataclasses import dataclass, field
from typing import Callable

import cv2
import numpy as np
from openai import OpenAI

import scene_def as sd
from brain import MODEL, load_api_key
from camera import TableCalibration, draw_metric_grid
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
    log: list[dict] = field(default_factory=list)
    finished: bool = False
    summary: str = ""

    def cost_usd(self) -> float:
        return self.usage_in * 10e-6 + self.usage_out * 50e-6


class GPT6Agent:
    def __init__(self, robot: RobotAPI, cameras: dict[str, Callable[[], np.ndarray]], calibrations: dict[str, TableCalibration], run_dir, on_event: Callable[[str, dict], None] | None = None, effort: str = "low", max_steps: int = 60, budget_usd: float = 4.0, model: str = MODEL):
        self.robot = robot
        self.cameras = cameras
        self.cal = calibrations
        self.run_dir = run_dir
        self.on_event = on_event or (lambda kind, payload: None)
        self.effort = effort
        self.max_steps = max_steps
        self.budget_usd = budget_usd
        self.model = model
        self.client = OpenAI(api_key=load_api_key())
        self.robot.budget_actions = max_steps
        self.robot.camera_names = list(cameras)
        self.system = system_prompt(robot)
        self.tools = tool_defs(robot)
        self.photo_n = 0
        self.run = AgentRun()
        self.awaiting_done_confirm = False
        self.history: list[str] = []  # compact text log replaces chained context (keeps cost linear)
        self.consecutive_photos = 0
        self.unproductive = 0  # consecutive take_photo / home / note_parts calls
        self.inventory: list[dict] = []
        self.post_action_size = (1024, 768)

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

    def run_loop(self) -> AgentRun:
        first_parts, first_files = self.photo("all")
        self.on_event("agent_photo", {"files": first_files, "note": "initial survey"})
        inputs = [self._context_message("Begin. Initial view from every camera. Survey the parts, then act.", first_parts)]
        while self.run.steps < self.max_steps and self.run.cost_usd() < self.budget_usd:
            t0 = time.time()
            resp = self.client.responses.create(model=self.model, instructions=self.system, input=inputs, tools=self.tools, reasoning={"effort": self.effort}, store=False)
            self.run.calls += 1
            if resp.usage is not None:
                self.run.usage_in += resp.usage.input_tokens
                self.run.usage_out += resp.usage.output_tokens
            calls = [it for it in resp.output if getattr(it, "type", "") == "function_call"]
            texts = [it for it in resp.output if getattr(it, "type", "") == "message"]
            if not calls:
                msg = "".join(c.text for it in texts for c in getattr(it, "content", []) if getattr(c, "type", "") == "output_text")
                self.on_event("agent_text", {"text": msg})
                self.history.append(f"(you wrote: {msg[:200]})")
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
                    self.run.finished = True
                    self.run.summary = args.get("summary", "")
                    self.on_event("tool_result", {"step": self.run.steps, "name": "done", "ok": True, "text": self.run.summary, "files": []})
                    return self.run
                self.awaiting_done_confirm = False
                res, parts, files = self._execute(call.name, args)
                self.on_event("tool_result", {"step": self.run.steps, "name": call.name, "ok": res.ok, "text": res.text, "files": files})
                self.history.append(f"step {self.run.steps}: {call.name}({json.dumps(args)}) → {'OK' if res.ok else 'FAILED'}: {res.text}")
                inputs.append({"type": "function_call_output", "call_id": call.call_id, "output": json.dumps({"ok": res.ok, "message": res.text, "state": self.robot.state()})})
                photo_parts += parts
            inputs.append(self._context_message(note, photo_parts))
        self.run.summary = "stopped: step or cost budget exhausted"
        return self.run
