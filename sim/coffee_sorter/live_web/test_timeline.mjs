import assert from 'node:assert/strict';
import test from 'node:test';

import {samePresentationTimeline} from './timeline.mjs';

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
