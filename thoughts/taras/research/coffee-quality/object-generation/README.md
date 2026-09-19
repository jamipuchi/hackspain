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
Initial generation calls use a 10,000-token output limit.
All models except Gemini 3.8 use temperature zero.
Gemini 3.8 uses its endpoint default because the available Vertex endpoint does not accept temperature.
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
python3 thoughts/taras/research/coffee-quality/object-generation/analyze.py
python3 thoughts/taras/research/coffee-quality/object-generation/build_gallery.py
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

Gemini 3.8 initially returned HTTP 404 for all three briefs.
Endpoint metadata identified unsupported temperature as the routing constraint.
Removing only temperature allowed all three requests to complete. The original failures remain saved.

Qwen's star repeated its first boundary vertex at the end.
The strict validator rejected that representation. A separate copy removes only that redundant closing vertex for display.
The geometry still fails the requested five-point outline. No provider call or manual design repair changed it.

The first Blender run rendered both images but failed while serializing its binary build hash.
The renderer now converts that hash to text. The initial failed run and its images remain saved.

## Explicit follow-ups

DeepSeek's separate retry changes only its token limit:

```bash
python3 thoughts/taras/research/coffee-quality/object-generation/probe.py \
  --env-file /Users/taras/Documents/code/hackspain/.env \
  --out thoughts/taras/research/coffee-quality/object-generation/results/deepseek-repair \
  --case logo --model deepseek/deepseek-v4.1-flash --max-tokens 16000 --live
```

The retry completed with fewer than 10,000 output tokens. This does not establish a deterministic token-budget effect.
The gallery labels the retry and includes the original failed call in its displayed cost and latency totals.
The Qwen normalization has a separate `repair.json` record and retains every other generated field.

## Manual E2E

Serve the gallery on the reserved demonstration port:

```bash
python3 -m http.server 8891 --bind 127.0.0.1 \
  --directory thoughts/taras/research/coffee-quality/object-generation
agent-browser --session coffee-object-probe open http://127.0.0.1:8891/gallery.html
agent-browser --session coffee-object-probe snapshot -i
```

Compare perspective and top views. Use blind labels to hide model names and costs.
Open any saved `object.blend` in Blender to inspect its parts.
Use `object.glb` for downstream asset inspection. GLB dimensions use meters.
The recipes and Blender scenes use millimeters.
Do not use these visual assets as validated physical colliders or training labels.
