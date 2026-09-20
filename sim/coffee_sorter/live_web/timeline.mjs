function scoreEpoch(state) {
  return state?.score_epoch_id || state?.reject_policy?.score_epoch_id || null;
}

export function samePresentationTimeline(left, right) {
  return left?.session_id === right?.session_id && scoreEpoch(left) === scoreEpoch(right);
}

export function formatEngineRate(value) {
  return typeof value === 'number' && Number.isFinite(value) ? `${value.toFixed(2)}×` : null;
}

export class PolicyIntentBuffer {
  constructor() {
    this.catalog = new Set();
    this.intents = new Map();
    this.expiredEpoch = null;
  }

  setCatalog(names) {
    this.catalog = new Set(names);
    for (const name of this.intents.keys()) {
      if (!this.catalog.has(name)) this.intents.delete(name);
    }
  }

  record(name, reject) {
    if (!this.catalog.has(name)) return false;
    this.intents.set(name, Boolean(reject));
    return true;
  }

  clear() {
    this.intents.clear();
    this.expiredEpoch = null;
  }

  take() {
    const taken = new Map(this.intents);
    this.intents.clear();
    return taken;
  }

  requeue(olderIntents) {
    const newerIntents = this.intents;
    this.intents = new Map();
    for (const [name, reject] of olderIntents) this.record(name, reject);
    for (const [name, reject] of newerIntents) this.record(name, reject);
  }

  apply(baseClasses, intents = this.intents) {
    const target = new Set(baseClasses);
    for (const [name, reject] of intents) {
      if (reject) target.add(name);
      else target.delete(name);
    }
    return target;
  }

  waitForFreshEpoch(expiredEpoch) {
    this.expiredEpoch = expiredEpoch;
  }

  canDispatch(commandEpoch) {
    return Boolean(commandEpoch) && commandEpoch !== this.expiredEpoch;
  }

  acceptEpoch(commandEpoch) {
    if (this.canDispatch(commandEpoch)) this.expiredEpoch = null;
  }

  get size() {
    return this.intents.size;
  }
}

const PREVIEW_SHAPES = new Set(['ellipsoid', 'half', 'box', 'capsule']);

export function normalizedClassPreview(item) {
  const preview = item?.preview;
  if (preview?.schema_version !== 1 || preview.source !== 'profile' || !PREVIEW_SHAPES.has(preview.shape)) return null;
  const validVector = (value, positive = false) => Array.isArray(value) && value.length === 3
    && value.every(component => Number.isFinite(component) && (!positive || component > 0));
  if (!validVector(preview.axes_m, true) || !validVector(preview.rgb)
      || preview.rgb.some(component => component < 0 || component > 1)) return null;
  return {shape: preview.shape, axes: preview.axes_m.slice(), rgb: preview.rgb.slice()};
}

export function profilePreviewScale(preview) {
  if (!preview) return null;
  const [x, y, z] = preview.axes;
  if (preview.shape === 'capsule') return [y, y, x + y];
  // The half primitive has normalized bounds 2 x 2 x 1. Its profile z value
  // is the full thickness after the parent ellipsoid is cut in half.
  return [x, y, z];
}

const COLLECTION_SURFACE_NAMES = Object.freeze([
  'splitter', 'bin_accept', 'bin_reject', 'bin_accept_end', 'bin_reject_end',
]);

export function normalizedCollectionSurfaces(value) {
  if (!Array.isArray(value) || value.length !== COLLECTION_SURFACE_NAMES.length) return null;
  const finiteVector = (vector, length, positive = false) => Array.isArray(vector)
    && vector.length === length
    && vector.every(component => Number.isFinite(component) && (!positive || component > 0));
  const byName = new Map();
  for (const surface of value) {
    if (!COLLECTION_SURFACE_NAMES.includes(surface?.name) || byName.has(surface.name)
        || !finiteVector(surface.center_m, 3)
        || !finiteVector(surface.half_size_m, 3, true)
        || !finiteVector(surface.quaternion_wxyz, 4)) return null;
    const magnitude = Math.hypot(...surface.quaternion_wxyz);
    if (magnitude < .999 || magnitude > 1.001) return null;
    byName.set(surface.name, {
      name: surface.name,
      center: surface.center_m.slice(),
      halfSize: surface.half_size_m.slice(),
      quaternionWxyz: surface.quaternion_wxyz.map(component => component / magnitude),
    });
  }
  if (COLLECTION_SURFACE_NAMES.some(name => !byName.has(name))) return null;
  return COLLECTION_SURFACE_NAMES.map(name => byName.get(name));
}

export function emptyMetricState({metric, warmingUp, catalogSize, rejectSize}) {
  const cohortCanReceiveSamples = metric === 'reject_capture' || metric === 'defect_capture'
    ? rejectSize > 0
    : metric === 'keep_loss' || metric === 'good_loss'
      ? rejectSize < catalogSize
      : catalogSize > 0;
  return warmingUp && cohortCanReceiveSamples ? 'Computing' : 'No samples';
}

const JOB_STATE_LABELS = {
  queued: 'Queued',
  generating_recipe: 'Generating recipe',
  operator_required: 'Operator action needed',
  interrupted_uncertain: 'Provider interrupted',
  waiting_for_render: 'Waiting for render',
  rendering_previews: 'Rendering previews',
  worker_unavailable: 'Worker unavailable',
  preview_ready: 'Preview ready',
  proposing_physics: 'Proposing physics',
  validating_physics: 'Validating physics',
  physics_blocked: 'Physics blocked',
  selecting_training_baseline: 'Selecting training baseline',
  queued_for_training: 'Queued for training',
  training: 'Training',
  validating_candidate: 'Validating candidate',
  waiting_for_replacement: 'Waiting for replacement',
  draining_for_activation: 'Draining for activation',
  activating: 'Activating',
  active: 'Active',
  replacement_conflict: 'Replacement conflict',
  activation_conflict: 'Activation conflict',
  failed: 'Failed',
};
const JOB_ACTION_LABELS = {
  resolve_provider: 'Resolve provider',
  resolve_replacement: 'Resolve replacement',
  confirm_cleanup: 'Confirm cleanup',
};
const JOB_ACTION_PATHS = {
  resolve_provider: 'resolve-provider',
  resolve_replacement: 'resolve-replacement',
  confirm_cleanup: 'confirm-cleanup',
};
const JOB_ERROR_LABELS = {
  history_full: 'The item history is full. An operator must archive it.',
  invalid_description: 'Use 1 to 600 characters for the description.',
  invalid_request: 'The service does not accept those request fields.',
  invalid_action: 'That action is not supported.',
  origin_required: 'Use the page served by this loopback service.',
  not_available: 'That recovery action is not available yet.',
  unknown_job: 'That job is no longer available.',
  unknown_preview: 'That preview name is not served.',
  preview_unavailable: 'The preview is not available.',
  unsupported_job_schema: 'The stored job record uses an unsupported schema version.',
  fake_provider_not_activatable: 'A fake job can never be activated.',
  stale_token: 'A newer worker owns this job.',
  request_conflict: 'That request id already carries a different description.',
  queue_full: 'The queue was full.',
  catalog_revision_conflict: 'The catalog changed before admission.',
  credentials_missing: 'No credential was available for an authorized request.',
  provider_cache_miss: 'Not in the provider cache. Nothing was sent and nothing was billed.',
  paid_mode_disabled: 'Paid mode is disabled, so no billable request is possible.',
  provider_interrupted: 'A provider request was interrupted. Its billing state is unknown.',
  generation_failed: 'Recipe generation failed.',
  render_failed: 'Preview rendering failed.',
  cache_entry_invalid: 'The cached provider evidence failed verification.',
  worker_timeout: 'The worker exceeded its lease.',
  worker_unavailable: 'A worker process group did not confirm its exit.',
  physics_unsupported: 'The engine has no honest contact proxy for this shape.',
  training_failed: 'Candidate training failed.',
  candidate_validation_failed: 'Candidate validation failed.',
  replacement_conflict: 'The replacement type changed.',
  activation_failed: 'Activation failed and the previous bundle stayed active.',
};
// Plain wording for the one row an operator must act on.
const JOB_STATE_NOTES = {
  operator_required: 'An operator must act. Nothing was sent to a provider and nothing was billed.',
  interrupted_uncertain: 'An operator must act. One request may have reached the provider.',
  worker_unavailable: 'An operator must confirm that the worker process group exited.',
};
// A definitive rejection proves the server holds NO job for the pending id. A network
// error, a timeout, an aborted fetch, or a 5xx proves nothing and never resolves.
const PENDING_RESOLVING_ERRORS = new Set([
  'invalid_description', 'invalid_request', 'queue_full', 'history_full',
  'catalog_revision_conflict', 'origin_required',
]);

export function jobStateLabel(state) {
  // An unknown state stays visible, verbatim, with a cue. It must never vanish.
  return JOB_STATE_LABELS[state] || `Unknown state: ${state}`;
}

export function jobStateNote(state) {
  return JOB_STATE_NOTES[state] || null;
}

export function jobErrorLabel(error) {
  if (!error) return null;
  return JOB_ERROR_LABELS[error] || `Unknown error: ${error}`;
}

// The authoritative provider mode decides this copy. The page never defaults it.
const QUEUE_MODE_CUES = {
  cached: 'Shared queue. Cached provider results only.',
  paid: 'Shared queue. One operator approval permits one generation attempt, '
    + 'which can send up to two provider requests.',
  fake: 'Shared queue. Test data only: no provider call and no activation can occur.',
};

export function queueModeCue(providerMode) {
  if (providerMode === undefined || providerMode === null) return 'Connecting';
  return QUEUE_MODE_CUES[providerMode] || `Shared queue. Unknown provider mode: ${providerMode}`;
}

export function jobStateLabelKeys() {
  return Object.keys(JOB_STATE_LABELS);
}

export function jobErrorLabelKeys() {
  return Object.keys(JOB_ERROR_LABELS);
}

// One immutable snapshot per pending request. A retry sends exactly these bytes, so an
// edited form or a moved catalog revision can never turn a retry into a second job.
export function freezeItemRequest({requestId, description, requesterName, catalogRevision}) {
  const body = {request_id: requestId, description, expected_catalog_revision: catalogRevision};
  if (requesterName) body.requester_name = requesterName;
  return Object.freeze(body);
}

export function resolvePendingRequest(snapshot, outcome) {
  if (!snapshot) return {resolved: true, adopt: false, failed: false, cue: ''};
  const id = snapshot.request_id;
  if (outcome?.kind === 'accepted' && outcome.requestId === id) {
    return {resolved: true, adopt: false, failed: false, cue: 'Queued'};
  }
  if (outcome?.kind === 'queue' && (outcome.requestIds || []).includes(id)) {
    // The first response was lost but the job exists. Adopt it and never resend.
    return {resolved: true, adopt: true, failed: false,
            cue: 'Your request is already queued.'};
  }
  if (outcome?.kind === 'rejected') {
    if (outcome.errorCode === 'request_conflict') {
      // The server already holds a job for this id. A new id would duplicate it.
      return {resolved: true, adopt: true, failed: true,
              cue: 'The server already holds this request. Showing the existing job.'};
    }
    if (PENDING_RESOLVING_ERRORS.has(outcome.errorCode)) {
      // Proven that no job exists. Keep what the user typed so they can edit and resend.
      return {resolved: true, adopt: false, failed: true,
              cue: jobErrorLabel(outcome.errorCode) || 'The service rejected the request.'};
    }
  }
  return {resolved: false, adopt: false, failed: true,
          cue: 'Waiting for the server to confirm your request. Retry sends the same request.'};
}

export function jobActionLabel(action) {
  return JOB_ACTION_LABELS[action] || null;
}

export function jobActionPath(requestId, action) {
  const suffix = JOB_ACTION_PATHS[action];
  return suffix && requestId ? `/item-jobs/${encodeURIComponent(requestId)}/${suffix}` : null;
}

const ACTIVATION_PHASES = new Set(['draining', 'activating', 'active', 'failed']);
const ACTIVATION_RESULTS = new Set([
  'active', 'replacement_conflict', 'activation_conflict', 'activation_failed',
]);
const EVIDENCE_KINDS = new Set([
  'cached_generation_cached_physics', 'paid_generation_cached_physics', 'fake',
]);
const PHYSICS_SOURCES = new Set(['cached_llm_replay', 'paid_llm_call']);

export function jobEvidenceLabel(evidence) {
  if (evidence?.kind === 'cached_generation_cached_physics'
      && evidence?.physicsSource === 'cached_llm_replay') {
    return 'Real cached generation + cached physics estimate';
  }
  if (evidence?.kind === 'fake') return 'Fake test adapter';
  if (evidence?.kind === 'paid_generation_cached_physics'
      && evidence?.physicsSource === 'cached_llm_replay') {
    return 'Provider generation + cached physics estimate';
  }
  if (evidence?.physicsSource === 'paid_llm_call') return 'Provider physics estimate';
  if (evidence?.providerSubmission || evidence?.physicsSource || evidence?.kind) {
    return 'Provider evidence available';
  }
  return null;
}

export function jobReplacementLabel(replacement) {
  if (replacement?.newLabel && replacement?.victimLabel) {
    return `${replacement.newLabel} replaces ${replacement.victimLabel}`;
  }
  if (replacement?.newLabel) return `New class ${replacement.newLabel}`;
  if (replacement?.victimLabel) return `Replaces ${replacement.victimLabel}`;
  return null;
}

export function jobActivationLabel(activation) {
  if (activation?.phase === 'draining') {
    return Number.isInteger(activation.activeObjects)
      ? `Draining · ${activation.activeObjects} on belt` : 'Draining';
  }
  if (activation?.phase === 'activating') return 'Activating';
  if (activation?.phase === 'active' || activation?.result === 'active') return 'Active';
  if (activation?.phase === 'failed' || activation?.result) {
    return activation?.rolledBack === true ? 'Activation failed · previous bundle restored'
      : 'Activation failed';
  }
  return null;
}

// The server owns queue truth. This keeps one presentation shape and rejects anything else.
export function normalizedJobSummary(value) {
  const requestId = value?.request_id;
  // An unknown state is shown verbatim with a cue. Dropping the row would hide a job.
  if (typeof requestId !== 'string' || typeof value.state !== 'string' || !value.state) return null;
  const text = (item, limit) => typeof item === 'string' && item.trim() ? item.trim().slice(0, limit) : null;
  const digest = item => typeof item === 'string' && /^[0-9a-f]{64}$/.test(item) ? item : null;
  const textList = item => Array.isArray(item) && item.length <= 64
    && item.every(entry => typeof entry === 'string' && entry.length > 0)
    ? item.map(entry => entry.slice(0, 200)) : null;
  const replacement = value.replacement && typeof value.replacement === 'object' ? value.replacement : {};
  const activation = value.activation && typeof value.activation === 'object' ? value.activation : {};
  const identities = value.identities && typeof value.identities === 'object' ? value.identities : {};
  const evidence = value.evidence && typeof value.evidence === 'object' ? value.evidence : {};
  const previewPrefix = `/item-jobs/${requestId}/previews/`;
  return {
    requestId,
    name: text(value.display_name, 120) || text(value.description, 120) || 'Untitled item',
    description: text(value.description, 600),
    requester: text(value.requester_name, 80),
    state: value.state,
    error: text(value.error, 80),
    progress: text(value.progress, 120),
    updatedAt: text(value.updated_at, 20),
    createdAt: text(value.created_at, 20),
    preview: typeof value.preview === 'string' && value.preview.startsWith(previewPrefix) ? value.preview : null,
    attempts: value.attempts && typeof value.attempts === 'object' ? {...value.attempts} : {},
    action: JOB_ACTION_LABELS[value.primary_action] ? value.primary_action : null,
    providerMode: text(value.provider_mode, 16),
    cacheHit: typeof value.provider_cache_hit === 'boolean' ? value.provider_cache_hit : null,
    replacement: {
      victimTypeId: text(replacement.victim_type_id, 200),
      victimLabel: text(replacement.victim_label, 120),
      newTypeId: text(replacement.new_type_id, 200),
      newLabel: text(replacement.new_label, 120),
      activeTypeIdsBefore: textList(replacement.active_type_ids_before),
      activeTypeIdsAfter: textList(replacement.active_type_ids_after),
    },
    activation: {
      phase: ACTIVATION_PHASES.has(activation.phase) ? activation.phase : null,
      activeObjects: Number.isInteger(activation.active_objects) && activation.active_objects >= 0
        ? activation.active_objects : null,
      drainTimeoutSeconds: typeof activation.drain_timeout_seconds === 'number'
        && Number.isFinite(activation.drain_timeout_seconds) && activation.drain_timeout_seconds >= 0
        ? activation.drain_timeout_seconds : null,
      result: ACTIVATION_RESULTS.has(activation.result) ? activation.result : null,
      rolledBack: typeof activation.rolled_back === 'boolean' ? activation.rolled_back : null,
      message: text(activation.message, 240),
    },
    identities: {
      baselineCatalogRevision: digest(identities.baseline_catalog_revision),
      candidateCatalogRevision: digest(identities.candidate_catalog_revision),
      modelArtifactSha256: digest(identities.model_artifact_sha256),
      bundleSha256: digest(identities.bundle_sha256),
      previousBundleSha256: digest(identities.previous_bundle_sha256),
      baselinePolicyVersion: text(identities.baseline_policy_version, 200),
      validationPolicyVersion: text(identities.validation_policy_version, 200),
      activationPolicyVersion: text(identities.activation_policy_version, 200),
      sessionId: text(identities.session_id, 200),
      scoreEpochId: text(identities.score_epoch_id, 200),
    },
    evidence: {
      kind: EVIDENCE_KINDS.has(evidence.evidence_kind) ? evidence.evidence_kind : null,
      providerSubmission: text(evidence.provider_submission, 80),
      physicsSource: PHYSICS_SOURCES.has(evidence.physics_source) ? evidence.physics_source : null,
      physicsMeasurementStatus: text(evidence.physics_measurement_status, 80),
      recipeSha256: digest(evidence.recipe_sha256),
      glbSha256: digest(evidence.glb_sha256),
      recipeRequestSha256: digest(evidence.recipe_request_sha256),
      physicsRequestSha256: digest(evidence.physics_request_sha256),
      physicsCacheEntrySha256: digest(evidence.physics_cache_entry_sha256),
    },
  };
}

export function jobQueueSignature(summaries) {
  return (Array.isArray(summaries) ? summaries : []).map(item =>
    [item?.request_id, item?.state, item?.error, item?.updated_at, item?.preview,
     item?.progress, item?.primary_action, JSON.stringify(item?.replacement || null),
     JSON.stringify(item?.activation || null), JSON.stringify(item?.identities || null),
     JSON.stringify(item?.evidence || null)].join(':')).join('|');
}

export function compareExpectedOutcome(expected, outcome) {
  const expectedLabel = expected === 'reject' ? 'Reject' : expected === 'accept' ? 'Keep' : 'In progress';
  const actualLabel = outcome === 'reject' ? 'Rejected' : outcome === 'accept' ? 'Passed' : outcome === 'spilled' ? 'Spilled' : 'In progress';
  if (!['reject', 'accept'].includes(expected) || !['reject', 'accept', 'spilled'].includes(outcome)) {
    return {expectedLabel, actualLabel, verdict: 'In progress'};
  }
  return {expectedLabel, actualLabel, verdict: expected === outcome ? 'As expected' : 'Unexpected'};
}
