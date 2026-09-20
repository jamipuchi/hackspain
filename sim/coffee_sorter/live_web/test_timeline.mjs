import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';

import {PolicyIntentBuffer, compareExpectedOutcome, emptyMetricState, formatEngineRate, freezeItemRequest, jobActionLabel, jobActionPath, jobErrorLabel, jobQueueSignature, jobStateLabel, jobStateNote, normalizedClassPreview, normalizedJobSummary, profilePreviewScale, queueModeCue, resolvePendingRequest, samePresentationTimeline} from './timeline.mjs';

const snapshot = {
  session_id: 'session-a',
  command_epoch: 'commands-1',
  score_epoch_id: 'scores-1',
};

test('command retention epochs do not interrupt pose interpolation', () => {
  assert.equal(samePresentationTimeline(snapshot, {...snapshot, command_epoch: 'commands-2'}), true);
});

test('session and score epochs stop pose interpolation', () => {
  assert.equal(samePresentationTimeline(snapshot, {...snapshot, session_id: 'session-b'}), false);
  assert.equal(samePresentationTimeline(snapshot, {...snapshot, score_epoch_id: 'scores-2'}), false);
});

test('engine speed preserves zero and rejects unavailable values', () => {
  assert.equal(formatEngineRate(0), '0.00×');
  assert.equal(formatEngineRate(.2), '0.20×');
  assert.equal(formatEngineRate(undefined), null);
  assert.equal(formatEngineRate(null), null);
  assert.equal(formatEngineRate(Number.NaN), null);
  assert.equal(formatEngineRate(Number.POSITIVE_INFINITY), null);
});

test('policy intents stay bounded and reconnect with only the latest choices', () => {
  const pending = new PolicyIntentBuffer();
  pending.setCatalog(['good', 'stone', 'stick']);
  for (let index = 0; index < 1000; index++) {
    pending.record('good', index % 2 === 0);
    pending.record('stone', index % 3 === 0);
  }

  assert.equal(pending.size, 2);
  const reconnectIntents = pending.take();
  assert.equal(pending.size, 0);
  assert.deepEqual([...pending.apply(['stick'], reconnectIntents)].sort(), ['stick', 'stone']);
});

test('newer policy intent supersedes an active intent after a stale version', () => {
  const pending = new PolicyIntentBuffer();
  pending.setCatalog(['good', 'stone', 'stick']);
  const activeIntents = new Map([['good', true], ['stone', false]]);
  pending.record('good', false);
  pending.requeue(activeIntents);

  assert.equal(pending.size, 2);
  assert.deepEqual([...pending.apply(['good', 'stone', 'stick'])].sort(), ['stick']);
});

test('expired command epoch retains intent until fresh command metadata arrives', () => {
  const pending = new PolicyIntentBuffer();
  pending.setCatalog(['stone']);
  pending.record('stone', false);
  const sent = pending.take();
  pending.requeue(sent);
  pending.waitForFreshEpoch('commands-1');

  assert.equal(pending.canDispatch('commands-1'), false);
  assert.equal(pending.size, 1);
  assert.equal(pending.canDispatch('commands-2'), true);
  assert.deepEqual([...pending.apply(['stone'])], []);
});

test('class previews require explicit profile geometry with meter dimensions', () => {
  const item = {preview: {schema_version: 1, source: 'profile', shape: 'box', axes_m: [.004, .003, .002], rgb: [.4, .5, .6]}};
  assert.deepEqual(normalizedClassPreview(item), {shape: 'box', axes: [.004, .003, .002], rgb: [.4, .5, .6]});
  assert.equal(normalizedClassPreview({...item, preview: {...item.preview, source: 'prediction'}}), null);
  assert.equal(normalizedClassPreview({...item, preview: {...item.preview, axes_m: [4, 3, 0]}}), null);
});

test('profile half preview keeps the declared cut-half thickness', () => {
  const half = {shape: 'half', axes: [.00245, .0018, .00255], rgb: [.5, .4, .3]};
  assert.deepEqual(profilePreviewScale(half), [.00245, .0018, .00255]);
});

test('empty score metrics distinguish warm-up from structurally empty cohorts', () => {
  assert.equal(emptyMetricState({metric: 'reject_capture', warmingUp: true, catalogSize: 10, rejectSize: 9}), 'Computing');
  assert.equal(emptyMetricState({metric: 'reject_capture', warmingUp: true, catalogSize: 10, rejectSize: 0}), 'No samples');
  assert.equal(emptyMetricState({metric: 'keep_loss', warmingUp: true, catalogSize: 10, rejectSize: 10}), 'No samples');
  assert.equal(emptyMetricState({metric: 'sorting_accuracy', warmingUp: false, catalogSize: 10, rejectSize: 9}), 'No samples');
});

const summary = {
  request_id: '2b2d5a4e-1f2e-4d3c-8a9b-0c1d2e3f4a5b',
  display_name: '  Brass star token  ',
  description: 'A small brass star token',
  requester_name: ' Taras ',
  state: 'preview_ready',
  error: null,
  updated_at: '2026-09-20T09:01:00Z',
  created_at: '2026-09-20T09:00:00Z',
  preview: '/item-jobs/2b2d5a4e-1f2e-4d3c-8a9b-0c1d2e3f4a5b/previews/perspective.png',
  attempts: {generation: 1, render: 2, training: 0},
  progress: null,
  primary_action: null,
};

test('queue states and primary actions use one label table', () => {
  assert.equal(jobStateLabel('waiting_for_render'), 'Waiting for render');
  assert.equal(jobStateLabel('interrupted_uncertain'), 'Provider interrupted');
  assert.equal(jobStateLabel('not_a_state'), 'Unknown state: not_a_state');
  assert.equal(jobActionLabel('confirm_cleanup'), 'Confirm cleanup');
  assert.equal(jobActionLabel('delete_everything'), null);
  assert.equal(jobActionPath('abc', 'resolve_provider'), '/item-jobs/abc/resolve-provider');
  assert.equal(jobActionPath('abc', 'delete_everything'), null);
});

test('queue summaries normalize text and reject foreign preview URLs', () => {
  const normalized = normalizedJobSummary(summary);
  assert.equal(normalized.name, 'Brass star token');
  assert.equal(normalized.requester, 'Taras');
  assert.equal(normalized.preview, summary.preview);
  assert.deepEqual(normalized.attempts, {generation: 1, render: 2, training: 0});

  assert.equal(normalizedJobSummary({...summary, display_name: null}).name, 'A small brass star token');
  assert.equal(normalizedJobSummary({...summary, preview: 'https://elsewhere/p.png'}).preview, null);
  assert.equal(normalizedJobSummary({...summary, preview: '/item-jobs/other/previews/top.png'}).preview, null);
  assert.equal(normalizedJobSummary({...summary, primary_action: 'unsupported'}).action, null);
  // An unknown state keeps its row. Only a missing id or a missing state drops it.
  assert.equal(normalizedJobSummary({...summary, state: ''}), null);
  assert.equal(normalizedJobSummary({...summary, request_id: 7}), null);
  assert.equal(normalizedJobSummary(null), null);
});

test('queue signature changes only with visible queue fields', () => {
  const same = jobQueueSignature([summary]);
  assert.equal(jobQueueSignature([{...summary, description: 'unchanged row text'}]), same);
  assert.notEqual(jobQueueSignature([{...summary, state: 'failed'}]), same);
  assert.notEqual(jobQueueSignature([{...summary, error: 'render_failed'}]), same);
  assert.notEqual(jobQueueSignature([summary, summary]), same);
  assert.equal(jobQueueSignature(null), '');
});

test('queue rows and archived rows share one head builder', () => {
  // These rows have no DOM harness here. This keeps the duplicated structure from returning.
  const source = readFileSync(new URL('./live.js', import.meta.url), 'utf8');
  const body = name => source.split(`function ${name}(`)[1].split('\nfunction ')[0];
  assert.equal(source.split("className = 'job-head'").length - 1, 1);
  assert.match(body('jobRow'), /buildJobHead\(\{/);
  assert.match(body('wallRow'), /buildJobHead\(\{/);
  assert.equal(source.includes('innerHTML'), false);
  // t3: one error table only. live.js must not carry a second one.
  assert.equal(source.includes('ITEM_JOB_ERRORS'), false);
  // T5: no dead discard path.
  assert.equal(source.includes('adoptPendingJob'), false);
  assert.equal(source.includes('item-discard'), false);
});

test('a pending cue is applied only while the id is absent from the summaries', () => {
  // T4: a packet that does not hold the id must not restate the waiting cue.
  const source = readFileSync(new URL('./live.js', import.meta.url), 'utf8');
  const body = source.split('function updateItemQueue(')[1].split('\nfunction ')[0];
  assert.match(body, /known\.includes\(pendingItemRequest\.request_id\)/);
  const snapshot = freezeItemRequest({requestId: 'id-9', description: 'A token', catalogRevision: 'a'.repeat(64)});
  assert.equal(resolvePendingRequest(snapshot, {kind: 'queue', requestIds: ['id-9']}).resolved, true);
  assert.equal(resolvePendingRequest(snapshot, {kind: 'queue', requestIds: []}).resolved, false);
});

test('physical outcome comparison treats spills as unexpected', () => {
  assert.deepEqual(compareExpectedOutcome('reject', 'reject'), {expectedLabel: 'Reject', actualLabel: 'Rejected', verdict: 'As expected'});
  assert.deepEqual(compareExpectedOutcome('accept', 'spilled'), {expectedLabel: 'Keep', actualLabel: 'Spilled', verdict: 'Unexpected'});
  assert.deepEqual(compareExpectedOutcome('reject', null), {expectedLabel: 'Reject', actualLabel: 'In progress', verdict: 'In progress'});
});

test('a pending request snapshot carries exactly the submitted fields', () => {
  const snapshot = freezeItemRequest({
    requestId: 'id-1', description: 'A brass star token', requesterName: 'Taras',
    catalogRevision: 'a'.repeat(64),
  });
  assert.deepEqual(Object.keys(snapshot).sort(),
    ['description', 'expected_catalog_revision', 'request_id', 'requester_name']);
  assert.equal(snapshot.expected_catalog_revision, 'a'.repeat(64));
  // An absent requester must not become an empty string in the request body.
  const anonymous = freezeItemRequest({requestId: 'id-1', description: 'x', requesterName: '', catalogRevision: 'b'});
  assert.equal('requester_name' in anonymous, false);
});

test('a lost response then a moved catalog revision retries the same bytes', () => {
  // The client under test: it only ever sends the snapshot it holds.
  let pending = null;
  const sent = [];
  const submit = (form, state) => {
    pending = pending || freezeItemRequest({
      requestId: 'id-1', description: form.description, catalogRevision: state.revision});
    sent.push(JSON.stringify(pending));
  };

  submit({description: 'A token'}, {revision: 'a'.repeat(64)});
  assert.equal(resolvePendingRequest(pending, {kind: 'network'}).resolved, false);
  // The catalog moved and the user retyped. Neither may reach the wire.
  submit({description: 'Something else'}, {revision: 'b'.repeat(64)});

  assert.equal(sent.length, 2);
  assert.equal(sent[0], sent[1]);
  assert.equal(JSON.parse(sent[1]).expected_catalog_revision, 'a'.repeat(64));
  assert.equal(JSON.parse(sent[1]).description, 'A token');
});

test('a definitive rejection keeps the typed text and unlocks a new request', () => {
  const snapshot = freezeItemRequest({requestId: 'id-2', description: 'Original text', catalogRevision: 'c'.repeat(64)});
  const decision = resolvePendingRequest(snapshot, {kind: 'rejected', errorCode: 'invalid_description'});
  assert.equal(decision.resolved, true);
  assert.equal(decision.failed, true);
  assert.equal(decision.adopt, false);
  assert.match(decision.cue, /1 to 600 characters/);
});

test('a job appearing in the queue resolves a lost response without a resend', () => {
  const snapshot = freezeItemRequest({requestId: 'id-3', description: 'A token', catalogRevision: 'd'.repeat(64)});
  const decision = resolvePendingRequest(snapshot, {kind: 'queue', requestIds: ['other', 'id-3']});
  assert.equal(decision.resolved, true);
  assert.equal(decision.failed, false);
  assert.equal(decision.adopt, true);
  assert.equal(resolvePendingRequest(snapshot, {kind: 'queue', requestIds: ['other']}).resolved, false);
});

test('request_conflict adopts the existing job and never mints a new id', () => {
  const snapshot = freezeItemRequest({requestId: 'id-4', description: 'A token', catalogRevision: 'e'.repeat(64)});
  const decision = resolvePendingRequest(snapshot, {kind: 'rejected', errorCode: 'request_conflict'});
  assert.equal(decision.adopt, true);
  assert.equal(decision.resolved, true);
  assert.equal(decision.failed, true);
  assert.match(decision.cue, /already holds this request/);
});

test('only a definitive rejection allows discarding the pending request', () => {
  const snapshot = freezeItemRequest({requestId: 'id-5', description: 'A token', catalogRevision: 'f'.repeat(64)});
  for (const errorCode of ['invalid_description', 'invalid_request', 'queue_full', 'catalog_revision_conflict', 'origin_required']) {
    const decision = resolvePendingRequest(snapshot, {kind: 'rejected', errorCode});
    assert.equal(decision.resolved, true, errorCode);
    assert.equal(decision.failed, true, errorCode);
    assert.notEqual(decision.cue, '');
  }
  for (const outcome of [{kind: 'network'}, {kind: 'rejected', errorCode: 'internal'}, {kind: 'rejected', errorCode: undefined}]) {
    const decision = resolvePendingRequest(snapshot, outcome);
    assert.equal(decision.resolved, false);
    assert.equal(decision.adopt, false);
    assert.match(decision.cue, /Retry sends the same request/);
  }
  // No decision field may be dead: every one of them is read by live.js.
  assert.deepEqual(Object.keys(resolvePendingRequest(snapshot, {kind: 'network'})).sort(),
    ['adopt', 'cue', 'failed', 'resolved']);
});

test('an unknown queue state stays visible with a cue instead of vanishing', () => {
  const unknown = normalizedJobSummary({...summary, state: 'invented_state'});
  assert.notEqual(unknown, null);
  assert.equal(unknown.state, 'invented_state');
  assert.equal(jobStateLabel('invented_state'), 'Unknown state: invented_state');
  assert.equal(jobErrorLabel('invented_error'), 'Unknown error: invented_error');
  assert.equal(jobErrorLabel(null), null);
});

test('the operator_required row says plainly that nothing was sent or billed', () => {
  assert.equal(jobStateLabel('operator_required'), 'Operator action needed');
  assert.match(jobStateNote('operator_required'), /operator must act/);
  assert.match(jobStateNote('operator_required'), /nothing was billed/);
  assert.match(jobErrorLabel('provider_cache_miss'), /Nothing was sent and nothing was billed/);
  assert.match(jobErrorLabel('paid_mode_disabled'), /Paid mode is disabled/);
});

test('the queue cue comes from the authoritative provider mode', () => {
  assert.equal(queueModeCue('cached'), 'Shared queue. Cached provider results only.');
  assert.match(queueModeCue('paid'), /one operator approval permits one generation attempt/i);
  assert.match(queueModeCue('paid'), /up to two provider requests/);
  assert.match(queueModeCue('fake'), /no provider call and no activation/);
  // Before the first packet the page says nothing about providers.
  assert.equal(queueModeCue(null), 'Connecting');
  assert.equal(queueModeCue(undefined), 'Connecting');
  assert.match(queueModeCue('invented'), /Unknown provider mode: invented/);
  // The page must not carry a default that contradicts the mode.
  const html = readFileSync(new URL('./index.html', import.meta.url), 'utf8');
  assert.equal(html.includes('paid approval'), false);
  assert.match(html, /id="item-mode-cue" class="policy-status">Connecting</);
});
