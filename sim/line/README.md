# `line/` — the physical coffee-bean sorting line, one module per subsystem

The chute (cartón pluma, 15°, 40 cm) carries one bean at a time past the iPhone (zone 9–15 cm from the
top). The laptop detects and classifies it and, for a suspect, swings the 6 cm door at 30–36 cm (SG90 on
**D9**, magnet_arm firmware `S <door> 90 75`) so it leaves into the review cup. Good beans roll into the bowl.

```
 FrameSource ──▶ BeanDetector ──▶ BeanClassifier ──▶ pipeline.SortingLine ──▶ GateDriver ──▶ ArduinoLink ──▶ Uno
  (iPhone)        (bean_vision)     (classifier)        (state machine)         (gate)        (serial/http)
                                                              │
                                                     panel.py (control panel :8800)  ◀── selftest.py
```

## Rules that keep us from breaking each other

1. **Code against `contracts.py`, never against another agent's implementation.** Protocols + dataclasses
   there are frozen; propose changes in `~/robotics/INTEGRATOR.md`.
2. **One owner per file.** Create and edit only the files in your row below. Need something from another
   module? Ask in INTEGRATOR.md, or use the stub in `_stubs.py` until it lands.
3. **Config, not constants.** Anything tunable lives in your section of `config.py` / `config.json`.
4. **Tests run without hardware.** `line/run_tests.sh` must pass before you announce "done". Hardware-only
   checks go in your module's `selftest()` and are run from the panel.
5. **Nothing moves by surprise.** `config.dry_run` is true by default; the panel is the only place that
   switches it off. Every actuator method logs what it sent.
6. **Additive only** outside `line/`: do not change the behaviour of `magnet_sorter/` or `coffee_sorter/`
   code that others import.
7. **Report in INTEGRATOR.md** under `## <role> — HH:MM` (roles: arduino, camera, coffee-sim, build,
   integrator). Say: what landed (files), how you verified it, what you need.

## Ownership

| file(s) | owner | provides (see contracts.py docstrings) |
| --- | --- | --- |
| `contracts.py`, `config.py`, `_stubs.py`, `pipeline.py`, `selftest.py`, `panel.py`, `static/`, `run_tests.sh`, `README.md` | integrator | contracts, stubs, closed loop, control panel |
| `arduino_link.py`, `gate.py`, `tests/test_arduino_link.py`, `tests/test_gate.py` | **arduino** | `DirectSerial`, `HttpPanelLink`, `FakeArduino`, `Gate` |
| `camera_source.py`, `bean_vision.py`, `tests/test_camera_source.py`, `tests/test_bean_vision.py`, `tools/capture_samples.py` | **camera** | `RealCamera`, `FileCamera`, `SyntheticCamera`, `PaperBeanDetector`, `FEATURES` |
| `classifier.py`, `timing.py`, `tests/test_classifier.py`, `tests/test_timing.py`, `models/` | **coffee-sim** | `RuleClassifier`, `SklearnClassifier`, `train_from_dataset`, `evaluate`, bean kinematics |
| `BUILD_ASBUILT.md` | **build** | as-built dimensions, servo angles, measured bean transit times |

Implementations are resolved by name from config (`arduino.backend`, `camera.backend`, `classifier.backend`)
in `panel.py`'s `build_modules()`; a missing module falls back to the stub, so the panel always starts.

## Run

```bash
cd ~/robotics
.venv/bin/python -m line.panel                 # http://127.0.0.1:8800 (stubs for anything not yet landed)
.venv/bin/python -m line.panel --all-fake      # no hardware at all
line/run_tests.sh                              # pytest, no hardware
```

The Arduino agent's `magnet_sorter/conveyor_button.py` (port 8765) keeps holding the serial port; the line
talks to it over HTTP (`arduino.backend = "http"`) so both panels can be open at once.
