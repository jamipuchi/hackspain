---
date: 2026-09-19
researcher: Codex
git_commit: 91dedc339512a6673617e23f639b74c9d8ae0251
branch: codex/coffee-core-live
repository: hackspain
topic: Coffee feed motion and the next quality increment
tags: [research, coffee, physics, sorting]
status: complete
autonomy: critical
last_updated: 2026-09-19
last_updated_by: Codex
---

# Coffee motion follow-up

## Research question

Taras observed objects dropping and bouncing. He requested smoother motion and the next step toward the agreed sorting targets.

## Summary

The first demonstration missed the agreed targets: 52.82% capture, 6.45% good loss, and 0.189 simulated seconds per wall second.
Taras's subsequent session also missed them: 50.81% capture and 6.98% good loss.
That session included 28 manual stone injections, so it is separate evidence.

Commit `91dedc3` corrects one physical insertion error. Horizontally oriented sticks previously spawned using their upright height.
They now start with the configured 6 mm clearance below their collision geometry.
Other shapes still bounce. This correction does not establish smoother overall motion or acceptable sorting quality.

## Detailed findings

### Physical spawning

The feeder uses a 6 mm drop, a 2.6 m/s initial forward speed, and a 3.0 m/s belt.
The simulator adds small random tilt and velocity to each object.
Sticks use capsule geometry with their long axis rotated toward horizontal.
Their former placement used the unrotated capsule height.

The correction computes vertical support as `radius + half_length * abs(Rzz)` from the actual spawn quaternion.
It preserves horizontal placement clearance, random draws, initial velocities, contacts, and other shapes.

A 1.5-second physics diagnostic used development seed 8 and requested 500 objects/s.
Both runs spawned 750 objects and recorded zero pool starvation. Five objects were sticks.

| Measurement | Before | After |
|---|---:|---:|
| Stick spawn clearance | 13.29 to 17.71 mm | 6.00 mm |
| Maximum stick clearance after the feeder | 11.36 mm | 2.45 mm |
| Maximum good-bean clearance after the feeder | 15.62 mm | 14.68 mm |
| Maximum shell clearance after the feeder | 29.62 mm | 48.88 mm |

Every non-capsule class retained identical measured spawn-clearance extrema.
Later collision trajectories changed. Shell motion became worse in this short trace.
The later extrema measure collision-bottom clearance at sampled steps. They do not measure average smoothness or sorting quality.
The diagnostic calls `mj_forward` for measurements and runs without camera control or jets.

### Displayed motion

The diagnostic page draws the latest received poses directly, without interpolation.
Pose updates arrive at up to 10 Hz. Its approximately 60 display callbacks/s do not provide 60 distinct physical states/s.
This can make motion appear discontinuous. It does not establish the source of Taras's observed bouncing.
Confirmation of the affected viewer remains pending. Existing UI and rendering files were not edited.

### Missed captures

The committed full baseline has 161 missed defects with an associated rejection target but no own-pulse hit.
Their 201 associated rejection decisions all scheduled and activated. None was marked late.
Only seven of those objects had merged observations. One received collateral contact.

The current hit region spans plus or minus 10 mm along travel and about 5.86 mm across the belt.
The controller estimates forward velocity but selects nozzles from smoothed camera y without estimating lateral velocity.
The report does not include target coordinates during each pulse, schedule headroom, or availability clamps.
It cannot distinguish timing error, sideways error, and height error for these misses.

## Verification

Standards and spec reviews found no blocking defect in the capsule correction.
Python syntax passed. The model bootstrap rebuilt its artifact for the changed simulator source in 20.27 wall seconds.
The previous model remains preserved under `/tmp/coffee-feed-before-2026-09-19-1629`.

The two-second engine check ran revision `91dedc3` with model `6afe9ecd7676f335fb4b1e7fed89b9a5f57b7edd7d9081eb346ea0f966c20356`.
It completed in 9.33 wall seconds, with 33/40 captured defects and 17/261 lost keep objects.
The capture lower confidence bound was 68.05%. Good loss was 6.51%.
The model also changed, so this run cannot isolate the physical correction's effect on sorting quality.
It is a startup and integration check, not evidence that the 80% capture target passed.

```bash
cd /private/tmp/hackspain-coffee-core
.venv-coffee/bin/python sim/coffee_sorter/bootstrap_model.py
.venv-coffee/bin/python sim/coffee_sorter/engine.py --seconds 2 --out /tmp/coffee-motion-check
.venv-coffee/bin/python sim/coffee_sorter/live.py --port 8890
```

Stop the existing service before starting another on port 8890.
Do not run diagnostics while the live simulation is active.

## Next bounded increment

First measure feeder collisions and object motion at the inspection strip and jet plane.
For each intended jet contact, record schedule headroom, availability clamps, and distance from each axis of its active region.
Run one development-seed diagnostic before changing jet timing, nozzle selection, or pulse strength.
Use those measurements to select one correction, then compare capture and total good loss at the same requested throughput.
Freeze the resulting configuration before testing reserved acceptance seeds 111, 112, and 113.

UI interpolation remains a separate display change after confirmation of the affected viewer.
Natural language, learning controls, and realistic assets remain outside this quality increment.
Taras owns functional QA and acceptance.

## Code references

- [Capsule placement](https://github.com/tarasyarema/hackspain/blob/91dedc339512a6673617e23f639b74c9d8ae0251/sim/coffee_sorter/sim.py#L157)
- [Valve scheduling](https://github.com/tarasyarema/hackspain/blob/91dedc339512a6673617e23f639b74c9d8ae0251/sim/coffee_sorter/controller.py#L175)
- [Direct pose drawing](https://github.com/tarasyarema/hackspain/blob/91dedc339512a6673617e23f639b74c9d8ae0251/sim/coffee_sorter/live_web/live.js#L165)
- [Recorded measurements](coffee-motion/)
- [First full demonstration](coffee-core-live/REPORT.md)

## Open questions

The affected viewer remains unconfirmed.
The present evidence does not establish the causes of later bounce or missed jet contact.
No full quality evaluation ran after this correction.
