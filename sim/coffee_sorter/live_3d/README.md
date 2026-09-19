# Coffee sorter live 3D preview

This isolated preview renders the existing live service with Three.js.
It is read-only and does not change the shared live UI or backend.

## Run

Keep the continuous service available on port 8892. Then run:

```sh
/Users/taras/Documents/code/hackspain/.venv-coffee/bin/python \
  sim/coffee_sorter/live_3d/server.py \
  --backend http://127.0.0.1:8892 \
  --port 8895
```

Open `http://127.0.0.1:8895/`.

The server accepts a different explicit backend URL through `--backend`.
The browser always connects to the preview on the same origin.
The proxy selects `ws:` or `wss:` from the configured backend scheme.
It preserves the live server's Host and Origin checks.

## Visual contract

Live appearance uses only authoritative `shape`, `axes`, `rgb`, `pos`, and `quat` values.
Classifier predictions appear as text and never select a model.
Ellipsoids use the generic coffee-bean LOD and server color.
Half shapes use the broken-bean LOD.
Boxes and capsules use labeled primitive fallbacks.

The Assets view displays four runtime bean LODs and one multipart generated earring.
The generated object is a visual compatibility specimen only.
It has no live injection, physics, classifier, or training support.

The live transport still needs immutable `object_type_id` and content-hash `visual_asset_id` fields.
Physics proxies need a separate reviewed definition.
Multipart runtime assets need per-part prototypes or a reviewed merged LOD.

## Rendering limits

The browser uses glTF metallic-roughness materials and baked textures.
It does not reproduce Cycles procedural nodes, bounce lighting, world lighting, or compositor effects.
The viewer caps pixel ratio and uses instancing for the live object pool.
Its telemetry is diagnostic during concurrent Full-HD rendering.
It is not a clean GPU benchmark.

Primary references:

- [Blender glTF 2.0 exporter](https://docs.blender.org/manual/en/dev/addons/scene_gltf2.html)
- [Khronos glTF 2.0 specification](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html)
- [Three.js GLTFLoader](https://threejs.org/docs/pages/GLTFLoader.html)
