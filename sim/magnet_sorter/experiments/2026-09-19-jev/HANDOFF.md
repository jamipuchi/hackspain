# Jev decisions and vision routing experiments

Taras requested this continuation on 2026-09-19.
Repository: https://github.com/tarasyarema/hackspain
Branch: `codex/jev`.

## Current implementation

`agent_brain.py` has an optional Jev tool selector.
Astra or an OpenRouter model describes images. Jev receives text and selects a locally defined action.
The original Astra agent remains the default.

`--reuse-pick-observation` skips one vision request between successful pickup motion and placement.
A successful motion does not prove attachment. The existing camera feedback checks attachment during placement.
Placement, failed motion, explicit photos, refill, and final confirmation require fresh vision.

Camera grids now show signed labels inside the image bounds.
The observer specifies a shared coordinate frame and the centre of each part's footprint.
OpenRouter requests require providers that support the requested parameters.
This capability check does not establish coordinate accuracy.

Runs record model latency, tokens, reported OpenRouter cost, decisions, and independent simulator scores.
Provider failures and interruptions preserve partial results.
The final fix marks interrupted OpenRouter requests as incomplete accounting.
The historical GLM and Qwen results predate that fix. Their correction appears in `evidence.json`.

## Results

All experiments used `theker_v1`, seed 4, cameras A and B, and MuJoCo rendering.
The task contains nine ferrous parts across two batches.
These are exploratory results from one seed.

| Vision model | Configuration | Measured vision latency | Cost | Outcome |
| --- | --- | --- | --- | --- |
| Astra | medium, observation reuse, headless | 8.06s median, 35 calls | $2.2395 estimated combined run | Agent declared done. Simulator scored 8/9 |
| DeepSeek V4.1 Flash | medium, observation reuse, viewer | 69.16s median, 4 calls | See raw evidence | Stopped for latency and poor observations |
| Gemini 3.8 Flash | low, corrected grid, observation reuse, viewer | 4.24s median, 25 calls | $0.1070 estimated recorded requests | Stopped during final-confirmation observation because reported usage cost was missing. Recovered score 8/9 |
| GLM 5.3 Flash | low observation probe | 6.81s | $0.003124 reported | Valid schema, wrong coordinates |
| GLM 5.3 Flash | medium, strict provider probe | 17.32s | $0.005679 reported | Reasonable coordinates in one probe |
| GLM 5.3 Flash | medium, strict provider, observation reuse, viewer | 2.74s median, 11 returned calls | $0.027793 known combined cost | Interrupted after bad coordinates and two failed pickups. Partial score 1/9 |
| Qwen 3.8 Flash | low observation probe | 28.80s wall | $0.001506 reported | Invalid observation schema |
| Qwen 3.8 Flash | low diagnostic repeat | Interrupted after 145.49s | Unknown | No response arrived |
| Qwen 3.8 Flash | reasoning none, strict provider probe | 6.59s | $0.000697 reported | Valid schema, wrong coordinates |

The GLM viewer run did not reproduce its good observation probe.
It falsely accepted its first batch after 13 tool calls. It had not sorted that batch correctly.
The partial 1/9 score does not establish a successful pick-and-place action.
Its interrupted request may add cost beyond the known amount.

Qwen's final probe changed reasoning and provider routing together.
These data cannot isolate which change corrected the schema.
Neither GLM nor Qwen demonstrated reliable coordinates in these experiments.

Gemini reduced median vision latency by 47.4% relative to Astra.
Its recorded requests cost approximately 95.2% less under the rates used for the estimate.
Request counts, prompts, and viewer conditions differed. These are not controlled end-to-end savings.
Astra used 409.5s wall time headless. Gemini used approximately 330s with the viewer.
The Gemini estimate excludes the failed request with missing usage cost.
We did not finish a matched Astra-only decision baseline.

The oracle control also scored 8/9 for seed 4.
One screw started near a container rim and remained there in both the oracle and Astra runs.
Do not interpret 8/9 as proof that perception has reached its accuracy limit.

Machine-readable evidence: [evidence.json](evidence.json) and [astra-gemini-cost-timing.json](astra-gemini-cost-timing.json).
Evidence includes observations and returned usage, without credentials, images, or hidden reasoning.
Full local recordings remain under the ignored `sim/magnet_sorter/runs/` directory.

## Continuation requested for the swarm lead

Improve the sorter with measured routing between models.
Use Jev where bounded text decisions provide value.
Prefer explicit routing in code before training or adding a model router.

1. Establish comparable baselines across several fixed seeds with identical rendering and pacing.
2. Evaluate calibrated geometry as a validation source for model coordinates.
3. Keep ordinary kind-to-container mapping, retry limits, budgets, and completion eligibility in code.
4. Use Jev for categorical classification, candidate ranking, and bounded recovery choices.
5. Evaluate Gemini for routine descriptions or part crops, with Astra for difficult observations.
6. Route on measurable checks, including camera disagreement, invalid geometry, occlusion, and failed pickup feedback.
7. Prevent repeated photos without new information and premature completion claims.
8. Measure accuracy, completed runs, request latency, wall time, request counts, and complete or unknown costs separately.

Jev cannot repair invented coordinates in its text input.
Do not use Jev confidence alone to accept coordinates or declare completion.
Do not expose simulator object labels or positions to perception, decisions, or routing.
Use simulator truth only for independent scoring after the run.

`jev_brain.py::find_parts` already computes calibrated centroids and appearance features.
It uses one camera and old detector assumptions. Evaluate it as a validation source before promoting it into control.
Adaptive routing is not implemented in this branch.

Implement small changes with paired evaluations. Preserve negative results.
Use bounded requests and experiments. Do not repeat the failed slow probes without a specific hypothesis.
Keep this work in simulation. Do not operate physical hardware.
Commit and push continuation changes to a reviewable branch. Do not merge or deploy.
Finish with a concise results message in Slack `#x-hackspain`.
Include the branch or PR, measured accuracy, latency, cost, limitations, and the recommended configuration.
Include the Slack message link in the final task output.

## Environment and commands

Use Python 3.14 to match the local environment when available.
Install the existing requirements and the tested OpenAI SDK version:

```bash
python3 -m venv contracts/.venv
contracts/.venv/bin/python -m pip install -r contracts/learning/requirements.txt openai==3.16.2
```

Required credentials:

- `TYPESAFE_API_KEY`: Jev.
- `OPENROUTER_API_KEY`: OpenRouter vision.
- `OPENAI_API_KEY`: Astra vision and the original Astra agent.

Retrieve credentials through authorized swarm configuration. Export them into the worker process before running the commands.
Never place credentials in Git, task text, logs, or Slack.
The Jev loader also accepts the repository `.env` and `~/.config/typesafe/api_key`.
Other provider credentials must be exported.
`JEV_MODEL=jev-latest` is optional. It resolved to `jev-1.13.0` in these runs.

Run Astra plus Jev:

```bash
PYTHONDONTWRITEBYTECODE=1 contracts/.venv/bin/python sim/magnet_sorter/run_demo.py \
  --build theker_v1 --brain agent --decision-model jev \
  --reuse-pick-observation --effort medium --cameras A,B --phone mujoco \
  --seed 4 --max-steps 60 --budget 4
```

Run Gemini plus Jev:

```bash
PYTHONDONTWRITEBYTECODE=1 contracts/.venv/bin/python sim/magnet_sorter/run_demo.py \
  --build theker_v1 --brain agent --decision-model jev \
  --vision-provider openrouter --vision-model google/gemini-3.8-flash \
  --reuse-pick-observation --effort low --cameras A,B --phone mujoco \
  --seed 4 --max-steps 60 --budget 4
```

For a macOS viewer, replace `python` with `mjpython` and add `--viewer`.
The viewer paces arm movement in real time. Use identical viewer settings for timing comparisons.
For Linux without a display, use an available MuJoCo renderer, such as `MUJOCO_GL=egl`.
Verify renderer support in the worker environment before paid model runs.

Other exact model identifiers: `deepseek/deepseek-v4.1-flash`, `z-ai/glm-5.3-flash`, and `qwen/qwen3.8-flash`.

## Verification before handoff

The focused suite passes 22 tests under each build configuration:

```bash
PYTHONDONTWRITEBYTECODE=1 contracts/.venv/bin/python -m unittest sim/magnet_sorter/test_agent_brain.py sim/magnet_sorter/test_camera.py
SORTER_BUILD=theker_v1 PYTHONDONTWRITEBYTECODE=1 contracts/.venv/bin/python -m unittest sim/magnet_sorter/test_agent_brain.py sim/magnet_sorter/test_camera.py
git diff --check
```

Live evidence includes a completed Astra run, an incomplete Gemini run, GLM viewer failures, and Qwen observation failures.
Tests cover Jev contract handling, candidate limits, observation reuse, provider accounting, request interruption, and visible coordinate labels.
