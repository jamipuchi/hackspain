"""Gate: command strings per channel, non-blocking pulse timing, coalescing, dry_run, selftest."""
import time

import pytest

from line.arduino_link import FakeArduino, LinkError
from line.config import LineConfig
from line.contracts import GateDriver
from line.gate import Gate


@pytest.fixture
def env():
    cfg = LineConfig()  # base=D9, flush 90, open 65, hold (90, 75)
    fake = FakeArduino()
    gates = []

    def make(**kw):
        g = Gate(fake, cfg, **kw)
        gates.append(g)
        return g

    yield cfg, fake, make
    for g in gates:
        g.close()


def wait_state(g, state, timeout=2.0):
    t0 = time.monotonic()
    while g.state != state and time.monotonic() - t0 < timeout:
        time.sleep(0.005)
    return g.state == state


def test_command_strings_per_channel(env):
    cfg, fake, make = env
    g = make()
    assert isinstance(g, GateDriver)
    assert g.command(65) == "S 65 90 75" and g.command(90) == "S 90 90 75"
    cfg.gate.channel = "shoulder"
    assert g.command(65) == "S 90 65 75"
    cfg.gate.channel = "elbow"
    assert g.command(65) == "S 90 75 65"
    cfg.gate.channel = "wrist"
    with pytest.raises(ValueError):
        g.command(65)
    assert not g.selftest()[0].ok


def test_flush_and_open_send_immediately(env):
    cfg, fake, make = env
    g = make()
    g.open()
    assert fake.sent[-1] == "S 65 90 75" and g.state == "open"
    g.flush()
    assert fake.sent[-1] == "S 90 90 75" and g.state == "flush"
    assert [e["action"] for e in g.log] == ["open", "flush"] and all(e["sent"] for e in g.log)


def test_pulse_is_non_blocking_and_on_time(env):
    cfg, fake, make = env
    g = make()
    t0 = time.monotonic()
    g.pulse(0.10, 0.20)
    assert time.monotonic() - t0 < 0.02, "pulse() must return immediately"
    assert g.state == "scheduled" and fake.sent == []
    assert wait_state(g, "open") and fake.sent[-1] == "S 65 90 75"
    assert wait_state(g, "flush") and fake.sent[-1] == "S 90 90 75"
    opens = [e for e in g.log if e["action"] == "open"]
    flushes = [e for e in g.log if e["action"] == "flush"]
    assert len(opens) == 1 and len(flushes) == 1
    assert abs((opens[0]["t"] - t0) - 0.10) <= 0.020
    assert abs((flushes[0]["t"] - t0) - 0.30) <= 0.020
    assert g.n_pulses == 1 and g.n_coalesced == 0


def test_overlapping_pulses_coalesce_into_one_open_and_extended_flush(env):
    cfg, fake, make = env
    g = make()
    t0 = time.monotonic()
    g.pulse(0.10, 0.10)  # open 0.10, flush 0.20
    time.sleep(0.03)
    g.pulse(0.15, 0.30)  # would open 0.18, flush 0.48 → open stays 0.10, flush extends to 0.48
    time.sleep(0.15)  # door is now open
    g.pulse(0.00, 0.10)  # while open: flush at 0.28 < 0.48 → no change
    assert wait_state(g, "flush", timeout=1.5)
    actions = [e["action"] for e in g.log]
    assert actions == ["open", "flush"], actions
    t_open, t_flush = g.log[0]["t"] - t0, g.log[1]["t"] - t0
    assert abs(t_open - 0.10) <= 0.02
    assert abs(t_flush - 0.48) <= 0.03
    assert g.n_pulses == 3 and g.n_coalesced == 2


def test_pulse_while_manually_open_only_schedules_the_flush(env):
    cfg, fake, make = env
    g = make()
    g.open()
    g.pulse(0.0, 0.10)
    assert g.state == "open"
    assert wait_state(g, "flush")
    assert [e["action"] for e in g.log] == ["open", "flush"]


def test_earlier_second_pulse_moves_open_forward(env):
    cfg, fake, make = env
    g = make()
    t0 = time.monotonic()
    g.pulse(0.30, 0.10)
    g.pulse(0.05, 0.10)  # earlier: open at 0.05, flush stays at 0.40
    assert wait_state(g, "flush", timeout=1.0)
    assert abs((g.log[0]["t"] - t0) - 0.05) <= 0.02
    assert abs((g.log[1]["t"] - t0) - 0.40) <= 0.02


def test_flush_cancels_a_scheduled_pulse(env):
    cfg, fake, make = env
    g = make()
    g.pulse(0.2, 0.2)
    g.flush()
    time.sleep(0.5)
    assert [e["action"] for e in g.log] == ["flush"] and fake.sent == ["S 90 90 75"]


def test_dry_run_records_but_sends_nothing(env):
    cfg, fake, make = env
    g = make(dry_run=True)
    g.open()
    g.pulse(0.02, 0.05)
    assert wait_state(g, "flush")
    assert fake.sent == []
    assert [e["action"] for e in g.log] == ["open", "flush"]  # pulse while open only schedules the flush
    assert all(e["sent"] is False for e in g.log)
    assert g.status()["dry_run"] is True


def test_default_dwell_and_status(env):
    cfg, fake, make = env
    cfg.gate.default_dwell_s = 0.05
    g = make()
    g.pulse(0.05)
    s = g.status()
    assert s["state"] == "scheduled" and 0 < s["open_in_s"] <= 0.05 and 0 < s["flush_in_s"] <= 0.10
    assert wait_state(g, "flush")
    s = g.status()
    assert s["open_in_s"] is None and s["flush_in_s"] is None and s["n_pulses"] == 1 and len(s["log"]) == 2


def test_link_error_is_logged_not_raised(env):
    cfg, fake, make = env
    g = make()
    fake.close()  # every cmd now raises LinkError
    g.open()
    assert g.n_errors == 1 and g.log[-1]["reply"].startswith("ERROR") and "closed" in g.last_error


def test_selftest_uses_a_fake_and_never_moves_the_real_link(env):
    cfg, fake, make = env
    g = make()
    checks = g.selftest()
    assert all(c.ok for c in checks), [(c.name, c.detail) for c in checks if not c.ok]
    assert all(not s.startswith("S") for s in fake.sent), "selftest must not send S to the real link"
    assert fake.sent == ["?"]


def test_ramp_override_is_sent_once_at_start_when_enabled(env):
    cfg, fake, make = env
    g = make()
    assert not any(s.startswith("R") for s in fake.sent), "off by default"
    cfg.gate.ramp_override = True
    g2 = make()
    assert fake.sent[-1] == "R 0 400 8000" and g2.log[-1]["action"] == "ramp" and g2.log[-1]["reply"] == "ok"
    assert fake.vmax[0] == 400.0 and fake.amax[0] == 8000.0
    assert g2.status()["ramp_override"] is True
    cfg.gate.channel = "elbow"
    assert g2.ramp_command() == "R 2 400 8000"


def test_ramp_override_dry_run_and_old_firmware(env):
    cfg, fake, make = env
    cfg.gate.ramp_override = True
    g = make(dry_run=True)
    assert fake.sent == [] and g.log[-1]["action"] == "ramp" and g.log[-1]["sent"] is False
    fake._handle_orig = fake._handle
    fake._handle = lambda line: "err" if line.startswith("R") else fake._handle_orig(line)  # firmware without R
    g2 = make()
    assert g2.n_errors == 1 and "refused" in g2.last_error and g2.state == "flush"


# ------------------------------------------------------------------ channel "belt": door servo wired to D6, driven with C
def test_belt_channel_maps_angles_to_C_and_never_sends_C0(env):
    from line.gate import belt_angle_for_speed, belt_speed_for_angle

    cfg, fake, make = env
    cfg.gate.channel = "belt"
    g = make()
    assert g.command(65) == "C -28" and g.command(90) == "C 1" and g.command(45) == "C -50" and g.command(135) == "C 50"
    assert g.command(0) == "C -100" and g.command(180) == "C 100" and g.command(-40) == "C -100"
    for deg in range(0, 181):
        sp = belt_speed_for_angle(deg)
        assert sp != 0 and -100 <= sp <= 100
        assert abs(belt_angle_for_speed(sp) - deg) <= 1 or deg in (90,), (deg, sp)
    g.open()
    assert fake.sent[-1] == "C -28" and fake.belt_angle == 65 and fake.belt_attached
    g.flush()
    assert fake.sent[-1] == "C 1" and fake.belt_angle == 90 and fake.belt_attached
    e = g.log[-1]
    assert e["deg"] == 90 and e["sp"] == 1 and e["deg_actual"] == 90 and e["line"] == "C 1"
    assert not any(s == "C 0" for s in fake.sent)


def test_belt_channel_pulse_and_selftest(env):
    cfg, fake, make = env
    cfg.gate.channel = "belt"
    cfg.gate.ramp_override = True  # must be a harmless no-op on belt
    g = make()
    assert g.log[-1]["action"] == "ramp" and g.log[-1]["sent"] is False and "n/a" in g.log[-1]["reply"]
    assert not any(s.startswith("R") for s in fake.sent)
    g.pulse(0.02, 0.05)
    assert wait_state(g, "flush")
    assert [s for s in fake.sent if s.startswith("C")] == ["C -28", "C 1"]
    checks = g.selftest()
    assert all(c.ok for c in checks), [(c.name, c.detail) for c in checks if not c.ok]
    assert any("never sends C 0" in c.name for c in checks) and any("D6 angle" in c.name for c in checks)
    assert fake.sent[-1] == "?"  # the real link still only gets ? from selftest


def test_belt_channel_disarms_the_links_C0_selftest(env):
    cfg, fake, make = env
    fake.selftest_belt_cmd = "C 0"
    cfg.gate.channel = "belt"
    make()
    assert fake.selftest_belt_cmd is None
