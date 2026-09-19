"""Experimental THEKER policy: image geometry, categorical models, bounded actions.

The policy accepts camera pixels and actuator feedback only. Simulator part identities
and positions remain in run_demo's independent scorer. This is opt-in, simulation only.
"""
from __future__ import annotations

import base64
import json
import math
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np

import scene_def as sd
from agent_brain import AgentRun
from camera import TableCalibration
from jev_brain import JevClient, USD_PER_INPUT_TOKEN

CHEAP = "z-ai/glm-5.3-flash"
STRONG = "google/gemini-3.8-flash"
KINDS = ("screw", "nut", "washer", "other")
MATERIALS = ("steel", "nonferrous", "unknown")
TARGETS = dict(screw="screws", nut="nuts", washer="washers", other="unknown")
# Ceiling uses each model's ENTIRE context at the permitted per-token rate,
# plus 2048 output tokens, rounded up. No SDK/network retries or extra tools.
# Catalog: https://openrouter.ai/api/v1/models, checked 2026-09-19.
RESERVE = {CHEAP: 0.13, STRONG: 0.81, "jev": 0.003}
PRICES = {CHEAP: (0.09, 0.30), STRONG: (0.75, 3.75)}


class BudgetStop(RuntimeError):
    pass


class Ledger:
    """Sequential sweep ledger. Persist reservation BEFORE a potentially billable call.

    An interrupted/unknown request retains its reservation and blocks more calls.
    Explicit HTTP rejections retain the full ceiling but permit other configurations.
    A ledger is intentionally single-process; the sweep never runs configurations in parallel.
    """
    def __init__(self, path, run_id, run_limit):
        self.path = Path(path)
        self.run_id = run_id
        if not math.isfinite(float(run_limit)) or float(run_limit) < 0:
            raise ValueError("run budget must be finite and nonnegative")
        self.run_limit = min(float(run_limit), 5.0)
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {"cap_usd": 5.0, "requests": []}
        if self.data.get("cap_usd") != 5.0:
            raise ValueError("sweep cap must be $5")

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.data, indent=2))
        temp.replace(self.path)

    def begin(self, model):
        rows = self.data["requests"]
        if any(r["status"] not in ("accounted", "rejected") for r in rows):
            raise BudgetStop("unresolved request accounting; paid sweep stopped")
        charged = lambda r: r["cost_usd"] if r["cost_usd"] is not None else r["reserved_usd"]
        cost = sum(charged(r) for r in rows)
        run_cost = sum(charged(r) for r in rows if r["run"] == self.run_id)
        reserve = RESERVE[model]
        if cost + reserve > 5.0 or run_cost + reserve > self.run_limit:
            raise BudgetStop("insufficient remaining budget for request reservation")
        row = {"run": self.run_id, "model": model, "status": "pending", "reserved_usd": reserve, "cost_usd": None}
        rows.append(row)
        self.save()
        return row

    def finish(self, row, cost, usage, latency, basis):
        if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
            raise BudgetStop("missing or invalid provider accounting")
        row.update(status="accounted", cost_usd=float(cost), usage=usage, latency_s=latency, cost_basis=basis)
        self.save()
        if cost > row["reserved_usd"]:
            row["status"] = "ceiling_exceeded"
            self.save()
            raise BudgetStop("provider cost exceeded reserved ceiling")


def detect(frame, cal):
    """Segment loose silhouettes on the blue card after ArUco rectification.

    0.5 mm/pixel; no simulator segmentation, object names, or poses. The configured
    workspace and container footprints are fixed installation geometry.
    """
    scale = 2000.0
    table_to_image = np.array([[0, -scale, 280], [-scale, 0, 400], [0, 0, 1.]])
    warped = cv2.warpPerspective(frame, table_to_image @ cal.H, (560, 440))
    hsv = cv2.cvtColor(warped, cv2.COLOR_BGR2HSV)
    blue = ((hsv[..., 0] >= 95) & (hsv[..., 0] <= 125) & (hsv[..., 1] > 80)).astype(np.uint8) * 255
    contours, _ = cv2.findContours(blue, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return [], False
    hull = cv2.convexHull(max(contours, key=cv2.contourArea))
    card = np.zeros(blue.shape, np.uint8)
    cv2.fillConvexPoly(card, hull, 255)
    yy, xx = np.indices(blue.shape)
    x, y = (400 - yy) / scale, (280 - xx) / scale
    radius, yaw = np.hypot(x, y), np.arctan2(y, x)
    ws = (radius >= sd.WORKSPACE["r"][0]) & (radius <= sd.WORKSPACE["r"][1]) & (yaw >= sd.WORKSPACE["yaw"][0]) & (yaw <= sd.WORKSPACE["yaw"][1])
    for t in sd.TARGETS.values():
        ws &= np.hypot(x - t["pos"][0], y - t["pos"][1]) > max(t["size"][:2]) + .002
    mask = ((blue == 0) & (card > 0)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    parts, blocked = [], False
    for contour in contours:
        area = cv2.contourArea(contour) / 4  # square mm
        if area < 3:
            continue
        # Test a whole silhouette's centroid, not a workspace-clipped fragment
        # of a large fixture (especially a lid wall in the oblique view).
        moment = cv2.moments(contour)
        u, v = moment["m10"] / moment["m00"], moment["m01"] / moment["m00"]
        if not ws[int(round(v)), int(round(u))]:
            continue
        (_, _), (a, b), _ = cv2.minAreaRect(contour)
        long_mm, short_mm = max(a, b) / 2, min(a, b) / 2
        if long_mm > 35 or short_mm > 20:
            blocked = True
            continue
        parts.append({"x": float((400 - v) / scale), "y": float((280 - u) / scale), "long_mm": long_mm, "short_mm": short_mm})
    return sorted(parts, key=lambda p: (p["x"], p["y"])), not blocked


def agree(a, b, tolerance=.008):
    """Only unambiguous mutual nearest matches may control the arm."""
    pairs = []
    for i, p in enumerate(a):
        near = [j for j, q in enumerate(b) if math.hypot(p["x"] - q["x"], p["y"] - q["y"]) <= tolerance]
        if len(near) != 1:
            continue
        j = near[0]
        reverse = [k for k, q in enumerate(a) if math.hypot(q["x"] - b[j]["x"], q["y"] - b[j]["y"]) <= tolerance]
        if reverse == [i]:
            pairs.append({**p, "id": f"p{i}", "other": b[j], "disagreement_mm": 1000 * math.hypot(p["x"] - b[j]["x"], p["y"] - b[j]["y"])})
    return pairs, len(pairs) == len(a) == len(b)


def validate_labels(body, ids):
    if not isinstance(body, dict) or set(body) != set(ids):
        raise ValueError("classification must cover precisely the supplied crop IDs")
    for label in body.values():
        if not isinstance(label, dict) or set(label) != {"kind", "material"} or label["kind"] not in KINDS or label["material"] not in MATERIALS:
            raise ValueError("invalid categorical classification")
    return body


class AdaptiveAgent:
    def __init__(self, robot, cameras, calibrations, run_dir, on_event=None, effort="low", max_steps=60, budget_usd=1.5, refill=None, *, routing="adaptive", ledger_path):
        if sd.BUILD != "theker_v1" or set(cameras) != {"A", "B"}:
            raise ValueError("calibrated experiment requires theker_v1 and cameras A,B")
        self.robot, self.cameras, self.cal = robot, cameras, calibrations
        self.run_dir = Path(run_dir)
        self.refill, self.routing = refill, routing
        self.model = {"cheap": CHEAP, "strong": STRONG, "adaptive": f"{CHEAP} -> {STRONG}"}[routing]
        self.decision_model = "jev-latest"
        self.max_steps = max_steps
        self.batches_done = 0
        self.run = AgentRun()
        self.ledger = Ledger(ledger_path, str(self.run_dir.resolve()), budget_usd)
        self.jev = JevClient(max_retries=0, timeout=25)
        self.attempts = []
        self.failed_actions = 0
        self.photo_n = 0
        self.on_event = on_event or (lambda *a: None)

    def trace(self, **event):
        with (self.run_dir / "adaptive_trace.jsonl").open("a") as f:
            f.write(json.dumps(event) + "\n")

    def observe(self):
        frames, detections, clear = {}, {}, True
        self.photo_n += 1
        for name in ("A", "B"):
            frames[name] = self.cameras[name]()
            cv2.imwrite(str(self.run_dir / f"raw_{self.photo_n:03}_{name}.png"), frames[name])
            cal = TableCalibration()
            if not cal.fit(frames[name]) or not math.isfinite(cal.reprojection_error_mm()) or cal.reprojection_error_mm() > 2.0:
                raise RuntimeError(f"camera {name} failed 2 mm calibration gate")
            self.cal[name] = cal
            detections[name], ok = detect(frames[name], cal)
            clear &= ok
        pairs, matched = agree(detections["A"], detections["B"])
        self.trace(kind="geometry", photo=self.photo_n, detections=detections, pairs=pairs, clear=clear, matched=matched)
        return frames, pairs, clear and matched

    def classify(self, frames, parts, model):
        ids, tiles = [], []
        for p in parts:
            for name in ("A", "B"):
                loc = p if name == "A" else p["other"]
                u, v = self.cal[name].table_to_pixel(loc["x"], loc["y"])
                img = frames[name]
                x, y = max(0, min(img.shape[1] - 160, u - 80)), max(0, min(img.shape[0] - 160, v - 80))
                tile = img[y:y + 160, x:x + 160].copy()
                key = f"{p['id']}_{name}"
                ids.append(key)
                cv2.putText(tile, key, (4, 18), cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 255, 255), 1)
                tiles.append(tile)
        if not ids:
            return {}
        if len(ids) > 24:
            raise RuntimeError("too many geometric candidates")
        canvas = np.zeros((len(parts) * 160, 320, 3), np.uint8)
        for i, tile in enumerate(tiles):
            canvas[(i // 2) * 160:(i // 2 + 1) * 160, (i % 2) * 160:(i % 2 + 1) * 160] = tile
        cv2.imwrite(str(self.run_dir / f"crops_{self.photo_n:03}_{model.split('/')[-1]}.png"), canvas)
        _, encoded = cv2.imencode('.jpg', canvas)
        schema = {"type": "object", "properties": {key: {"type": "object", "properties": {"kind": {"type": "string", "enum": list(KINDS)}, "material": {"type": "string", "enum": list(MATERIALS)}}, "required": ["kind", "material"], "additionalProperties": False} for key in ids}, "required": ids, "additionalProperties": False}
        payload = {"model": model, "messages": [{"role": "user", "content": [{"type": "text", "text": "Classify the hardware at the CENTER of each labelled crop independently. Do not return coordinates, confidence, actions or completion. Screw = long shaft with head; nut = thick hexagonal body with hole; washer = thin round ring. White/silver and black hardware may be steel, gold is brass/nonferrous. Use unknown if material is ambiguous; other for no identifiable part. IDs: " + ', '.join(ids)}, {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(encoded).decode()}}]}], "response_format": {"type": "json_schema", "json_schema": {"name": "labels", "strict": True, "schema": schema}}, "max_tokens": 2048, "reasoning": {"effort": "low"}, "provider": {"require_parameters": True, "max_price": {"prompt": PRICES[model][0], "completion": PRICES[model][1]}}}
        row = self.ledger.begin(model)
        t0 = time.monotonic()
        self.run.calls += 1
        self.run.openrouter_calls += 1
        try:
            req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(payload).encode(), headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"], "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=45) as response:
                body = json.load(response)
            usage = body.get("usage", {})
            self.ledger.finish(row, usage.get("cost"), usage, time.monotonic() - t0, "provider_reported")
        except urllib.error.HTTPError as exc:
            # A returned rejection is different from a lost response: keep its
            # entire reservation, never invent a zero charge or retry this run.
            raw = exc.read().decode(errors="replace")
            try:
                error = json.loads(raw).get("error", {})
                detail = {"message": error.get("message"), "provider": error.get("metadata", {}).get("provider_name")}
            except (ValueError, AttributeError):
                detail = {"message": "non-JSON HTTP rejection"}
            row.update(status="rejected", http_status=exc.code, latency_s=time.monotonic() - t0, error=detail)
            self.ledger.save()
            self.run.cost_complete = False
            raise
        except BaseException:
            self.run.cost_complete = False
            raise
        self.run.openrouter_cost_usd += usage["cost"]
        self.run.openrouter_usage_in += usage.get("prompt_tokens", 0)
        self.run.openrouter_usage_out += usage.get("completion_tokens", 0)
        content = body["choices"][0]["message"]["content"]
        try:
            labels = validate_labels(json.loads(content), ids)
        except (ValueError, TypeError):
            self.trace(kind="invalid_classification", model=model, expected_ids=ids, content=content,
                       finish_reason=body["choices"][0].get("finish_reason"), latency_s=row["latency_s"], usage=usage)
            raise
        self.trace(kind="vision", model=body.get("model", model), labels=labels, latency_s=row["latency_s"], usage=usage)
        return labels

    def choose(self, parts):
        if len(parts) == 1:
            return parts[0]
        candidates = {p["id"]: f"{p['kind']}; size {p['long_mm']:.1f} mm; camera disagreement {p['disagreement_mm']:.1f} mm" for p in parts}
        row = self.ledger.begin("jev")
        self.run.calls += 1
        self.run.jev_calls += 1
        t0 = time.monotonic()
        try:
            body = self.jev.system_one({"task": "Rank these geometrically validated candidates for the next pickup. Prefer clearly identified, isolated pieces."}, {"candidate": {"type": "choice", "instructions": "Choose one supplied candidate ID only.", "criteria": candidates}})
            usage = body.get("usage", {})
            tokens = usage.get("input_tokens")
            if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
                raise BudgetStop("Jev input usage missing")
            self.ledger.finish(row, tokens * USD_PER_INPUT_TOKEN, usage, time.monotonic() - t0, "token_rate_estimate")
        except BaseException:
            self.run.cost_complete = False
            raise
        self.run.jev_usage_in += tokens
        self.run.jev_usage_out += usage.get("output_tokens", 0)
        choice = body["answers"]["candidate"]["choice"]
        self.trace(kind="ranking", model=body.get("model"), selected=choice, candidates=candidates, latency_s=row["latency_s"], usage=usage)
        if choice not in candidates:
            raise ValueError("Jev selected an unoffered candidate")
        return next(p for p in parts if p["id"] == choice)

    def attempted(self, part):
        return any(math.hypot(part["x"] - x, part["y"] - y) <= .010 for x, y in self.attempts)

    def run_loop(self):
        empty_checks = 0
        while self.run.steps + 3 <= self.max_steps:
            frames, parts, geometry_ok = self.observe()
            # Each returned pair has passed its own geometry gate. Unmatched
            # silhouettes elsewhere block completion, not other valid pairs.
            usable = [p for p in parts if not self.attempted(p)]
            reobserved = len(usable) != len(parts)
            try:
                labels = self.classify(frames, usable, STRONG if self.routing == "strong" else CHEAP)
            except urllib.error.HTTPError as exc:
                if self.routing != "adaptive" or exc.code not in (429, 503):
                    raise
                self.trace(kind="escalation", reason=f"cheap_provider_http_{exc.code}", ids=[p["id"] for p in usable])
                labels = self.classify(frames, usable, STRONG)
            disagree = [p for p in usable if labels[f"{p['id']}_A"] != labels[f"{p['id']}_B"] or labels[f"{p['id']}_A"]["kind"] == "other" or labels[f"{p['id']}_A"]["material"] == "unknown"]
            if self.routing == "adaptive" and disagree:
                labels.update(self.classify(frames, disagree, STRONG))
                self.trace(kind="escalation", reason="categorical_camera_disagreement_or_unknown", ids=[p["id"] for p in disagree])
            actionable, unresolved = [], False
            for p in usable:
                a, b = labels[f"{p['id']}_A"], labels[f"{p['id']}_B"]
                if a != b or a["kind"] == "other":
                    unresolved = True
                    continue
                if a["material"] != "nonferrous":
                    actionable.append({**p, "kind": a["kind"]})
            if not actionable:
                if not geometry_ok or unresolved or reobserved:
                    self.run.summary = "stopped: unresolved geometry or categorical camera disagreement"
                    return self.run
                empty_checks += 1
                if empty_checks < 2:
                    continue
                # Eligibility means all OBSERVED candidates are addressed. It is
                # not a claim that detector recall or sorting accuracy is perfect.
                if self.refill and self.refill():
                    self.batches_done += 1
                    self.attempts = []
                    empty_checks = 0
                    continue
                self.run.finished = self.failed_actions == 0
                self.run.summary = (
                    "eligible: two calibrated views, no unresolved observed candidates; independent score required"
                    if self.run.finished else "stopped: bounded passes exhausted with failed actions; independent score required"
                )
                return self.run
            empty_checks = 0
            p = self.choose(actionable)
            self.attempts.append((p["x"], p["y"]))
            pick = self.robot.pick_at(p["x"] * 100, p["y"] * 100)
            self.run.steps += 1
            place = self.robot.place_in(TARGETS[p["kind"]]) if pick.ok else None
            self.run.steps += int(place is not None)
            self.failed_actions += int(not pick.ok or place is None or not place.ok)
            self.robot.home()
            self.run.steps += 1
            self.trace(kind="action", candidate=p, pick_ok=pick.ok, place_ok=place.ok if place else False, feedback=place.text if place else pick.text)
            # A failed motion can displace a part and invalidate position-based
            # identity. Stop this run rather than risk retrying it as a new part.
            if self.failed_actions:
                self.run.summary = "stopped: failed actions; no retry without renewed part identity"
                return self.run
        self.run.summary = "stopped: action limit"
        return self.run
