"""Control panel for the sorting line: every module's health, its self-tests, manual control of motors, cameras and
classifier, and the closed loop with a dry-run switch. Owner: integrator.

    cd ~/robotics && .venv/bin/python -m line.panel [--all-fake] [--port 8800] [--no-browser]

Stdlib HTTP only (no new dependencies). The page is `static/index.html`, served from disk so it can be edited live.
Implementations are resolved from config in `build_modules()`; anything missing or broken falls back to the stub in
`_stubs.py` and the panel says so, so the panel always starts.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
import traceback
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from line import _stubs, selftest
from line.config import LineConfig, load, save
from line.contracts import now
from line.pipeline import SortingLine

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
RAW_ALLOWED = set("SMCH?") | {"STOP"}
LOG: deque[str] = deque(maxlen=400)


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    LOG.append(line)
    print(line, flush=True)


def _try(info: dict, key: str, label: str, factory, fallback):
    """Build a module from another agent's code; on any failure use the stub and remember why."""
    try:
        m = factory()
        info[key] = label
        return m
    except Exception as exc:  # noqa: BLE001
        info[key] = f"stub ({label} failed: {type(exc).__name__}: {str(exc)[:120]})"
        log(f"{key}: {label} unavailable → stub. {type(exc).__name__}: {exc}")
        return fallback()


class DoorOnD6:
    """Bench workaround (19 Sep): the SG90 only responds on D6, the firmware's conveyor pin. This wraps any ArduinoLink and
    turns the door's `S <deg> <hold> <hold>` into `C <speed>` with deg = 90 + 0.9*speed (what the firmware writes to D6).
    Speed 0 would DETACH the servo (door goes limp), so 0 maps to 1 (≈ 90.9°). `?` replies get the commanded door angle
    patched into the base slot so the panel dial and `Gate` see the truth. Conveyor commands are refused while active."""

    name = "door-on-d6"
    IDX = {"base": 1, "shoulder": 2, "elbow": 3}

    def __init__(self, inner, cfg: LineConfig):
        self.inner, self.cfg = inner, cfg
        self.angle = int(cfg.gate.flush_deg)
        self.n_translated = 0
        self.pos = None  # 'OPEN' | 'CLOSED' | None (unknown until the first pulse)
        self._t_support: bool | None = None

    @staticmethod
    def deg_to_speed(deg: float) -> int:
        sp = int(round((float(deg) - 90.0) / 0.9))
        sp = max(-100, min(100, sp))
        return 1 if sp == 0 else sp

    def _pulse(self, direction: int, ms: int, what: str) -> str:
        """Continuous servo: spin `direction` at door_d6_speed for `ms`, then C 0 (detach = stop). Blocking for `ms`; always stops."""
        sp = max(5, min(100, int(self.cfg.door_d6_speed))) * (1 if direction > 0 else -1) * (1 if int(self.cfg.door_d6_dir) >= 0 else -1)
        sp = max(-100, min(100, sp + int(getattr(self.cfg, "door_d6_trim", 0))))
        if sp == 0:
            sp = 1 if direction * int(self.cfg.door_d6_dir) > 0 else -1
        self.n_translated += 1
        ms = max(0, int(ms))
        t0 = time.monotonic()
        if self._has_T():
            # firmware times the pulse on the MCU (T <speed> <ms>): no host/USB jitter, detaches itself at the deadline
            reply = self.inner.cmd(f"T {sp} {ms}")
            time.sleep(ms / 1000.0 + 0.02)  # keep the operator/loop semantics (call returns when the move is over)
            log(f"door→D6 {what}: T {sp} {ms} (MCU-timed) → {reply.strip()}")
            return reply
        try:
            reply = self.inner.cmd(f"C {sp}")
            time.sleep(ms / 1000.0)
        finally:
            stop = self.inner.cmd("C 0")
        log(f"door→D6 {what}: C {sp} for {ms} ms host-timed, stop → {stop.strip()} ({(time.monotonic() - t0) * 1000:.0f} ms)")
        return reply

    def _has_T(self) -> bool:
        """Probe once per connection whether the flashed firmware knows `T` (a `T 0 0` is a harmless abort)."""
        if self._t_support is None:
            try:
                self._t_support = self.inner.cmd("T 0 0").strip().startswith("ok")
            except Exception:  # noqa: BLE001
                self._t_support = False
            log(f"door→D6: firmware {'HAS' if self._t_support else 'lacks'} the MCU-timed pulse command T")
        return self._t_support

    def reprobe(self) -> bool:
        self._t_support = None
        return self._has_T()

    def go(self, pos: str, force: bool = False) -> str:
        """Operator action: move the door to 'OPEN' or 'CLOSED' with the configured pulse (ignores dry_run: a human pressed it)."""
        pos = "CLOSED" if pos.upper().startswith("CLOSE") else "OPEN"
        if self.pos == pos and not force:
            return "already " + pos
        self.pos = pos
        if self.positional():
            return self.hold(self.cfg.gate.flush_deg if pos == "CLOSED" else self.cfg.gate.open_deg, pos)
        self.angle = self.cfg.gate.flush_deg if pos == "CLOSED" else self.cfg.gate.open_deg
        return self._pulse(-1 if pos == "CLOSED" else +1, self.cfg.door_d6_close_ms if pos == "CLOSED" else self.cfg.door_d6_open_ms, pos)

    def positional(self) -> bool:
        return getattr(self.cfg, "door_d6_mode", "continuous") == "positional"

    def hold(self, deg: int, what: str) -> str:
        """Positional servo on D6: command the angle through the C mapping (deg = 90 + 0.9*sp) and KEEP it attached."""
        deg = max(0, min(180, int(deg)))
        self.angle = deg
        self.n_translated += 1
        reply = self.inner.cmd(f"C {self.deg_to_speed(deg)}")
        log(f"door→D6 {what}: hold {deg}° as C {self.deg_to_speed(deg)} → {reply.strip()}")
        return reply

    def nudge(self, towards: str, ms: int) -> str:
        """Operator calibration. Continuous: spin towards OPEN/CLOSED for `ms`. Positional: step the held angle by `ms` degrees."""
        closed = towards.upper().startswith("CLOSE")
        self.pos = None
        if self.positional():
            step = max(1, min(30, int(ms)))
            return self.hold(self.angle + (-step if closed else step), f"nudge→{'CLOSED' if closed else 'OPEN'}")
        return self._pulse(-1 if closed else +1, max(10, min(600, int(ms))), f"nudge→{'CLOSED' if closed else 'OPEN'}")

    def cmd(self, line: str) -> str:
        p = line.strip().split()
        if p and p[0] == "S" and len(p) == 4:
            deg = max(0, min(180, int(float(p[self.IDX.get(self.cfg.gate.channel, 1)]))))
            self.n_translated += 1
            gate_open = abs(deg - self.cfg.gate.open_deg) < abs(deg - self.cfg.gate.flush_deg)  # what the gate driver asked for
            if self.positional():
                go_closed = gate_open if getattr(self.cfg, "action_position", "closed") == "closed" else not gate_open
                self.pos = "CLOSED" if go_closed else "OPEN"
                return self.hold(self.cfg.gate.flush_deg if go_closed else self.cfg.gate.open_deg, self.pos)
            if getattr(self.cfg, "door_d6_mode", "continuous") == "continuous":
                # the gate driver "opens" to act on a bean; the operator chooses which physical position that is
                go_closed = gate_open if getattr(self.cfg, "action_position", "closed") == "closed" else not gate_open
                if self.pos == ("CLOSED" if go_closed else "OPEN"):
                    return "ok"  # already there: do not grind into the stop again
                self.pos = "CLOSED" if go_closed else "OPEN"
                self.angle = self.cfg.gate.flush_deg if go_closed else self.cfg.gate.open_deg
                return self._pulse(-1 if go_closed else +1, self.cfg.door_d6_close_ms if go_closed else self.cfg.door_d6_open_ms, self.pos)
            self.angle = deg
            reply = self.inner.cmd(f"C {self.deg_to_speed(deg)}")
            log(f"door→D6: {deg}° as C {self.deg_to_speed(deg)} → {reply.strip()}")
            return reply
        if p and p[0] == "STOP":
            return self.inner.cmd("C 0")
        if p and p[0] == "C":
            return "err: conveyor channel is driving the door (door_on_d6)"
        if p and p[0] == "H":
            return self.cmd(f"S {self.cfg.gate.flush_deg} {self.cfg.gate.hold_deg[0]} {self.cfg.gate.hold_deg[1]}")
        reply = self.inner.cmd(line)
        if p and p[0] == "?" and reply.startswith("P "):
            q = reply.split()
            try:
                q[self.IDX.get(self.cfg.gate.channel, 1)] = str(self.angle)
                reply = " ".join(q)
            except IndexError:
                pass
        return reply

    def query(self):
        st = self.inner.query()
        try:
            pos = list(st.pos) if st.pos else None
            if pos:
                pos[self.IDX.get(self.cfg.gate.channel, 1) - 1] = self.angle
                st.pos = tuple(pos)
            st.raw = self.cmd("?")
        except Exception:  # noqa: BLE001
            pass
        return st

    def status(self) -> dict:
        d = dict(self.inner.status())
        d.update({"name": f"{d.get('name', '?')} +door-on-D6", "door_pos": self.pos or "unknown", "mode": getattr(self.cfg, "door_d6_mode", "continuous"), "pulse_timing": "MCU (T)" if self._t_support else ("host (C/sleep/C 0)" if self._t_support is False else "unknown"), "door_deg": self.angle, "door_speed_cmd": self.deg_to_speed(self.angle), "translated": self.n_translated})
        if isinstance(d.get("pos"), list) and len(d["pos"]) == 3:
            d["pos"][self.IDX.get(self.cfg.gate.channel, 1) - 1] = self.angle
        return d

    def selftest(self):
        from line.contracts import Check
        return list(self.inner.selftest()) + [Check("door_on_d6.mapping", self.deg_to_speed(90) == 1 and self.deg_to_speed(65) == -28 and self.deg_to_speed(180) == 100, "90°→C 1, 65°→C -28, 180°→C 100")]

    def close(self) -> None:
        try:
            if not self.positional():
                self.inner.cmd("C 0")  # continuous servo: make sure it is not spinning when we leave (positional: keep holding)
        finally:
            self.inner.close()


def build_modules(cfg: LineConfig, all_fake: bool = False) -> tuple[dict, dict]:
    info: dict[str, str] = {}
    # arduino: the arduino agent's make_link(cfg) picks Fake / HttpPanelLink / DirectSerial from cfg.arduino.backend
    def mk_ard():
        from line.arduino_link import FakeArduino, make_link
        return FakeArduino() if all_fake else make_link(cfg)
    ard = _try(info, "arduino", "arduino_link.FakeArduino" if all_fake else f"arduino_link.make_link({cfg.arduino.backend})", mk_ard, _stubs.StubArduino)
    if getattr(cfg, "door_on_d6", False):
        ard = DoorOnD6(ard, cfg)
        info["arduino"] += " + DoorOnD6 (door servo driven through pin 6)"
    # gate
    def mk_gate():
        from line.gate import Gate
        return Gate(ard, cfg, dry_run=cfg.dry_run)
    gate = _try(info, "gate", "gate.Gate", mk_gate, lambda: _stubs.StubGate(ard, cfg, dry_run=cfg.dry_run))
    # camera
    backend = "synthetic" if all_fake else cfg.camera.backend
    def mk_cam():
        from line import camera_source as cs
        if backend == "real":
            return cs.RealCamera(cfg)
        if backend == "file":
            return cs.FileCamera(cfg.camera.file_paths, cfg.camera.fps)
        return cs.SyntheticCamera(cfg)
    cam = _try(info, "camera", f"camera_source.{ {'real': 'RealCamera', 'file': 'FileCamera'}.get(backend, 'SyntheticCamera') }", mk_cam, lambda: _stubs.StubCamera(cfg))
    # detector
    def mk_det():
        from line.bean_vision import PaperBeanDetector
        return PaperBeanDetector(cfg)
    det = _try(info, "detector", "bean_vision.PaperBeanDetector", mk_det, lambda: _stubs.StubDetector(cfg))
    # classifier
    def mk_clf():
        from line import classifier as cl
        if cfg.classifier.backend == "color":
            return _stubs.ColorClassifier(cfg)
        if cfg.classifier.backend == "sklearn":
            return cl.SklearnClassifier(str(ROOT / cfg.classifier.model_path))
        return cl.RuleClassifier(cfg)
    clf = _try(info, "classifier", f"classifier.{ {'sklearn': 'SklearnClassifier', 'color': 'ColorClassifier(dark fraction)'}.get(cfg.classifier.backend, 'RuleClassifier') }", mk_clf, lambda: _stubs.StubClassifier(cfg))
    return {"arduino": ard, "gate": gate, "camera": cam, "detector": det, "classifier": clf}, info


class Panel:
    def __init__(self, cfg: LineConfig, all_fake: bool = False):
        self.cfg = cfg
        self.modules, self.info = build_modules(cfg, all_fake)
        m = self.modules
        self.line = SortingLine(cfg, m["camera"], m["detector"], m["classifier"], m["gate"], m["arduino"], log=log)
        self.checks: list[dict] = []
        self.checks_t = 0.0
        self.belt = 0
        self.line.start()
        log("panel up: " + ", ".join(f"{k}={v}" for k, v in self.info.items()))

    # ------------------------------------------------------------ state
    def state(self) -> dict:
        mods = {}
        for k, m in self.modules.items():
            try:
                mods[k] = m.status()
            except Exception as exc:  # noqa: BLE001
                mods[k] = {"name": getattr(m, "name", "?"), "ok": False, "error": str(exc)}
        return {"t": time.time(), "cfg": json.loads(json.dumps(self.cfg, default=lambda o: o.__dict__)), "impl": self.info, "modules": mods, "line": self.line.status(),
                "events": [{"t": e.t, "kind": e.kind, **e.data} for e in list(self.line.events)[-80:]], "checks": self.checks, "checks_t": self.checks_t, "log": list(LOG)[-120:], "belt": self.belt}

    # ------------------------------------------------------------ actions
    def set_config(self, path: str, value):
        obj = self.cfg
        parts = path.split(".")
        for p in parts[:-1]:
            obj = getattr(obj, p)
        cur = getattr(obj, parts[-1])
        if isinstance(cur, bool):
            value = bool(value)
        elif isinstance(cur, int) and not isinstance(cur, bool):
            value = int(value)
        elif isinstance(cur, float):
            value = float(value)
        elif isinstance(cur, tuple):
            value = tuple(value)
        setattr(obj, parts[-1], value)
        save(self.cfg)
        log(f"config {path} = {value}")
        return {"path": path, "value": value}

    def cmd(self, line: str) -> str:
        line = line.strip()
        if not line or (line[0] not in RAW_ALLOWED and line.split()[0] not in RAW_ALLOWED) or "\n" in line:
            raise ValueError("command must start with S, M, C, H or ?")
        reply = self.modules["arduino"].cmd(line)
        log(f"> {line}  → {reply.strip()}")
        return reply

    def conveyor(self, speed: int) -> str:
        speed = max(-100, min(100, int(speed)))
        self.belt = speed
        return self.cmd(f"C {speed}")

    def gate(self, action: str, delay: float = 0.0, dwell: float | None = None) -> dict:
        g = self.modules["gate"]
        if action == "flush":
            g.flush()
        elif action == "open":
            g.open()
        elif action == "pulse":
            g.pulse(float(delay), float(dwell if dwell is not None else self.cfg.gate.default_dwell_s))
        else:
            raise ValueError("gate action must be flush, open or pulse")
        log(f"gate {action} (dry_run={getattr(g, 'dry_run', '?')})")
        return g.status()

    def line_action(self, action: str, confirm: str = "") -> dict:
        g = self.modules["gate"]
        if action == "start":
            self.line.enabled = True
        elif action == "stop":
            self.line.enabled = False
        elif action == "reset":
            self.line.reset()
        elif action == "dry":
            g.dry_run = True
            self.cfg.dry_run = True
            save(self.cfg)
        elif action == "live":
            if confirm != "LIVE":
                raise ValueError("switching the gate live needs confirm='LIVE'")
            g.dry_run = False
            self.cfg.dry_run = False
            save(self.cfg)
        else:
            raise ValueError("unknown line action")
        log(f"line {action}: enabled={self.line.enabled} dry_run={getattr(g, 'dry_run', '?')}")
        return self.line.status()

    def run_selftests(self, only: str | None = None) -> list[dict]:
        res = selftest.run(self.modules, only)
        if only:
            self.checks = [c for c in self.checks if c["module"] != only] + res
        else:
            self.checks = res
        self.checks_t = time.time()
        bad = [c for c in res if not c["ok"]]
        log(f"selftest{' ' + only if only else ''}: {len(res) - len(bad)}/{len(res)} ok" + (f"; failing: {', '.join(c['name'] for c in bad)}" if bad else ""))
        return res

    def close(self) -> None:
        self.line.stop()
        for k, m in self.modules.items():
            try:
                m.close()
            except Exception as exc:  # noqa: BLE001
                log(f"close {k}: {exc}")


def make_handler(panel: Panel):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, default=str).encode(), "application/json")

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            if not raw:
                return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {k: v[0] for k, v in parse_qs(raw.decode()).items()}

        def do_GET(self):
            p = urlparse(self.path).path
            try:
                if p == "/" or p == "/index.html":
                    return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
                if p.startswith("/static/") and ".." not in p:
                    f = STATIC / p[len("/static/"):]
                    ctype = "text/css" if f.suffix == ".css" else "application/javascript" if f.suffix == ".js" else "application/octet-stream"
                    return self._send(200, f.read_bytes(), ctype)
                if p == "/api/captures":
                    from line.pipeline import CAPTURES
                    items = []
                    for f in sorted(CAPTURES.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)[:60]:
                        try:
                            m = json.loads(f.read_text())
                            fe = m.get("features", {})
                            items.append({"stem": m["stem"], "t": m["t"], "label": m.get("label"), "verdict": m["verdict"]["label"], "suspect": m["verdict"]["suspect"], "dark_core": fe.get("core_dark_frac", fe.get("dark_frac")), "gray": fe.get("mean_gray"), "major_mm": fe.get("major_mm"), "partial": m.get("partial")})
                        except Exception:  # noqa: BLE001
                            continue
                    return self._json({"ok": True, "items": items})
                if p.startswith("/captures/") and p.endswith(".png") and ".." not in p:
                    from line.pipeline import CAPTURES
                    f = CAPTURES / p[len("/captures/"):]
                    return self._send(200, f.read_bytes(), "image/png") if f.exists() else self._json({"ok": False, "error": "no such capture"}, 404)
                if p == "/api/state":
                    return self._json(panel.state())
                if p == "/snapshot.jpg":
                    j = panel.line.latest_jpeg() or b""
                    return self._send(200 if j else 503, j, "image/jpeg")
                if p == "/stream.mjpg":
                    return self._stream()
                return self._json({"ok": False, "error": "not found"}, 404)
            except Exception as exc:  # noqa: BLE001
                log(f"GET {p} failed: {exc}\n{traceback.format_exc()[-400:]}")
                return self._json({"ok": False, "error": str(exc)}, 500)

        def _stream(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            last = None
            try:
                while True:
                    j = panel.line.latest_jpeg()
                    if j and j is not last:
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(j)).encode() + b"\r\n\r\n" + j + b"\r\n")
                        self.wfile.flush()
                        last = j
                    time.sleep(1 / 20)
            except (BrokenPipeError, ConnectionResetError):
                return

        def do_POST(self):
            p = urlparse(self.path).path
            try:
                b = self._body()
                if p == "/api/label":
                    from line.pipeline import CAPTURES, DATASETS
                    import shutil
                    stem, label = str(b.get("stem", "")), str(b.get("label", ""))
                    f = CAPTURES / f"{stem}.json"
                    if not f.exists() or ".." in stem:
                        return self._json({"ok": False, "error": "no such capture"}, 404)
                    m = json.loads(f.read_text())
                    m["label"] = label or None
                    f.write_text(json.dumps(m))
                    if label:
                        d = DATASETS / label
                        d.mkdir(parents=True, exist_ok=True)
                        shutil.copy(CAPTURES / f"{stem}.png", d / f"{stem}.png")
                        (d / f"{stem}.json").write_text(json.dumps(m))
                    log(f"capture {stem} labelled {label or '(cleared)'}")
                    return self._json({"ok": True, "stem": stem, "label": m["label"]})
                if p == "/api/delete_capture":
                    from line.pipeline import CAPTURES, DATASETS
                    stem = str(b.get("stem", ""))
                    if not stem or ".." in stem or "/" in stem:
                        return self._json({"ok": False, "error": "bad stem"}, 400)
                    if b.get("all"):
                        stems = [f.stem for f in CAPTURES.glob("*.json")]
                    else:
                        stems = [stem]
                    n = 0
                    for st in stems:
                        for f in list(CAPTURES.glob(f"{st}.*")) + list(DATASETS.glob(f"*/{st}.*")):
                            f.unlink(missing_ok=True)
                            n += 1
                    log(f"deleted capture(s): {stems if len(stems) < 4 else str(len(stems)) + ' items'}")
                    return self._json({"ok": True, "deleted_files": n})
                if p == "/api/threshold_from_labels":
                    from line.pipeline import CAPTURES
                    vals = {"white": [], "black": []}
                    for f in CAPTURES.glob("*.json"):
                        try:
                            m = json.loads(f.read_text())
                            if m.get("label") in vals:
                                fe = m["features"]
                                vals[m["label"]].append(float(fe.get("core_dark_frac", fe.get("dark_frac", 0))))
                        except Exception:  # noqa: BLE001
                            continue
                    if not vals["white"] or not vals["black"]:
                        return self._json({"ok": False, "error": "label at least one white and one black bean first", "counts": {k: len(v) for k, v in vals.items()}}, 400)
                    hi_white, lo_black = max(vals["white"]), min(vals["black"])
                    thr = round((hi_white + lo_black) / 2, 4)
                    panel.set_config("classifier.color_white_max_dark_frac", thr)
                    return self._json({"ok": True, "threshold": thr, "white_max": hi_white, "black_min": lo_black, "separable": lo_black > hi_white, "counts": {k: len(v) for k, v in vals.items()}})
                if p == "/api/cmd":
                    return self._json({"ok": True, "reply": panel.cmd(b.get("line", ""))})
                if p == "/api/conveyor":
                    return self._json({"ok": True, "reply": panel.conveyor(int(b.get("speed", 0)))})
                if p == "/api/door":
                    ard = panel.modules["arduino"]
                    act = str(b.get("action", "")).lower()
                    if act == "assume" and hasattr(ard, "pos"):
                        ard.pos = "CLOSED" if str(b.get("pos", "")).lower().startswith("close") else "OPEN"
                        log(f"door position assumed {ard.pos} (operator)")
                        return self._json({"ok": True, "door_pos": ard.pos})
                    if act == "nudge" and hasattr(ard, "nudge"):
                        return self._json({"ok": True, "reply": ard.nudge(str(b.get("towards", "open")), int(b.get("ms", 60))), "door_pos": ard.pos})
                    if act == "reprobe" and hasattr(ard, "reprobe"):
                        return self._json({"ok": True, "mcu_timed": ard.reprobe()})
                    if act == "stop":
                        return self._json({"ok": True, "reply": ard.cmd("STOP") if hasattr(ard, "go") else panel.cmd("C 0")})
                    if not hasattr(ard, "go"):
                        return self._json({"ok": False, "error": "door_on_d6 is off: use the gate buttons"}, 400)
                    log(f"door {act} (operator)")
                    return self._json({"ok": True, "reply": ard.go(act, force=bool(b.get("force", False))), "door_pos": ard.pos})
                if p == "/api/gate":
                    return self._json({"ok": True, "gate": panel.gate(b.get("action", ""), float(b.get("delay", 0) or 0), b.get("dwell"))})
                if p == "/api/line":
                    return self._json({"ok": True, "line": panel.line_action(b.get("action", ""), str(b.get("confirm", "")))})
                if p == "/api/config":
                    return self._json({"ok": True, **panel.set_config(b["path"], b["value"])})
                if p == "/api/selftest":
                    return self._json({"ok": True, "checks": panel.run_selftests(b.get("module") or None)})
                if p == "/api/classify_now":
                    return self._json({"ok": True, "results": panel.line.classify_now()})
                if p == "/api/sample":
                    return self._json({"ok": True, **panel.line.save_sample(str(b.get("label", "good")))})
                return self._json({"ok": False, "error": "not found"}, 404)
            except Exception as exc:  # noqa: BLE001
                log(f"POST {p} failed: {exc}")
                return self._json({"ok": False, "error": str(exc)}, 400)

    return H


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all-fake", action="store_true", help="fake Arduino + synthetic camera: no hardware needed")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    cfg = load()
    port = a.port or cfg.panel_port
    panel = Panel(cfg, all_fake=a.all_fake)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(panel))
    srv.daemon_threads = True
    url = f"http://127.0.0.1:{port}"
    log(f"line control panel: {url}")
    if not a.no_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        log("shutting down: flushing the gate, stopping the belt")
        try:
            panel.conveyor(0)
        except Exception:  # noqa: BLE001
            pass
        panel.close()


if __name__ == "__main__":
    main()
