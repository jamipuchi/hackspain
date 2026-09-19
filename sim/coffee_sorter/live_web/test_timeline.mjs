import assert from 'node:assert/strict';
import test from 'node:test';

import {PolicyIntentBuffer, samePresentationTimeline} from './timeline.mjs';

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
