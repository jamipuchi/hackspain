# Collection geometry validation

## Candidate 1: failed retention

The first candidate exactly implements the four authorized box changes.
It emits eight Accept events for Keep and eight Reject events for Reject.
Every object resolves and retires once.
No object emits Spill or remains unresolved in the absorbing simulation.

The required unabsorbed continuation changes that conclusion.
All eight Keep objects retain Accept support.
Only four Reject objects retain Reject support.
Reject cases 7/1, 8/0, 8/1, and 9/1 reach the ground.

Each continuation copies native `MjData` immediately before retirement.
It advances 1.5 simulated seconds with the same model and no actuator force.
The observer preserves the original data and reproduces belt and roller updates.
Terminal support requires contact during 90% of the final 100 ms.
It also requires speed below 0.01 m/s and displacement below 1 mm.

The four failed objects penetrate the Reject floor while inside its footprint.
The floor spans z=0.177 through 0.183 m, with a midplane at z=0.180 m.
Their centers cross the midplane within 1 through 3 ms after the saved first-contact step.
Their x positions remain between 0.604 and 0.756 m.
Their absolute y positions remain below 0.150 m.
Those positions lie inside the floor footprint.
Their post-contact downward speeds remain between 1.86 and 2.20 m/s.

The observer stores contact solve pose and post-step pose separately.
Those poses must not be treated as identical.
The final ground contact covers all 100 terminal samples for each failed object.

The unchanged bean geometry has priority one and `solref="0.006 1"`.
The static floor has priority zero.
This gives the bean contact a 6 ms response time constant.

The failed candidate remains preserved under `evidence/2026-09-20-cinta-collection/geometry-stone-attempt-1`.
The directory contains source, runner, log, summary, and lossless compressed object traces.
Its manifest records each original JSON hash.

## Downward thickness proposal

The proposal keeps the Reject floor top at z=0.183 m.
Its center becomes `(split_x+0.20, 0, belt_z-0.447)`.
Its half-size becomes `(0.26, belt_w/2+0.02, 0.030)`.
Default vertical bounds become 0.123 through 0.183 m.
The footprint and other three boxes remain unchanged.

The recorded normal-force impulse and mass imply incoming downward speeds up to 3.184 m/s.
This incoming speed is inferred, since Candidate 1 did not save velocity before its contact step.
Travel over one 6 ms response period is approximately 19.1 mm.
The worst recorded initial penetration adds 2.9 mm.
The proposed 30 mm top-to-midplane distance leaves approximately 8 mm against that estimate.
This estimate does not guarantee solver retention.
The same sixteen unabsorbed continuations must verify the proposal before broader validation.

No timestep, force, friction, solver, or collection-semantic change is proposed.
