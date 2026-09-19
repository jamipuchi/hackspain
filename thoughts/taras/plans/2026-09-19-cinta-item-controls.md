---
date: 2026-09-19
owner: taras
planner: Codex
status: ready
baseline_revision: baa797f50561679412a1b25321588ca87eca1f00
implementation_branch: future-from-main
---

# CINTA item controls implementation plan

## Overview

CINTA means Class-agnostic INline Transport Analyzer.

This plan defines one small future increment. It creates and displays an asynchronous generated-item draft. It does not activate that item.

The current rejection-control work is a prerequisite. The runtime agent owns policy changes and score epochs. The UI agent owns the compact class card and controls.

## Current state

- `live.py` already separates the HTTP process from the engine worker. It bounds commands, pending requests, and retained results.
- Current runtime work adds `class_catalog`, `reject_policy`, `policy_version`, `score_epoch_id`, and `spawn_region` to state.
- The `set_reject_policy` WebSocket command carries the command, session, epoch, expected policy version, and complete reject-class set.
- An accepted policy change creates a new score epoch. It does not require a service restart.
- `probe.py` already provides exact-request caching and the existing Gemini recipe flow.
- `render_suite.py` already creates cached GLB evidence while respecting the shared Blender lock.
- `object_definitions.py` already validates draft object definitions and proposes reviewed engine proxy types.
- Generated definitions remain drafts. The runtime has no safe path for arbitrary mesh physics, classification, or activation.

## Desired end state

The existing class card remains compact and responsive. It shows every authoritative handled class and its current reject policy.

Below it, one compact draft creator accepts a short item description. The browser submits the work and remains responsive. The live engine continues without waiting for generation or rendering.

A completed draft shows its preview, stable IDs, dimensions, proxy proposal, proxy-based mass estimate, sorting status, and a visible `Not active` label.

## Non-goals

- Do not register a new class or add it to the injection control.
- Do not modify physics, inference, training data, model weights, or score history.
- Do not infer object appearance from a classifier prediction.
- Do not treat a generated mesh as a reviewed collision shape.
- Do not add a database, task framework, or new runtime dependency.
- Do not retry a billable provider request automatically.

## Ownership and contract

### Current rejection controls

The runtime agent owns the policy mutation, idempotency, version checks, and score-epoch transition. The UI agent owns the class rows and pending, applied, conflict, and error presentation.

Each class row shows:

- authoritative class name
- authoritative shape and defect status
- severity
- current action, `Reject` or `Pass`
- pending state when a command is in flight
- policy version after acknowledgement
- a short conflict or command error

### Future generated-item draft

Jaume owns later UI integration, retraining, and activation. The future draft increment uses new runtime code plus a small endpoint addition. It does not reuse the engine command queue.

Proposed HTTP contract:

```text
POST /item-drafts
{ request_id: UUID, description: string }

GET /item-drafts/{request_id}
```

An exact duplicate request returns the existing job. A changed payload with the same UUID returns `409 request_conflict`. A full queue returns `429 queue_full`.

Keep one active job, at most four queued jobs, and 32 retained summaries. Store job records as atomic JSON files under a configured output directory. Do not add a database.

Use these states:

```text
queued
generating_recipe
waiting_for_render
rendering
proposing_physics
draft_ready
failed
```

Expose these errors when applicable:

```text
invalid_description
request_conflict
queue_full
credentials_missing
generation_failed
render_failed
physics_proposal_failed
artifact_validation_failed
```

A busy Blender lock is a visible `waiting_for_render` state. An unsupported physics proxy is a valid draft result. It requires human review and blocks activation.

The compact draft card shows:

- description and `Create draft` button
- current stage or one short error
- generated preview when available
- `object_type_id` and content-hash `visual_asset_id`
- dimensions in metres and declared orientation
- proxy type or `Unsupported`
- proxy-based mass estimate with an `Unmeasured estimate` label
- sorting status, including `Unassigned`
- a persistent `Not active` label

The card has no activation control. A bounded presentation buffer can retain recent job summaries. Rendering and job execution stay outside the browser render loop and the live engine worker.

## Phase 1: Create one asynchronous item draft

### Changes

1. Add `sim/coffee_sorter/item_drafts.py` with a bounded queue, exact-request idempotency, atomic status files, and one worker process.
2. Call the existing recipe generator and cached renderer from that process.
3. Call `propose_physics`, `build_object_definition`, and `validate_object_definition` after artifact validation.
4. Add the two same-origin HTTP endpoints to `live.py`. Keep the existing WebSocket policy command unchanged.
5. Add the compact draft card after the UI owner completes the current rejection controls.
6. Document the output directory, provider prerequisites, Blender lock behavior, and draft-only boundary in `LIVE.md`.

### Acceptance criteria

- The state and health endpoints advance while a draft job runs.
- Generation never runs on the HTTP event loop or engine worker.
- Queue and retained history bounds appear in state and tests.
- Exact retries never start a second provider request or render.
- Changed payloads with a reused UUID fail clearly.
- Missing credentials, busy rendering, provider failures, and invalid artifacts remain visible.
- A ready draft contains validated IDs, units, conventions, provenance, and review status.
- The draft cannot become injectable or active through this increment.
- Desktop and short-phone layouts keep the conveyor, current policy, job state, errors, and `Not active` label reachable without page scrolling.

### Verification

```sh
cd sim/coffee_sorter
/Users/taras/Documents/code/hackspain/.venv-coffee/bin/python -m unittest \
  test_item_drafts.py test_live.py test_object_definitions.py -v
/Users/taras/Documents/code/hackspain/.venv-coffee/bin/python -m py_compile \
  item_drafts.py live.py object_definitions.py
node --check live_web/live.js
cd ../..
git diff --check
```

Use fake generator and renderer adapters for queue, retry, and failure tests. Run one intentional real provider smoke only with Taras's authorization and configured credentials.

## Phase 2: Review and register the physical class

This is a later increment. A human reviews dimensions, orientation, collision proxy, mass basis, visual asset, sorting proposal, and multipart limits. Registration then adds a reviewed `ClassSpec`, pool capacity, camera representation, and immutable asset mapping.

Registration remains inactive. Unsupported geometry cannot pass this gate.

### Verification

```sh
cd sim/coffee_sorter
/Users/taras/Documents/code/hackspain/.venv-coffee/bin/python -m unittest \
  test_object_definitions.py test_generalization.py -v
/Users/taras/Documents/code/hackspain/.venv-coffee/bin/python bootstrap_model.py --help
git diff --check
```

Reviewers must compare proxy volume and mass assumptions with the actual contact-enabled engine shape.

## Phase 3: Retrain and prove model compatibility

This is a separate later increment. Build new training and holdout data for the registered class. Retrain through the existing pipeline. Reject activation if the artifact, manifest, class order, policy, or preset is incompatible.

Do not use generated beauty renders as proof of classifier performance.

### Verification

```sh
cd sim/coffee_sorter
/Users/taras/Documents/code/hackspain/.venv-coffee/bin/python -m unittest \
  test_generalization.py test_live.py -v
/Users/taras/Documents/code/hackspain/.venv-coffee/bin/python bootstrap_model.py --help
git diff --check
```

Record independent holdout results and artifact hashes before any activation proposal.

## Phase 4: Activate one reviewed item set

This is a separate later increment. Activation publishes one compatible set of profile, model, policy, preset, and viewer registry revisions. It starts a new session and new model, policy, and score epochs.

Activation must support rollback to the previous complete set. It must not hot-swap one component in isolation.

### Verification

```sh
cd sim/coffee_sorter
/Users/taras/Documents/code/hackspain/.venv-coffee/bin/python -m unittest \
  test_continuous_engine.py test_live.py test_generalization.py -v
cd ../..
git diff --check
```

Confirm that `/health`, `/state`, version fields, and score epochs all identify the same activated set.

## Manual E2E

After Phase 1 exists, start a local continuous backend with the proposed draft feature enabled:

```sh
cd /Users/taras/Documents/code/hackspain
.venv-coffee/bin/python sim/coffee_sorter/live.py \
  --port 8892 \
  --preset sim/coffee_sorter/configs/continuous_demo.json \
  --out /tmp/cinta-item-drafts \
  --item-drafts \
  --provider-env-file /absolute/path/to/provider.env \
  --blender /Applications/Blender.app/Contents/MacOS/Blender
```

Create and inspect one intentional draft. This command can trigger a paid provider request:

```sh
REQUEST_ID=$(python3 -c 'import uuid; print(uuid.uuid4())')
curl --fail-with-body -X POST http://127.0.0.1:8892/item-drafts \
  -H 'Content-Type: application/json' \
  -H 'Origin: http://127.0.0.1:8892' \
  --data "{\"request_id\":\"$REQUEST_ID\",\"description\":\"A small brass star token\"}"
curl --fail http://127.0.0.1:8892/item-drafts/$REQUEST_ID
curl --fail http://127.0.0.1:8892/health
curl --fail http://127.0.0.1:8892/state
```

Taras verifies these points in the browser:

1. The conveyor and scores continue while the draft progresses.
2. Rejection toggles remain usable during generation.
3. The stage, errors, preview, IDs, dimensions, proxy, and mass basis remain readable.
4. The card always shows `Not active`.
5. The injection choices do not change.
6. An unsupported proxy blocks later activation without marking generation as failed.
7. Reconnect and exact retry preserve one job and one provider request.
8. Desktop and short-phone layouts need no page scrolling.

Taras owns final functional acceptance.
