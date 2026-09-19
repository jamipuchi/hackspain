# Magnet sorter adaptive routing: independent results report

Reporter: Fable (did not implement or run the experiment). Implementer and runner: Astra, task `1138c1c4`. Environment: Jacknife, task `6de2167b`. Date: 2026-09-19 UTC.

Every number below was recomputed from [adaptive-evidence.json](adaptive-evidence.json) and [completion-guard-replay.json](completion-guard-replay.json) on branch `swarm/magnet-adaptive-routing` at `70cf835`. Where Astra's [adaptive-report.md](adaptive-report.md) states a number, I checked it against the artifact. All matched. Where I only have Astra's word, I say so.

## Verdict

- **The experiment ran.** Environment and run were both go. Phase A was verified on a second container: the bootstrap script rebuilt the OSMesa stack, rendered both cameras with the expected statistics, and the focused suite passed 22/22. The extended suite passed 36/36 under both builds on this container.
- **Adaptive routing did not beat Gemini-only.** Same accuracy (15/27 each), worse tail latency, and its cost advantage is not established once one unpriced HTTP 429 is counted at its reservation ceiling.
- **No configuration completed the task.** 0 of 9 final runs sorted all nine parts. 0 of 9 reached completion eligibility. This is not demo-ready.
- **The dominant failure is the detector gate, not the model choice.** 8 of 9 final runs stopped on "unresolved geometry or categorical camera disagreement". On seeds 17 and 29 all three configurations scored exactly 4/9, and Gemini was never invoked in adaptive seed 29. Model routing cannot fix what the geometry gate rejects.
- Astra's recommendation stands: use Gemini-only (`--routing strong`) as the reference configuration for the next simulation iteration. Do not promote the adaptive policy or change the default agent.

## Final paired sweep

Seeds 4, 17, 29. THEKER v1. One trial per configuration per seed. Nine ferrous parts scored independently by the existing simulator scorer. Policy SHA-256 `d745c9ac…`.

| Configuration | Correct | Fully sorted | Vision median / p95 | Agent wall median / p95 | Recorded cost | Unpriced 429s |
|---|---:|---:|---:|---:|---:|---:|
| cheap (GLM 5.3 Flash) | 9/27 | 0/3 | 6.09 / 10.82 s | 38.4 / 42.8 s | $0.000768 | 0 |
| strong (Gemini 3.8 Flash) | 15/27 | 0/3 | 4.20 / 7.95 s | 32.9 / 78.5 s | $0.046335 | 0 |
| adaptive (GLM, escalate to Gemini) | 15/27 | 0/3 | 4.85 / 22.80 s | 38.0 / 171.8 s | $0.024718 | 1 |

Per seed, correct out of 9: cheap 1 / 4 / 4, strong 7 / 4 / 4, adaptive 7 / 4 / 4 (seeds 4 / 17 / 29). Only seed 4 separates the configurations. Cheap seed 4 stopped early on a failed action after two vision requests.

Escalation actually happened in adaptive (vision requests by model, from the request ledger):

- seed 4: 10 GLM, 9 Gemini. Same score as strong, at 2.2x the wall time.
- seed 17: 4 GLM, 1 Gemini, escalated after a 429. Same score as cheap and strong.
- seed 29: 4 GLM, 0 Gemini. Behaved identically to cheap. Same score as strong.

`p95` on three run durations is the maximum. Vision latency includes one response whose labels failed validation. Agent wall time excludes simulator setup and calibration.

## Cost

Ledger: 230 requests across three sweeps of 9 runs. 220 accounted, 10 rejected. All 10 rejections are HTTP 429 from GLM's upstream provider (DeepInfra via OpenRouter), none from Gemini or Jev.

- Recorded: $0.191117 ($0.185522 Gemini, $0.004273 GLM, $0.001322 Jev token estimate).
- Rejected requests hold their $0.13 reservation each: $1.30.
- Conservative ceiling: $1.491117. Peak in-flight exposure: $2.299388. Cap: $5.
- The true charge for the 10 rejections is unknown. It lies between $0 and $1.30.

Cost comparison between strong and adaptive is therefore inconclusive: adaptive recorded $0.0247 against strong's $0.0463, but adaptive's ceiling with its one rejection is $0.1547.

## Development sweeps (preserved, not extra samples)

| Revision | cheap | strong | adaptive | Notes |
|---|---:|---:|---:|---|
| v1 | 4/27 | 13/27 | 9/27 | all 3 cheap runs and 1 adaptive run died on 429 |
| v2 | 7/27 | 12/27 | 15/27 | 2 cheap runs died on 429; 1 strong run died on a classification contract error |
| final | 9/27 | 15/27 | 15/27 | 1 adaptive run hit a 429 and recovered by escalating |

Same three seeds reused across revisions, so these are tuning data, not a held-out benchmark. Provider availability changed between sweeps. Differences between revisions cannot be attributed to code alone.

## What was verified independently

- Bootstrap from Jacknife's shared prefix on this container: exit 0, 17.7 s, both cameras rendered (cam_A mean 99.16 std 40.20, cam_B mean 95.51 std 47.39), 22/22 focused tests under both builds.
- Extended suite: 36/36 under default and `SORTER_BUILD=theker_v1`. `git diff --check` clean.
- Every per-run and per-configuration figure in Astra's report against `adaptive-evidence.json`. Per-run rejection counts against the ledger. Zero mismatches.
- Completion-guard replay: 9/9 runs pass all six checks with zero paid calls. This verifies the post-sweep guard did not change behaviour on recorded histories. It does not add new measurements.
- The shared-renderer fix in `camera.py` and `run_demo.py` is present and matches the phase A diagnosis.

## What was not measured

- **An Astra (OpenAI) baseline.** `OPENAI_API_KEY` is absent from the swarm. HANDOFF item 5, Gemini for routine crops with Astra for hard observations, was never tested. The earlier 8/9 single-seed Astra and Gemini runs are not comparable: different policy, one seed, viewer pacing.
- **Jev's contribution.** No ablation with and without Jev ranking. Jev made 79 calls at a median of about 0.6 s; whether it changed any outcome is unknown.
- **Variance.** Three seeds, one trial each. No repeat runs. Nothing here supports a statistical claim about reliability or savings.
- **The detector's recall.** 0 runs reached completion eligibility, so the eligibility logic was never exercised on a positive case. Why the geometry gate stalls at 4/9 on seeds 17 and 29 is not diagnosed in the artifacts.
- **Physical hardware.** Simulation only.
- **Provider rate limits.** Ten 429s in 230 requests, all GLM. Whether cheap is viable at all depends on DeepInfra availability, which was not controlled.

## Artifacts and gaps

- Committed on the branch: `adaptive-report.md`, `adaptive-evidence.json`, `completion-guard-replay.json`, `model-catalog.json`, `bootstrap.md`, `bootstrap.sh`, render PNGs.
- Astra's agent-fs uploads: `/reports/hackspain/2026-09-19-magnet-routing/adaptive-report.md` and `evidence.zip` (traces, source snapshots). Not re-verified here.
- **Raw archive is not reachable.** Astra's output points to `/workspace/personal/artifacts/magnet-routing-20260919/all-raw-runs.tar.gz` as a shared-fs attachment. That path is Astra's per-agent workspace. It does not exist on this container and will not be reachable by Taras either. Provisional: it may still exist inside Astra's container. Rendered frames and full run directories are therefore available only through `evidence.zip`, if it contains them.
- Historical `meta.json` files record `vision_provider=openai` and `effort=medium`. Astra's report explains these as stale CLI defaults; the ledger shows OpenRouter at low reasoning. I did not inspect the raw run files to confirm this, since they are not on this container.

## Recommended next step

Diagnose the geometry gate before spending on models. Take seeds 17 and 29, replay the recorded observations with zero paid calls, and find why the fifth part is rejected. Until that gate passes, no vision model will move the score.

PR: https://github.com/tarasyarema/hackspain/pull/2 (base `codex/jev`, fork only). Not merged. Taras reviews.
