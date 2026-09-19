# Magnet sorter: paired adaptive-routing evaluation

**Recommendation: use Gemini-only (`--routing strong`) as the reference for the next simulation iteration. Keep the existing default agent unchanged.** None of the evaluated configurations reliably finishes the task; the calibrated detector and visual contract failures need work before promoting this policy. Adaptive routing has not demonstrated a reliable end-to-end improvement.

Repository: `tarasyarema/hackspain`. Branch: `swarm/magnet-adaptive-routing`. [PR #2](https://github.com/tarasyarema/hackspain/pull/2), based on `codex/jev`; not merged. Evaluation date: 2026-09-19 UTC.

## Final paired results

Each configuration used seeds **4, 17, 29**, THEKER v1, identical OSMesa A/B cameras, low vision reasoning, headless pacing, calibrated silhouette geometry, 60 action steps maximum and $1.50 per-run reservation limits. One common $5 ledger covers all three development sweeps. Nine ferrous parts are scored independently per run.

| Configuration | Seed | Correct / 9 | Agent wall, s | Vision / Jev attempts | Recorded cost | Unpriced HTTP rejections |
|---|---:|---:|---:|---:|---:|---:|
| cheap | 4 | 1/9 | 19.97 | 2 / 2 | $0.000202 | 0 |
| strong | 4 | 7/9 | 78.50 | 10 / 4 | $0.030150 | 0 |
| adaptive | 4 | 7/9 | 171.80 | 19 / 6 | $0.022829 | 0 |
| cheap | 17 | 4/9 | 38.39 | 4 / 2 | $0.000280 | 0 |
| strong | 17 | 4/9 | 32.85 | 4 / 3 | $0.007673 | 0 |
| adaptive | 17 | 4/9 | 34.98 | 5 / 3 | $0.001615 | 1 |
| cheap | 29 | 4/9 | 42.76 | 4 / 3 | $0.000286 | 0 |
| strong | 29 | 4/9 | 29.84 | 4 / 3 | $0.008513 | 0 |
| adaptive | 29 | 4/9 | 37.99 | 4 / 3 | $0.000273 | 0 |

| Configuration | Total correct | Fully sorted runs | Completion-eligible runs | Vision median / p95, s | Agent-wall median / p95, s |
|---|---:|---:|---:|---:|---:|
| cheap | 9/27 (33.3%) | 0/3 | 0/3 | 6.09 / 10.82 (n=10) | 38.39 / 42.76 |
| strong | 15/27 (55.6%) | 0/3 | 0/3 | 4.20 / 7.95 (n=18) | 32.85 / 78.50 |
| adaptive | 15/27 (55.6%) | 0/3 | 0/3 | 4.85 / 22.80 (n=27) | 37.99 / 171.80 |

Vision distributions include every response with accounted usage, including a response whose labels fail validation. HTTP rejections are counted as attempts but excluded from successful-response latency distributions. `p95` uses nearest rank; for three run durations it is the maximum. Agent wall time is the existing `run_demo` timer, which excludes initial simulator setup and calibration. It includes perception, model calls and arm motion. Jev distributions and every returned usage record are in [adaptive-evidence.json](adaptive-evidence.json).

### Stop reasons

- cheap, seed 4: stopped: failed actions; no retry without renewed part identity.
- strong, seed 4: stopped: unresolved geometry or categorical camera disagreement.
- adaptive, seed 4: stopped: unresolved geometry or categorical camera disagreement.
- cheap, seed 17: stopped: unresolved geometry or categorical camera disagreement.
- strong, seed 17: stopped: unresolved geometry or categorical camera disagreement.
- adaptive, seed 17: stopped: unresolved geometry or categorical camera disagreement.
- cheap, seed 29: stopped: unresolved geometry or categorical camera disagreement.
- strong, seed 29: stopped: unresolved geometry or categorical camera disagreement.
- adaptive, seed 29: stopped: unresolved geometry or categorical camera disagreement.

## Cost and hard cap

Across **27 paid-run attempts**, OpenRouter returned **$0.189795** of billed usage. Jev returned input-token usage costing **$0.001322** at the configured $0.042 per million input tokens (output free). Combined recorded cost: **$0.191117**.

**The exact total charge is unknown.** 10 explicit HTTP rejections returned no usage cost. They retain $1.300000 in reservations, rather than being treated as free. The conservative final total is **at most $1.491117** under the recorded price limits. Peak exposure including an in-flight reservation was **$2.299388**, below the $5 cap. No paid calls were made outside this ledger. Renderer probes, tests and the final guard replay made no paid calls.

OpenRouter reservations cover its entire catalog context at request-enforced `provider.max_price`, plus 2,048 output tokens: $0.13 for GLM and $0.81 for Gemini. Jev reserves $0.003 for its 64k-token contract at the configured rate. The [catalog snapshot](model-catalog.json) records the [OpenRouter source](https://openrouter.ai/api/v1/models). A request is reserved on disk before dispatch. Unreturned/unknown responses block further paid work; explicit HTTP rejections retain their full ceiling and allow another configuration. There are no client retries. This ledger is for a sequential process, not concurrent writers.

## What was implemented

- One shared MuJoCo renderer fixes corrupted OSMesa camera contexts. A no-spend live test measured 0.57 mm and 0.62 mm ArUco fit error for A and B; the provisioned libraries were reused.
- The opt-in policy rectifies camera pixels at 0.5 mm/pixel, segments silhouettes against the visible blue card, checks whole-contour centroids against fixed workspace/container geometry and requires an unambiguous A/B match within 8 mm. Both cameras must pass a 2 mm marker-fit gate. Motion uses camera A’s calibrated centroid, avoiding an average with oblique-view height parallax.
- Models receive labelled image crops and return only kind/material categories for an exact allowlist of IDs. Extra coordinates, missing/extra IDs and invalid labels are rejected. No model confidence validates geometry or completion.
- Code maps kinds to containers. Jev ranks only already-validated candidate IDs when there is more than one option. It cannot repair geometry, produce coordinates, select completion or change destinations. Physical recovery remains a stop on failure; no model-selected physical recovery was added.
- Cheap = GLM 5.3 Flash. Strong = Gemini 3.8 Flash. Adaptive starts with GLM and escalates on measured cross-camera label disagreement, an unknown category, or an explicit HTTP 429/503. Invalid geometry is never repaired by escalation.
- A failed pickup/placement stops the run, preventing a displaced part from being retried under a new location. A candidate reappearing near a previously successful pickup blocks completion. Completion eligibility requires two fresh calibrated observations, no unresolved observed candidates and no failed actions. It is not proof of detector recall or correct bin contents; independent simulation scoring remains mandatory.
- Sweep manifests pin the policy source, seeds, configurations and ledger path. Resume refuses mixed policy revisions. Every failure remains in the results.

## Preserved development results

These are development sweeps, not extra samples of the final policy. No bad run was removed or substituted. The same three seeds were reused for iteration, so they are not a held-out benchmark.

| Revision | Configuration | Seed 4 | Seed 17 | Seed 29 | Total |
|---|---|---:|---:|---:|---:|
| v1 | cheap | 0/9 | 1/9 | 3/9 | 4/27 |
| v1 | strong | 3/9 | 5/9 | 5/9 | 13/27 |
| v1 | adaptive | 3/9 | 2/9 | 4/9 | 9/27 |
| v2 | cheap | 3/9 | 0/9 | 4/9 | 7/27 |
| v2 | strong | 4/9 | 4/9 | 4/9 | 12/27 |
| v2 | adaptive | 7/9 | 4/9 | 4/9 | 15/27 |

V1 clipped silhouettes at the workspace boundary, turning a lid wall into an extra camera-B candidate and blocking refill. V2 detected whole silhouettes before checking their centroids and added fallback on provider rejection. Final evaluation added stop-on-failed-action and rejected mixed-revision resume. Provider availability and stochastic labels also varied between sweeps, so cross-revision differences cannot be attributed solely to code changes.

## Limits and assumptions

- Three simulation seeds, one trial per configuration per revision. This cannot establish general sorting reliability or statistically reliable savings. No tested final run should be presented as a successful full-task demo.
- The detector assumes a blue card and a known installation. Marker/workspace/container constants are calibration and controller configuration. Dynamic simulator object labels, positions and target assignments never enter the policy; they are used only by the existing independent scorer.
- Part occlusion, hardware touching a lid, split/merged contours and oblique-view parallax remain failure modes. Marker-fit residual is not object-centroid accuracy. Two cameras can miss the same part. The 8 mm matching tolerance is a gate, not a proof of magnetic capture.
- The inherited scorer and physical simulation are unchanged. Results are not directly comparable with the earlier 8/9 Astra/Gemini single-seed runs: those used a different policy and conditions. `OPENAI_API_KEY` was absent, so no direct Astra experiment was run. `TYPESAFE_API_KEY` and `OPENROUTER_API_KEY` were supplied through the authorized process environment; no secret values were saved.
- Explicit 429 failures came from upstream GLM providers. Reported costs omit their unknown charges; the retained ceilings cover them. The first rejection predates response-error logging, so its body/latency is unavailable; its HTTP status, failure result and full reservation are preserved.

Historical `meta.json` files retained the CLI defaults `vision_provider=openai` and `effort=medium`. Those two fields were wrong for the opt-in policy: its preserved source and request ledger establish OpenRouter with low reasoning for every evaluated vision request. The current runner records those fields correctly. Original run files remain unaltered as evidence.

## Verification and reproduction

Original focused suite: 22 tests pass under each build. Extended suite: 36 tests pass under each build. It covers coordinate injection, ambiguous/disagreeing camera matches, fixture clipping, unknown accounting, full-context reservations, interrupted-ledger restart, rejected-request ceilings, failed/displaced pickups, persistent observed candidates, pinned resume and shared renderer contexts. `git diff --check` passes. The repository has no GitHub Actions workflows.

The paid final sweep’s policy SHA-256 is `d745c9ac643a79052e9af4e37713dba94efad64aa46ef95e24108aa32e290bf5`. A final completion guard was then added for candidates still visible near a prior successful pickup. A **zero-paid-call replay of all nine recorded request/action histories** verified identical actions, observations, request sequences, refills and eligibility after that guard. Its source SHA-256 is `1a84a574bd1912197a3524da874d44e81e48aab6c473b479f495ba51f4d692a1`; see [completion-guard-replay.json](completion-guard-replay.json). Wall times and costs remain those of the paid sweep, not the replay.

```bash
bash sim/magnet_sorter/experiments/2026-09-19-lead/bootstrap.sh
source sim/magnet_sorter/experiments/2026-09-19-lead/env.sh
contracts/.venv/bin/python sim/magnet_sorter/evaluate_adaptive.py \
  --output sim/magnet_sorter/runs/new-paired-sweep
```

The default matrix is cheap/strong/adaptive × seeds 4/17/29. For another sweep within the same authorized budget, pass `--ledger <existing-ledger.json>`; do not reset that ledger. Resume uses the same command plus `--resume`. Set credentials by environment name only. Use the host Python 3.12 venv; no CPython source build is needed.

Full run results, request ledger, traces, policy snapshots, every model crop and representative raw frames are attached through agent-fs. The complete 297 MB raw archive exceeded the upload size limit and is retained at `/workspace/personal/artifacts/magnet-routing-20260919/all-raw-runs.tar.gz` as a shared-workspace attachment. `APP_URL`, `AGENT_FS_LIVE_URL` and `AGENT_FS_APP_URL` were absent, so delivery uses agent-fs signed links plus the durable file paths. This change remains opt-in and simulation-only.
