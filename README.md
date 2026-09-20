# CINTA

CINTA means **Class-agnostic INline Transport Analyzer**. It simulates an optical coffee sorter from feed to physical outcome.

Choose how CINTA handles each item class, watch the conveyor, and compare every decision with its physical outcome.

```text
feed -> inspection camera -> classifier and anomaly detector -> air jets -> Keep or Reject
```

## Public demo

[Open CINTA at hack-growth.dev](https://hack-growth.dev).

The public demo runs one continuous shared engine. Every visitor sees the same session, objects, and Keep or Reject policy.

Policy changes affect all visitors. The engine continues to run when no browser is connected.

## What you can use now

The live page provides these controls and views:

- **Overview**, **Sorting**, and **Belt** show the machine from useful 3D camera positions.
- **3D** shows the machine and current objects. **2D** shows top and side projections.
- **Labels** shows technical positions and dimensions.
- **Items** shows every active class and its current Keep or Reject policy.
- **View items** opens the class gallery. One selected item rotates in the shared preview.
- **Keep all** and **Reject all** apply one policy change to the complete active catalog.
- **Drop test stone** adds one manual stone and follows its expected and actual outcomes.
- **Details** shows engine telemetry, object evidence, score context, and version identifiers.

Keep and Reject set the intended outcome. Anomaly detection or physical motion can still send a Keep item to Reject.

The top badge separates browser rendering from engine speed. **FPS** measures browser display callbacks. **Sim** shows average simulation speed since the engine session started.

`Sim 1.00×` means one simulation second per wall second. `Sim 0.20×` is five times slower than wall time.

## Run one local service

Use Python 3.13. Run all commands from the repository root.

First, check whether the canonical local service already runs:

```bash
curl -fsS http://127.0.0.1:8899/health
```

If the command returns a running status, open [http://127.0.0.1:8899](http://127.0.0.1:8899). Do not start a second service.

For a fresh checkout, create the environment and restore the evaluated model:

```bash
python3.13 -m venv .venv-coffee
source .venv-coffee/bin/activate
python -m pip install -r thoughts/taras/research/coffee-quality/requirements-resolved.txt
python - <<'PY'
from pathlib import Path
import gzip

source = Path('thoughts/taras/research/coffee-quality')
target = Path('sim/coffee_sorter/models')
target.mkdir(parents=True, exist_ok=True)
for suffix in ('joblib', 'manifest.json'):
    content = gzip.decompress((source / f'model-selected.{suffix}.gz').read_bytes())
    output = target / f'live_green_arabica.{suffix}'
    if output.exists() and output.read_bytes() != content:
        raise SystemExit(f'Preserve your existing artifact before replacing {output}')
    output.write_bytes(content)
PY
```

Start one continuous engine on the canonical local port:

```bash
.venv-coffee/bin/python sim/coffee_sorter/live.py \
  --host 127.0.0.1 \
  --port 8899 \
  --preset sim/coffee_sorter/configs/continuous_demo.json
```

Open [http://127.0.0.1:8899](http://127.0.0.1:8899). The engine continues when no browser is connected.

Stop the service with `Ctrl+C`. Shutdown writes the retained report and final state to the evidence directory printed at startup.

## Current limits

- The measured Mac runs the engine at approximately `0.2×` real time under the tested workload. Concurrent work can change this rate.
- The public engine currently runs below real time. The **Sim** value shows its actual cumulative rate.
- Browser FPS does not measure simulation speed, pose-packet rate, or sorting throughput.
- Keep all sets class rejection probability to zero. The anomaly detector remains active.
- Some stones can reach Reject without jet contact because of their passive physical trajectory. Stone routing remains unresolved.
- Recycling is fixed in the current runtime. The live scores still describe a simulation, not a production sorter.
- Manual test stones do not enter rolling feed scores.
- The local command above accepts loopback connections only. The public service uses separate reviewed deployment packaging.
- The public demo has no visitor login. Policy changes affect every connected visitor.
- The public engine accepts up to four connected browsers.
- Generated-item activation is planned, not delivered. The current Items gallery is limited to the active catalog.

## Technical documentation

- [Live engine and UI guide](sim/coffee_sorter/LIVE.md)
- [Sorter model and experiment guide](sim/coffee_sorter/README.md)
- [Measured live checkpoint](thoughts/taras/research/coffee-core-live/REPORT.md)
- [Public deployment and QA guide](thoughts/taras/deployment/hack-growth.dev/README.md)
- [Generated-item controls plan](thoughts/taras/plans/2026-09-19-cinta-item-controls.md)
