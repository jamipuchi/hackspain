# Direct OpenRouter object generation

Taras requested actual model calls, Blender renders, and a visual comparison.
This experiment owns only this directory. It does not modify the sorter or shared assets.

## Frozen comparison

Each model receives the same earring, star, and fictional NOVA badge descriptions.
Jev classifies each description once. Cached classifications enter every model's prompt.
This classification uses text. It does not recognize an image or measure sorting accuracy.

Models generate geometry recipes. A fixed Blender script constructs and renders the geometry.
The models choose dimensions, outlines, placement, colors, and materials.
The renderer supplies six geometry primitives, cameras, lighting, and small edge bevels.
The experiment does not execute Python returned by a model.

Models:

- `google/gemini-2.5-flash-lite`, inexpensive baseline.
- `deepseek/deepseek-v4.1-flash`.
- `z-ai/glm-5.3`.
- `qwen/qwen3.8-max-0902`.
- `google/gemini-3.8-flash`.

The catalog snapshot records available model IDs and published prices.
All calls use temperature zero and a 10,000-token output limit.
Modern models receive `reasoning.effort=low`. The baseline keeps its default reasoning setting.
These settings compare practical configurations. They do not equalize internal reasoning across providers.

## Execution

1. Generate three recipes per model. Retain requests, responses, usage, and failures.
2. Validate recipes locally. Render valid recipes without manual geometry repair.
3. Inspect perspective and top views. Compare recognition, connected geometry, lettering, and visual appeal.
4. Report first-attempt failures and any separate repair attempts.

Verification:

```bash
cd /private/tmp/hackspain-coffee-object-probe
python3 -m py_compile thoughts/taras/research/coffee-quality/object-generation/*.py
python3 thoughts/taras/research/coffee-quality/object-generation/suite.py \
  --env-file /Users/taras/Documents/code/hackspain/.env --live
python3 thoughts/taras/research/coffee-quality/object-generation/render_suite.py
```

Omit `--live` to require cached API responses. The runner never repeats a cached request.
Every Blender subprocess holds `/private/tmp/hackspain-coffee-runtime.lock` with exclusive `fcntl.flock`.
If the lock is occupied, the renderer exits with code 75. Retry after independent work.
Rendering uses one native thread. The scripts record native thread environment values and host load.

## Initial negative results

Google rejected the first schema because its numeric and array constraints produced too many grammar states.
The revised provider schema keeps types and required fields. Local validation retains numeric and size limits.
Both the rejected request and provider error remain in the baseline earring directory.

The baseline earring passes schema validation but places its bead below the hoop without a connection.
DeepSeek exhausted its 10,000-token limit on the initial badge request.
These failures remain visible. Schema compliance does not prove geometric correctness.

## Manual E2E

Open the resulting `gallery.html` in a browser and compare perspective and top views.
Open any saved `object.blend` in Blender to inspect its parts.
Use `object.glb` for downstream asset inspection. GLB dimensions use meters.
The recipes and Blender scenes use millimeters.
Do not use these visual assets as validated physical colliders or training labels.
