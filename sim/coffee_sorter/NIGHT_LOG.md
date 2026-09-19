# Coffee sorter night log

One branch: `coffee-sorter-closed-loop`.
One [PR](https://github.com/tarasyarema/hackspain/pull/1).
Do not merge; Taras reviews in the morning.

Start here: [morning verdict](#morning-verdict),
[rate plot](runs/rate-sweep/rate_summary.png),
[latency plot](runs/latency-sweep/latency_summary.png),
[tuning plot](runs/tuning-sweep/tuning_1000_summary.png).
Watch the [fresh-seed demo](https://hack-s3.agent-swarm.dev/agentfs/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/drives/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/demo-1000-force006.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=minioadmin%2F20260919%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260919T031054Z&X-Amz-Expires=86400&X-Amz-Signature=83f972f690e442ae38a914626c71250110b77843719e91cc537d109c4a288de5&X-Amz-SignedHeaders=host&response-content-disposition=attachment%3B%20filename%2A%3DUTF-8%27%27demo-1000-force006.mp4&x-amz-checksum-mode=ENABLED&x-id=GetObject);
[all final phone links](#final-phone-visuals-and-archives).

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

### Latency versus the camera-to-jet budget

Ran five serial 4 s runs at 1,000 beans/s, seed 0.
Added controller availability delays: 0, 20, 30, 40 and 60 ms.
All runs have 2,600 eligible beans and six annotated camera sheets.
The nominal centre-to-jet budget is 73.33 ms. The controller declares late
when availability exceeds predicted arrival by more than 2 ms.

**+0 ms injected delay**
- Total latency p50/p99: 37.33/42.38 ms.
- Actual median headroom: 33.42 ms.
- Late rejects: 0/532 (0.00%).
- Physical defect recall: 44.44%.

**+20 ms injected delay**
- Total latency p50/p99: 56.98/61.79 ms.
- Actual median headroom: 13.66 ms.
- Late rejects: 0/538 (0.00%).
- Physical defect recall: 49.71%.

**+30 ms injected delay**
- Total latency p50/p99: 66.83/71.25 ms.
- Actual median headroom: 4.45 ms.
- Late rejects: 39/566 (6.89%).
- Physical defect recall: 44.17%.

**+40 ms injected delay**
- Total latency p50/p99: 77.52/83.19 ms.
- Actual median headroom: -6.85 ms.
- Late rejects: 437/553 (79.02%).
- Physical defect recall: 16.53%.

**+60 ms injected delay**
- Total latency p50/p99: 97.25/104.30 ms.
- Actual median headroom: -26.22 ms.
- Late rejects: 513/516 (99.42%).
- Physical defect recall: 5.75%.

Surprise: +30 ms misses some deadlines despite positive median headroom.
Median timing alone is not a sufficient design margin.
The last two points lose both timely decisions and physical rejection.
This validates the simulated availability/deadline path, not real-time hardware.
Camera backlog and dropped frames remain unmodeled. Measured CPU time varies;
these are single-seed screens rather than bit-identical recorded-frame replays.

- [Phone latency/headroom graph](https://hack-s3.agent-swarm.dev/agentfs/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/drives/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/latency-summary.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=minioadmin%2F20260919%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260919T024732Z&X-Amz-Expires=86400&X-Amz-Signature=ae56e05a7827564795c3d304b5af94a53676f9392799ab1d0a3ae8482e764ac6&X-Amz-SignedHeaders=host&response-content-disposition=attachment%3B%20filename%2A%3DUTF-8%27%27latency-summary.png&response-content-type=image%2Fpng&x-amz-checksum-mode=ENABLED&x-id=GetObject) (direct link expires 20 September).
- [Durable latency plot](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/latency-summary.png).
- [Complete latency evidence](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/latency-sweep.tar.gz).
- [Local plot](runs/latency-sweep/latency_summary.png); every recorded late flag matches the minus-2-ms threshold.

### Physical tuning screens

Each setting ran for 4 s at 1,000 beans/s, seed 0, with 2,600 eligible beans.
All used a 60 ms minimum total controller latency; measured CPU can exceed it.
One setting changed at a time. Base pulse duration is scaled by estimated mass.
These are short screening comparisons, not independent-bean replays.

**Baseline: 0.09 N, base pulse 3 ms, splitter drop 125 mm, adaptive valves**
- Recall: 44.44%; good false ejects: 5.74%.
- Spills: 2.38%; precision: 56.16%.
- [Metrics and all denominators](runs/tuning-sweep/physics-base/metrics.json).

**Jet force 0.06 N**
- Recall: 51.45%; good false ejects: 4.08%.
- Spills: 2.00%; precision: 65.93%.
- [Metrics and all denominators](runs/tuning-sweep/force-0.06/metrics.json).

**Jet force 0.12 N**
- Recall: 40.97%; good false ejects: 6.16%.
- Spills: 3.23%; precision: 54.21%.
- [Metrics and all denominators](runs/tuning-sweep/force-0.12/metrics.json).

**Base pulse 2 ms**
- Recall: 44.04%; good false ejects: 5.98%.
- Spills: 2.85%; precision: 54.27%.
- [Metrics and all denominators](runs/tuning-sweep/pulse-2ms/metrics.json).

**Base pulse 5 ms**
- Recall: 43.09%; good false ejects: 6.32%.
- Spills: 3.54%; precision: 53.00%.
- [Metrics and all denominators](runs/tuning-sweep/pulse-5ms/metrics.json).

**Splitter 100 mm below belt**
- Recall: 45.85%; good false ejects: 9.73%.
- Spills: 2.62%; precision: 42.22%.
- [Metrics and all denominators](runs/tuning-sweep/split-0.10/metrics.json).

**Splitter 150 mm below belt**
- Recall: 45.92%; good false ejects: 3.47%.
- Spills: 2.92%; precision: 67.63%.
- [Metrics and all denominators](runs/tuning-sweep/split-0.15/metrics.json).

**One valve per target, 64-valve bank**
- Recall: 45.43%; good false ejects: 5.58%.
- Spills: 1.85%; precision: 56.75%.
- [Metrics and all denominators](runs/tuning-sweep/target-nozzles-1/metrics.json).

**Three valves per target, 64-valve bank**
- Recall: 46.54%; good false ejects: 7.24%.
- Spills: 2.65%; precision: 50.91%.
- [Metrics and all denominators](runs/tuning-sweep/target-nozzles-3/metrics.json).

The 0.06 N force screen improves recall by 7.00 percentage points,
reduces good false ejects by 1.66 points, and reduces spills by 0.38 points.
It is the strongest screened recall gain without increasing either loss measure.
The lower splitter reduces good false ejects further but increases spills.
Three valves improve coverage at the cost of collateral rejection.
Defaults remain the reference configuration; the fresh-seed confirmation is below.

### Pool capacity at 3,000 beans/s

Both runs used 4 s, seed 0 and the same 60 ms minimum latency.
The enlarged pools are 1,800 ellipsoids, 72 halves, 30 boxes and 30 capsules.

**Original pool**
- Admitted: 2188.75 beans/s; starved: 2,949.
- Defect recall: 38.50%; good false ejects: 8.37%.
- Spills: 4.36%; accuracy: 81.77%.
- Precision: 44.76%; late rejects: 0.
- Wall/sim: 21.54 s/s; eligible beans: 5,775.

**Enlarged pool**
- Admitted: 3000.00 beans/s; starved: 0.
- Defect recall: 38.60%; good false ejects: 10.10%.
- Spills: 4.85%; accuracy: 79.65%.
- Precision: 38.91%; late rejects: 0.
- Wall/sim: 24.49 s/s; eligible beans: 7,800.

Larger pools remove the admission bottleneck, but do not fix sorting quality.
No higher-rate screen: the full-rate plant still ejects 10.10% of good beans
and rejects only 38.60% of defects. Capacity and useful sorting are separate.

### Fresh-seed confirmation and demo

Both runs: 1,000 beans/s, 4 s, seed 1, 60 ms minimum total latency.
Each evaluated 2,600 eligible beans; both had zero late rejects and zero starvation.
The candidate changes only jet force from 0.09 N to 0.06 N.

- Defect recall: 48.50% → 51.53%.
- Good false ejects: 6.27% → 4.26%.
- Rejection precision: 58.43% → 68.24%.
- Physical accuracy: 86.08% → 88.54%.
- Spills: 2.65% → 2.12%.
- Own-pulse hits: 298/542 → 331/509 associated activations.

The improvement repeats on a second seed. Two short runs do not establish
robustness across lots, seed distributions or real coffee.
Use `--jet-force 0.06` as an explicit demo setting; defaults remain unchanged.
The candidate video is included only on the second run, so wall times are
not a matched performance comparison.

Artifacts: [confirmation plot](runs/confirmation-seed1/confirmation_summary.png),
[baseline metrics](runs/confirmation-seed1/physics-base/metrics.json),
[candidate metrics](runs/confirmation-seed1/force-0.06/metrics.json),
[HUD video](runs/confirmation-seed1/force-0.06/overview_h264.mp4),
and [annotated camera strip](runs/confirmation-seed1/force-0.06/inspection_1.png).
Exact commands and logs: `runs/confirmation-seed1/run.sh` and sibling `.log` files.
The simulation reached 4.000 s. H.264 video: 200 frames, 50 fps, 1.34 MB.
Midpoint and final frames decode; all six annotated sheets are present.
The HUD uses white text on a dark panel and labels the counter `queued`.

### Morning verdict

- Best next experiment: longer paired seeds with 0.06 N, then jet-hit geometry.
- First fix: merged targets and missed jet intersections, not classifier accuracy.
- Ever-merged baseline defect recall: 26.23%; single-only: 47.04%.
- Merged blob action accuracy: 84.47% over 3,760 repeated observations.
  Action recall: 43.78%; precision: 85.32%. These are not independent beans.
- The larger pool sustains 3,000 beans/s but loses 10.10% of good beans.
- Added latency reveals the failure boundary; camera backlog remains unmodeled.
- Detector remains above 5 ms. Physical sorting and real-time claims remain open.

All 13 tuning/confirmation runs reached 4.0 s and have metrics, decisions,
evidence JSON and six annotated PNGs each. The full 8 s / 2,000 baseline remains
`runs/baseline-2000-measured/`, committed in `cc5481a`.
Its HUD was fixed, all 400 frames retained, and final-frame decode checked.
The failed `20260919_020354` directory is excluded.
Final regression suite: 26 tests pass. Standards and Spec source reviews pass.
Plot QA caught crowded labels; numbered points and separate legends fix them.
No CI workflows are configured. Keep PR #1 draft and unmerged.

### Final phone visuals and archives

Uploaded with the agent-fs skill. Upload sizes and SHA-256 hashes verified.
Direct downloads expire 20 September 2026 at about 03:11 UTC;
durable viewer links remain in the shared drive.

- [Demo video, 1.34 MB](https://hack-s3.agent-swarm.dev/agentfs/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/drives/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/demo-1000-force006.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=minioadmin%2F20260919%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260919T031054Z&X-Amz-Expires=86400&X-Amz-Signature=83f972f690e442ae38a914626c71250110b77843719e91cc537d109c4a288de5&X-Amz-SignedHeaders=host&response-content-disposition=attachment%3B%20filename%2A%3DUTF-8%27%27demo-1000-force006.mp4&x-amz-checksum-mode=ENABLED&x-id=GetObject) · [durable](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/demo-1000-force006.mp4)
- [Annotated demo strip](https://hack-s3.agent-swarm.dev/agentfs/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/drives/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/demo-annotated-strip.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=minioadmin%2F20260919%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260919T031055Z&X-Amz-Expires=86400&X-Amz-Signature=08208a96625c229fd85242971054ab0e7e05b96db5e9f0827102776acc23c55f&X-Amz-SignedHeaders=host&response-content-disposition=attachment%3B%20filename%2A%3DUTF-8%27%27demo-annotated-strip.png&response-content-type=image%2Fpng&x-amz-checksum-mode=ENABLED&x-id=GetObject) · [durable](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/demo-annotated-strip.png)
- [Tuning screen](https://hack-s3.agent-swarm.dev/agentfs/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/drives/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/tuning-summary.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=minioadmin%2F20260919%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260919T031053Z&X-Amz-Expires=86400&X-Amz-Signature=d9ec54b40c12dcbd211cb6c7443c0937ed0633db37eac59fafe402e433621136&X-Amz-SignedHeaders=host&response-content-disposition=attachment%3B%20filename%2A%3DUTF-8%27%27tuning-summary.png&response-content-type=image%2Fpng&x-amz-checksum-mode=ENABLED&x-id=GetObject) · [durable](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/tuning-summary.png)
- [Pool comparison](https://hack-s3.agent-swarm.dev/agentfs/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/drives/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/pool-summary.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=minioadmin%2F20260919%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260919T031053Z&X-Amz-Expires=86400&X-Amz-Signature=f769eb9642b6cc8381aa01857d44cc33c9be7a36679cada63aba343dbe44fd2f&X-Amz-SignedHeaders=host&response-content-disposition=attachment%3B%20filename%2A%3DUTF-8%27%27pool-summary.png&response-content-type=image%2Fpng&x-amz-checksum-mode=ENABLED&x-id=GetObject) · [durable](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/pool-summary.png)
- [Fresh-seed confirmation](https://hack-s3.agent-swarm.dev/agentfs/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/drives/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/confirmation-summary.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=minioadmin%2F20260919%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260919T031054Z&X-Amz-Expires=86400&X-Amz-Signature=66a7de4587c2e517b51898200fb91b6eec22224072bb24726bb995167ec85435&X-Amz-SignedHeaders=host&response-content-disposition=attachment%3B%20filename%2A%3DUTF-8%27%27confirmation-summary.png&response-content-type=image%2Fpng&x-amz-checksum-mode=ENABLED&x-id=GetObject) · [durable](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/confirmation-summary.png)
- [All tuning evidence, 38.85 MB](https://hack-s3.agent-swarm.dev/agentfs/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/drives/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/tuning-sweep.tar.gz?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=minioadmin%2F20260919%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260919T031056Z&X-Amz-Expires=86400&X-Amz-Signature=bd9f4af1fcf82e9cc0ca965f37fa45f388d0c695b955a6e3266c6c202aa5ab9f&X-Amz-SignedHeaders=host&response-content-disposition=attachment%3B%20filename%2A%3DUTF-8%27%27tuning-sweep.tar.gz&response-content-type=application%2Fgzip&x-amz-checksum-mode=ENABLED&x-id=GetObject) · [durable](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/tuning-sweep.tar.gz)
- [All confirmation evidence, 8.23 MB](https://hack-s3.agent-swarm.dev/agentfs/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/drives/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/confirmation-seed1.tar.gz?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=minioadmin%2F20260919%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260919T031057Z&X-Amz-Expires=86400&X-Amz-Signature=afa1360c296727549eeceee45e45b3e84ba5a42f30f3a5a4bbe8ea57a31bcedc&X-Amz-SignedHeaders=host&response-content-disposition=attachment%3B%20filename%2A%3DUTF-8%27%27confirmation-seed1.tar.gz&response-content-type=application%2Fgzip&x-amz-checksum-mode=ENABLED&x-id=GetObject) · [durable](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/confirmation-seed1.tar.gz)

### Generalization experiments started

The roasted profile already exists. The experiment trains a separate model,
then compares both products with identical controller and physical settings.
The open-set experiment separately measures anomaly-only flags, combined
reject decisions and physical reject-bin outcomes. Neither experiment changes
controller.py, vision.py or sim.py. Any exception will be reported here.

### New product: roasted, unchanged shared controller

**The pipeline transfers after retraining; sorting quality does not transfer unchanged.**
The stock `run.py train --profile roasted --seconds 24 --rate 900 --boost 5 --seed 0`
trained all six roasted classes. No edits were made to `profiles.py`, `controller.py`,
`vision.py`, `sim.py`, `classifier.py` or `run.py` for this product change.
The separate experiment harness stages models and records evidence; it does not
replace perception, decisions or physics.

Training produced 58,143 blobs: 43,607 train and 14,536 test. Roasted holdout
accuracy is 98.62%; green is 97.91%. This is a random **blob** split, so repeated
views of one bean can occur in both partitions. These scores are not independent
lot validation. The anomaly model is also calibrated on all collected good blobs.

Matched physical runs: seed 1, 4 s, 1,000 beans/s, specialty policy, 0.5 reject
probability, 0.06 N jets and a 60 ms minimum controller latency. Both use the
same physical layout, pulse settings and source hashes. Each evaluates 2,600
beans spawned from 0.8 to 3.4 s; 2,599 resolve. Spills and the unresolved bean
remain in the denominator. Feed composition and bean properties differ by profile. Wilson intervals describe
within-run bean counts, not variation across seeds or lots.

| Physical metric | Green arabica | Roasted |
| --- | ---: | ---: |
| Correct routing / eligible | 88.54% | 89.85% |
| Defect recall | 202/392 = 51.53% | 96/243 = 39.51% |
| Recall, Wilson 95% interval | 46.59–56.44% | 33.57–45.77% |
| Rejection precision | 202/296 = 68.24% | 96/186 = 51.61% |
| Good false ejects | 94/2,208 = 4.26% | 90/2,357 = 3.82% |
| Spills / eligible | 55/2,600 = 2.12% | 42/2,600 = 1.62% |
| Late reject decisions | 0/561 | 0/363 |
| Admitted objects/s | 1,000 | 1,000 |
| Pool-starved attempts | 0 | 0 |
| Total latency p50/p99 | 60/60 ms | 60/60 ms |
| Measured compute p50/p99 | 26.06/38.43 ms | 14.64/26.22 ms |
| Wall seconds / simulated second | 15.88 | 13.24 |

Roasted per-class rejection: quaker 44/96, burnt 22/48, broken 19/72,
stone 4/15 and stick 7/12. Changing only the model lets the existing controller
run the new product, but 60.49% of eligible roasted defects do not reach reject.
Overall accuracy also depends on the different class mix;
it does not establish a better sorter. Timing is a shared-host measurement with
a simulated latency floor; camera backlog and dropped frames remain unmodeled.

- [Matched metrics and all confidence intervals](runs/generalization/comparison/metrics.json).
- [Side-by-side confusion matrices](runs/generalization/comparison/confusion_matrices.png).
- [Single labelled camera contact sheet](runs/generalization/comparison/camera_strip_contact_sheet.png).
- [Stock training command and output](runs/generalization/train.log).
- [Green physical metrics](runs/generalization/green_arabica/run/metrics.json) and [roasted physical metrics](runs/generalization/roasted/run/metrics.json).
- [Protected source integrity](runs/generalization/comparison/shared_source_integrity.json).

Viewable copies: [confusion matrices](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/generalization/comparison/confusion_matrices.png)
and [camera contact sheet](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/generalization/comparison/camera_strip_contact_sheet.png).
Durable viewer links require drive access. Roasted source and Spec reviews pass;
34 regression tests pass ([output](runs/generalization/roasted-checks.log)).

### Open-set physical threshold comparison

**More anomaly commands did not translate into much more physical capture.**
The green classifier and good-class covariance are unchanged. New test-only
classes are a cyan plastic chip, a magenta bean-shaped object and an oversized
object with normal good-bean colour and texture. The oversized long diameter is
14.4–17.6 mm, compared with the trained good range of 8.4–11.2 mm. No examples of these classes
were added to training.

Three 8 s runs use seed 42, 100 objects/s, a 60 ms latency floor and 0.06 N jets.
Each evaluates 660 objects: 444 good and 216 unknown, spawned in the conservative
0.8–7.4 s window. All resolve, and all three runs have zero late decisions.
The lower threshold 8.27 is exploratory, selected using camera data from seed 41.
The high threshold 1e9 disables the anomaly criterion for all observed scores;
the learned classifier still operates. The same seed does not imply identical
trajectories after changing actuation.

| Setting | Unknown reject criterion met | Unknown reaching reject bin | Good false ejects | Unknown / good spills |
| --- | ---: | ---: | ---: | ---: |
| Anomaly disabled | 84/216 = 38.89% | 71/216 = 32.87% | 11/444 = 2.48% | 15 / 6 |
| Trained threshold 18.4872 | 127/216 = 58.80% | 81/216 = 37.50% | 9/444 = 2.03% | 16 / 2 |
| Lower threshold 8.27 | 191/216 = 88.43% | 85/216 = 39.35% | 19/444 = 4.28% | 15 / 2 |

The lower threshold adds four unknown captures while losing ten more good beans.
Keep the trained threshold as the reference; this small, single-seed result does
not support deploying the lower candidate. Physical unknown-recall Wilson 95%
intervals overlap: disabled 26.95–39.39%, trained 31.32–44.12%, lower 33.08–46.00%.
These intervals describe bean counts within a run, not lot-to-lot uncertainty.

Oversize is the sharpest failure: at the trained threshold **0/73** reach reject;
at 8.27 only **5/73**, despite 63 targeted and 58 intersecting a jet. Better anomaly
flags alone do not solve this physical load. A separate oversize removal path
remains the next experiment; the UR5e extension is not implemented by this task.

Capture is not proof of a jet-caused ejection. With anomaly disabled, 26/77 plastic
chips reach reject with **zero** jet hits; at the trained threshold the figure is
27/77, of which only ten were both jet-hit and rejected. The chip shape already
tends to fall into reject. The magenta class at the trained threshold has 54/66
reject-bin captures, all 54 with recorded jet hits. The demo must identify its
selected object's hit and outcome, not relabel a spontaneous fall as successful
air-jet actuation.

- [Measured physical tradeoff](runs/generalization/physical-thresholds/physical_tradeoff.png).
- [Counts and confidence intervals](runs/generalization/physical-thresholds/summary.json).
- [Exact run commands](runs/generalization/physical-thresholds/run.sh).
- [Trained-threshold metrics](runs/generalization/physical-thresholds/trained/physics_metrics.json), [lower-threshold metrics](runs/generalization/physical-thresholds/low/physics_metrics.json), [classifier-only metrics](runs/generalization/physical-thresholds/disabled/physics_metrics.json).

All three numerical runs executed source SHA-256 `982b69bf9924d065679fe5c38fc637d90b20b80b406776e2da00e4d613e26db1`,
archived as [source_openset.py](runs/generalization/physical-thresholds/source_openset.py).
Each manifest records matching start/end source and model hashes. Later fixes
address video JSON serialization, trajectory lifetime and offline sweep coverage;
they do not alter these completed no-video physical runs. The first video export
hit a NumPy-boolean JSON error after encoding; that incomplete output is excluded
from the final evidence and the corrected demo is rerun.

### Open-set camera threshold curve

The final offline sweep uses seed 41, 8 s and 300 objects/s. Its curve is
conditional on **full, single-object detections**: 1,948 eligible objects
(1,429 good and 519 unknown), represented by 6,208 preserved camera observations.
A physical UID aggregates repeated views into the maximum anomaly score and mean
classifier reject probability across its whole passage. The real controller
finalizes earlier, near the camera centre or after missed observations; this
curve is not a replay of its decisions. Eligibility excludes the last 0.6 s of
spawns, so the curve and detection-coverage denominators refer to the same objects.

At the unchanged threshold 18.4872:

- Anomaly alone flags 319/519 unknowns (61.46%) and 0/1,429 good objects.
  The good-flag Wilson 95% upper bound is 0.27%; zero observed is not zero risk.
- Plastic: 100/100 flagged; magenta: 218/218; oversized green: **1/201 (0.50%)**.
- Classifier alone flags 256/519 unknowns (49.33%) and 24/1,429 good objects (1.68%).
- Classifier OR anomaly flags 327/519 unknowns (63.01%), with the same 24/1,429
  good flags. Flags are not physical ejects.

Detection coverage limits the colour result: only 100/221 eligible plastic chips
produce a usable full singleton observation, versus 218/238 magenta objects and
201/211 oversized objects. Of the plastic chips, 115/221 are never mapped into
any detected component, four are merged-only and two partial-only. The good
cohort has 1,429/1,542 usable singletons. No matched component does not establish
that the object's pixels never entered the camera. The full per-class coverage
ledger prevents a 100% conditional flag rate from becoming a 100% feed rejection claim.

- [Anomaly threshold curve](runs/generalization/openset/threshold_sweep.png).
- [All threshold rows and coverage](runs/generalization/openset/threshold_sweep.json).
- [Object scores](runs/generalization/openset/camera_objects.csv) and [per-observation scores](runs/generalization/openset/camera_observations.csv).
- [Model classes, test-only profiles and source hashes](runs/generalization/openset/manifest.json).

This generalizes to conspicuous synthetic colour changes better than to size-only
changes. Training contamination by repeated/merged views and the broad good-class
covariance are possible causes, not isolated causal findings. No real-camera,
real-coffee or hardware generalization is established by these experiments.

### Verified unknown-object demo

[Watch the 8× slow close-up](runs/generalization/openset/demo/unknown_8x_slow.mp4)
or [the complete 8 s mixed-stream video](runs/generalization/openset/demo/overview_h264.mp4).
The close-up uses the original frames from 1.18–1.42 s, crops the discharge region,
keeps the HUD, repeats frames for 8× slow playback and holds the last frame.
It adds no simulated event or interpolated motion. All 188 excerpt frames and
all 400 original H.264 frames decode.

The selected object is UID 88, a never-trained magenta bean-shaped object.
Its anomaly score is **194.58** against threshold 18.4872, and its classifier
reject probability is **0.958**. **Both criteria flag this example**; it is not
an anomaly-exclusive success. The controller queues and activates its pulse;
its own pulse intersects the object, and the object reaches the reject side.
The 102-sample identity-preserving trajectory crosses the splitter at
x=0.3417 m, z=0.4711 m at 1.338 s. Yellow is a display-only highlight restored
before the next inspection; class/UID and outcome labels are evaluation truth,
not controller inputs. This is a selected successful example, not a success-rate
estimate; the mixed-stream and threshold results above supply the denominators.

[Demo record](runs/generalization/openset/demo/demo_unknown.json),
[trajectory](runs/generalization/openset/demo/demo_unknown_trajectory.csv),
[source/video manifest](runs/generalization/openset/demo/manifest.json),
[slow-excerpt provenance](runs/generalization/openset/demo/unknown_8x_slow.json)
and [exact excerpt command](runs/generalization/openset/demo/make_slow_demo.sh)
make the clip attributable. No shared controller/vision/physics changes were
needed for either generalization experiment.

Final Standards and Spec reviews pass. One minor future-seed display limitation
remains: a merged decision with multiple unknown UIDs can nominate a different
featured UID from its first linked UID. This does not affect the independently
verified single-object UID 88 demo. Do not use a different-seed clip as evidence
without checking its own trajectory and featured UID.

### Published generalization evidence

The single camera contact sheet now includes all **19** examples: ten green,
six roasted and three unseen types. The added panel uses the same green model
and labels true type, trained-class guess and anomaly score. Its wrong-size
example scores 9.51, below the unchanged threshold 18.49; that failure remains
visible. Regenerate only this image with `python run_generalization.py contact-sheet`.

All six viewable files were uploaded with agent-fs, downloaded again and compared
byte-for-byte ([upload verification](runs/generalization/upload-verification.log)):

- [8× slow unknown ejection](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/generalization/openset/demo/unknown_8x_slow.mp4).
- [Full unknown-object video](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/generalization/openset/demo/overview_h264.mp4).
- [Green versus roasted confusion](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/generalization/comparison/confusion_matrices.png).
- [All 19 camera examples](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/generalization/comparison/camera_strip_contact_sheet.png).
- [Anomaly threshold curve](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/generalization/openset/threshold_sweep.png).
- [Physical ejection tradeoff](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/generalization/physical-thresholds/physical_tradeoff.png).
- [Complete raw evidence and scripts](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/generalization/evidence.tar.gz).

Final validation: **36 tests pass**, syntax checks and diff checks pass, and
independent Standards and Spec reviews pass. [Actual test output](runs/generalization/final-tests.log)
is also included in the PR body. No CI workflows are configured; this is local
verification. Human morning acceptance remains pending. Keep the existing
[fork PR #1](https://github.com/tarasyarema/hackspain/pull/1) draft and unmerged.

## 19 September: sensor realism and economics

Task `29f7117e-b13a-481c-adcd-8a249331141a` continues the same branch and draft
fork PR #1 from `6515364`. Shared production defaults remain unchanged.
The implementation plan is [SENSOR_PLAN.md](SENSOR_PLAN.md).

### Assumptions and execution constraints

No camera model, measured shutter exposure, electron conversion gain, read-noise
calibration or belt encoder trace is present in the repository. The controller's
4 ms exposure-plus-transfer floor does not specify shutter duration. The sweep
therefore tests explicit assumed exposures/noise/jitter, not a calibrated digital
twin. At 3 m/s and 4,000 px/m, 100 µs integrates 0.30 mm (1.2 px) of travel and
500 µs integrates 1.50 mm (6 px). Image rows follow travel; the 2,080 columns span
the belt width. Brightness and gradient perturb the rendered RGB signal rather
than reconstructing raw sensor radiometry.

Native background-agent launch failed with a missing parent-thread error. The
installed Codex executor provides the implementation/review fallback. Its initial
workspace sandbox could not create a namespace; the restarted executor uses the
session's existing unrestricted filesystem mode. Work remains scoped to this
worktree. These infrastructure failures do not establish a simulation failure.

Economics uses a declared 0.20 g/object approximation, 80% duty cycle and measured
effective feed rate. EUR 6/kg unsorted and EUR 0.50/kg additional accepted-stream
value are hypothetical scenario prices, not market observations. The premium is
conditional on buyer acceptance of the remaining defect level. Rejected material
has zero assumed salvage, and spilled/unresolved material is unsold. Operating,
labor and capital costs are excluded, so the reported uplift is not net profit.
The complete ledger must count all lost mass, including good-bean spills.

### Historical rate-sweep economics

**Headline: 576.0 kg/h input, 497.6 kg/h accepted, EUR −221.76/h conditional
uplift before operating costs** at the historical 1,000 beans/s setting.
This reuses the earlier 0.09 N rate sweep, not the later 0.06 N sensor sweep.
The arithmetic is:

- Input: `1000 objects/s × 3600 s/h × 0.00020 kg/object × 0.80 = 576 kg/h`.
- Accepted: `576 × 2246/2600 = 497.575 kg/h`.
- Grade-premium credit, conditional on buyer acceptance:
  `497.575 kg/h × EUR 0.50/kg = EUR 248.788/h`.
- Lost unsorted-feed opportunity value:
  `(576 − 497.575) kg/h × EUR 6/kg = EUR 470.548/h`.
- Uplift: `248.788 − 470.548 = EUR −221.760/h`.

It rejects 164/369 defects (44.44%), or **36.332 kg/h** under equal mass.
It falsely ejects 128/2,231 good beans (5.74%), or **28.357 kg/h**, and spills
another **3.323 kg/h** of good beans. Total good loss is **31.680 kg/h**.
No spilled defect is credited as a successful rejection. The accepted stream
still contains 158/2,246 policy defects (**7.03% by count**), down from 14.19%
incoming; whether that earns any grade premium is unknown. These are policy
labels in simulation, not a measured coffee grading score.

Break-even requires **EUR 0.946/kg additional accepted-stream value**, before
operating costs, at zero salvage. With no premium the same run loses EUR
470.55/h against selling all input unsorted. Neither value demonstrates a
business case. Salvage contracts, measured class masses, real buyer grades and
actual labor/compressor costs are the next inputs to obtain.

| Requested objects/s | Effective objects/s | Input kg/h | Accepted kg/h | Conditional uplift EUR/h | Break-even premium EUR/kg |
| --- | --- | --- | --- | --- | --- |
| 500 | 500.0 | 288.00 | 251.45 | −93.60 | 0.872 |
| 1,000 | 1,000.0 | 576.00 | 497.58 | −221.76 | 0.946 |
| 2,000 | 2,000.0 | 1,152.00 | 947.52 | −753.12 | 1.295 |
| 3,000 | 2,214.4 | 1,275.48 | 1,038.20 | −904.60 | 1.371 |

All rows use the same prices, duty and mass assumptions. Effective rates are
simulated admitted feed, not real-time processing capability. The source's
throughput window is 0.8–4.0 s and its completed-outcome eligibility window is
0.8–3.4 s; extrapolation assumes those outcome fractions remain representative.

[Ledger JSON](runs/economics/summary.json), [CSV](runs/economics/summary.csv),
[plot](runs/economics/summary.png) and [assumptions](configs/economics.json)
preserve counts, input hashes and the calculation. Reproduce from this directory:

```bash
.venv/bin/python economics.py --config configs/economics.json --output runs/economics
```

### Sensor validation corrections

The first sensor pass is diagnostic only and is excluded from final evidence.
Independent Standards review found stale source could pass resume validation,
shape bounds understated capsule/rotated-box footprints, and some non-finite
config values passed validation. The nominal 100 µs exposure also rounded 1.2 px
to an identity kernel; a six-row 500 µs kernel shifted the centroid by 0.5 px
without adjusting the timestamp. The corrected pass uses centered fractional
box integration and checks source identity before and after each run.

The first executor stopped on a model-capacity error, after the high-feed
reference and during the crowded case. A replacement executor resumes from
preserved code and archives the preliminary outputs before regeneration. No
preliminary nominal-exposure result is used to claim robustness at 100 µs.

The final harness separates the product stream from placement randomness. Each
planned object has reproducible class, size, appearance and velocity; a blocked
object remains pending. The 1,000/s comparison rejects a summary unless the
eligible product-identity hashes match. The high-feed group reports admission
limits explicitly and does not claim a paired cohort if crowding changes which
objects arrive in the evaluation window. These are matched product identities,
not a claim that trajectories remain identical after jets or belt speed change.

Completed cases now use a checksum chain: `completion.json` binds `metrics.json`,
which binds decoded inspection images, decision/evidence files and the feed
manifest. Source, configuration, frozen model and software versions are recorded.
Changing source or configuration in an already-running process is rejected.
Use `--rerun` to archive a completed/incomplete case and actually recompute it.
The full corrected regression suite passes **58 tests** before the final batch.

## 19 September 05:02 UTC: final sensor realism and economics

**Result: stabilize illumination first; the current physical sorter does not yet justify a positive value claim.** The full 12-case batch completed on the preserved final source. All nine 1,000/s cases have the same 2,600 eligible products (376 defects, 2,224 keep), verified by product-identity hash. Crowded admission changes the high-feed cohorts, so those comparisons are descriptive, not causally paired.

| Scenario | Effective beans/s | Eligible | Physical accuracy | Defect recall | Good false eject |
|---|---:|---:|---:|---:|---:|
| base-1000 | 1000.00 | 2600 | 88.65% | 48.67% | 4.14% |
| brightness-minus30-1000 | 1000.00 | 2600 | 69.81% | 34.84% | 22.80% |
| brightness-plus30-1000 | 1000.00 | 2600 | 76.42% | 47.61% | 17.45% |
| gradient-30-1000 | 1000.00 | 2600 | 83.15% | 42.82% | 8.99% |
| exposure-nominal-100us-1000 | 1000.00 | 2600 | 86.96% | 48.94% | 5.98% |
| exposure-stress-500us-1000 | 1000.00 | 2600 | 77.73% | 44.68% | 15.78% |
| noise-nominal-1000 | 1000.00 | 2600 | 87.42% | 52.93% | 6.43% |
| noise-stress-1000 | 1000.00 | 2600 | 83.96% | 51.06% | 9.80% |
| belt-jitter-5pct-1000 | 1000.00 | 2600 | 89.85% | 46.81% | 2.61% |
| high-feed-reference-3000 | 3000.00 | 7800 | 80.74% | 38.81% | 8.55% |
| touching-3000 | 1192.81 | 3107 | 82.46% | 39.43% | 9.61% |
| combined-assumed-worst-3000 | 1201.25 | 3136 | 68.59% | 31.36% | 24.40% |

Accuracy counts correctly accepted keep objects plus rejected defects over all eligible objects. Recall uses eligible policy defects; good false eject uses eligible keep objects. Spilled and unresolved objects remain in the denominators; spills are separately charged in the economics. Per-case Wilson intervals and all counts are in [summary.json](runs/sensor-realism/summary.json). One run per scenario means these intervals do not measure between-run uncertainty.

### Which failure matters

- **Illumination is the worst measured single factor.** At −30% gain, physical accuracy loses 18.85 percentage points and good false eject rises 18.66 points. Singleton vision top-1 falls 97.83% → 67.56%; observation-level good reject flags rise 1.47% → 32.06%. This is a substantial vision failure before actuation. +30% gain and the horizontal 0.7→1.3 gain gradient also damage vision. Uniform, regulated lighting and a measured photometric robustness check are the first hardware priorities.
- **Exposure blur is the next large vision penalty.** Assumed 100 µs and 500 µs shutters at 3 m/s and 4,000 px/m produce 1.2 px and 6 px centered integration kernels. Singleton vision top-1 is 95.98% and 72.60%; physical good false eject is 5.98% and 15.78%. Hardware shutter duration is unknown. The existing 4 ms exposure/transfer pipeline floor is not an exposure measurement. Centered integration uses the exposure midpoint and does not add an artificial half-pixel tracking shift.
- **Noise also hurts vision.** Gaussian shot-noise approximation plus read noise uses assumed 16 electrons/DN + 1 DN RMS and 4 electrons/DN + 3 DN RMS. Singleton top-1 is 95.42% and 88.81%, with physical good false eject 6.43% and 9.80%. The occasional recall increase is not evidence that noise improves a sorter. These are RGB-space approximations, not a calibrated raw sensor or photon simulation.
- **Jitter changes transport, not the controller speed estimate.** The simulated belt spans 2.850–3.150 m/s at assumed 8 Hz; the controller retains 3 m/s. Vision stays strong (98.14% singleton top-1), while physical recall is 46.81% versus 48.67%. Own-pulse-hit decisions that end in rejection fall 290/343 → 250/354. Good false eject also falls, so this case does not establish overall harm or a ±5% acceptance bound. Timing, trajectory and capture remain coupled; zero late no-fire decisions does not prove correct pulse intersection.
- **Crowding is primarily an admission/segmentation/physical limitation here.** The clean high-feed reference sustains 3,000/s, with 31.72% ever merged. Constraining feed to ±60 mm with a conservative 0.2 mm non-penetrating spawn gap admits 1,192.81/s and gives 38.46% ever merged. Contact is real: 1,979 timesteps and 2,723 unique lifetime-UID contact pairs. Singleton top-1 remains 97.16%; all-observation defect-action recall is 81.67%. This models overlapping camera blobs and physically touching objects, not impossible interpenetrating spawns. The three high-feed eligible cohorts differ.
- **Combined stress is mixed vision and plant failure.** It admits 1,201.25/s, with 66.79% singleton vision top-1, 61.56% observation defect-action recall, 68.59% physical accuracy, 31.36% physical defect recall and 24.40% good false eject. There are 598 associated activated own-pulse misses; 520/722 own-hit decisions lead to rejection. These diagnostics cannot allocate all loss to one cause.

All cases keep the frozen green classifier, specialty policy, seed 1, 0.06 N jets, 250 Hz simulated camera cadence, four simulated seconds and a 60 ms minimum total controller latency. Synthetic transform cost is excluded from detector/controller CPU and reported separately. The clean reference overruns the 4 ms camera interval on 996/1,000 frames. Camera backlog is not modeled: these runs are not real-time or hardware throughput validation.

### One economic number, with every assumption

**Clean reference: 576.0 kg/h input, 502.9 kg/h accepted, EUR −187.20/h incremental value before costs.** Assume every object, including fragments and foreign matter, has equal 0.20 g mass; use measured 1,000 objects/s and 80% duty. Thus input = 1,000 × 3,600 × 0.00020 × 0.8 = 576 kg/h. Use the observed eligible outcome fractions to partition that mass. This deliberately differs from the simulator’s variable-mass feed estimate.

Assume the unsorted lot sells for EUR 6/kg and the accepted stream earns EUR 0.50/kg extra only if a buyer accepts its grade. Reject salvage, spilled/unresolved sale value are zero. Operating, labor and capital costs are excluded. The accepted stream retains 6.52% policy defects by count (incoming 14.46%); a real grade premium is unverified. Revenue change = 502.8923 × 6.50 − 576 × 6 = EUR −187.20/h. At zero premium the change is EUR −438.65/h. Break-even needs EUR 0.872/kg additional accepted-stream value before costs.

The clean case rejects 40.542 kg/h of defects and falsely rejects 20.382 kg/h of good objects. Another 2.215 kg/h of good objects and 9.969 kg/h of defects spill; none of the spilled defects receives a rejection benefit credit. There are no unresolved clean-reference objects. Total good loss is 22.597 kg/h. This is a conditional lot-value ledger, not profit or a price forecast.

| Scenario | Input kg/h | Accepted kg/h | Defects rejected kg/h | Total good lost kg/h | Value change EUR/h | Break-even premium EUR/kg |
|---|---:|---:|---:|---:|---:|---:|
| base-1000 | 576.00 | 502.89 | 40.54 | 22.60 | -187.20 | 0.872 |
| belt-jitter-5pct-1000 | 576.00 | 517.07 | 38.99 | 14.18 | -95.04 | 0.684 |
| brightness-minus30-1000 | 576.00 | 416.94 | 29.02 | 119.63 | -745.92 | 2.289 |
| brightness-plus30-1000 | 576.00 | 434.44 | 39.66 | 92.16 | -632.16 | 1.955 |
| exposure-nominal-100us-1000 | 576.00 | 493.81 | 40.76 | 32.57 | -246.24 | 0.999 |
| exposure-stress-500us-1000 | 576.00 | 445.74 | 37.22 | 82.19 | -558.72 | 1.753 |
| gradient-30-1000 | 576.00 | 480.52 | 35.67 | 49.40 | -332.64 | 1.192 |
| noise-nominal-1000 | 576.00 | 488.27 | 44.09 | 33.23 | -282.24 | 1.078 |
| noise-stress-1000 | 576.00 | 472.76 | 42.54 | 51.62 | -383.04 | 1.310 |
| touching-3000 | 687.06 | 578.48 | 39.58 | 59.71 | -362.22 | 1.126 |
| combined-assumed-worst-3000 | 691.92 | 502.61 | 31.55 | 148.27 | -884.54 | 2.260 |
| high-feed-reference-3000 | 1728.00 | 1417.18 | 103.68 | 169.26 | -1156.32 | 1.316 |

The [historical four-rate ledger](runs/economics/report.md) remains separate: its 1,000/s EUR −221.76/h result used the earlier 0.09 N setting and a different cohort. The final sensor ledger above uses 0.06 N. All negative scenarios are retained; source metric hashes and complete rejected/spilled/unresolved partitions appear in [sensor economics JSON](runs/sensor-economics/summary.json).

### Deliverables and validation

- [Sensor plot](runs/sensor-realism/summary.png), [camera contact sheet](runs/sensor-realism/camera_contact_sheet.png), [case table](runs/sensor-realism/REPORT.md), and six full-resolution annotated inspection sheets in each scenario directory. [Economic plot](runs/sensor-economics/summary.png) and [ledger](runs/sensor-economics/report.md). README commands regenerate both suites.
- All 12 completion checks pass; each completion binds metrics, and metrics bind decoded inspection images, camera evidence, decisions and feed manifests. Resuming validates source/config/model identity. Exact 13-file runtime source snapshot and scenario config are in `runs/sensor-realism/source/`; they match the final runtime. Shared simulation/controller/detector/model code remains unchanged from 6515364. The frozen tracked model SHA-256 is `0b9e164cbf4df18f904f30fde38e0b7ed352877653849cc55da25e6f143051c8`.
- **65 tests pass.** [Full output](runs/sensor-realism/final-tests.log), [resume validation](runs/sensor-realism/resume-validation.log), and separate [Standards/Spec findings and resolutions](runs/sensor-realism/REVIEW.md) are preserved. Compile and scoped-diff checks pass. The fork has zero GitHub Actions workflows; there is no CI result to claim. PR #1 remains draft and unmerged.

Remaining hardware blockers: measure actual illumination/exposure/noise and encoder jitter; validate air-jet intersection/capture and feeder singulation over longer independent runs; model camera backlog; obtain buyer grade acceptance and prices. No blocking questions were needed. Earlier incomplete development runs were archived outside the published final suite before recomputation.


## 19 September 06:07 UTC: UR5e infeed picking

**Result: 2/2 idealized removals at 0.10 m/s; 3/4 at 0.28 m/s in a burst.**
This is a bounded kinematic prototype, not verified contact grasp or hardware
performance. It continues branch `coffee-sorter-closed-loop` and draft fork
PR #1. The implementation plan is [PICKING_PLAN.md](PICKING_PLAN.md).

| Case | Infeed speed | Duration | Oversize inputs | Ideal placements | Misses | Workspace exclusions | Maximum queue wait |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Nominal | 0.10 m/s | 18 s | 2 | 2 | 0 | 0 | 0 s |
| Burst | 0.28 m/s | 11 s | 4 | 3 | 1 | 0 | 3.38 s |
| Workspace policy | 0.10 m/s | 5 s | 1 | 0 | 0 | 1 | 0 s |

Queue residence covers all selected objects, including the burst object lost
before service: it was selected at 2.48 s and missed at 5.86 simulated seconds.

All seven oversize inputs remain accounted for. Neither nominal nor burst
selected its two non-large controls; four controls cannot establish a false
selection rate. No timeout occurred. Queue delay and a missed burst object are
observed together; this experiment does not isolate overload causality. The
workspace case rejects an object outside the configured x bound and makes no
claim about the arm's certified physical reach. Each case is one constructed,
deterministic run, not an independent-seed robustness study.

The pinned Menagerie UR5e runs actual mink/DAQP differential IK. The independent
infeed uses y for travel and x for lane; the existing optical sorter and its
3 m/s defaults remain unchanged. Shared `render.hud` labels each video frame.
Camera capture is class-independent at 12.5 Hz, with a red-foreground mask and
45 mm measured-footprint selection threshold. True identities assist association
and known object heights assist targeting. General foreign-matter perception,
occlusion and real camera calibration are not established by these red pieces.

The joint-position ranges come from the model; configured joint-speed limits
are 2.094 rad/s for the shoulder joints and 3.142 rad/s for the others. IK runs
at 20 ms steps; saved joint/end-effector trajectories sample every 100 ms.
Attachment requires an 18 mm tolerance, followed by lift and a reject pose
within 12 mm; pick timeout is 3.2 s. Attachment and final bin placement are
idealized, fixture collisions are disabled, and no gripper contact, actuator
dynamics, payload slip, collision avoidance or dynamic bin capture is modeled.
The infeed keeps moving. A credited success is the entire idealized sequence,
not tool proximity alone. The cheapest next physical check is a guarded
stationary-object grasp-and-lift trial, followed by measured moving-target
tracking and collision checks before claiming removal reliability.

The initial development run reported 2/2 nominal and 2/4 mixed stress outcomes.
Review found an oracle-gated camera trigger and incomplete artifact safeguards;
that preliminary suite was archived outside the final directory. The corrected
burst case and separate workspace case above replace it. Reruns now refuse
nonempty output unless explicitly archived, validate robot/source contents,
reject nonfinite solver state and write a completion hash inventory last.
The report exposes the remaining oracle inputs and collision/placement limits.

All **72 project tests pass**, including seven picking tests with the actual
robot model required. The original environment also retains its 65 passing
tests, with an explicit optional-module skip when picking dependencies are
absent. Package versions are pinned; the optional root-free Ubuntu Mesa recipe
is explicitly unpinned. No GitHub Actions workflows are configured.

- [Report and reproduction](runs/ur5e-infeed/REPORT.md), [all metrics](runs/ur5e-infeed/metrics.json), [outcome plot](runs/ur5e-infeed/outcomes.png), [test output](runs/ur5e-infeed/tests.log), [completion inventory](runs/ur5e-infeed/completion.json).
- [Nominal video](runs/ur5e-infeed/nominal/overview.mp4), [burst video](runs/ur5e-infeed/burst/overview.mp4), [workspace-policy video](runs/ur5e-infeed/workspace/overview.mp4). Each case also contains its input manifest, events, trajectory and contact sheet.
- [Separate Standards/Spec review and resolutions](runs/ur5e-infeed/REVIEW.md) and [independent saved-evidence audit](runs/ur5e-infeed/validation.log).
- The final morning review's original 18,614 bytes are preserved. Only the requested short addendum is appended. PR #1 stays draft and unmerged for human review; no Slack post.
