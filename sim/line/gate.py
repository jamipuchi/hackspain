"""The swing door on D9 (owner: arduino agent). `Gate(link, cfg, dry_run=False)` implements contracts.GateDriver.

    flush()                 door in line with the wall:  S <flush_deg> <hold…>
    open()                  door 25° into the channel:   S <open_deg>  <hold…>
    pulse(delay_s, dwell_s) NON-blocking: open at now+delay_s, flush at now+delay_s+dwell_s, on a worker thread.
                            Overlapping pulses coalesce: the open time is the earliest requested, the flush time
                            the latest, so a second suspect while the door is open just extends the dwell.
    state                   'flush' | 'scheduled' | 'open'

`cfg.gate.channel` says which slot of `S b s e` is the door (base = D9); the other two slots carry
`cfg.gate.hold_deg`. `channel = "belt"` drives a positional servo wired to **D6** instead: `command(deg)` becomes
`C <sp>` with `sp = round((deg - 90) / 0.9)` clamped to -100..100 (firmware: `belt.write(90 + sp*90/100)`), unramped, and
sp == 0 is mapped to 1 because `C 0` detaches the servo and the door would go limp. The ramp override does not apply. With `cfg.gate.ramp_override` the gate sends `R <ch> <door_vmax_deg_s> <door_accel_deg_s2>` once at
start (and via `apply_ramp()`), so the door swings in ≈0.11 s while the arm channels keep the gentle default ramp. In `dry_run` every command is logged with sent=False and nothing reaches the link.
Every command is logged with a monotonic timestamp (`gate.log`). `selftest()` never moves the real door: it runs
a pulse against a FakeArduino with the same config and checks the open/flush timing (±20 ms).
"""

from __future__ import annotations

import json
import os
import threading
from collections import deque

from line.arduino_link import FakeArduino, LinkError
from line.contracts import Check, now

_CHANNEL_INDEX = {"base": 0, "shoulder": 1, "elbow": 2}
BELT = "belt"  # positional door servo on D6 through the conveyor output (C <sp> = angle)
BELT_SPIN = "belt_spin"  # continuous-rotation door servo on D6: MCU-timed `T <speed> <ms>` pulses into mechanical end stops
D6 = "d6"  # POSITIONAL door servo on D6 via `D <deg>`: attached, ramped like S, held between moves (firmware >= 19 Sep 17:20)


def belt_speed_for_angle(deg: float) -> int:
    """deg 0..180 → `C` speed whose firmware angle (90 + sp*90/100, C integer division) is closest to deg; never 0."""
    sp = int(round((float(deg) - 90.0) / 0.9))
    sp = max(-100, min(100, sp))
    return 1 if sp == 0 else sp


def belt_angle_for_speed(sp: int) -> int:
    return 90 + int(sp * 90 / 100)  # what the firmware actually writes (truncation toward zero)


class Gate:
    name = "gate"

    def __init__(self, link, cfg, dry_run: bool = False, clock=None):
        self.link = link
        self.cfg = cfg
        self.dry_run = dry_run
        self.clock = clock or now
        self._cfg_mtime = None
        self._disk_channel = None
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
        if (self.on_belt or self.on_spin or self.on_d6) and hasattr(self.link, "selftest_belt_cmd"):
            self.link.selftest_belt_cmd = None  # the link's selftest must not send C 0 any more: it would drop the door
        if self.on_spin or self.on_d6:
            self.apply_servo_config()
        if getattr(self.cfg.gate, "ramp_override", False):
            self.apply_ramp()

    def servo_config_commands(self) -> list[str]:
        """`N <neutral_us>` (speed-mode neutral) and, for the positional D6 door, `L 3 <min> <max>` travel limits."""
        g = self.cfg.gate
        out = [f"N {max(1300, min(1700, int(getattr(g, 'neutral_us', 1500))))}"]
        if self.on_d6:
            lo, hi = (int(v) for v in getattr(g, "door_limits_deg", (0, 180)))
            out.append(f"L 3 {max(0, min(179, lo))} {max(1, min(180, hi))}")
        return out

    def apply_servo_config(self) -> None:
        """Send the documented servo configuration once; old firmware answers `err` and we log it, nothing else changes."""
        for line in self.servo_config_commands():
            entry = {"t": self.clock(), "action": "config", "line": line, "sent": not self.dry_run, "reply": ""}
            if not self.dry_run:
                try:
                    entry["reply"] = self.link.cmd(line)
                    if entry["reply"] != "ok":
                        self.last_error = f"{line} refused ({entry['reply']}): firmware without N/L? running with defaults"
                except LinkError as e:
                    self.n_errors += 1
                    self.last_error = str(e)
                    entry["reply"] = f"ERROR {e}"
            self.log.append(entry)

    def ramp_command(self) -> str:
        g = self.cfg.gate
        slot = 3 if self.on_d6 else self._slot()  # firmware channel 3 = the D door
        return f"R {slot} {int(g.door_vmax_deg_s)} {int(g.door_accel_deg_s2)}"

    def apply_ramp(self) -> None:
        """Send the door channel's ramp override (`R`). Logged as action 'ramp'; respects dry_run. No-op on channel belt."""
        if self.on_belt or self.on_spin:
            self.log.append({"t": self.clock(), "action": "ramp", "line": "", "sent": False,
                             "reply": "n/a: D6 channels are unramped (servo's own speed)"})
            return
        line = self.ramp_command()
        entry = {"t": self.clock(), "action": "ramp", "line": line, "sent": not self.dry_run, "reply": ""}
        if not self.dry_run:
            try:
                entry["reply"] = self.link.cmd(line)
                if entry["reply"] != "ok":  # older firmware answers `err`: keep going with the slow ramp
                    self.n_errors += 1
                    self.last_error = f"ramp override refused ({entry['reply']}); firmware without R? door stays on the slow ramp"
            except LinkError as e:
                self.n_errors += 1
                self.last_error = str(e)
                entry["reply"] = f"ERROR {e}"
        self.log.append(entry)

    # --- command building
    def channel(self) -> str:
        """`gate.channel`, preferring config.json on disk over the cfg object we were handed (the panel's in-memory
        copy went stale three times on 19 Sep and drove the door on D9). Re-read only when the file's mtime changes."""
        try:
            from line.config import CONFIG_PATH

            m = os.stat(CONFIG_PATH).st_mtime
            if m != self._cfg_mtime:
                ch = json.loads(CONFIG_PATH.read_text()).get("gate", {}).get("channel")
                self._cfg_mtime, self._disk_channel = m, ch
            if self._disk_channel:
                if self._disk_channel != self.cfg.gate.channel:
                    self.cfg.gate.channel = self._disk_channel  # heal the copy we were given
                return self._disk_channel
        except (OSError, ValueError, AttributeError):
            pass
        return self.cfg.gate.channel

    @property
    def on_belt(self) -> bool:
        return self.channel() == BELT

    @property
    def on_spin(self) -> bool:
        return self.channel() == BELT_SPIN

    @property
    def on_d6(self) -> bool:
        return self.channel() == D6

    def spin_command(self, action: str) -> str:
        """`T <speed> <ms>` for 'open' / 'flush' on channel belt_spin (never speed 0: that would be an abort)."""
        g = self.cfg.gate
        speed = max(1, min(100, abs(int(g.spin_speed)))) * (1 if int(g.spin_open_dir) >= 0 else -1)
        if action == "open":
            return f"T {speed} {max(1, min(2000, int(g.spin_open_ms)))}"
        return f"T {-speed} {max(1, min(2000, int(g.spin_close_ms)))}"

    def _slot(self) -> int:
        ch = self.channel()
        if ch not in _CHANNEL_INDEX:
            raise ValueError(f"gate.channel must be one of {list(_CHANNEL_INDEX) + [BELT, BELT_SPIN, D6]}, got {ch!r}")
        return _CHANNEL_INDEX[ch]

    def command(self, door_deg: int) -> str:
        """`S b s e` with the door angle in the configured slot and hold_deg in the other two; on channel belt, `C <sp>`."""
        if self.on_d6:
            return f"D {max(0, min(180, int(door_deg)))}"
        if self.on_belt:
            return f"C {belt_speed_for_angle(door_deg)}"
        if self.on_spin:  # angles are meaningless for a continuous servo: open if above level, else close
            return self.spin_command("open" if door_deg != self.cfg.gate.flush_deg else "flush")
        hold = list(self.cfg.gate.hold_deg)
        vals = []
        for i in range(3):
            vals.append(int(door_deg) if i == self._slot() else int(hold.pop(0)))
        return "S " + " ".join(str(max(0, min(180, v))) for v in vals)

    def _send(self, action: str, door_deg: int) -> None:
        line = self.spin_command("open" if action == "open" else "flush") if self.on_spin else self.command(door_deg)
        entry = {"t": self.clock(), "action": action, "line": line, "sent": not self.dry_run, "reply": "", "deg": int(door_deg)}
        if self.on_belt:
            sp = int(line.split()[1])
            entry.update(sp=sp, deg_actual=belt_angle_for_speed(sp))
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
                    "ramp_override": bool(getattr(self.cfg.gate, "ramp_override", False)),
                    "settle_ms": self.cfg.gate.settle_ms,
                    "open_in_s": None if self._open_at is None else round(self._open_at - t, 3),
                    "flush_in_s": None if self._flush_at is None else round(self._flush_at - t, 3),
                    "n_pulses": self.n_pulses, "n_coalesced": self.n_coalesced, "n_errors": self.n_errors,
                    "last_error": self.last_error, "log": list(self.log)[-10:]}

    def selftest(self) -> list[Check]:
        """Timing check on a FakeArduino with this gate's config; the real link only gets a `?`."""
        out: list[Check] = []
        try:
            c_open, c_flush = self.command(self.cfg.gate.open_deg), self.command(self.cfg.gate.flush_deg)
            out.append(Check("command strings", c_open != c_flush, f"open={c_open!r} flush={c_flush!r}"))
            if self.on_belt:
                out.append(Check("belt channel never sends C 0", all(l.split()[1] != "0" for l in (c_open, c_flush)),
                                 f"{c_open} / {c_flush} (C 0 would detach the door servo)"))
            if self.on_spin:
                out.append(Check("belt_spin sends opposite T pulses", c_open.startswith("T ") and c_flush.startswith("T ")
                                 and int(c_open.split()[1]) == -int(c_flush.split()[1]), f"{c_open} / {c_flush}"))
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
            out.append(Check("fake received both door commands", fake.sent[-2:] == [g.command(self.cfg.gate.open_deg), g.command(self.cfg.gate.flush_deg)],
                             " | ".join(fake.sent[-2:])))
            if self.on_belt:
                sp = int(g.command(self.cfg.gate.flush_deg).split()[1])
                out.append(Check("fake D6 still attached after flush", fake.belt_attached and fake.belt_us == fake.neutral_us + sp * 5,
                                 f"D6 pulse {fake.belt_us} us for flush (C {sp}); NOTE: on firmware >= 17:20 C is speed, use channel d6"))
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
