# CINTA Stone mechanical diagnosis

Taras, the approved observer confirms a scoring defect and a physical collection problem.
Every commanded Reject Stone settled in the Accept bin.
Two Keep controls missed both bins and reached the ground.

The original result of 2/8 Reject and 8/8 Keep measures an early height threshold, not bin collection.
Correct the scorer and retirement behavior before further force or timing calibration.

| Existing cases | Original labels | Observed named-bin contact | Stable support after continuation |
| --- | --- | --- | --- |
| Reject, eight Stones | 6 Accept, 2 Reject | 8 Accept, 0 Reject | 8 Accept |
| Keep, eight Stones | 8 Accept | 6 Accept, 0 Reject | 6 Accept, 2 ground |

Keep cases 7/0 and 9/0 reached the ground without contacting either bin.
These are repeated diagnostic seeds, not an accuracy estimate.
The [observer summary](/Users/taras/.codex/worktrees/1a72/hackspain/thoughts/taras/qa/evidence/2026-09-20-cinta-stone-mechanics/summary.json) contains every route and contact result.

The scorer freezes an outcome when the center reaches `x >= 0.34`, using `z > 0.475` for Accept.
The tilted splitter starts near `x=0.34386`; its leading face spans `z=0.50770..0.51157`.
The nominal threshold therefore differs from the collision geometry by at least 32.7 mm vertically.
The scorer ignores subsequent contacts and object orientation.
See [sim.py:413](/private/tmp/cinta-final-initial-qa/sim/coffee_sorter/sim.py:413) and [scene.py:160](/private/tmp/cinta-final-initial-qa/sim/coffee_sorter/scene.py:160).

The simulator then parks objects at `x > 0.50`, before collection.
All eight Reject-command Stones are below the blade at that boundary, with centers at `z=0.320..0.397`.
Lower-branch passage does not ensure Reject collection.
The Reject floor spans `x=0.28..0.52`, with its top at `z=0.183`.
The Accept floor starts at `x=0.52`, with its top at `z=0.303`.
At `x=0.52`, their centers remain 121..198 mm above the Reject floor, which ends at that boundary.
The continued trajectories intersect the higher Accept floor instead of the Reject floor.
Their first Accept contacts occur at `x=0.520..0.623`.
See [retirement](/private/tmp/cinta-final-initial-qa/sim/coffee_sorter/sim.py:423) and [bin geometry](/private/tmp/cinta-final-initial-qa/sim/coffee_sorter/scene.py:163).

Both bin floors and their end walls have active collision geometry.
The two chute side panels are visual only.
Every Keep case contacts the splitter, but two subsequently escape collection.
Keep 7/0 strikes the Accept wall near its 0.420 m top and escapes.
Keep 9/0 passes above that wall, with its center near 0.437 m.
Thus a corrected height threshold alone would still misrepresent physical collection.

Classification, decisions, scheduling, activation, and own jet contact succeed for every commanded Reject Stone.
The nozzle application counts imply impulses of 240, 300, or 600 microN s.
Older detailed traces reproduce vertical acceleration from gravity and impulse exactly within floating-point error.
They show no residual mass or force-integration defect.
Those traces use the older model, so their detailed mass comparisons are supporting evidence only.

The [negative calibration report](/private/tmp/hackspain-cinta-rejection-calibration/thoughts/taras/qa/2026-09-20-cinta-rejection-calibration-proposal.md) found limited spatial exposure and no accepted force or timing candidate.
Its routing percentages also use the defective early score.
They cannot establish bin collection performance or justify further tuning.

The observer replayed seeds 7, 8, 9, and 42 with both pooled reuses and both policies.
Source remained `289a362c50ba94dc35080369e0083536a30f686a`.
Model SHA256 remained `f5b26f26aed62db18fbef23b4df712b96b2946b1ae2fce699e1a2e85a20844a3`.
Force, lead, pool, and timestep remained 0.06 N, 1.5 ms, 488 bodies, and 1 ms.

At retirement, the observer advanced copied MuJoCo data for 1.5 seconds while preserving the original engine.
It verified zero feed, one active object, expired valve pulses, and zero applied forces.
It reproduced conveyor and roller updates and preserved original pose, velocity, warm start, and time exactly.
Contact timestamps describe the pre-integration solve, consistent with [MuJoCo 3.13.0](https://github.com/google-deepmind/mujoco/blob/3.13.0/src/engine/engine_forward.c#L1762).

All sixteen original result rows matched exactly except `decision.t_available_s`, which measures wall-clock latency.
The coordinator approved that sole comparison exception.
Both reference and observed availability preceded the unchanged valve window.
Raw differences remain saved.

Stable support required named-bin contact during at least 90% of the final 100 ms.
It also required terminal speed below 0.01 m/s and displacement below 1 mm.
All fourteen bin-contact cases had 100% support and agreed with their first named-bin contact.
Independent reviews verified the observer source and every raw route.

Two earlier observer attempts failed: full equality included wall-clock latency, then serialization rejected a NumPy body identifier.
Their source and logs remain preserved as negative evidence.
The final sixteen-case run passed all assertions and used 40.69 wall seconds.
Each run held the shared `fcntl` runtime lock. The lock was released and verified available afterward.

Correct outcome capture separately from mechanical tuning.
Use positive contact with a named bin as the collection event, with explicitly absorbing collection semantics.
Remove the early height outcome and premature `x > 0.50` retirement.
Retire collected objects once, and record missed-bin ground or downstream escape as Spill.

Update the shared scoring deadline consistently.
Observed Reject-command bin contacts occurred 0.521..0.555 seconds after injection.
Successful Keep collection took 0.743..0.848 seconds, beyond the current 0.6-second deadline.
The missed Keep objects reached the ground after 0.842 and 0.966 seconds.
Other classes and representative feed must validate the replacement deadline and pool capacity.

Focused regressions must verify:

- Crossing `x=0.34` or `x=0.50` without bin contact leaves the object active and unresolved.
- Each named bin emits its correct outcome once, followed by one retirement event.
- Ground or downstream escape emits Spill, and pooled reuse clears previous outcome state.
- Collection after 0.6 seconds does not become a premature settled-cohort failure.
- The same sixteen routes retain their classification and jet evidence while reporting actual collection.

After those checks, separately review the chute and bin geometry that intercepts lower-branch Stones and loses two Keep controls.
Do not substitute larger force, earlier timing, or a different score height for verified collection.

Application source, model, policy, service, and deployment were unchanged.
Reserved seeds were not inspected or run.
Branch `codex/cinta-physics-repair` remains at `d4eddb6310f4cb58052c3cfa22b2b4edb5748a71`.

The [exact observer](/Users/taras/.codex/worktrees/1a72/hackspain/thoughts/taras/qa/evidence/2026-09-20-cinta-stone-mechanics/observe_routes.py) has SHA256 `d162e23f4b7eff587462581a28415af5c2dbf0ccba9f1f669d3b0aff96267539`.
The [artifact manifest](/Users/taras/.codex/worktrees/1a72/hackspain/thoughts/taras/qa/evidence/2026-09-20-cinta-stone-mechanics/artifact-sha256.json) hashes the summary, all trajectories, comparisons, and failed attempts.
