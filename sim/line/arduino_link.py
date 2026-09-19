"""ArduinoLink implementations for the magnet_arm firmware (owner: arduino agent).

    FakeArduino()                       the firmware's protocol in Python: S/M/C/H/?/R, trapezoidal ramp, belt memory
    HttpPanelLink(url)                  proxies to the running magnet_sorter/conveyor_button.py (POST /cmd, GET /status)
    DirectSerial(port_glob, baud, ...)  pyserial; holds the port, `C 0` on connect/close, reconnects on ENXIO

All three return the firmware reply STRIPPED of its trailing CRLF ("ok", "err", "P 150 125 75 M 0 B 0") and
implement `contracts.ArduinoLink`: name, cmd, query, status, selftest, close. `cmd` raises `LinkError` when the
board cannot be reached; `query`/`status` never raise (they return ok=False + error). Real links' `selftest` never
moves a servo: it only sends `?` and `C 0`.
"""

from __future__ import annotations

import glob
import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque

from line.contracts import ArduinoState, Check, now

HOME_POSE = (150, 125, 75)
MAX_DEG_PER_S = 200.0  # firmware cruise speed
ACCEL_DEG_S2 = 700.0  # firmware acceleration limit
TICK_S = 0.020  # firmware servo pulse period


class LinkError(RuntimeError):
    """The Uno did not answer (port gone, panel down, timeout)."""


def parse_status(raw: str, belt: int, port: str) -> ArduinoState:
    """`P <b> <s> <e> M <0|1> B <0|1>` → ArduinoState. Anything else → ok=False with the raw text kept."""
    r = raw.strip().split()
    try:
        if r[0] != "P" or r[4] != "M" or r[6] != "B":
            raise ValueError("unexpected token layout")
        return ArduinoState(pos=(int(r[1]), int(r[2]), int(r[3])), magnet=r[5] == "1", moving=r[7] == "1",
                            belt=belt, raw=raw.strip(), port=port)
    except (IndexError, ValueError) as e:
        return ArduinoState(pos=None, magnet=False, moving=False, belt=belt, raw=raw.strip(), port=port,
                            ok=False, error=f"cannot parse status reply {raw.strip()!r}: {e}")


class _Log:
    """Bounded list of (t, line, reply) for status() and the panel's log view."""

    def __init__(self, n: int = 200):
        self.items: deque = deque(maxlen=n)

    def add(self, line: str, reply: str, t: float | None = None):
        self.items.append({"t": now() if t is None else t, "line": line, "reply": reply})

    def tail(self, n: int = 10) -> list[dict]:
        return list(self.items)[-n:]


# ------------------------------------------------------------------ fake
class FakeArduino:
    """magnet_arm.ino in Python. Same protocol, same trapezoidal ramp (200 °/s, 700 °/s²) integrated in 20 ms
    ticks from the injected `clock` (default time.monotonic), so `?` reports `B 1` while a move is in flight.
    Remembers the last `C` speed (the firmware does not report it). Thread-safe."""

    name = "fake-arduino"

    def __init__(self, clock=None, port: str = "fake"):
        self.clock = clock or time.monotonic
        self.port = port
        self._lock = threading.RLock()
        self.current = [float(v) for v in HOME_POSE]
        self.target = list(HOME_POSE)
        self.vel = [0.0, 0.0, 0.0]
        self.magnet = False
        self.belt = 0
        self.belt_attached = False
        self.belt_angle: int | None = None  # angle the firmware writes on D6 (90 + sp*90/100, C integer division); None = detached/limp
        self.vmax = [MAX_DEG_PER_S] * 3  # per-channel ramp limits, changed by `R <ch> <vmax> <accel>`
        self.amax = [ACCEL_DEG_S2] * 3
        self._last_tick = self.clock()
        self.log = _Log()
        self.sent: list[str] = []  # every accepted line, for tests
        self.closed = False

    # --- firmware model
    def _advance(self):
        t = self.clock()
        while t - self._last_tick >= TICK_S:
            self._last_tick += TICK_S
            for i in range(3):
                d = self.target[i] - self.current[i]
                vdes = math.copysign(min(self.vmax[i], math.sqrt(2 * self.amax[i] * abs(d))), d) if abs(d) > 1e-6 else 0.0
                dv = max(-self.amax[i] * TICK_S, min(self.amax[i] * TICK_S, vdes - self.vel[i]))
                self.vel[i] += dv
                move = self.vel[i] * TICK_S
                if abs(move) >= abs(d):
                    move, self.vel[i] = d, 0.0
                self.current[i] += move

    def moving(self) -> bool:
        self._advance()
        return any(abs(self.target[i] - self.current[i]) > 0.01 or abs(self.vel[i]) > 1e-6 for i in range(3))

    def pos(self) -> tuple[int, int, int]:
        self._advance()
        return tuple(int(c + 0.5) for c in self.current)  # type: ignore[return-value]

    def _handle(self, line: str) -> str:
        c = line[0]
        if c == "S":
            parts = line[1:].split()
            try:
                vals = [int(p) for p in parts[:3]]
                if len(vals) != 3:
                    raise ValueError
            except ValueError:
                return "err"
            self._advance()
            self.target = [max(0, min(180, v)) for v in vals]
            return "ok"
        if c == "M":
            self.magnet = len(line) > 2 and line[2] == "1"
            return "ok"
        if c == "C":
            try:
                sp = int(line[1:].strip() or "0")
            except ValueError:
                sp = 0  # atoi() semantics: garbage → 0
            self.belt = max(-100, min(100, sp))
            self.belt_attached = self.belt != 0
            self.belt_angle = (90 + int(self.belt * 90 / 100)) if self.belt_attached else None  # trunc toward 0 like C
            return "ok"
        if c == "H":
            self._advance()
            self.target = list(HOME_POSE)
            return "ok"
        if c == "R":  # per-channel ramp override; 0 restores the default (mirrors the .ino)
            parts = line[1:].split()
            try:
                ch, v, a = (int(x) for x in parts[:3])
                if len(parts) < 3 or not 0 <= ch <= 2:
                    raise ValueError
            except ValueError:
                return "err"
            self._advance()
            self.vmax[ch] = MAX_DEG_PER_S if v <= 0 else float(max(1, min(2000, v)))
            self.amax[ch] = ACCEL_DEG_S2 if a <= 0 else float(max(1, min(50000, a)))
            return "ok"
        if c == "?":
            p = self.pos()
            return f"P {p[0]} {p[1]} {p[2]} M {1 if self.magnet else 0} B {1 if self.moving() else 0}"
        return "err"

    # --- ArduinoLink
    def cmd(self, line: str) -> str:
        line = line.strip()
        if self.closed:
            raise LinkError("fake arduino closed")
        with self._lock:
            reply = self._handle(line) if line else "err"
            self.sent.append(line)
            self.log.add(line, reply)
            return reply

    def query(self) -> ArduinoState:
        try:
            return parse_status(self.cmd("?"), self.belt, self.port)
        except LinkError as e:
            return ArduinoState(None, False, False, self.belt, "", self.port, ok=False, error=str(e))

    def status(self) -> dict:
        st = self.query()
        return {"name": self.name, "backend": "fake", "port": self.port, "ok": st.ok, "pos": st.pos, "target": list(self.target),
                "magnet": st.magnet, "moving": st.moving, "belt": self.belt, "belt_angle": self.belt_angle,
                "vmax": list(self.vmax), "amax": list(self.amax),
                "n_cmds": len(self.sent), "log": self.log.tail()}

    def selftest(self) -> list[Check]:
        t0 = now()
        st = self.query()
        return [Check("fake ? parses", st.ok and st.pos is not None, st.raw, (now() - t0) * 1000),
                Check("fake C 0", self.cmd("C 0") == "ok", "belt stopped")]

    def close(self) -> None:
        self.closed = True


# ------------------------------------------------------------------ http proxy to conveyor_button.py
class HttpPanelLink:
    """Talks to magnet_sorter/conveyor_button.py, which owns the serial port. Two programs never fight for it."""

    name = "http-panel"

    def __init__(self, url: str = "http://127.0.0.1:8765", timeout_s: float = 1.0):
        self.url = url.rstrip("/")
        self.timeout_s = timeout_s
        self.belt = 0
        self.port = ""
        self.log = _Log()
        self._lock = threading.Lock()
        self.n_cmds = 0
        self.n_errors = 0
        self.last_error = ""
        self.last_rtt_ms = 0.0
        self.selftest_belt_cmd: str | None = "C 0"  # Gate sets None when D6 drives the door (C 0 would drop it)

    def _http(self, method: str, path: str) -> dict:
        req = urllib.request.Request(self.url + path, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:  # 4xx/5xx still carry a JSON body from the panel
            try:
                return json.loads(e.read().decode())
            except Exception:
                raise LinkError(f"panel HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            raise LinkError(f"panel unreachable at {self.url}: {e}") from e

    def cmd(self, line: str) -> str:
        line = line.strip()
        with self._lock:
            t0 = now()
            try:
                j = self._http("POST", "/cmd?line=" + urllib.parse.quote(line, safe=""))
            except LinkError as e:
                self.n_errors += 1
                self.last_error = str(e)
                self.log.add(line, f"ERROR {e}")
                raise
            self.last_rtt_ms = (now() - t0) * 1000
            self.n_cmds += 1
            if not j.get("ok"):
                self.n_errors += 1
                self.last_error = str(j.get("error", "panel refused"))
                self.log.add(line, f"ERROR {self.last_error}")
                raise LinkError(self.last_error)
            reply = str(j.get("reply", "")).strip()
            if line and line[0] == "C" and reply == "ok":
                try:
                    self.belt = max(-100, min(100, int(line[1:].strip() or "0")))
                except ValueError:
                    self.belt = 0
            self.log.add(line, reply)
            return reply

    def query(self) -> ArduinoState:
        try:
            return parse_status(self.cmd("?"), self.belt, self.port)
        except LinkError as e:
            return ArduinoState(None, False, False, self.belt, "", self.port, ok=False, error=str(e))

    def status(self) -> dict:
        d = {"name": self.name, "backend": "http", "url": self.url, "port": self.port, "belt": self.belt, "n_cmds": self.n_cmds,
             "n_errors": self.n_errors, "last_error": self.last_error, "last_rtt_ms": round(self.last_rtt_ms, 1), "log": self.log.tail()}
        try:
            j = self._http("GET", "/status")  # the panel's own poll of `?`, includes the serial port name
            self.port = j.get("port", self.port)
            d.update(ok=bool(j.get("ok")), port=self.port, pos=j.get("pos"), magnet=j.get("magnet"), moving=j.get("moving"))
            if not j.get("ok"):
                d["last_error"] = str(j.get("error", ""))
        except LinkError as e:
            d.update(ok=False, last_error=str(e))
        return d

    def selftest(self) -> list[Check]:
        out = []
        t0 = now()
        st = self.query()
        ms = (now() - t0) * 1000
        out.append(Check("panel reachable + ? answers", st.ok, st.error or st.raw, ms))
        out.append(Check("? within 300 ms", st.ok and ms <= 300, f"{ms:.1f} ms", ms))
        out.append(Check("? reply parses", st.ok and st.pos is not None, st.raw if st.ok else st.error))
        out.append(self._belt_check())
        return out

    def _belt_check(self) -> Check:
        if self.selftest_belt_cmd is None:
            return Check("C 0 skipped", True, "D6 drives the door: C 0 would detach it")
        try:
            t0 = now()
            r = self.cmd(self.selftest_belt_cmd)
            return Check("C 0 → ok (belt stays stopped)", r == "ok", r, (now() - t0) * 1000)
        except LinkError as e:
            return Check("C 0 → ok (belt stays stopped)", False, str(e))

    def close(self) -> None:
        pass  # the panel owns the port; leaving it running is the point


# ------------------------------------------------------------------ direct pyserial
class DirectSerial:
    """Own the serial port. Opening it resets the Uno (DTR), so wait `boot_wait_s` for `magnet_arm ready`.
    Sends `C 0` on connect and close, reconnects once per command on OSError/ENXIO (USB re-plug), serialises
    access with a lock. `serial_factory(port, baud)` is injectable for tests."""

    name = "direct-serial"

    def __init__(self, port_glob: str = "/dev/cu.usbmodem*", baud: int = 115200, boot_wait_s: float = 2.5,
                 timeout_s: float = 0.5, serial_factory=None, sleep=time.sleep):
        self.port_glob, self.baud, self.boot_wait_s, self.timeout_s = port_glob, baud, boot_wait_s, timeout_s
        self._factory = serial_factory or self._pyserial_factory
        self._sleep = sleep
        self._lock = threading.RLock()
        self.ser = None
        self.port = ""
        self.belt = 0
        self.log = _Log()
        self.n_cmds = 0
        self.n_reconnects = 0
        self.last_error = ""
        self.connected = False
        self.selftest_belt_cmd: str | None = "C 0"  # Gate sets None when D6 drives the door (C 0 would drop it)
        self._open()
        self.cmd("C 0")

    def _pyserial_factory(self, port: str, baud: int):
        import serial  # lazy: tests may run without pyserial

        return serial.Serial(port, baud, timeout=self.timeout_s)

    def _find_port(self) -> str:
        ports = sorted(glob.glob(self.port_glob))
        if not ports:
            raise LinkError(f"no serial port matches {self.port_glob}")
        return ports[0]

    def _open(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None
        self.port = self._find_port()
        self.ser = self._factory(self.port, self.baud)
        self._sleep(self.boot_wait_s)  # DTR reset → bootloader → "magnet_arm ready"
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass
        self.connected = True
        self.log.add("<open>", self.port)

    def _io(self, line: str) -> str:
        self.ser.reset_input_buffer()
        self.ser.write((line + "\n").encode())
        return self.ser.readline().decode(errors="replace").strip()

    def cmd(self, line: str) -> str:
        line = line.strip()
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self.ser is None:
                        self._open()
                    reply = self._io(line)
                    if reply == "":
                        raise LinkError("no reply from Uno (timeout)")
                    self.n_cmds += 1
                    if line and line[0] == "C" and reply == "ok":
                        try:
                            self.belt = max(-100, min(100, int(line[1:].strip() or "0")))
                        except ValueError:
                            self.belt = 0
                    self.log.add(line, reply)
                    return reply
                except (OSError, LinkError) as e:  # SerialException is an OSError subclass; ENXIO = "Device not configured"
                    self.connected = False
                    self.last_error = str(e)
                    self.log.add(line, f"ERROR {e}")
                    if attempt == 2:
                        raise LinkError(f"serial {self.port}: {e}") from e
                    self.n_reconnects += 1
                    try:
                        self._open()
                    except (OSError, LinkError) as e2:
                        raise LinkError(f"reconnect failed: {e2}") from e2
        raise LinkError("unreachable")  # pragma: no cover

    def query(self) -> ArduinoState:
        try:
            return parse_status(self.cmd("?"), self.belt, self.port)
        except LinkError as e:
            return ArduinoState(None, False, False, self.belt, "", self.port, ok=False, error=str(e))

    def status(self) -> dict:
        return {"name": self.name, "backend": "serial", "port": self.port, "connected": self.connected, "belt": self.belt,
                "n_cmds": self.n_cmds, "n_reconnects": self.n_reconnects, "last_error": self.last_error, "log": self.log.tail()}

    def selftest(self) -> list[Check]:
        out = []
        t0 = now()
        st = self.query()
        ms = (now() - t0) * 1000
        out.append(Check("port open + ? answers", st.ok, st.error or f"{self.port}: {st.raw}", ms))
        out.append(Check("? within 300 ms", st.ok and ms <= 300, f"{ms:.1f} ms", ms))
        out.append(Check("? reply parses", st.ok and st.pos is not None, st.raw if st.ok else st.error))
        out.append(HttpPanelLink._belt_check(self))
        return out

    def close(self) -> None:
        with self._lock:
            if self.ser is not None:
                try:
                    self._io("C 0")
                except Exception:
                    pass
                try:
                    self.ser.close()
                except Exception:
                    pass
                self.ser = None
            self.connected = False


def make_link(cfg) -> FakeArduino | HttpPanelLink | DirectSerial:
    """Resolve `cfg.arduino.backend` ('http' | 'serial' | 'fake') to a link. Used by panel.build_modules()."""
    a = cfg.arduino
    if a.backend == "serial":
        return DirectSerial(a.port_glob, a.baud, a.boot_wait_s)
    if a.backend == "fake":
        return FakeArduino()
    return HttpPanelLink(a.http_url)
