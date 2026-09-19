# OpenRouter to Blender: measured object generation

Date: 2026-09-19. Owner: Taras's isolated object probe.
Branch: `codex/coffee-object-probe`. Base: `ea43a1287901729c675c91d8addc0b820bc9d2e3`.

This experiment generates actual mesh recipes through direct OpenRouter calls.
A trusted Blender script builds the objects, exports GLBs, and renders two views.
It tests an earring, a five-point star, and a fictional NOVA company badge.
It does not change the live sorter, training model, shared assets, or existing web pages.

## Visual verdict

**Gemini 3.8 Flash is the best overall choice in this small comparison.**
Its earring and five-point star look the most convincing. Its badge still has visible seams in the N.
DeepSeek's retried badge has the strongest layout. GLM's badge is a close alternative.
Neither model performs consistently across the other objects.
Qwen costs the most and takes the longest here, while its star is malformed.
Its earring measures 33.5 mm along its longest axis, exceeding the requested 28 mm envelope.

A separate reviewer saw anonymous columns A through E, without model names or costs.
That reviewer selected E for the earring and star, B for the badge, and E overall.
Those columns correspond to Gemini 3.8 and DeepSeek, respectively.
The review is a visual judgment, not a statistical benchmark or manufacturing acceptance.

The [gallery](gallery.html) contains all fifteen objects, both views, measured dimensions, costs, and explicit repair labels.
Each object also has a saved Blender scene and GLB.

## API results

Each model received identical messages for each brief. Saved request comparisons verified that equality.
Jev classified each description once. Every model received the same cached classification.
Jev returned `open_ring`, `flat_silhouette`, and `layered_badge`, respectively.
This experiment does not establish whether adding Jev improves generation.

| Model | Original API mean | Cost for three briefs | Locally valid original recipes |
| --- | ---: | ---: | ---: |
| Gemini 2.5 Flash Lite | 2.06 s | $0.000876 | 3/3 |
| DeepSeek V4.1 Flash | 37.04 s | $0.029198 | 2/3 |
| GLM 5.3 | 5.73 s | $0.021066 | 3/3 |
| Qwen3.8 Max 0902 | 104.70 s | $0.095696 | 2/3 |
| Gemini 3.8 Flash | 7.82 s | $0.016319 | 3/3 |

DeepSeek's separate badge retry cost $0.007371 and took 20.25 seconds.
Its total cost across the three briefs and retry was $0.036570.
Total reported OpenRouter cost was **$0.170526494**, including that retry and the truncated initial response.
These totals come from `usage.cost`, not catalog estimates.
Jev returned token counts but no billing amount. Its cost is not included.
HTTP errors did not return billing amounts. The totals include all completed generation response records.

Qwen's earring took 185.76 seconds. Its slowest result cost $0.056730.
One request per brief cannot establish production latency distributions or general model rankings.

## Controlled settings and exceptions

The provider schema fixes field names and types. Local checks enforce numeric limits and basic polygon topology.
Models choose geometry parts, outlines, dimensions, locations, colors, and materials.
The renderer supplies cameras, lights, a floor, primitive construction, and small bevels.
It does not execute model-written Python.

All initial generations allow 10,000 output tokens. Modern models use low reasoning effort.
Gemini 2.5 Flash Lite keeps its default reasoning setting.
Models use temperature zero except Gemini 3.8, whose available Vertex endpoint does not support that parameter.
The original Gemini routing failures remain in `results/gemini-routing-failure/`.
The initial overly constrained schema also failed. Its request and provider error remain preserved.

Blender uses Cycles CPU, 24 samples, 768 by 768 pixels, fixed seed 20260919, and one native thread.
Both camera views are orthographic. The angled view uses the filename `perspective.png`.
All object dimensions use millimeters. GLB transforms convert those dimensions to meters.
Every Blender subprocess obtains the exclusive shared runtime lock.
The first eight renders and final seven renders use the same renderer source hash.
The final set contains fifteen objects and thirty PNG views.
Each pair of views takes 40.70 seconds on average, ranging from 35.69 to 45.14 seconds.
Total time inside the render calls is 610.55 seconds, excluding lock waits and the initial failed harness run.
Renderer SHA256: `42924fc046d5fd500c55e4517089bc138ca9c880f12cb18d8389a4290320499b`.

## Demonstrated geometry failures

| Model | Requested star | Measured outline |
| --- | --- | --- |
| Gemini 2.5 Flash Lite | Five points | Five convex points and five concave valleys |
| DeepSeek V4.1 Flash | Five points | Ten convex vertices, zero concave valleys |
| GLM 5.3 | Five points | Six convex points and six concave valleys |
| Qwen3.8 Max | Five points | Six convex vertices and four concave valleys after closure normalization |
| Gemini 3.8 Flash | Five points | Five convex points and five concave valleys |

DeepSeek named its decagon a five-point star. Its description also claimed five concave valleys.
Those claims contradict the actual coordinates and render.
GLM's earring becomes disconnected rods. The baseline earring places its bead below a floating hoop.
These examples show why recipe validation cannot establish class correctness or physical assembly quality.

DeepSeek's initial badge response exhausted its token limit. A separate request raises only that limit to 16,000.
The retry completed with 6,137 output tokens. That result does not prove a deterministic token-budget effect.
The gallery labels the retry and displays combined cost and latency for both attempts.

Qwen's original star repeats its first boundary vertex at the end.
The validator rejects that redundant vertex. A separate recipe removes only the repeated closing point.
That normalization adds no provider call and does not change the intended closed boundary.
The original response, rejection, source hash, and normalization record remain available.

## Integrity and reproduction

Requests and responses remain immutable during cache replay. Changed requests require a separate output directory.
The Qwen normalization references the exact SHA256 of its original provider response.
Bounded checks verified that replay preserves that file and that changed attempts are refused.
Cached replays originally changed the local `cached` metadata flag. The review caught that provenance error.
The original live-call flags were restored from the immutable cache records. Separate files preserve the replay versions.
No provider content, usage, latency, request identifier, or geometry changed during that correction.
Environment files reflect the final cache replay used during development, rather than immutable metadata for every initial call.
The exact requests, model IDs, responses, recipes, and renderer hash provide the reproduction evidence.
All displayed recipes pass the final topology checks. This is not acceptance of their requested shape or physical assembly.

Browser checks confirmed that all fifteen images loaded in both views. The view toggle updated every image.
Syntax checks passed for the experiment scripts. No QA framework or routine unit tests were added.
The first Blender run had a metadata serialization failure after producing its images.
That failed run remains preserved. The renderer's build-hash serialization was corrected before the final render set.

```bash
cd /private/tmp/hackspain-coffee-object-probe
python3 -m py_compile thoughts/taras/research/coffee-quality/object-generation/*.py
python3 thoughts/taras/research/coffee-quality/object-generation/suite.py \
  --env-file /Users/taras/Documents/code/hackspain/.env
python3 thoughts/taras/research/coffee-quality/object-generation/render_suite.py
python3 thoughts/taras/research/coffee-quality/object-generation/analyze.py
python3 thoughts/taras/research/coffee-quality/object-generation/build_gallery.py
python3 -m http.server 8891 --bind 127.0.0.1 \
  --directory thoughts/taras/research/coffee-quality/object-generation
```

The generation command above reads the cache. Add `--live` only when new provider calls are intended.
The preserved DeepSeek and Qwen original failures remain failures during cache replay.
The separate DeepSeek retry command is in [README.md](README.md).

## Scope of the conclusion

This tests three prompts through a fixed geometry vocabulary. It does not benchmark unrestricted 3D generation.
No Quiver call was made. The tested path uses JSON geometry recipes directly.
No classifier retraining or physical collider validation occurred.
The 16-vCPU, 128-GB production machine was not benchmarked.
These local preview render times do not measure the time required to construct a mesh without rendering.

The next production design should use verified shape constructors for classes such as a five-point star.
The model can choose dimensions, materials, and composition within those constructors.
Generated names cannot supply trustworthy training labels without checking the geometry.

Source availability and endpoint behavior were checked against the [OpenRouter model catalog](https://openrouter.ai/api/v1/models).
The [Gemini 3.8 endpoint listing](https://openrouter.ai/api/v1/models/google/gemini-3.8-flash/endpoints) explains the temperature capability difference.
