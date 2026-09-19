# Object recipe asset job

Use this probe to create one small object asset from text. The default model is
`google/gemini-3.8-flash`. The probe uses low reasoning and a 10,000-token cap.
It omits `temperature` for Vertex compatibility.

The probe requires Python 3.13 or later and Blender 5.2.2. It has no pip
dependencies. It imports credentials from `contracts/learning/providers.py`.

## Run one custom asset job

Run these commands from the repository root. Set the variables for this job.
Do not print the environment file or its secrets.

```sh
TASK_ROOT="thoughts/taras/research/coffee-quality/object-generation"
RESULTS_ROOT="$TASK_ROOT/results"
JOB_ID="my-object-v1"
DESCRIPTION="A small polished brass compass token with a raised star"
ENV_FILE="/absolute/path/to/provider.env"
BLENDER_BIN="blender"

python3 "$TASK_ROOT/probe.py" \
  --env-file "$ENV_FILE" \
  --out "$RESULTS_ROOT/$JOB_ID" \
  --description "$DESCRIPTION" \
  --live

python3 "$TASK_ROOT/render_suite.py" \
  --results-root "$RESULTS_ROOT" \
  --model "$JOB_ID" \
  --case custom \
  --blender "$BLENDER_BIN"
```

`OPENROUTER_API_KEY` is required. Put it in `ENV_FILE`, or set it in the
environment before the commands. `TYPESAFE_API_KEY` is optional. When present,
the probe requests a Jev text classification.

Without `--env-file`, the probe reads `.env` from the current directory.
Values in that file override matching environment variables.

Jev supplied coarse shape labels in the comparison. Every model received the
same hint. We did not compare generation with and without Jev, so its benefit
remains unmeasured. Jev does not generate geometry or validate the rendered asset.

`--live` permits a new billable provider call. Omit it for a cache replay.
Use a new `JOB_ID` when the description or prompt changes. The cache preserves
the original request and response.
An existing job reuses its saved Jev response even when its credential is absent.

The custom description always writes `custom/recipe.json`. Do not combine
`--description` with `--case`.

## Run an existing named case

The named cases are `earring`, `star`, and `logo`. They remain available.

```sh
TASK_ROOT="thoughts/taras/research/coffee-quality/object-generation"
RESULTS_ROOT="$TASK_ROOT/results"
JOB_ID="named-case-v1"
CASE="logo"
ENV_FILE="/absolute/path/to/provider.env"
BLENDER_BIN="blender"

python3 "$TASK_ROOT/probe.py" \
  --env-file "$ENV_FILE" \
  --out "$RESULTS_ROOT/$JOB_ID" \
  --case "$CASE" \
  --live

python3 "$TASK_ROOT/render_suite.py" \
  --results-root "$RESULTS_ROOT" \
  --model "$JOB_ID" \
  --case "$CASE" \
  --blender "$BLENDER_BIN"
```

In `render_suite.py`, `--model` selects a results folder. It is not an API
model ID. The renderer uses the `blender` command on `PATH` when `--blender` is
not set. The command fails when no recipe matches the requested job and case.

## Contract and outputs

`probe.py` owns the JSON schema and `validate_recipe`. Treat them as the input
contract. The trusted renderer supports six primitives: `ring`, `ellipsoid`,
`box`, `cylinder`, `polygon`, and `text`. The model returns data only. The
system never executes model-generated code.

Each recipe contains `name`, `design_notes`, and a `parts` array with 1 to 12
parts. Each part supplies all fields below, including unused fields.

| Fields | Meaning |
| --- | --- |
| `name`, `kind` | Part name and one supported primitive |
| `position_mm`, `rotation_deg` | Center position and XYZ Euler rotation |
| `size_mm` | Full local dimensions in millimeters |
| `color`, `metallic`, `roughness` | RGB color and material settings |
| `tube_mm` | Ring tube radius, or zero for other primitives |
| `outline` | Normalized XY polygon boundary, or an empty array |
| `text` | Lettering content, or an empty string |

Use the generated `schema.json` for exact bounds. Call `validate_recipe` before
rendering recipes received outside this command. Validation checks structure
and polygon boundaries. It does not prove that parts connect or match the brief.

For a custom job, the files are under `$RESULTS_ROOT/$JOB_ID/custom/`:

- `recipe.json` contains the validated geometry recipe.
- `openrouter_request.json` and `openrouter_response.json` retain API evidence.
- `render/object.glb` uses meter dimensions.
- `render/object.blend` keeps the millimeter scene.
- `render/perspective.png` is an angled orthographic preview.
- `render/top.png` is the top orthographic preview.
- `render/render.json` records renderer metrics and bounds.

The render lock is shared with other jobs. Exit code 75 means the lock is busy.
Reschedule this job later. Do not stop another job that holds the lock.

Observed runs took about 8 seconds for an API response. Two one-thread preview
renders took about 41 seconds. That time includes rendering both images. It is
not the mesh-generation time alone.

## Later system integration

Run this probe as a background asset job. Return the GLB URL and preview image
URLs when the job completes. Keep these future ownership boundaries separate:

- Class registration belongs to the application domain layer.
- Physics collider and mass selection belongs to simulation ownership.
- Policy mapping belongs to the sorter policy owner.
- Retraining belongs to the model-training owner.

This probe does not deploy public assets. It does not integrate with the live
engine.
