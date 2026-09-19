"""TypeSafe Jev as the sorter's brain: code sees, Jev decides, code acts.

Jev (TypeSafe's "System One" model, https://docs.typesafe.ai) is text-only and answers typed
questions (Choice / Score / Noul = yes-no) with calibrated probabilities. It does not look at
images, does not generate text and does not call tools, so it cannot replace GPT-6 one-for-one.
The job is split instead:

  perception (code)   find the loose parts in the calibrated photo and measure each one: footprint
                      in mm, elongation, hole, colour, brightness against the board, shine
  judgement  (Jev)    one request per part, all questions in parallel: kind, material, ferrous?,
                      which container; plus the arm's feedback on that part so far
  control    (code)   confidence-gated program: try the best-supported candidates first (three per
                      photo), never retry a part the magnet could not lift more than once

Same plan() interface as GPT6Planner / MockPlanner / OraclePlanner, so
    ../.venv/bin/python run_demo.py --planner jev
and make_video.py work unchanged (program brain: one plan per photo).

Key: TYPESAFE_API_KEY, else ~/.config/typesafe/api_key. HTTP API only (no SDK dependency):
POST https://api.typesafe.ai/v1/systemone, Bearer auth, 64k tokens/request, $0.042 per M input
tokens, output free, 429/529 → exponential backoff.
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

import scene_def as sd
from controller import in_workspace

MODEL = os.environ.get("JEV_MODEL", "jev-latest")
ENDPOINT = "https://api.typesafe.ai/v1/systemone"
KEY_FILE = Path.home() / ".config" / "typesafe" / "api_key"
USD_PER_INPUT_TOKEN = 0.042e-6  # output tokens are free


def load_api_key() -> str:
    if os.environ.get("TYPESAFE_API_KEY"):
        return os.environ["TYPESAFE_API_KEY"].strip()
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            name, separator, raw = line.strip().removeprefix("export ").partition("=")
            if separator and name.strip() == "TYPESAFE_API_KEY":
                values = shlex.split(raw, comments=True)
                if len(values) == 1 and values[0]:
                    return values[0]
    if KEY_FILE.exists():
        key = KEY_FILE.read_text().strip()
        if key:
            return key
    raise RuntimeError(f"no TypeSafe key: set TYPESAFE_API_KEY or write it to {KEY_FILE}")


class JevClient:
    """Minimal System One client: one POST, retries on 429/529/network errors, usage bookkeeping."""

    def __init__(self, api_key: str | None = None, model: str = MODEL, timeout: float = 30.0, max_retries: int = 4):
        self.api_key = api_key or load_api_key()
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.usage: list[dict] = []
        self.calls = 0

    def system_one(self, state, questions: dict) -> dict:
        payload = json.dumps({"model": self.model, "state": state, "questions": questions}).encode()
        delay = 0.5
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(ENDPOINT, data=payload, headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = json.loads(resp.read())
                self.calls += 1
                u = body.get("usage") or {}
                self.usage.append({"input": int(u.get("input_tokens", 0)), "output": int(u.get("output_tokens", 0))})
                return body
            except urllib.error.HTTPError as exc:
                text = exc.read().decode(errors="replace")[:400]
                if exc.code in (429, 529, 500, 502, 503) and attempt < self.max_retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise RuntimeError(f"TypeSafe HTTP {exc.code}: {text}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt < self.max_retries:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise RuntimeError(f"TypeSafe unreachable: {exc}") from exc
        raise RuntimeError("TypeSafe: retries exhausted")

    def cost_usd(self) -> float:
        return sum(u["input"] for u in self.usage) * USD_PER_INPUT_TOKEN


# ---------------------------------------------------------------- perception (no model)
def degrid(img: np.ndarray, step: int = 100) -> np.ndarray:
    """Erase the 1-px white grid run_demo draws on the photo every `step` px (the labels are at the edges)."""
    out = img.copy()
    h, w = out.shape[:2]
    for x in range(0, w, step):
        lo, hi = max(0, x - 1), min(w - 1, x + 2)
        out[:, lo : hi + 1] = ((img[:, max(0, lo - 1)].astype(np.uint16) + img[:, min(w - 1, hi + 1)]) // 2)[:, None, :].astype(np.uint8)
    for y in range(0, h, step):
        lo, hi = max(0, y - 1), min(h - 1, y + 2)
        out[lo : hi + 1, :] = ((out[max(0, lo - 1), :].astype(np.uint16) + out[min(h - 1, hi + 1), :]) // 2)[None, :, :].astype(np.uint8)
    return out


def _mm_per_px(cal, u: float, v: float) -> float:
    x0, y0 = cal.pixel_to_table(u, v)
    x1, y1 = cal.pixel_to_table(u + 10, v)
    x2, y2 = cal.pixel_to_table(u, v + 10)
    return float((math.hypot(x1 - x0, y1 - y0) + math.hypot(x2 - x0, y2 - y0)) / 2 * 1000 / 10)


def _strict_workspace(x: float, y: float, margin_m: float = 0.006, margin_rad: float = 0.06) -> bool:
    r, yaw = math.hypot(x, y), math.atan2(y, x)
    return sd.WORKSPACE["r"][0] + margin_m <= r <= sd.WORKSPACE["r"][1] - margin_m and sd.WORKSPACE["yaw"][0] + margin_rad <= yaw <= sd.WORKSPACE["yaw"][1] - margin_rad


def find_parts(frame_bgr: np.ndarray, cal) -> list[dict]:
    """Loose parts inside the arm's workspace, measured. Background = large median of the photo itself,
    so it works on MDF, wood or a white pad; the workspace sector removes the arm, containers and markers."""
    img = degrid(frame_bgr)
    h, w = img.shape[:2]
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2Lab).astype(np.int16)
    bg = cv2.medianBlur(img, 61)
    bg_lab = cv2.cvtColor(bg, cv2.COLOR_BGR2Lab).astype(np.int16)
    diff = np.abs(lab - bg_lab)
    d = diff[..., 0] * 1.0 + diff[..., 1] * 1.5 + diff[..., 2] * 1.5

    # pixel mask of the (strict) workspace sector, to get an adaptive threshold from the board texture
    ws = np.zeros((h, w), np.uint8)
    r0, r1 = sd.WORKSPACE["r"]
    a0, a1 = sd.WORKSPACE["yaw"]
    pts = [cal.table_to_pixel(r * math.cos(a), r * math.sin(a)) for r in (r0 + 0.006, r1 - 0.006) for a in np.linspace(a0 + 0.06, a1 - 0.06, 24)]
    poly = np.array(pts[:24] + pts[24:][::-1], np.int32)
    cv2.fillPoly(ws, [poly], 255)
    inside = d[ws > 0]
    if inside.size == 0:
        return []
    thr = float(max(12.0, min(30.0, np.percentile(inside, 80) * 2.5)))
    mask = ((d > thr) & (ws > 0)).astype(np.uint8) * 255
    mm = _mm_per_px(cal, *poly.mean(axis=0))
    k_open = 3
    k_close = max(3, int(round(2.5 / mm)) * 2 + 1)  # bridge ~2.5 mm gaps: highlights split rings and hex faces
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((k_open, k_open), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k_close, k_close), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    parts = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < 20:
            continue
        m = cv2.moments(c)
        u, v = int(m["m10"] / m["m00"]), int(m["m01"] / m["m00"])
        x, y = cal.pixel_to_table(u, v)
        if not _strict_workspace(x, y):
            continue
        mm = _mm_per_px(cal, u, v)
        # footprint measured on the table plane (undoes the oblique view): contour → table mm → min-area rectangle
        tbl = cv2.perspectiveTransform(c.astype(np.float32).reshape(-1, 1, 2), cal.H.astype(np.float64)).reshape(-1, 2) * 1000.0
        (_tx, _ty), (rw, rh), _ang = cv2.minAreaRect(tbl.astype(np.float32))
        long_mm, short_mm = max(rw, rh), min(rw, rh)
        if long_mm < 2.5 or long_mm > 45 or short_mm > 30:
            continue
        fill = abs(cv2.contourArea(tbl.astype(np.float32))) / max(rw * rh, 1.0)
        if fill < 0.25 or long_mm / max(short_mm, 0.1) > 8:
            continue  # a scratch, a cable or a grid remnant, not a part
        cm = np.zeros((h, w), np.uint8)
        cv2.drawContours(cm, [c], -1, 255, -1)
        pix = img[cm > 0]
        hsv = cv2.cvtColor(pix.reshape(-1, 1, 3), cv2.COLOR_BGR2HSV).reshape(-1, 3)
        bgpix = bg[v, u].astype(np.int16)
        bg_hsv = cv2.cvtColor(bg[v, u].reshape(1, 1, 3), cv2.COLOR_BGR2HSV)[0, 0]
        # colour cast relative to the board (cancels the lamp's warm light): Lab a/b offsets of the mid-tone pixels
        plab = lab[cm > 0]
        mid = plab[(plab[:, 0] > 40) & (plab[:, 0] < np.percentile(plab[:, 0], 85))] if len(plab) > 8 else plab
        mid = mid if len(mid) else plab
        da = float(mid[:, 1].mean() - bg_lab[v, u, 1])
        db = float(mid[:, 2].mean() - bg_lab[v, u, 2])
        rr = max(2, int(round(short_mm * 0.18 / mm)))
        core = img[max(0, v - rr) : v + rr, max(0, u - rr) : u + rr].reshape(-1, 3).astype(np.int16)
        core_bg = float((np.abs(core - bgpix).sum(axis=1) < 45).mean()) if core.size else 0.0
        core_dark = float((core.mean(axis=1) < max(60, bg_hsv[2] * 0.45)).mean()) if core.size else 0.0
        parts.append(dict(
            u=u, v=v, x=float(x), y=float(y), area_px=int(area), long_mm=round(long_mm, 1), short_mm=round(short_mm, 1), aspect=round(long_mm / max(short_mm, 0.1), 2), fill=round(fill, 2),
            hue=int(np.median(hsv[:, 0])), sat=int(np.median(hsv[:, 1])), val=int(np.median(hsv[:, 2])), val_p90=int(np.percentile(hsv[:, 2], 90)), val_std=int(hsv[:, 2].std()),
            bg_val=int(bg_hsv[2]), bg_sat=int(bg_hsv[1]), core_bg=round(core_bg, 2), core_dark=round(core_dark, 2),
            L=int(np.median(plab[:, 0])), da=round(da, 1), db=round(db, 1),
        ))
    parts.sort(key=lambda p: (p["v"], p["u"]))
    return parts


def describe(p: dict) -> dict:
    """Turn measurements into the plain-English record Jev reads (Jev is not a calculator: words first, numbers after)."""
    # shape
    if p["aspect"] >= 1.8:
        shape = "elongated, clearly longer than wide"
        if p["fill"] < 0.6:
            shape += " with one end wider than the rest (a head)"
    elif p["aspect"] <= 1.3:
        shape = "compact, about as long as wide, roundish or hexagonal outline"
    else:
        shape = "slightly oblong outline"
    if p["core_bg"] >= 0.6:
        centre = "the board shows through a hole in the middle (an open ring)"
    elif p["core_dark"] >= 0.5:
        centre = "a small dark spot in the middle (a threaded hole or a cross slot)"
    else:
        centre = "solid in the middle, no hole visible"
    # size words (footprint on the table, mm)
    L = p["long_mm"]
    size = "tiny (under 6 mm)" if L < 6 else "small (6-10 mm)" if L < 10 else "medium (10-16 mm)" if L < 16 else "large (16-24 mm)" if L < 24 else "long (over 24 mm)"
    # colour: cast relative to the board, so the lamp's warm light does not turn every part brass
    val = p["val"]
    da, db = p["da"], p["db"]
    chroma = math.hypot(da, db)
    if val < 90 or p["L"] < 95:
        colour = "dark grey to black, no real colour visible"
    elif chroma < 1.5:
        colour = "neutral grey/silver, the same colour cast as the board"
    elif chroma < 4.5:
        tint = "yellowish/warm" if db > abs(da) else "bluish/cool (whiter)" if db < -abs(da) else "reddish" if da > 0 else "greenish"
        colour = f"grey/silver with a slight {tint} tint"
    elif db > 1.4 * abs(da):
        colour = "distinctly yellow-gold (much yellower than the board)"
    elif da > 0.5 * abs(db) and db > 0:
        colour = "distinctly reddish-orange (much redder than the board)"
    elif da > 0:
        colour = "distinctly reddish/pink"
    else:
        colour = "distinctly bluish/cool grey"
    # brightness vs board and shine
    dv = val - p["bg_val"]
    if val < 90:
        bright = "dark, almost black"
    elif dv > 12:
        bright = "brighter than the board around it"
    elif dv < -25:
        bright = "noticeably darker than the board"
    elif dv < -8:
        bright = "a little darker than the board"
    else:
        bright = "about as bright as the board"
    shine = "strong specular highlights (very shiny)" if p["val_std"] >= 26 else "some highlights (shiny)" if p["val_std"] >= 15 else "even, dull/matte finish"
    return {
        "shape": shape,
        "centre": centre,
        "size": size,
        "footprint_mm": f"{p['long_mm']:.0f} x {p['short_mm']:.0f}",
        "colour": colour,
        "brightness": bright,
        "finish": shine,
    }


# ---------------------------------------------------------------- questions
KIND_HINTS = {
    "screw": "elongated: a round pan head at one end and a thinner threaded shaft; clearly longer than wide; may show a dark cross slot",
    "bolt": "elongated: a hexagonal head at one end and a threaded shaft; clearly longer than wide",
    "nut": "compact and thick, hexagonal outline, about as long as wide, a dark threaded hole in the middle",
    "washer": "flat thin ring: round outline, the board shows through the hole in the middle",
    "standoff": "plain short rod or tube of constant width, no head",
    "other": "none of the above (wire clipping, chip, unknown object)",
}

MATERIAL_HINTS = {
    "zinc_steel": "zinc-plated carbon steel: dull bluish or greyish silver, sometimes a warm/yellowish tint, moderately shiny. FERROUS",
    "black_steel": "black-oxide carbon steel: dark grey to black, matte. FERROUS",
    "rusty_steel": "rusty carbon steel: brown/orange, rough, dull. FERROUS",
    "stainless": "stainless steel: bright neutral silver, cleaner and shinier than zinc, no colour cast. NOT lifted by the magnet",
    "aluminum": "aluminium: light matte silver, whiter than steel, no colour cast, dull. NOT magnetic",
    "brass": "brass: yellow-gold. NOT magnetic",
    "copper": "copper: reddish-orange. NOT magnetic",
    "unknown": "cannot tell from the description",
}


def _target_criteria() -> dict:
    return {name: t["label"] for name, t in sd.TARGETS.items()}


def questions_for(kinds: list[str], materials: list[str]) -> dict:
    return {
        "kind": {"type": "choice", "instructions": "What kind of hardware part is described in `part`? Use the shape, the centre and the size.", "criteria": {k: KIND_HINTS.get(k, k) for k in kinds}},
        "material": {"type": "choice", "instructions": "What is the part made of? Use colour, brightness and finish in `part` as priors; `magnet_feedback` is ground truth and overrides appearance when present.", "criteria": {m: MATERIAL_HINTS.get(m, m) for m in materials}},
        "ferrous": {"type": "noul", "instructions": "Will the electromagnet lift this part? True only for carbon steel (zinc-plated, black-oxide or rusty). If `magnet_feedback` says the part is NOT ferrous, answer false; a single inconclusive miss should only lower your answer a little.",
                    "criteria": {"true": "carbon steel: the magnet lifts it", "false": "brass, copper, aluminium or stainless: the magnet does not lift it; or the magnet tried repeatedly and the part always stayed"}},
        # whether to move the part at all is decided by code from P(ferrous) + feedback; Jev only says where it belongs
        "target": {"type": "choice", "instructions": "Assume the electromagnet CAN lift this part. According to `task`, which container should the arm drop it in? Decide from the kind of part (and its size/finish if the task grades by those).", "criteria": _target_criteria()},
    }


# ---------------------------------------------------------------- the planner
class JevPlanner:
    """Program-brain planner: photo in, JSON program out; Jev does the per-part judgement."""

    name = MODEL

    def __init__(self, cal, model: str = MODEL, max_picks: int = 3, max_attempts: int = 2, min_ferrous: float = 0.5, try_ferrous: float = 0.3, min_target: float = 0.4, workers: int = 8):
        self.cal = cal
        self.client = JevClient(model=model)
        self.name = f"typesafe-{self.client.model}"
        self.max_picks = max_picks
        self.max_attempts = max_attempts
        self.min_ferrous = min_ferrous  # label a part ferrous
        self.try_ferrous = try_ferrous  # worth an attempt: appearance alone cannot separate zinc from stainless, the magnet can
        self.min_target = min_target
        self.workers = workers
        self.usage = self.client.usage  # shared list: run_demo sums it
        self.last_input_text = ""
        self.memory: list[dict] = []  # tracked parts: x, y (m), attempts, outcomes
        self._ingested_lines = 0
        self.questions = questions_for(sd.TASK["kinds"], sd.TASK["materials"])

    # -------------------------------------------------- feedback
    _PICK_RE = re.compile(r"pick at \((-?\d+\.\d), (-?\d+\.\d)\) cm(?::)? (succeeded|FAILED|cameras disagree|unclear)")

    def _ingest(self, history: list[str]) -> None:
        """Read the arm's verdicts (from controller.place) into per-part memory. History is cumulative: only new lines are read."""
        for line in history[self._ingested_lines :]:
            for mm_ in self._PICK_RE.finditer(line):
                x, y, verdict = float(mm_.group(1)) / 100, float(mm_.group(2)) / 100, mm_.group(3)
                rec = self._nearest(x, y, 0.015)
                if rec is None:
                    rec = {"x": x, "y": y, "attempts": 0, "outcomes": []}
                    self.memory.append(rec)
                rec["attempts"] += 1
                rec["outcomes"].append({"succeeded": "lifted", "FAILED": "stayed", "cameras disagree": "unclear", "unclear": "unclear"}[verdict])
        self._ingested_lines = len(history)

    def _nearest(self, x: float, y: float, radius: float) -> dict | None:
        best, bd = None, radius
        for rec in self.memory:
            dd = math.hypot(rec["x"] - x, rec["y"] - y)
            if dd < bd:
                best, bd = rec, dd
        return best

    @staticmethod
    def _feedback_text(rec: dict | None) -> str:
        if not rec or not rec["outcomes"]:
            return "not tried yet"
        stayed = sum(1 for o in rec["outcomes"] if o == "stayed")
        if stayed >= 2:
            return f"the magnet was lowered onto it and energised {stayed} times and the part STAYED on the table every time: it is NOT ferrous"
        if stayed == 1:
            return "the magnet was tried once and the part stayed on the table; a single miss is inconclusive (the magnet may have been off-centre), the part may still be steel"
        return f"{len(rec['outcomes'])} attempt(s): " + "; ".join({"lifted": "the magnet lifted it (it left its spot) but a part is at this spot again", "unclear": "the cameras could not tell whether it moved"}[o] for o in rec["outcomes"])

    # -------------------------------------------------- Jev
    def _judge(self, part: dict, rec: dict | None) -> dict:
        desc = describe(part)
        state = {
            "task": sd.TASK["goal"],
            "part": desc,
            "magnet_feedback": self._feedback_text(rec),
        }
        body = self.client.system_one(state, self.questions)
        a = body["answers"]
        kind = a["kind"]
        mat = a["material"]
        tgt = a["target"]
        return {
            "kind": kind["choice"], "kind_p": float(kind["probabilities"].get(kind["choice"], kind.get("confidence", 0))),
            "material": mat["choice"], "material_p": float(mat["probabilities"].get(mat["choice"], mat.get("confidence", 0))),
            "ferrous_p": float(a["ferrous"]["noul"]),
            "target": tgt["choice"], "target_p": float(tgt["probabilities"].get(tgt["choice"], tgt.get("confidence", 0))), "target_probs": tgt["probabilities"],
            "state": state,
        }

    # -------------------------------------------------- plan
    def plan(self, frame_bgr: np.ndarray, history: list[str], round_no: int) -> dict:
        self._ingest(history)
        parts = find_parts(frame_bgr, self.cal)
        recs = [self._nearest(p["x"], p["y"], 0.012) for p in parts]
        # parts that were "lifted" but are still visible here were not really lifted: count as stayed
        for rec in recs:
            if rec and rec["outcomes"] and rec["outcomes"][-1] == "lifted" and not rec.get("relabelled"):
                rec["outcomes"][-1] = "stayed"
                rec["relabelled"] = True
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            judged = list(ex.map(lambda pr: self._judge(*pr), zip(parts, recs)))

        pieces, candidates = [], []
        for i, (p, rec, j) in enumerate(zip(parts, recs, judged), 1):
            pid = f"p{i}"
            attempts = rec["attempts"] if rec else 0
            target = j["target"]
            if j["target_p"] < self.min_target and "unknown" in sd.TARGETS:
                target = "unknown"  # confidence-gated: the task's own fallback container
            ferrous = j["ferrous_p"] >= self.min_ferrous
            conf = min(j["kind_p"], j["material_p"], j["target_p"], j["ferrous_p"] if ferrous else 1 - j["ferrous_p"])
            fb = self._feedback_text(rec)
            reason = f"P(ferrous)={j['ferrous_p']:.2f} {j['material']} {j['material_p']:.2f} {j['kind']} {j['kind_p']:.2f} → {j['target']} {j['target_p']:.2f}; {p['long_mm']:.0f}x{p['short_mm']:.0f} mm; {fb[:60]}"
            worth_a_try = j["ferrous_p"] >= self.try_ferrous and attempts < self.max_attempts
            pieces.append({"id": pid, "u": p["u"], "v": p["v"], "kind": j["kind"], "material": j["material"], "ferrous": ferrous, "target": target if (ferrous or worth_a_try) else "leave", "confidence": round(conf, 2), "reason": reason})
            if worth_a_try:
                score = j["ferrous_p"] * (0.5 + 0.5 * j["target_p"]) * (0.6 if attempts else 1.0)
                candidates.append((score, pid, p, target))
        candidates.sort(key=lambda t: -t[0])
        program = []
        for _score, pid, p, target in candidates[: self.max_picks]:
            program += [{"op": "pick", "u": p["u"], "v": p["v"], "piece": pid}, {"op": "place", "target": target}]
            rec = self._nearest(p["x"], p["y"], 0.012)
            if rec is None:
                self.memory.append({"x": p["x"], "y": p["y"], "attempts": 0, "outcomes": []})
        if program:
            program.append({"op": "home"})
        n_fe = sum(1 for q in pieces if q["ferrous"])
        message = f"Jev: {len(pieces)} parts seen, {n_fe} judged ferrous, {len(candidates)} worth a try, trying {len(program) // 2}" + ("" if program else ("; nothing left worth trying" if pieces else "; no loose parts in reach"))
        self.last_input_text = self._input_text(round_no, frame_bgr.shape, parts, recs, judged)
        return {"pieces": pieces, "program": program, "done": not program, "message": message}

    def _input_text(self, round_no, shape, parts, recs, judged) -> str:
        lines = [f"Round {round_no}. Photo {shape[1]}x{shape[0]} px; {len(parts)} loose parts found by the detector, one Jev request each (4 questions: kind, material, ferrous?, target)."]
        for i, (p, rec, j) in enumerate(zip(parts, recs, judged), 1):
            d = j["state"]["part"]
            lines.append(f"p{i} @({p['x'] * 100:.1f},{p['y'] * 100:.1f}) cm: {d['footprint_mm']} mm, {d['shape']}; {d['centre']}; {d['colour']}, {d['brightness']}, {d['finish']}. feedback: {self._feedback_text(rec)}")
        return "\n".join(lines)

    def cost_usd(self) -> float:
        return self.client.cost_usd()
