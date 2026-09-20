# Proposed collection geometry

Taras, raise the Accept floor above the observed lower branch, then extend the Reject floor beneath it.
Keep the splitter, jets, belt, model, and controller unchanged.
This proposal changes four existing collision boxes. It adds no mechanism or actuator.
No proposed geometry has been implemented or simulated.

All coordinates below use meters and the current continuous preset.
MuJoCo box sizes are half-extents.

| Existing surface | Proposed center `(x, y, z)` | Proposed half-size | Change |
| --- | --- | --- | --- |
| `bin_accept` | `(0.64, 0, 0.427)` | `(0.12, 0.27, 0.003)` | Raise floor top from 0.303 to 0.430 |
| Accept end wall | `(0.76, 0, 0.490)` | `(0.003, 0.27, 0.060)` | Raise wall to span 0.430 through 0.550 |
| `bin_reject` | `(0.54, 0, 0.180)` | `(0.26, 0.27, 0.003)` | Extend floor from `x=0.28..0.52` to `0.28..0.80` |
| Reject end wall | `(0.80, 0, 0.240)` | `(0.003, 0.27, 0.060)` | Move wall to the new downstream edge |

Equivalent layout-relative centers are `split_x + 0.30`, `split_x + 0.42`, `split_x + 0.20`, and `split_x + 0.46`.
The Accept floor center is `split_z - 0.048`; its wall center is `split_z + 0.015`.
Preserve the current width, thickness, friction, and collision settings.
These changes belong at [scene.py:163](/private/tmp/hackspain-cinta-collection-scoring/sim/coffee_sorter/scene.py:163).

The observed Reject centers cross `x=0.52` at `z=0.304..0.381`.
The proposed Accept floor underside is `z=0.424`.
Even a 7.30 mm Stone bounding radius leaves at least 35.7 mm clearance below that floor.
The trajectories descend there, so the raised floor should not intercept the lower branch.

At `x` near 0.50, Keep centers remain at `z=0.478..0.482` above the proposed floor.
The splitter's lowest downstream corner is about `z=0.4384`, which remains above the proposed floor top.
Keep objects can leave the splitter and reach the raised Accept floor.
The taller wall should contain both observed misses, whose centers approached the old wall near `z=0.420..0.437`.

A gravity-only projection from the eight Reject retirement states predicts center crossings at `z=0.183` around `x=0.603..0.752`.
The extended floor ends at 0.80, giving 48 mm beyond the largest projected center crossing.
This projection excludes new contact effects. It supports a candidate, not a validated design.
The source states and velocities remain in the [observer evidence](/private/tmp/hackspain-cinta-collection-scoring/thoughts/taras/qa/evidence/2026-09-20-cinta-stone-mechanics/summary.json).

Moving the Reject end wall is necessary.
Its current position at `x=0.52` obstructs the extended lower route.
Raising only the Accept floor would leave that obstruction and the short Reject floor unchanged.

The live renderer currently differs from the collision model.
It draws a flat 0.15-meter splitter and large collection volumes at [live.js:1386](/private/tmp/hackspain-cinta-collection-scoring/sim/coffee_sorter/live_web/live.js:1386).
The physical splitter is 0.28 meters long and tilted by 0.25 radians.
A later authorized geometry change must update that renderer's plate and collection surfaces to the same dimensions and rotation.
The current task makes no frontend edits.

The offline renderer imports recorded collision surfaces and their quaternions at [scene_machine.py:163](/private/tmp/hackspain-cinta-collection-scoring/sim/coffee_sorter/visual_assets/scene_machine.py:163).
Its decorative tray undersides and brackets use fixed coordinates at [scene_machine.py:254](/private/tmp/hackspain-cinta-collection-scoring/sim/coffee_sorter/visual_assets/scene_machine.py:254).
Update those decorations separately when a rendered deliverable is requested. They do not affect physics.

Validation must follow the corrected collection scorer.
First verify the four compiled box transforms and collision flags, including clearance beneath the splitter.
Then run the existing sixteen Stone cases with unchanged seeds, sampled bodies, force, timing, and model.
Require eight actual Reject collections and eight actual Keep collections, with no spill or unresolved cases.
Preserve every failed attempt and compare first collector contact with unabsorbed terminal support in a diagnostic observer.

Next run all ten built-in classes with seeds 7, 8, 9, and 42, including both pooled reuses.
Run representative feed on existing seeds 11, 17, 23, and 31 at 500 objects per second.
Keep the pool at 488 and preserve all existing quality gates.
Check contact identity, resolution age, late decisions, own and collateral jet contact, pool starvation, and paired wall/CPU cost.
Do not inspect reserved seeds or retrain.

Commands for an authorized geometry worktree, after its scorer and diagnostic scripts are available:

```sh
COFFEE_PY=/Users/taras/Documents/code/hackspain/.venv-coffee/bin/python
GEOMETRY_REPO=/path/to/approved-geometry-worktree
MODEL="$GEOMETRY_REPO/sim/coffee_sorter/models/live_green_arabica.joblib"
PRESET="$GEOMETRY_REPO/sim/coffee_sorter/configs/continuous_demo.json"
"$COFFEE_PY" "$GEOMETRY_REPO/thoughts/taras/qa/evidence/cinta_collection_routes.py" --repo "$GEOMETRY_REPO" --model "$MODEL" --preset "$PRESET" --mode stone --output-dir /private/tmp/cinta-geometry-stone-attempt-1
"$COFFEE_PY" "$GEOMETRY_REPO/thoughts/taras/qa/evidence/cinta_collection_routes.py" --repo "$GEOMETRY_REPO" --model "$MODEL" --preset "$PRESET" --mode classes --output-dir /private/tmp/cinta-geometry-classes-attempt-1
```

For manual E2E, inspect a private local service after frontend ownership and the geometry change are authorized.
Use `agent-browser open http://127.0.0.1:<port>`, then `agent-browser snapshot`.
Verify the displayed plate tilt, both floor elevations, actual collection labels, and both wall extents against compiled physics.
This proposal does not authorize a service change, browser run, or deployment.
