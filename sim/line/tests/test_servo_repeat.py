"""The repeatability tool runs end to end against FakeArduino and logs commands, pulse widths and approach directions."""
from line.arduino_link import servo_write_us
from line.tools import servo_repeat


def test_positional_plan_logs_pulse_and_approach(tmp_path):
    out = servo_repeat.main(["positional", "--fake", "--fast", "--quiet", "--channel", "base", "--a", "65", "--b", "90",
                             "--cycles", "2", "--csv", str(tmp_path / "log.csv")])
    moves = [r for r in out if r.get("line", "").startswith("S ")]
    assert len(moves) == 1 + 2 * 2 + 3 * 2
    b_arrivals = [r for r in moves if r["requested_deg"] == 90]
    assert {r["approach"] for r in b_arrivals if r["phase"].startswith("same-side #")} == {"+"}
    assert {r["approach"] for r in b_arrivals if r["phase"].startswith("other-side #")} == {"-"}
    assert all(r["pulse_us"] == servo_write_us(r["requested_deg"]) for r in moves)
    assert all(r["reply"] == "ok" for r in moves)
    assert (tmp_path / "log.csv").read_text().count("\n") == len(out) + 1


def test_positional_d6_uses_D_and_ramp_time_matches_profile():
    out = servo_repeat.main(["positional", "--fake", "--fast", "--quiet", "--channel", "d6", "--a", "65", "--b", "90", "--cycles", "1"])
    moves = [r for r in out if r.get("line", "").startswith("D ")]
    assert moves and all(r["line"] == f"D {r['requested_deg']}" for r in moves)
    assert abs(servo_repeat.ramp_time_s(25) - 0.378) < 0.005  # triangular: 2·sqrt(25/700)
    assert abs(servo_repeat.ramp_time_s(25, 400, 8000) - 0.1125) < 0.005  # trapezoid with the override


def test_continuous_and_neutral_modes_run_on_the_fake():
    out = servo_repeat.main(["continuous", "--fake", "--fast", "--quiet", "--speed", "12", "--ms", "300", "--cycles", "3"])
    pulses = [r for r in out if r.get("line", "").startswith("T ")]
    assert [r["speed"] for r in pulses] == [12, -12] * 3 and all(r["pulse_us"] in (1560, 1440) for r in pulses)
    out = servo_repeat.main(["calibrate-neutral", "--fake", "--fast", "--quiet", "--lo", "1480", "--hi", "1520", "--step", "20"])
    assert [r["line"] for r in out if r.get("line", "").startswith("N ")] == ["N 1480", "N 1500", "N 1520"]


def test_positional_refuses_a_continuous_channel():
    import pytest

    with pytest.raises(SystemExit):
        servo_repeat.main(["positional", "--fake", "--fast", "--quiet", "--channel", "belt_spin"])
