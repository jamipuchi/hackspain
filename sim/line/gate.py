"""The swing door on D9 (owner: arduino agent). `Gate(link, cfg, dry_run=False)` implements contracts.GateDriver.

    flush()                 door in line with the wall:  S <flush_deg> <hold…>
    open()                  door 25° into the channel:   S <open_deg>  <hold…>
    pulse(delay_s, dwell_s) NON-blocking: open at now+delay_s, flush at now+delay_s+dwell_s, on a worker thread.
                            Overlapping pulses coalesce: the open time is the earliest requested, the flush time
                            the latest, so a second suspect while the door is open just extends the dwell.
    state                   'flush' | 'scheduled' | 'open'

`cfg.gate.channel` says which slot of `S b s e` is the door (base = D9); the other two slots carry
`cfg.gate.hold_deg`. In `dry_run` every command is logged with sent=False and nothing reaches the link.
Every command is logged with a monotonic timestamp (`gate.log`). `selftest()` never moves the real door: it runs
a pulse against a FakeArduino with the same config and checks the open/flush timing (±20 ms).
"""

from __future__ import annotations

import threading
from collections import deque

from line.arduino_link import FakeArduino, LinkError
from line.contracts import Check, now

_CHANNEL_INDEX = {"base": 0, "shoulder": 1, "elbow": 2}


class Gate:
    name = "gate"

    def __init__(self, link, cfg, dry_run: bool = False, clock=None):
        self.link = link
        self.cfg = cfg
        self.dry_run = dry_run
        self.clock = clock or now
        self.state = "flush"
        self.log: deque = deque(maxlen=500)
        self.n_pulses = 0
        self.n_coalesced = 0
        self.n_errors = 0
        self.last_error = ""
        self._open_at: float | None = None
        self._flush_at: float | None = None
        self._cv = threading.Condition()
        self._stop = False
        self._worker = threading.Thread(target=self._run, name="gate-timer", daemon=True)
        self._worker.start()

    # --- command building
    def _slot(self) -> int:
        ch = self.cfg.gate.channel
        if ch not in _CHANNEL_INDEX:
            raise ValueError(f"gate.channel must be one of {list(_CHANNEL_INDEX)}, got {ch!r}")
        return _CHANNEL_INDEX[ch]

    def command(self, door_deg: int) -> str:
        """`S b s e` with the door angle in the configured slot and hold_deg in the other two."""
        hold = list(self.cfg.gate.hold_deg)
        vals = []
        for i in range(3):
            vals.append(int(door_deg) if i == self._slot() else int(hold.pop(0)))
        return "S " + " ".join(str(max(0, min(180, v))) for v in vals)

    def _send(self, action: str, door_deg: int) -> None:
        line = self.command(door_deg)
        entry = {"t": self.clock(), "action": action, "line": line, "sent": not self.dry_run, "reply": ""}
        if not self.dry_run:
            try:
                entry["reply"] = self.link.cmd(line)
            except LinkError as e:
                self.n_errors += 1
                self.last_error = str(e)
                entry["reply"] = f"ERROR {e}"
        self.log.append(entry)

    # --- GateDriver
    def flush(self) -> None:
        with self._cv:
            self._open_at = self._flush_at = None
            self.state = "flush"
            self._cv.notify()
        self._send("flush", self.cfg.gate.flush_deg)

    def open(self) -> None:
        with self._cv:
            self._open_at = self._flush_at = None
            self.state = "open"
            self._cv.notify()
        self._send("open", self.cfg.gate.open_deg)

    def pulse(self, delay_s: float, dwell_s: float | None = None) -> None:
        if dwell_s is None:
            dwell_s = self.cfg.gate.default_dwell_s
        t = self.clock()
        open_at = t + max(0.0, delay_s)
        flush_at = open_at + max(0.0, dwell_s)
        with self._cv:
            self.n_pulses += 1
            if self.state == "scheduled" and self._open_at is not None and self._flush_at is not None:
                self.n_coalesced += 1
                self._open_at = min(self._open_at, open_at)
                self._flush_at = max(self._flush_at, flush_at)
            elif self.state == "open":  # already open (pulse in progress, or manual open()): just extend the dwell
                self.n_coalesced += 1
                self._flush_at = flush_at if self._flush_at is None else max(self._flush_at, flush_at)
            else:
                self._open_at, self._flush_at = open_at, flush_at
                self.state = "scheduled"
            self._cv.notify()

    # --- worker: one thread, one deadline at a time
    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._stop:
                    deadline = self._open_at if self.state == "scheduled" else self._flush_at if self.state == "open" else None
                    if deadline is None:
                        self._cv.wait()
                        continue
                    remaining = deadline - self.clock()
                    if remaining > 0:
                        self._cv.wait(min(remaining, 0.05))  # re-check: an injected clock may not wake us
                        continue
                    if self.state == "scheduled":
                        self.state, self._open_at = "open", None
                        action, deg = "open", self.cfg.gate.open_deg
                    else:
                        self.state, self._flush_at = "flush", None
                        action, deg = "flush", self.cfg.gate.flush_deg
                    break
                if self._stop:
                    return
            self._send(action, deg)  # outside the lock: serial I/O must not block pulse()

    # --- Module
    def status(self) -> dict:
        with self._cv:
            t = self.clock()
            return {"name": self.name, "state": self.state, "dry_run": self.dry_run, "channel": self.cfg.gate.channel,
                    "flush_deg": self.cfg.gate.flush_deg, "open_deg": self.cfg.gate.open_deg,
                    "open_in_s": None if self._open_at is None else round(self._open_at - t, 3),
                    "flush_in_s": None if self._flush_at is None else round(self._flush_at - t, 3),
                    "n_pulses": self.n_pulses, "n_coalesced": self.n_coalesced, "n_errors": self.n_errors,
                    "last_error": self.last_error, "log": list(self.log)[-10:]}

    def selftest(self) -> list[Check]:
        """Timing check on a FakeArduino with this gate's config; the real link only gets a `?`."""
        out: list[Check] = []
        try:
            out.append(Check("command strings", self.command(self.cfg.gate.open_deg) != self.command(self.cfg.gate.flush_deg),
                             f"open={self.command(self.cfg.gate.open_deg)!r} flush={self.command(self.cfg.gate.flush_deg)!r}"))
        except ValueError as e:
            return [Check("command strings", False, str(e))]
        fake = FakeArduino()
        g = Gate(fake, self.cfg, dry_run=False)
        delay, dwell = 0.10, 0.20
        t0 = now()
        g.pulse(delay, dwell)
        import time as _t

        _t.sleep(delay + dwell + 0.15)
        opens = [e for e in g.log if e["action"] == "open"]
        flushes = [e for e in g.log if e["action"] == "flush"]
        ok_seq = len(opens) == 1 and len(flushes) == 1 and opens[0]["t"] < flushes[0]["t"]
        out.append(Check("pulse → open then flush", ok_seq, f"{len(opens)} open, {len(flushes)} flush"))
        if ok_seq:
            e_open = (opens[0]["t"] - t0 - delay) * 1000
            e_flush = (flushes[0]["t"] - t0 - delay - dwell) * 1000
            out.append(Check("open within ±20 ms", abs(e_open) <= 20, f"{e_open:+.1f} ms", e_open))
            out.append(Check("flush within ±20 ms", abs(e_flush) <= 20, f"{e_flush:+.1f} ms", e_flush))
            out.append(Check("fake received both S commands", fake.sent[-2:] == [g.command(self.cfg.gate.open_deg), g.command(self.cfg.gate.flush_deg)],
                             " | ".join(fake.sent[-2:])))
        out.append(Check("state back to flush", g.state == "flush", g.state))
        g.close()
        st = self.link.query()
        out.append(Check("real link answers ? (no movement)", st.ok, st.error or st.raw))
        return out

    def close(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        self._worker.join(timeout=1.0)
