"""FakeArduino protocol + ramp, HttpPanelLink against a local fake panel, DirectSerial with an injected serial."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from line.arduino_link import HOME_POSE, DirectSerial, FakeArduino, HttpPanelLink, LinkError, make_link, parse_status
from line.config import LineConfig
from line.contracts import ArduinoLink, ArduinoState


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


# ------------------------------------------------------------------ parse
def test_parse_status_ok_and_bad():
    st = parse_status("P 150 125 75 M 1 B 0\r\n", belt=-30, port="x")
    assert st == ArduinoState((150, 125, 75), True, False, -30, "P 150 125 75 M 1 B 0", "x")
    bad = parse_status("magnet_arm ready", 0, "x")
    assert not bad.ok and bad.pos is None and "parse" in bad.error


# ------------------------------------------------------------------ fake
def test_fake_is_an_arduinolink_and_boots_at_home():
    f = FakeArduino()
    assert isinstance(f, ArduinoLink)
    st = f.query()
    assert st.ok and st.pos == HOME_POSE and not st.moving and not st.magnet and st.belt == 0


def test_fake_protocol_replies():
    f = FakeArduino(clock=Clock())
    assert f.cmd("S 10 20 30") == "ok"
    assert f.cmd("S 1 2") == "err"
    assert f.cmd("S a b c") == "err"
    assert f.cmd("M 1") == "ok" and f.magnet is True
    assert f.cmd("M 0") == "ok" and f.magnet is False
    assert f.cmd("C 60") == "ok" and f.belt == 60 and f.belt_attached
    assert f.cmd("C -250") == "ok" and f.belt == -100
    assert f.cmd("C 0") == "ok" and f.belt == 0 and not f.belt_attached
    assert f.cmd("H") == "ok" and f.target == list(HOME_POSE)
    assert f.cmd("X 1") == "err"
    assert f.cmd("") == "err"
    assert f.cmd("S 999 -5 90") == "ok" and f.target == [180, 0, 90]


def test_fake_ramp_reports_moving_then_arrives():
    clk = Clock()
    f = FakeArduino(clock=clk)
    f.cmd("S 150 125 175")  # elbow +100°
    assert f.query().moving is True
    clk.advance(0.1)
    p = f.query().pos
    assert 75 < p[2] < 175, "should be mid-flight after 100 ms"
    # 100° at ≤200°/s with 700°/s² ramps needs ≈0.79 s; give it 1.5 s
    clk.advance(1.5)
    st = f.query()
    assert st.pos == (150, 125, 175) and st.moving is False


def test_fake_ramp_never_exceeds_cruise_speed():
    clk = Clock()
    f = FakeArduino(clock=clk)
    f.cmd("S 0 125 75")  # base -150°
    last = f.pos()[0]
    for _ in range(20):
        clk.advance(0.10)  # 5 firmware ticks exactly
        p = f.pos()[0]
        assert abs(p - last) <= 200 * 0.10 + 1.0  # cruise 200°/s (+1° rounding)
        last = p
    assert f.pos()[0] == 0


def test_fake_status_and_selftest():
    f = FakeArduino()
    s = f.status()
    assert s["pos"] == HOME_POSE and s["belt"] == 0 and isinstance(s["log"], list)
    assert all(c.ok for c in f.selftest())
    f.close()
    with pytest.raises(LinkError):
        f.cmd("?")


# ------------------------------------------------------------------ http (fake panel speaking conveyor_button.py's API)
@pytest.fixture
def fake_panel():
    """A stand-in for conveyor_button.py: POST /cmd?line=…, GET /status, same JSON shapes."""
    fake = FakeArduino(port="/dev/cu.fake")
    slow = {"delay": 0.0}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _json(self, obj, code=200):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            st = fake.query()
            self._json({"ok": True, "port": fake.port, "pos": list(st.pos), "magnet": st.magnet, "moving": st.moving})

        def do_POST(self):
            time.sleep(slow["delay"])
            u = urlparse(self.path)
            line = parse_qs(u.query).get("line", [""])[0]
            if not line or line[0] not in "SMCH?":
                return self._json({"ok": False, "error": "command must start with S, M, C, H or ?"}, 400)
            self._json({"ok": True, "reply": fake.cmd(line) + "\r\n"})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", fake, slow
    srv.shutdown()


def test_http_link_proxies_and_tracks_belt(fake_panel):
    url, fake, _ = fake_panel
    link = HttpPanelLink(url, timeout_s=1.0)
    assert isinstance(link, ArduinoLink)
    assert link.cmd("C 40") == "ok" and link.belt == 40 and fake.belt == 40
    assert link.cmd("S 100 90 75") == "ok" and fake.target == [100, 90, 75]
    st = link.query()
    assert st.ok and st.moving is True and st.belt == 40
    s = link.status()
    assert s["ok"] and s["port"] == "/dev/cu.fake" and s["n_cmds"] == 3 and s["last_rtt_ms"] >= 0
    with pytest.raises(LinkError):
        link.cmd("Z 1")  # panel refuses → LinkError with its message
    assert "must start with" in link.last_error


def test_http_link_selftest_passes_and_stops_belt(fake_panel):
    url, fake, _ = fake_panel
    fake.cmd("C 50")
    link = HttpPanelLink(url)
    checks = link.selftest()
    assert all(c.ok for c in checks), [c for c in checks if not c.ok]
    assert fake.belt == 0
    assert fake.target == list(HOME_POSE), "selftest must never move a servo"


def test_http_link_unreachable_never_raises_in_query_or_status():
    link = HttpPanelLink("http://127.0.0.1:1", timeout_s=0.3)  # nothing listens on port 1
    st = link.query()
    assert not st.ok and "unreachable" in st.error
    s = link.status()
    assert s["ok"] is False and s["n_errors"] == 1
    with pytest.raises(LinkError):
        link.cmd("?")
    assert all(not c.ok for c in link.selftest())


def test_http_link_timeout(fake_panel):
    url, _, slow = fake_panel
    slow["delay"] = 0.5
    link = HttpPanelLink(url, timeout_s=0.1)
    with pytest.raises(LinkError):
        link.cmd("?")


# ------------------------------------------------------------------ direct serial with an injected port object
class FakePort:
    """Behaves like serial.Serial over a FakeArduino. `die_after` writes raise ENXIO once (USB re-plug)."""

    instances = []

    def __init__(self, port, baud, arduino: FakeArduino, die_after=None):
        self.port, self.baud, self.arduino = port, baud, arduino
        self.buf = b"magnet_arm ready\r\n"
        self.writes = 0
        self.die_after = die_after
        self.closed = False
        FakePort.instances.append(self)

    def reset_input_buffer(self):
        self.buf = b""

    def write(self, data: bytes):
        self.writes += 1
        if self.die_after is not None and self.writes > self.die_after:
            self.die_after = None
            raise OSError(6, "Device not configured")
        for line in data.decode().split("\n"):
            if line:
                self.buf += (self.arduino.cmd(line) + "\r\n").encode()

    def readline(self):
        i = self.buf.find(b"\n")
        if i < 0:
            out, self.buf = self.buf, b""
        else:
            out, self.buf = self.buf[: i + 1], self.buf[i + 1 :]
        return out

    def close(self):
        self.closed = True


@pytest.fixture
def serial_env(tmp_path):
    FakePort.instances.clear()
    dev = tmp_path / "cu.usbmodem4242"
    dev.write_text("")
    arduino = FakeArduino(port=str(dev))
    state = {"die_after": None}
    slept = []

    def factory(port, baud):
        return FakePort(port, baud, arduino, die_after=state["die_after"])

    def make(**kw):
        return DirectSerial(str(tmp_path / "cu.usbmodem*"), 115200, boot_wait_s=2.5, serial_factory=factory, sleep=slept.append, **kw)

    return make, arduino, state, slept


def test_direct_serial_connects_waits_for_boot_and_stops_belt(serial_env):
    make, arduino, _, slept = serial_env
    arduino.cmd("C 70")
    link = make()
    assert isinstance(link, ArduinoLink)
    assert slept == [2.5], "must wait boot_wait_s after opening (DTR reset)"
    assert arduino.belt == 0 and arduino.sent[-1] == "C 0"
    assert link.port.endswith("cu.usbmodem4242") and link.connected
    assert link.cmd("S 120 90 75") == "ok"
    st = link.query()
    assert st.ok and st.moving and st.port == link.port
    assert all(c.ok for c in link.selftest())
    link.close()
    assert FakePort.instances[-1].closed and arduino.sent[-1] == "C 0"


def test_direct_serial_reconnects_on_enxio(serial_env):
    make, arduino, state, slept = serial_env
    state["die_after"] = 2  # the 3rd write on the first port object raises "Device not configured"
    link = make()  # write 1: C 0
    assert link.cmd("?").startswith("P ")  # write 2
    state["die_after"] = None
    r = link.cmd("C 30")  # write 3 fails → reopen → retried on the new port
    assert r == "ok" and arduino.belt == 30
    assert link.n_reconnects == 1 and len(FakePort.instances) == 2 and FakePort.instances[0].closed
    assert slept == [2.5, 2.5]
    assert link.status()["n_reconnects"] == 1


def test_direct_serial_no_port_raises(tmp_path):
    with pytest.raises(LinkError):
        DirectSerial(str(tmp_path / "nothing*"), serial_factory=lambda p, b: None, sleep=lambda s: None)


def test_make_link_resolves_backend():
    cfg = LineConfig()
    cfg.arduino.backend = "fake"
    assert isinstance(make_link(cfg), FakeArduino)
    cfg.arduino.backend = "http"
    assert isinstance(make_link(cfg), HttpPanelLink)


# ------------------------------------------------------------------ R: per-channel ramp override (door on D9)
def _swing_time(f: FakeArduino, clk: Clock, frm: str, to: str) -> float:
    f.cmd(frm)
    clk.advance(3.0)
    assert f.query().moving is False
    f.cmd(to)
    t0 = clk.t
    while f.query().moving:
        clk.advance(0.02)
    return clk.t - t0


def test_ramp_override_makes_the_door_fast_and_leaves_other_channels_slow():
    clk = Clock()
    f = FakeArduino(clock=clk)
    slow = _swing_time(f, clk, "S 90 90 75", "S 65 90 75")
    assert 0.30 <= slow <= 0.40, slow  # default ramp: triangular profile, 2·√(25/700) ≈ 0.38 s
    assert f.cmd("R 0 400 8000") == "ok"
    fast = _swing_time(f, clk, "S 90 90 75", "S 65 90 75")
    assert 0.08 <= fast <= 0.14, fast  # ≈0.11 s
    elbow = _swing_time(f, clk, "S 90 90 75", "S 90 90 100")
    assert 0.30 <= elbow <= 0.40, elbow  # untouched channel keeps the gentle ramp
    assert f.cmd("R 0 0 0") == "ok" and f.vmax[0] == 200.0 and f.amax[0] == 700.0
    assert f.status()["vmax"] == [200.0, 200.0, 200.0]


def test_ramp_override_rejects_bad_input_and_clamps():
    f = FakeArduino(clock=Clock())
    assert f.cmd("R 3 400 8000") == "err"
    assert f.cmd("R 0 400") == "err"
    assert f.cmd("R x 1 1") == "err"
    assert f.cmd("R 1 99999 99999999") == "ok" and f.vmax[1] == 2000.0 and f.amax[1] == 50000.0
