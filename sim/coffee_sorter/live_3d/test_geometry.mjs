import assert from 'node:assert/strict';
import test from 'node:test';

import {
  hasAuthoritativeLiveFields,
  liveInstanceScale,
  machineLayoutSignature,
  reserveInstance,
} from './geometry.mjs';

test('capsule scale stays in simulator local Z', () => {
  assert.deepEqual(liveInstanceScale('capsule', [.010, .002, .003]), [.002, .002, .012]);
  assert.deepEqual(liveInstanceScale('box', [.010, .002, .003]), [.010, .002, .003]);
});

test('instance reservation exposes capacity overflow', () => {
  const counts = new Map([['good', 0]]);
  assert.equal(reserveInstance(counts, 'good', 2), 0);
  assert.equal(reserveInstance(counts, 'good', 2), 1);
  assert.equal(reserveInstance(counts, 'good', 2), -1);
  assert.equal(counts.get('good'), 2);
});

test('machine layout signature changes with physical layout', () => {
  const layout = {
    belt_len: 1.1,
    belt_w: .5,
    belt_z: .6,
    cam_x: -.12,
    ej_x: .1,
    ej_z_offset: .045,
    n_nozzles: 64,
    split_x: .34,
    split_z_drop: .125,
  };
  assert.equal(machineLayoutSignature(layout), machineLayoutSignature({...layout}));
  assert.notEqual(machineLayoutSignature(layout), machineLayoutSignature({...layout, belt_w: .7}));
});

test('live objects require authoritative render fields', () => {
  const object = {
    object_id: 42,
    shape: 'ellipsoid',
    pos: [0, .1, .6],
    quat: [1, 0, 0, 0],
    axes: [.005, .0035, .0025],
    rgb: [.5, .6, .47],
  };
  assert.equal(hasAuthoritativeLiveFields(object), true);
  assert.equal(hasAuthoritativeLiveFields({...object, axes: undefined}), false);
  assert.equal(hasAuthoritativeLiveFields({...object, rgb: [2, 0, 0]}), false);
  assert.equal(hasAuthoritativeLiveFields({...object, quat: [0, 0, 0, 0]}), false);
  assert.equal(hasAuthoritativeLiveFields({...object, shape: 'generated'}), false);
});
