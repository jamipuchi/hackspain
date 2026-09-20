# Increment C: FROZEN summary field names (server to UI contract)

Status: frozen by the orchestrator for the UI owner. Names and types below do not change without a coordinated notice. Values may be `null` until the owning stage has run. Every string is path-free (the public walker applies). No new job state and no new error code comes with this contract.

Surface: every entry of `item_jobs.summaries` in the state packet. The EXISTING summary keys stay exactly as they are (`request_id`, `display_name`, `description`, `requester_name`, `state`, `error`, `updated_at`, `created_at`, `preview`, `attempts`, `progress`, `reason`, `blocked_stage`, `primary_action`, `provider_mode`, `provider_cache_hit`). `description` and `requester_name` are published verbatim. Four ADDITIVE blocks follow. Each block is always present as an object. Its members are `null` when unknown.

## `replacement` (replacement victim and active ordering)
| name | type | meaning |
|---|---|---|
| `victim_type_id` | string or null | `object_type_id` of the type that leaves the active set |
| `victim_label` | string or null | its classifier label |
| `new_type_id` | string or null | `object_type_id` of the generated type |
| `new_label` | string or null | its classifier label |
| `active_type_ids_before` | string[] or null | active ordering at the training baseline |
| `active_type_ids_after` | string[] or null | candidate ordering: the new type first, survivors keep their order, same count |

## `activation` (drain progress and activation result)
| name | type | meaning |
|---|---|---|
| `phase` | null, `"draining"`, `"activating"`, `"active"`, `"failed"` | where the activation is |
| `active_objects` | integer or null | objects still on the belt during the drain |
| `drain_timeout_seconds` | number or null | the wall bound of the drain (20.0) |
| `result` | null, `"active"`, `"replacement_conflict"`, `"activation_conflict"`, `"activation_failed"` | final outcome, same codes as the job `error` |
| `rolled_back` | boolean or null | true when a post-stop failure restored the prior bundle (new session, new score epoch, never a continuity claim) |
| `message` | string or null | short, path-free operator text (the rollback message lives here) |

## `identities` (model, policy, session, and score epoch identities)
| name | type | meaning |
|---|---|---|
| `baseline_catalog_revision` | string or null | catalog revision the training baseline bound |
| `candidate_catalog_revision` | string or null | revision of the candidate catalog |
| `model_artifact_sha256` | string or null | sha256 of the candidate model artifact |
| `bundle_sha256` | string or null | the activated bundle |
| `previous_bundle_sha256` | string or null | the bundle that was active before |
| `baseline_policy_version` | string or null | policy at the training baseline |
| `validation_policy_version` | string or null | policy that candidate validation observed |
| `activation_policy_version` | string or null | policy captured under the activation fence |
| `session_id` | string or null | engine session AFTER the activation |
| `score_epoch_id` | string or null | score epoch AFTER the activation |

The three policy versions are evidence only. They are never required to match.

## `evidence` (provider and cache evidence)
| name | type | meaning |
|---|---|---|
| `evidence_kind` | null, `"cached_generation_cached_physics"`, `"paid_generation_cached_physics"`, `"fake"` | the UI maps `cached_generation_cached_physics` to the EXACT label `Real cached generation + cached physics estimate` |
| `provider_submission` | string | the existing submission state (`not_submitted`, ...) |
| `physics_source` | null, `"cached_llm_replay"`, `"paid_llm_call"` | where the physics proposal came from. A live call is never labeled as a replay |
| `physics_measurement_status` | string or null | today only `unmeasured_proxy_estimate` |
| `recipe_sha256` | string or null | the recipe bytes |
| `glb_sha256` | string or null | the rendered GLB bytes |
| `recipe_request_sha256` | string or null | the recipe request identity |
| `physics_request_sha256` | string or null | the physics request identity |
| `physics_cache_entry_sha256` | string or null | the replayed physics cache entry |

`provider_mode` and `provider_cache_hit` stay at the summary top level, unchanged.

### `evidence_kind` derivation (exact, confirmed to root)
| provider mode of the job | generation came from | `physics_source` | `evidence_kind` |
|---|---|---|---|
| `fake` | any | any | `"fake"` |
| `cached` or `paid` | the provider cache (no call) | `cached_llm_replay` | `"cached_generation_cached_physics"` |
| `paid` | a real call | `cached_llm_replay` | `"paid_generation_cached_physics"` |
| any | any | `paid_llm_call` | `null` |
| anything else, or a stage that has not run | | | `null` |

`evidence_kind` NEVER names cached physics when `physics_source` is `paid_llm_call`. A `null` kind means: the UI shows its neutral label and may read `physics_source` and `provider_submission` directly. The UI tolerates a missing block or `null` members during rollout. No field is renamed silently: any change to this file is announced first.

## Record sources (for the two server owners, not for the UI)
- `replacement`: `training_baseline` (`victim_id`, `active_type_ids`), the candidate catalog, `definition.json`.
- ONE new record block `activation`, written only by the Phase 4 activator (L2) through the store's public `record`, with EXACTLY these eleven members: the six `activation` members (`phase`, `active_objects`, `drain_timeout_seconds`, `result`, `rolled_back`, `message`) plus `bundle_sha256`, `previous_bundle_sha256`, `activation_policy_version`, `session_id`, `score_epoch_id`. The summary maps the last five into `identities`.
- `identities.model_artifact_sha256`, `baseline_catalog_revision`, and `candidate_catalog_revision` come from the TRAINING side (`training_baseline`, the candidate model manifest), never from the activator.
- `identities` policy versions: `training_baseline.policy_version`, `artifacts.validation_policy.policy_version`, `activation.activation_policy_version`.
- `evidence`: `provider_submission`, `provider_evidence`, `artifacts.physics` (the content of `physics.json`).
