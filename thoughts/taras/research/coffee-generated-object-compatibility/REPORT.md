# Generated object compatibility audit

Audit baseline: `c599bd9988b9fff95c78209040e31e28697a4041`.

This baseline merges fork revision `d140c876` with Jaume revision `a23704791f3151730df164594693e4587a743322`.

## Direct answer

The current system cannot sort a new generated object type end to end.

The generator can produce a validated recipe, Blender scene, GLB, and preview images. The browser can load compatible GLB files.

Those facts prove visual asset support only. They do not prove physics, camera, classifier, policy, or score support.

The live backend accepts only classes in its selected product profile. The current UI always requests the existing `stone` class.

The first integration gap is an application class record with a reviewed physics proxy. Model and policy activation must follow that review.

## Verified path

1. A description enters the provider probe. Its default model is `google/gemini-3.8-flash`.
2. The model returns data under a closed recipe schema. The system does not execute generated code.
3. The trusted Blender renderer converts millimeters to meters during GLB export.
4. The generated GLB can enter a visual preview after hash and format checks.
5. No current bridge registers that asset in a sorter profile, physics pool, camera, model, policy, or live asset registry.

The generator contract supports `ring`, `ellipsoid`, `box`, `cylinder`, `polygon`, and `text` parts. It allows one to twelve parts.

The renderer records dimensions and content hashes. Its GLB uses meter dimensions. See [INTEGRATION.md](../coffee-quality/object-generation/INTEGRATION.md#L84) and [render_recipe.py](../coffee-quality/object-generation/render_recipe.py#L258).

The [probe output](probe-output.json) validates three existing fixtures without a provider call.

| Fixture | Size in millimeters | Meshes | Materials | Result |
| --- | ---: | ---: | ---: | --- |
| Star | 16.950 x 16.121 x 2.000 | 1 | 1 | Fits the current fixed proof loader shape restriction. |
| Earring | 14.000 x 23.800 x 6.000 | 8 | 8 | Loads as a visual specimen only. Current instancing proof rejects it. |
| Logo | 26.000 x 26.000 x 2.800 | 7 | 7 | Loads as a visual specimen only. Current instancing proof rejects it. |

The three GLBs use glTF 2.0. They have no image textures or required extensions.

The glTF specification defines meters, a right-handed coordinate system, and positive Y as up. It stores quaternion components in XYZW order. See the [Khronos glTF 2.0 specification](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html).

The backend publishes MuJoCo quaternions in WXYZ order. A viewer must convert component order at the interface.

## Compatibility matrix

| Boundary | Current verified support | First missing requirement | Status |
| --- | --- | --- | --- |
| Description to recipe | The provider probe defaults to Gemini 3.8 Flash. It validates a closed data schema. | Production job state and registry publication. | Generator only |
| Recipe to mesh | Blender creates one to twelve primitive parts. It exports meter-scale GLB files. | A reviewed runtime LOD for multipart or complex assets. | Visual only |
| Asset identity | Render metadata contains recipe and GLB hashes. | A stable `object_type_id` and registry entry. | Partial |
| Materials | Recipes carry RGB, metallic, and roughness values. Existing GLBs contain materials. | A live lighting and material quality target. Texture support remains unverified. | Visual only |
| Physics | The simulator has ellipsoid, half, box, and capsule body pools. | A reviewed proxy, dimensions, density, and mass. | Fixed types only |
| Admission | The backend resolves `class_name` through the active profile. Unknown names fail. | Manifest lookup and lifecycle admission gate. | Fixed classes only |
| UI injection | The live command supports one `class_name`. The button sends `stone`. | Registry-backed choices and asset readiness status. | Fixed class only |
| Camera features | The simulator camera produces 23 image-derived features from physics geometry. | A reviewed camera representation and new labeled observations. | Fixed types only |
| Model training | Offline bootstrap uses simulator observations and requires every profile class. | Add the class, collect separate train and holdout data, then validate. | Offline only |
| Model activation | Startup checks profile, classes, features, provenance, layout, and cadence. | A compatible artifact, manifest, preset, and engine restart. | Restart required |
| Controller policy | The controller maps active model classes to profile defect severity. | A reviewed defect label, severity, and action. | Fixed classes only |
| Live pose | Objects include shape, axes, RGB, position, WXYZ quaternion, decision, and outcome. | `object_type_id`, `visual_asset_id`, and registry epoch fields. | Geometry only |
| 3D lookup | The fixed proof loads four bean GLBs. It requires one mesh and one material. | A content-addressed live registry and multipart handling. | Fixed assets only |
| Rolling score | Scores report model, policy, source, and session-based score epoch. Manual injections stay excluded. | A new score epoch after active model or policy change. | Existing feed only |

## Source findings

### Physics and admission

The profile defines the allowed class names and their physical specifications. See [profiles.py](../../../../sim/coffee_sorter/profiles.py#L11).

The scene allocates fixed MuJoCo body pools for supported shapes. An arbitrary render mesh does not become a collision body.

The backend injection method calls the active profile lookup before spawning an object. See [engine.py](../../../../sim/coffee_sorter/engine.py#L466).

Recipe `size_mm` values are full local dimensions. Simulator axes use proxy-specific meanings.

Ellipsoids use semiaxes. Boxes use half extents. Capsules use radius and half length.

An integration must convert units and dimension semantics explicitly. It must not reuse GLB bounds as collision dimensions without review.

### Camera, model, and policy

The camera renders simulator geometry. It does not render the browser GLB.

The classifier receives 23 image-derived features from that camera. See [vision.py](../../../../sim/coffee_sorter/vision.py#L15).

The tracked green model contains ten profile classes. The tracked roasted model contains six profile classes.

Both tracked generalization models match the runtime feature list. Their SHA-256 values appear in [probe-output.json](probe-output.json).

The configured continuous model path is absent from this checkout. This audit does not claim that the shared live service uses either tracked generalization model.

Model bootstrap uses separate training and holdout seeds. It requires all profile classes before it writes a model and manifest. See [bootstrap_model.py](../../../../sim/coffee_sorter/bootstrap_model.py#L245).

Startup rejects class, feature, profile, provenance, layout, rate, or cadence mismatches. See [live.py](../../../../sim/coffee_sorter/live.py#L36).

No model replacement command exists in this baseline. Activation therefore requires an engine restart.

The controller derives its reject mapping from model classes and profile severity. An anomaly score can reject unusual appearance.

An anomaly result does not register or name a generated object class. It also does not create a physics representation.

### Live payload and viewer

The live object payload contains a session-local object ID, shape, axes, RGB, pose, decision, contact counts, and outcome.

It omits the truth class, object type, visual asset ID, material ID, mass, profile version, and registry epoch. See [engine.py](../../../../sim/coffee_sorter/engine.py#L482).

Use `(session_id, object_id)` as the runtime instance identity. `appearance_key` is session-derived and cannot identify an asset.

Positions and dimensions use meters. Server quaternions use WXYZ order.

The live viewer must select geometry from authoritative `shape`, `axes`, and asset fields. It must not select geometry from classifier predictions.

Classifier predictions remain decision evidence. A wrong prediction must not change the displayed physical type.

The current two-dimensional UI draws boxes explicitly. It draws every other shape as an ellipse.

The current GLB proof flattens the node transform and rotates the geometry 90 degrees around X. It then creates one instanced mesh.

That proof rejects assets with multiple meshes or material arrays. See [proof.html](../../../../sim/coffee_sorter/visual_assets/browser/proof.html#L27).

Three.js `GLTFLoader` loads glTF 2.0 assets. `MeshStandardMaterial` implements metallic-roughness rendering. See the [loader documentation](https://threejs.org/docs/pages/GLTFLoader.html) and [material documentation](https://threejs.org/docs/pages/MeshStandardMaterial.html).

Realistic appearance also depends on reviewed geometry, materials, lighting, and environment. A successful load does not establish realistic live rendering.

### Scores and epochs

The rolling ledger excludes manual injections. It reports a caller-provided score epoch plus model and policy versions.

The engine currently uses its session ID as the score epoch. See [rolling_scores.py](../../../../sim/coffee_sorter/rolling_scores.py#L56) and [engine.py](../../../../sim/coffee_sorter/engine.py#L583).

The live command epoch protects command freshness. It is not a model or score epoch.

A new active class changes the profile, model, and policy contract. Start a new engine session and score epoch after that activation.

Do not add manual generated-object tests to rolling feed scores. They remain excluded by the current score contract.

## Proposed shared manifest

Use [object-manifest.example.json](object-manifest.example.json) as the smallest current example.

The example records a valid star asset at `asset_ready`. It intentionally leaves physics, inspection, policy, and activation fields unset.

It records the source Y-up pivot and corrected Z-up bounds. Its runtime anchor remains unset until runtime LOD review.

The backend must reject `asset_ready` objects. Only `active` objects may enter automatic feed or manual physics injection.

Use these lifecycle states:

1. `draft`: The description or recipe can still change.
2. `asset_ready`: The recipe and visual artifact passed validation and hash checks.
3. `physics_reviewed`: A supported collision proxy, mass, and bounds passed review.
4. `model_candidate`: Camera data and a compatible model candidate exist.
5. `active`: Profile, model, policy, preset, viewer, and score epoch agree.
6. `retired`: New sessions cannot admit the type.

`object_type_id` identifies immutable type semantics. Any class or physics semantic change creates a new value.

`visual_asset_id` is the GLB content hash. A visual-only revision can change this ID without changing the runtime instance ID.

The active physics proxy must use one existing supported shape. It must provide named dimensions in meters.

Use `semi_axes_m` for ellipsoids. Use `half_extents_m` for boxes.

Use `radius_m` and `half_length_m` for capsules. Treat `half` as a reviewed fixed mesh family.

Keep the render mesh separate from this proxy. Do not send arbitrary GLB topology to MuJoCo as an automatic collider.

For a single-mesh runtime LOD, flatten node transforms, restore simulator Z-up, center the bounds, and apply the live pose.

The generated renderer lifts visual bounds onto its Z=0 floor before export. This visual pivot does not define the center of mass or collider origin.

Convert the server WXYZ quaternion before assigning a Three.js quaternion. Preserve the asset correction as a separate transform.

Multipart assets need per-part prototypes or a reviewed merged runtime LOD. The current instancing proof cannot preserve them directly.

## Integration checklist

### Generator and registry

- Validate the recipe before rendering.
- Record recipe, GLB, and preview hashes.
- Assign an immutable `object_type_id`.
- Use the GLB hash as `visual_asset_id`.
- Record GLB units, up axis, bounds, mesh count, and material count.
- Record the source pivot, corrected bounds, and reviewed runtime anchor.
- Mark new results as `asset_ready`. Do not mark them active.

### Physics and application domain

- Register a unique class label in one product profile.
- Select one supported collision proxy.
- Record explicit proxy dimension semantics in meters.
- Review mass, density, spawn clearance, pool capacity, and outcome boundaries.
- Reject unknown, retired, or incomplete registry records.
- Keep render geometry outside the physics truth path.

### Camera, training, and policy

- Add a reviewed camera representation for the proxy.
- Collect labeled simulator observations for the new profile class.
- Keep separate train and holdout object seeds.
- Require coverage for every profile class.
- Validate class order, feature list, provenance, layout, rate, and cadence.
- Map the class to defect state, severity, and controller action.
- Review anomaly behavior independently from named-class behavior.

### Live backend and viewer

- Add `object_type_id` and `visual_asset_id` to live object rows.
- Add profile and registry versions to the session payload.
- Resolve each asset through a content-addressed registry.
- Use `(session_id, object_id)` for live instance identity.
- Select visual geometry from authoritative object fields.
- Keep classifier predictions as text and decision evidence.
- Report primitive fallback when asset lookup or validation fails.
- Support multipart groups or publish a reviewed merged runtime LOD.
- Preserve WXYZ server order at the protocol boundary.
- Convert to the viewer quaternion order exactly once.

### Activation and scores

- Activate profile, model, policy, preset, and asset registry as one reviewed set.
- Create a new `model_epoch_id` for the active model set.
- Restart the engine under this baseline.
- Create a new `score_epoch_id` for the new session.
- Keep manual injections excluded from rolling feed scores.
- Publish artifact hashes and the active source revision.

## Verification commands

Run the compatibility probe without provider, training, or physics work:

```sh
/Users/taras/Documents/code/hackspain/.venv-coffee/bin/python \
  thoughts/taras/research/coffee-generated-object-compatibility/probe_compatibility.py \
  --baseline-revision c599bd9988b9fff95c78209040e31e28697a4041 \
  --output /private/tmp/coffee-generated-object-compatibility-probe.json
```

`execution_revision` records the commit that ran the probe. It changes when the working commit changes.

`source_revision` stays fixed at the audited baseline. The probe also verifies that audited source files still match that baseline.

Artifact and source hashes stay fixed while their bytes stay unchanged. Do not expect byte-identical probe output across execution commits.

Validate the probe source and JSON artifacts:

```sh
/Users/taras/Documents/code/hackspain/.venv-coffee/bin/python -m py_compile \
  thoughts/taras/research/coffee-generated-object-compatibility/probe_compatibility.py

python3 -m json.tool \
  /private/tmp/coffee-generated-object-compatibility-probe.json >/dev/null

python3 -m json.tool \
  thoughts/taras/research/coffee-generated-object-compatibility/object-manifest.example.json >/dev/null
```

Inspect the exact source revision and changed scope:

```sh
git rev-parse HEAD
git diff --check
git status --short
```

## Limits

This audit did not call Gemini or another provider. It reused saved generation evidence.

It did not train a model, run physics, or mutate the shared live session.

It did not test arbitrary topology, image textures, material extensions, multipart instancing, or hot model activation.

The example manifest is a proposed interface. Current production code does not consume it.

The selected baseline does not contain Jaume's planned live retraining and new-item controls. This audit preserves that ownership.
