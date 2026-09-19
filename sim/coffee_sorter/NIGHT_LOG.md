# Coffee sorter night log

One branch: `coffee-sorter-closed-loop`.
One [PR](https://github.com/tarasyarema/hackspain/pull/1).
Do not merge; Taras reviews in the morning.

## Task f2a7e430: first trained closed loop

### What ran

- Base: `cad5f9b`.
- Detector: 100 timed frames plus exact equivalence checks.
- Train: `train --profile green_arabica --seconds 24 --rate 900 --boost 5`.
- Run: `run --rate 2000 --seconds 8 --video`.
- Python 3.12, MuJoCo 3.13, headless OSMesa.
- Nine regression tests pass.

### Numbers

- Detector: 79.04 → 5.44 ms median.
- Exact feature and coordinate equality in ten cases.
- The <5 ms goal remains open.
- Training: 61,929 blob samples.
- Holdout accuracy: 97.91%; defect recall: 96.99%.
- Holdout shares repeated bean views; not independent-bean accuracy.
- Final run: 13,134 evaluated beans.
- Throughput: 1,983 simulated beans/s.
- Defect removal: 38.89%.
- Good-bean loss: 7.96%.
- Spills: 4.72%; pool-starved attempts: 122.
- Late reject decisions: 0.
- Latency p50/p99/max: 40.87 / 45.69 / 81.73 ms.
- Nominal budget: 73.33 ms. No worst-case timing guarantee.
- 8 simulated seconds took 284.22 wall seconds.

### Watch and inspect

- [Closed-loop MP4 with HUD](runs/20260919_015039_green_arabica_2000/overview.mp4).
- [Prediction strip 0](runs/20260919_015039_green_arabica_2000/inspection_0.png).
- [Prediction strip 3](runs/20260919_015039_green_arabica_2000/inspection_3.png).
- [Metrics](runs/20260919_015039_green_arabica_2000/metrics.json).
- [Controller decisions](runs/20260919_015039_green_arabica_2000/decisions.csv).
- These initial strips show predictions, not physical hit outcomes.
- Target-versus-hit visual recording is being added below.

### What surprised us

- A good classifier did not produce good physical sorting.
- 281 black beans were targeted; only 155 were hit by a jet.
- Of those 155 hits, 146 ended in rejection.
- Increasing force alone would not fix missed intersections.
- Early decisions initially created duplicate tracks and pulses.
- Keeping decided tracks associated fixed that regression.
- Transfer delay and scheduling were missing from latency accounting.
- Timing now adds modeled 4 ms capture/transfer to CPU work.
- Synthetic renderer wall time is not hardware-camera latency.

### Still broken

- Target-to-jet timing or coverage needs investigation.
- Collateral hits discard good beans.
- Shells, husks and stones spill heavily.
- Detector misses the 5 ms goal on this host.
- Force, pulse and splitter settings are unchanged pending evidence.
- Intermediate run `20260919_014250` had duplicate actuation.
  It is invalid as final validation.

### Overnight handoff

- Keep this branch and PR for subsequent tasks.
- Commit videos, annotated strips and metrics in each run directory.
- Add one task section here after each major result.
- Push each major result; never merge overnight.
- Rate sweep and roasted/open-set demos are queued separately.

## Task b4d760bf: rate, latency and physical limits

In progress on the same branch and draft PR.
The experiment plan is [NIGHT_PLAN.md](NIGHT_PLAN.md).
Each numerical run retains visual evidence in `runs/`.

The earlier interrupted visual run was incomplete and is excluded from results.
The replacement starts from tested source and writes a retained execution log.
The HUD now uses one white text pass on a dark panel, with separated rows.
A decoded frame from the delivered video was checked and the final frame decodes.

### Annotated baseline

Command: `run.py run --rate 2000 --seconds 8 --video --name baseline-2000-measured`.
Output: `runs/baseline-2000-measured/`.
Seed 0; original trained `green_arabica` model; specialty policy.
Jet force, pulse, splitter and pool defaults unchanged.

- Finished all 8 simulated seconds and 2,000 camera frames.
- 13,148 eligible beans; 13,134 resolved; 14 still in flight.
- Physical accuracy: 81.79% (10,754/13,148; spills/unresolved count as errors).
- Physical rejection recall: 38.85% (744/1,915 defects).
- Rejection precision: 45.45% (744/1,637 rejected beans).
- Good false-eject rate: 7.95% (893/11,233 good beans).
- Spills: 4.72%; pool-starved attempts: 122.
- Admitted throughput: 1,983.06 beans/s.
- Late reject decisions: 0/2,220.
- Latency p50/p99/max: 39.96 / 46.35 / 76.11 ms.
- Minimum reject-decision headroom: 3.07 ms.
- Runtime: 293.78 wall seconds, 36.72 s per simulated second.

The cohort is spawn times 0.8–7.4 s. Unresolved beans remain in the denominator,
so recall differs slightly from the earlier resolved-only 38.89% result.
Maximum frame latency still exceeds the nominal 73.33 ms budget.
Zero late reject decisions does not establish a worst-case timing guarantee.

Touching/overlap is measurably worse:

- Single observations: 38,480; multiclass accuracy 97.15%.
- Merged observations: 3,760; binary action accuracy 84.47%.
- Merged action recall: 43.78% (401/916 rejectable observations).
- Physical recall, single-only beans: 47.04% (588/1,250 defects).
- Physical recall, ever-merged beans: 26.23% (96/366 defects).
- Good false ejects: 7.04% single-only versus 9.32% ever-merged.
- 818 eligible beans were never mapped to a full camera blob.

Camera observation scores repeat views of the same beans. Physical cohorts
count each eligible bean once. Merged beans are a harder cohort, so the gap
is descriptive, not a controlled causal estimate of overlap alone.

The full-run reject funnel is 2,220 decisions → 2,220 queued → 2,203 activated.
Of 1,999 activated decisions with mapped constituents, 983 hit a constituent
with their own pulse, and 838 hit a constituent that was ultimately rejected.
These decision counts cover the whole run, not just the eligible bean cohort.

The readable HUD is fixed. The baseline's legacy HUD valve counter counts
queued commands; a visible video correction states this. The annotated sheets
distinguish actual `FIRED` activation from physical `own hits`.
All 400 frames are retained in a 4.2 MB H.264 video: 16 s playback at 25 fps.
No simulation was rerun or numerical values changed during video compression.
Accuracy and explicit metric aliases were derived after the run from preserved
counts; `metrics.json` records that derivation and the original source hashes.
Twenty-two regression tests and both independent review axes pass.

- [Watch full HUD video](https://hack-s3.agent-swarm.dev/agentfs/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/drives/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/baseline-overview.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=minioadmin%2F20260919%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260919T022753Z&X-Amz-Expires=86400&X-Amz-Signature=b8581a6d3802824a3a3916b3650d0811368499fe8f5c34e8989b78a2126065ff&X-Amz-SignedHeaders=host&response-content-disposition=attachment%3B%20filename%2A%3DUTF-8%27%27baseline-overview.mp4&x-amz-checksum-mode=ENABLED&x-id=GetObject) (direct link expires 20 September, 02:27 UTC).
- [Durable video](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/baseline-overview.mp4).
- [Local video](runs/baseline-2000-measured/overview.mp4).
- [Metrics and denominators](runs/baseline-2000-measured/metrics.json).
- [Fired and merged inspection sheet](runs/baseline-2000-measured/inspection_1.png).
- [Missed-target inspection sheet](runs/baseline-2000-measured/inspection_4.png).
- [Decoded HUD frame](runs/baseline-2000-measured/hud_frame200.png).
- [Run log](runs/baseline-2000-measured/run.log).

Phone previews (direct download links expire 20 September, 02:24 UTC):

- [Fired targets and merged beans](https://hack-s3.agent-swarm.dev/agentfs/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/drives/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/baseline-fired-and-merged.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=minioadmin%2F20260919%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260919T022400Z&X-Amz-Expires=86400&X-Amz-Signature=41b85c7593ed21c6965e6b33676fce3df80c86b1e613af752939d6345ba2248c&X-Amz-SignedHeaders=host&response-content-disposition=attachment%3B%20filename%2A%3DUTF-8%27%27baseline-fired-and-merged.png&response-content-type=image%2Fpng&x-amz-checksum-mode=ENABLED&x-id=GetObject)
- [Fired targets that missed the jets](https://hack-s3.agent-swarm.dev/agentfs/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/drives/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/baseline-missed-targets.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=minioadmin%2F20260919%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260919T022400Z&X-Amz-Expires=86400&X-Amz-Signature=153eb9a6ad117fbded7b11a9663bd1dc9d37370529eb05f227b1afa5dbe9df83&X-Amz-SignedHeaders=host&response-content-disposition=attachment%3B%20filename%2A%3DUTF-8%27%27baseline-missed-targets.png&response-content-type=image%2Fpng&x-amz-checksum-mode=ENABLED&x-id=GetObject)
- [Durable fired/merged image](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/baseline-fired-and-merged.png)
- [Durable missed-target image](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/baseline-missed-targets.png)

Measurement limits identified before the sweep:

- CPU time delays simulated valve availability. Camera backlog and dropped
  frames are not modeled, so this is not a real-time processor qualification.
- The 73.33 ms budget is nominal at the camera centre. Individual tracks have
  different deadlines; the sweep will also report their headroom.
- Pool exhaustion can hide offered load. Requested and admitted feed rates
  must stay separate.
- Merged means multiple projected ground-truth centres within one connected
  camera component. Fully occluded or off-centre constituents can be missed.

Prior task archives:

- [First-run evidence](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/first-run.tar.gz)
- [Final model, metrics and HUD video](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/final-validation.tar.gz)

- [Complete baseline archive](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/baseline-evidence.tar.gz).

### Rate sweep

Ran `run.py bench --rates 500,1000,2000,3000 --seconds 4 --name rate-sweep`.
Seed 0; original model and physical defaults; serial runs.
Each run has six annotated camera sheets and metrics under `runs/rate-sweep/`.
Eligible spawn window: 0.8–3.4 s; these are short, single-seed screens.

**500 beans/s requested**
- Admitted: 500.0 beans/s; eligible: 1,300.
- Accuracy: 92.00%; precision: 78.08%.
- Defect recall: 61.62%; good false ejects: 2.87%.
- Spills: 1.38%; late rejects: 0/288.
- Pool-starved attempts: 0; wall/sim: 15.12 s/s.

Surprise: quality degrades well before pool starvation. The 3,000 request
exceeds the base pool capacity, so it is not a valid 3,000-bean/s controller
qualification. Do not extend the rate upward until the pool is enlarged.
The three late decisions at 2,000 show why one zero-late run is insufficient.
Next: inject latency across the deadline, then compare physical settings.

- [Rate sweep plot](runs/rate-sweep/bench.png).
- [Raw benchmark results](runs/rate-sweep/bench.json).
- [Phone rate plot](https://hack-s3.agent-swarm.dev/agentfs/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/drives/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/rate-summary.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=minioadmin%2F20260919%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260919T023729Z&X-Amz-Expires=86400&X-Amz-Signature=02324f2335bd35f3bd74faf3428ba4445186cc7b357408bcd73677ffe60607e4&X-Amz-SignedHeaders=host&response-content-disposition=attachment%3B%20filename%2A%3DUTF-8%27%27rate-summary.png&response-content-type=image%2Fpng&x-amz-checksum-mode=ENABLED&x-id=GetObject) (direct link expires 20 September).
- [Durable rate plot](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/rate-summary.png).
- [Overlap cohort plot](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/rate-cohorts.png).
- [Complete rate-sweep evidence](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/rate-sweep.tar.gz).
**1000 beans/s requested**
- Admitted: 1000.0 beans/s; eligible: 2,600.
- Accuracy: 86.62%; precision: 56.16%.
- Defect recall: 44.44%; good false ejects: 5.74%.
- Spills: 2.38%; late rejects: 0/532.
- Pool-starved attempts: 0; wall/sim: 16.98 s/s.

Surprise: quality degrades well before pool starvation. The 3,000 request
exceeds the base pool capacity, so it is not a valid 3,000-bean/s controller
qualification. Do not extend the rate upward until the pool is enlarged.
The three late decisions at 2,000 show why one zero-late run is insufficient.
Next: inject latency across the deadline, then compare physical settings.

- [Rate sweep plot](runs/rate-sweep/bench.png).
- [Raw benchmark results](runs/rate-sweep/bench.json).
**2000 beans/s requested**
- Admitted: 2000.0 beans/s; eligible: 5,200.
- Accuracy: 82.23%; precision: 48.41%.
- Defect recall: 42.55%; good false ejects: 8.06%.
- Spills: 4.40%; late rejects: 3/1109.
- Pool-starved attempts: 0; wall/sim: 19.45 s/s.

Surprise: quality degrades well before pool starvation. The 3,000 request
exceeds the base pool capacity, so it is not a valid 3,000-bean/s controller
qualification. Do not extend the rate upward until the pool is enlarged.
The three late decisions at 2,000 show why one zero-late run is insufficient.
Next: inject latency across the deadline, then compare physical settings.

- [Rate sweep plot](runs/rate-sweep/bench.png).
- [Raw benchmark results](runs/rate-sweep/bench.json).
**3000 beans/s requested**
- Admitted: 2214.4 beans/s; eligible: 5,800.
- Accuracy: 81.05%; precision: 45.64%.
- Defect recall: 39.89%; good false ejects: 8.78%.
- Spills: 4.90%; late rejects: 0/1270.
- Pool-starved attempts: 2,867; wall/sim: 21.32 s/s.

Surprise: quality degrades well before pool starvation. The 3,000 request
exceeds the base pool capacity, so it is not a valid 3,000-bean/s controller
qualification. Do not extend the rate upward until the pool is enlarged.
The three late decisions at 2,000 show why one zero-late run is insufficient.
Next: inject latency across the deadline, then compare physical settings.

- [Rate sweep plot](runs/rate-sweep/bench.png).
- [Raw benchmark results](runs/rate-sweep/bench.json).
