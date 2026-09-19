# Motion checkpoint

Selected change: physics timestep decreases from 2 ms to 1 ms.
The camera interval remains 4 ms, or 250 Hz. Requested throughput remains 500 objects/s.
The model bytes remain fixed. No jet settings changed.

The two-second motion runs use seed 8, no camera control, and no jets.
Geometry measurements use separate MjData, so evaluation does not overwrite the live solver state.
Both runs admit 1,000 objects with zero insertion overlaps and zero pool starvation.

| Class | Maximum post-feed clearance, 2 ms | Maximum post-feed clearance, 1 ms |
|---|---:|---:|
| good | 23.57 mm | 19.51 mm |
| faded | 2.72 mm | 2.64 mm |
| black | 4.75 mm | 0.51 mm |
| sour | 12.72 mm | 1.81 mm |
| insect | 2.43 mm | 1.28 mm |
| broken | 6.99 mm | 0.87 mm |
| shell | 48.88 mm | 7.98 mm |
| husk | 48.94 mm | 0.31 mm |
| stone | 92.62 mm | 25.24 mm |
| stick | 2.45 mm | 1.66 mm |

These sampled extrema do not establish physical realism or acceptance.
Impulse distributions, penetration, class counts, inspection motion, and jet motion appear in the JSON evidence.
Some penetration extrema remain worse, including shells and sticks. Overall physical stability remains incomplete.

The four-second engine comparison uses the unchanged model and seed 8.
Capture improves from 133/190 to 140/190. Total good loss improves from 65/1110 to 57/1110.
No cohort object remains unresolved in either run. Both admit 2,000 objects.
Physics cost increases from 575.5 to 1113.7 ms per simulated second.
Total runtime increases from 20.217 to 23.402 seconds. Engine speed decreases from 0.198x to 0.171x.
Measured latency remains active in scheduling, so host timing can affect outcomes.

## Rejected candidates

- Identity inertia rotation: shell maximum clearance rose from 48.88 to 111.10 mm. Good maximum clearance rose to 205.27 mm.
- Matching feed speed to 3.0 m/s: shell clearance improved, but good maximum clearance rose to 165.37 mm.

Neither rejected candidate remains in production. Their evidence and candidate preset remain preserved.
The inertia experiment added `m.body_iquat[b] = [1, 0, 0, 0]` immediately after assigning body inertia.
The observed axis mismatch remains unresolved. The simple correction did not improve motion.

## Reproduce

```bash
.venv-coffee/bin/python thoughts/taras/research/coffee-quality/measure_motion.py --preset thoughts/taras/research/coffee-quality/timestep-1ms.json --seconds 2 --out /tmp/coffee-motion-1ms
.venv-coffee/bin/python sim/coffee_sorter/engine.py --preset sim/coffee_sorter/configs/default_demo.json --seconds 4 --out /tmp/coffee-quality-1ms
```

## Remaining model concerns

The capsule path assigns local AABB extents in the wrong axis order.
The simulator also changes geometry sizes and mass properties after compilation.
MuJoCo documents restrictions on these runtime changes.
These findings limit claims about physical fidelity. The short timestep experiment does not resolve them.
The inertia correction already failed the motion comparison. No unmeasured geometry or solver correction entered this branch.

Source: [MuJoCo model changes](https://mujoco.readthedocs.io/en/stable/programming/simulation.html#mjmodel-changes).
