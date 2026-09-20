import assert from 'node:assert/strict';
import test from 'node:test';

import {PolicyIntentBuffer, compareExpectedOutcome, emptyMetricState, formatEngineRate, normalizedClassPreview, profilePreviewScale, samePresentationTimeline} from './timeline.mjs';

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

test('physical outcome comparison treats spills as unexpected', () => {
  assert.deepEqual(compareExpectedOutcome('reject', 'reject'), {expectedLabel: 'Reject', actualLabel: 'Rejected', verdict: 'As expected'});
  assert.deepEqual(compareExpectedOutcome('accept', 'spilled'), {expectedLabel: 'Keep', actualLabel: 'Spilled', verdict: 'Unexpected'});
  assert.deepEqual(compareExpectedOutcome('reject', null), {expectedLabel: 'Reject', actualLabel: 'In progress', verdict: 'In progress'});
});
