# Coffee live 3D increment

Status: Implemented and locally verified on isolated branch `codex/coffee-live-3d`.

Baseline: `c599bd9988b9fff95c78209040e31e28697a4041`.

This baseline includes fork main `d140c8763871e7c227a7d4c42481fcca975b23a6` and Jaume main `a23704791f3151730df164594693e4587a743322`.

## Scope

This increment has three tracks.

1. Build an isolated read-only 3D preview under `sim/coffee_sorter/live_3d/`.
2. Check existing and generated GLB compatibility without changing simulation ownership.
3. Record deployment requirements without weakening the live server's Host or Origin checks.

The preview does not edit the shared live UI, service, engine, retrainer, or visual asset sources.

## Phase 1: Isolated preview

Use the existing Three.js r180 modules and GLTFLoader.
Read authoritative object poses from the existing live WebSocket through a read-only same-origin proxy.
Use `ws:` for HTTP pages and `wss:` for HTTPS pages.
Render server position, quaternion, axes, prediction, decision, air-contact evidence, and outcome without inventing state.

### Verification

```sh
python3 -m py_compile sim/coffee_sorter/live_3d/server.py
git diff --check
curl -fsS http://127.0.0.1:8895/health
```

## Phase 2: Asset compatibility

Reuse the four browser LOD GLBs and one tracked generated-object GLB.
Keep unsupported objects visible through primitive fallbacks.
Keep the generated-object specimen separate from live physics until the application supplies an explicit object-to-asset key.

### Verification

```sh
curl -fsS http://127.0.0.1:8895/asset-manifest.json
```

The browser check must confirm asset loads, live pose updates, fallback status, draw calls, triangles, and frame timing.

## Phase 3: Integration handoff

Document the minimum future shared change after Jaume confirms ownership.
The live object transport needs a stable asset key that does not expose evaluator truth.
Public deployment needs explicit allowed Host and Origin configuration while keeping the preview same-origin.

### Verification

```sh
git diff --name-only c599bd9988b9fff95c78209040e31e28697a4041..HEAD
```

Only the two owned directories may appear.

## Verified local evidence

The preview ran at `http://127.0.0.1:8895/` against the unchanged backend on port 8892.
The backend session was `fae8a089-0d6a-4801-b90b-f74aa873890f` during verification.
Simulation time advanced from 1276.316999976731 to 1276.3289999767308.

The live viewer rendered 488 active objects from authoritative server poses.
It converted quaternion `w,x,y,z` fields to Three.js `x,y,z,w` order.
It used server semiaxes as object scale in metres.
It used server shape and RGB for appearance selection and tint.
Prediction remained text evidence and did not select a model.

The five GLBs total 1,009,684 bytes.
The Assets view rendered 13 draw calls and 13,886 triangles.
That view showed four bean LODs and the multipart Gemini earring.
The live overview rendered 25 draw calls and 275,440 triangles with 488 objects.

The observed desktop frame sample reached 57 frames per second.
Median frame time was 16.7 ms and p95 was 16.8 ms.
Concurrent workstation workloads were not independently measured.
This sample is diagnostic and is not a clean GPU benchmark.

Desktop 1280 by 720 and phone 320 by 480 layouts had no document overflow.
The short phone layout kept score counts, camera controls, and the latest-object card visible.
Text and binary WebSocket commands both closed with policy code 1008.
The main read-only stream remained connected after those checks.
The browser console and page-error logs were empty.

No new injection occurred during this verification.
The four latest-object outcome labels were checked in code.
Taras must still verify those labels against a new physical injection.

## Independent review corrections

The preview now rejects missing, cross-origin, and non-loopback WebSocket origins before backend connection.
An attacker-origin handshake returned HTTP 403.
The same-origin browser remained compatible.

Capsule prototypes now use simulator-local Z before the authoritative body quaternion is applied.
Their scale uses simulator half-length `axes[0]` and radius `axes[1]`.

The multipart earring is optional at startup.
The Assets view reports when it is unavailable.
Required runtime bean LODs still fail startup when missing.

Capacity overflow now appears in browser telemetry instead of silently omitting objects.
The machine geometry rebuilds when the backend session or physical layout changes.

## Unchanged engine priorities

Rendering does not solve these engine priorities:

- Export actual continuous rolling-score rows for independent validation.
- Reduce good-bean loss.
- Improve runtime toward the real-time target.

## Manual E2E

Keep the existing backend running on port 8892. Start the isolated preview:

```sh
/Users/taras/Documents/code/hackspain/.venv-coffee/bin/python \
  sim/coffee_sorter/live_3d/server.py \
  --backend http://127.0.0.1:8892 \
  --port 8895
```

Open `http://127.0.0.1:8895/`.

Verify these behaviors:

1. The machine and live moving objects appear in 3D.
2. The browser remains read-only and sends no injection commands.
3. The latest injected object shows prediction, decision, air contact, and physical outcome separately.
4. A generated GLB appears only as a labeled visual specimen.
5. An unavailable asset uses a visible fallback and reports that fallback.
6. Desktop and short phone viewports require no page scrolling.
7. Disconnecting the backend shows a visible error without fabricated state.
