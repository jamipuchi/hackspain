# Shared generated object definitions

Baseline: `89d1b2fb92bf36f35a1b1acb4a601380da2775e2`.

This increment adds a callable draft object definition API. It does not change the live engine, UI, profiles, model, policy, or scores.

## Public API

`sim/coffee_sorter/object_definitions.py` exports three functions.

- `propose_physics(...)` requests one unreviewed proposal through the existing cached generator call.
- `build_object_definition(...)` combines generator artifacts with a manual or LLM proposal.
- `validate_object_definition(value)` validates fields, units, IDs, proxy volume, and estimated mass.

The builder verifies the recipe hash recorded by the renderer. It also hashes the GLB bytes for `visual_asset_id`.

The builder records sorting as `unassigned` unless the caller supplies a class and policy proposal.

`object_type_id` includes the object key and immutable physics semantics. A visual-only revision can retain the object type ID.

A physics change creates a different object type ID. The definition always starts as `draft` and `draft_unreviewed`.

## Physics proposal contract

The current engine supports these reusable proxy primitives.

| Shape | Input dimensions | Stored proxy fields |
| --- | --- | --- |
| Ellipsoid | Three full diameters in meters | `semi_axes_m` |
| Box | Three full dimensions in meters | `half_extents_m` |
| Capsule | Total length, diameter, diameter in meters | `radius_m`, `half_length_m`, local `+Z` axis |

Use `unsupported` when these primitives do not preserve meaningful geometry. Rings and multipart objects usually need that result.

The API computes `mass_kg` as density times collision-proxy volume. This is a proxy-based estimate.

The estimate does not measure rendered material volume. This limit is significant for rings, multipart assets, and concave shapes.

The density is also an assumption. Metallic color or a material shader does not establish bulk composition.

## Callable example

This example uses a manual draft proposal and existing validated star artifacts.

```python
from pathlib import Path
from object_definitions import build_object_definition

star = Path("thoughts/taras/research/coffee-quality/object-generation/results/gemini/star")
definition = build_object_definition(
    description="A small five-point gold star token.",
    recipe_path=star / "recipe.json",
    render_metadata_path=star / "render/render.json",
    glb_path=star / "render/object.glb",
    visual_uri="../coffee-quality/object-generation/results/gemini/star/render/object.glb",
    physics_proposal={
        "shape": "box",
        "dimensions_m": [0.0169, 0.0161, 0.002],
        "density_kg_m3": 1200.0,
        "material_assumption": "Hypothetical decorative polymer with a metallic finish.",
        "limitations": "The proxy includes empty space between the star points.",
    },
    sorting_proposal={
        "class_name": "star_token",
        "defect": True,
        "severity": "foreign",
        "proposed_action": "reject",
    },
)
```

Run the complete local example:

```sh
PYTHONPATH=sim/coffee_sorter python3 \
  thoughts/taras/research/coffee-object-definitions/example.py
```

## LLM proposal

`propose_physics` uses the existing generator's `probe.call`, credential loader, schema conversion, and exact-request cache.

It never retries a provider request. A cache miss fails unless the caller passes `live=True`.

The evidence directory stores an immutable request and normalized proposal. Neither file contains the provider credential.

```python
proposal = propose_physics(
    description="A small five-point gold star token.",
    visual_dimensions_m=[0.0169496, 0.0161209, 0.002],
    evidence_dir="/private/tmp/coffee-object-definition-physics-smoke",
    env_file=".env",
    live=False,
)
```

The bounded live smoke used Gemini 3.8 Flash. It returned a schema-valid box proposal.

The request SHA-256 is `cb64ef8da96955228b4e7086e3b239d2f44889d15517df2b9b8fdad1cdaddf0d`.

The proposal assumed solid gold density and produced a 0.010502674 kg proxy mass. That physical assumption is not validated.

Smoke evidence remains at `/private/tmp/coffee-object-definition-physics-smoke`. The cached replay completed without another provider request.

## Jaume integration handoff

Jaume revision `55006b3c0ad73016a98d7e6b1cab34e004969940` has no generated-object registry hook.

The live command still carries `class_name`. The engine resolves that name through the active profile before spawning an existing `ClassSpec`.

The live object payload has no `object_type_id` or `visual_asset_id`. The viewer must not derive visual identity from a classifier prediction.

Keep draft definitions outside the active profile. An `unassigned` or `proposal_unreviewed` sorting field does not register a runtime class.

Jaume owns UI integration, retraining, and activation. A future adapter needs these reviewed additions first.

1. Add a reviewed profile class and collision proxy.
2. Define camera rendering behavior for that class.
3. Train and validate a compatible model.
4. Review policy behavior and create new model and score epochs.
5. Add object and visual asset IDs to the live registry and payload.

No function in this increment performs those steps.

## Verification

The focused tests cover unit conversion, finite positive values, mass arithmetic, unsupported geometry, stable IDs, provenance, and artifact hashes.

```sh
cd sim/coffee_sorter
/Users/taras/Documents/code/hackspain/.venv-coffee/bin/python \
  -m unittest test_object_definitions.py -v
/Users/taras/Documents/code/hackspain/.venv-coffee/bin/python \
  -m py_compile object_definitions.py test_object_definitions.py
cd ../..
git diff --check
```

## Manual E2E

Use a new evidence directory for a new description. Set `live=True` only when one billable request is intended.

```sh
PYTHONPATH=sim/coffee_sorter python3 - <<'PY'
from object_definitions import propose_physics

print(propose_physics(
    description="<object description>",
    visual_dimensions_m=[<x_m>, <y_m>, <z_m>],
    evidence_dir="/private/tmp/<new-evidence-directory>",
    env_file=".env",
    live=True,
))
PY
```

Inspect the proposal and derived mass before any profile work. Do not treat successful generation as physics, model, policy, or activation approval.
