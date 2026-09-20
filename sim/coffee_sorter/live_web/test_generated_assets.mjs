import assert from 'node:assert/strict';
import test from 'node:test';

import {ASSET_CAPS, acceptedAssetRegistry, assetUrl, chooseObjectAsset, generatedAssetMetrics, instanceScale, mustResetGeneratedPools, parsedAssetRefusal, planAssetLoads, prepareGeometry, proxyExtents} from './generated_assets.mjs';

const REVISION = '5f1d7a3c'.repeat(8);
// The reviewed cached star from the contract. Every measured number below comes from it.
const STAR = '7182c057c83b501438fd6f7a9b1d45700bb0e39579b0a73dce73065af62abbdc';
const hashFor = index => index.toString(16).padStart(64, '0');
const idFor = hash => `sha256:${hash}`;

const near = (actual, expected, tolerance) => {
  assert.equal(actual.length, expected.length);
  for (let index = 0; index < expected.length; index++) {
    assert.ok(Math.abs(actual[index] - expected[index]) <= tolerance,
      `index ${index}: ${actual[index]} is not within ${tolerance} of ${expected[index]}`);
  }
};

function renderAsset(hash, overrides = {}) {
  return {
    visual_asset_id: idFor(hash),
    glb_sha256: hash,
    url: `/catalog-assets/${REVISION}/${hash}.glb`,
    media_type: 'model/gltf-binary',
    byte_length: 15804,
    mesh_count: 1,
    primitive_count: 1,
    triangle_count: 276,
    units: 'm',
    source_up_axis: '+Y',
    engine_up_axis: '+Z',
    bounds_dimensions_m: [.016949606, .016120852, .002],
    reference_axes_m: [.008474803, .008060426, .001],
    sim_from_asset_quaternion_wxyz: [.70710678, .70710678, 0, 0],
    runtime_lod_reviewed: false,
    ...overrides,
  };
}

function catalogState(assets, overrides = {}) {
  return {
    session_id: 'session-a',
    catalog_revision: REVISION,
    class_catalog: [
      // A built-in row carries no render_asset and keeps its existing pool.
      {name: 'good', preview: {schema_version: 1, source: 'profile', shape: 'ellipsoid'}},
      ...assets.map((asset, index) => ({object_type_id: `generated.item-${index}`, render_asset: asset})),
    ],
    ...overrides,
  };
}

const registryOf = assets => acceptedAssetRegistry(catalogState(assets), REVISION).accepted;

const refuse = (overrides = {}, snapshotRevision = REVISION, stateOverrides = {}) => {
  const result = acceptedAssetRegistry(catalogState([renderAsset(STAR, overrides)], stateOverrides), snapshotRevision);
  assert.equal(result.accepted.size, 0);
  assert.equal(result.refused.length, 1);
  return result.refused[0].reason;
};

test('the registry accepts one content-addressed row and skips built-in rows', () => {
  const {catalogRevision, accepted, refused} = acceptedAssetRegistry(catalogState([renderAsset(STAR)]), REVISION);
  assert.equal(catalogRevision, REVISION);
  assert.deepEqual(refused, []);
  assert.deepEqual([...accepted.keys()], [idFor(STAR)]);
  const asset = accepted.get(idFor(STAR));
  assert.equal(asset.url, assetUrl(REVISION, STAR));
  assert.equal(asset.byteLength, 15804);
  assert.equal(asset.triangleCount, 276);
  assert.deepEqual([...asset.referenceAxes], [.008474803, .008060426, .001]);
  assert.deepEqual([...asset.quaternionWxyz], [.70710678, .70710678, 0, 0]);
});

test('the registry refuses a stale revision, a bad hash, a foreign path, and a mismatched id', () => {
  assert.equal(refuse({}, hashFor(1)), 'revision_mismatch');
  assert.equal(refuse({}, undefined, {catalog_revision: undefined}), 'revision_mismatch');
  assert.equal(refuse({}, 'not-a-digest', {catalog_revision: 'not-a-digest'}), 'invalid_revision');
  assert.equal(refuse({glb_sha256: STAR.toUpperCase()}), 'invalid_hash');
  assert.equal(refuse({glb_sha256: STAR.slice(0, 63)}), 'invalid_hash');
  assert.equal(refuse({visual_asset_id: idFor(hashFor(2))}), 'asset_id_mismatch');
  assert.equal(refuse({visual_asset_id: STAR}), 'asset_id_mismatch');
  // Any path other than the one rebuilt from the state revision and the row hash.
  assert.equal(refuse({url: assetUrl(hashFor(3), STAR)}), 'url_mismatch');
  assert.equal(refuse({url: `/catalog-assets/${REVISION}/../../item-jobs/x/${STAR}.glb`}), 'url_mismatch');
  assert.equal(refuse({url: `/assets/${STAR}.glb`}), 'url_mismatch');
  assert.equal(refuse({byte_length: 0}), 'invalid_declaration');
  assert.equal(refuse({triangle_count: 276.5}), 'invalid_declaration');
  assert.equal(refuse({reference_axes_m: [.008474803, 0, .001]}), 'invalid_declaration');
  assert.equal(refuse({sim_from_asset_quaternion_wxyz: [.70710678, .70710678, 0]}), 'invalid_declaration');
});

const capReason = overrides =>
  planAssetLoads(registryOf([renderAsset(STAR, overrides)])).fallbacks.get(idFor(STAR)) || null;

test('per asset caps pass exactly at the limit and fall back one over it', () => {
  assert.equal(capReason({byte_length: ASSET_CAPS.bytesPerAsset}), null);
  assert.equal(capReason({byte_length: ASSET_CAPS.bytesPerAsset + 1}), 'over_byte_cap');
  assert.equal(capReason({primitive_count: ASSET_CAPS.primitivesPerAsset}), null);
  assert.equal(capReason({primitive_count: ASSET_CAPS.primitivesPerAsset + 1}), 'over_primitive_cap');
  assert.equal(capReason({triangle_count: ASSET_CAPS.trianglesPerAsset}), null);
  assert.equal(capReason({triangle_count: ASSET_CAPS.trianglesPerAsset + 1}), 'over_triangle_cap');
});

const planBulk = (count, overrides, caps) => planAssetLoads(
  registryOf(Array.from({length: count}, (_, index) => renderAsset(hashFor(index), overrides))), caps);

test('total caps stop at the configured budget and name the exceeded total', () => {
  const byCount = planBulk(11, {byte_length: 1000, triangle_count: 100});
  assert.equal(byCount.load.length, 10);
  assert.equal(byCount.fallbacks.get(idFor(hashFor(10))), 'over_asset_count_cap');

  // Eight assets at the per asset byte cap reach exactly 2 MiB. A ninth cannot fit.
  const byBytes = planBulk(9, {byte_length: 262144, triangle_count: 2000});
  assert.equal(byBytes.load.length, 8);
  assert.equal(byBytes.fallbacks.get(idFor(hashFor(8))), 'over_total_byte_cap');

  const byPrimitives = planBulk(6, {byte_length: 1000, primitive_count: 2, triangle_count: 100},
    {...ASSET_CAPS, assets: 20, primitivesPerAsset: 2});
  assert.equal(byPrimitives.load.length, 5);
  assert.equal(byPrimitives.fallbacks.get(idFor(hashFor(5))), 'over_total_primitive_cap');

  const byTriangles = planBulk(11, {byte_length: 1000, triangle_count: 2000},
    {...ASSET_CAPS, assets: 20, primitivesTotal: 40});
  assert.equal(byTriangles.load.length, 10);
  assert.equal(byTriangles.fallbacks.get(idFor(hashFor(10))), 'over_total_triangle_cap');
});

test('a total cap stops loading, but one oversized asset only refuses itself', () => {
  // Eight assets fill 2 MiB exactly. The ninth exceeds it and the tiny tenth never loads.
  const big = Array.from({length: 9}, (_, index) => renderAsset(hashFor(index), {byte_length: 262144}));
  const stopped = planAssetLoads(registryOf([...big, renderAsset(hashFor(9), {byte_length: 100})]));
  assert.equal(stopped.load.length, 8);
  assert.equal(stopped.fallbacks.get(idFor(hashFor(8))), 'over_total_byte_cap');
  assert.equal(stopped.fallbacks.get(idFor(hashFor(9))), 'over_total_byte_cap');

  // A per asset cap is not a budget. Planning continues past it.
  const skipped = planAssetLoads(registryOf([
    renderAsset(hashFor(0), {byte_length: ASSET_CAPS.bytesPerAsset + 1}),
    renderAsset(hashFor(1), {triangle_count: ASSET_CAPS.trianglesPerAsset + 1}),
    renderAsset(hashFor(2)),
  ]));
  assert.deepEqual(skipped.load.map(asset => asset.visualAssetId), [idFor(hashFor(2))]);
  assert.equal(skipped.fallbacks.get(idFor(hashFor(0))), 'over_byte_cap');
  assert.equal(skipped.fallbacks.get(idFor(hashFor(1))), 'over_triangle_cap');
});

test('the load order stays deterministic whatever order the catalog rows arrive in', () => {
  const accepted = registryOf([renderAsset(hashFor(3)), renderAsset(hashFor(1)), renderAsset(hashFor(2))]);
  assert.deepEqual(planAssetLoads(accepted).load.map(asset => asset.visualAssetId),
    [1, 2, 3].map(index => idFor(hashFor(index))));
});

test('parsed counts and bytes must equal the declared registry evidence', () => {
  const asset = registryOf([renderAsset(STAR)]).get(idFor(STAR));
  const parsed = {primitiveCount: 1, triangleCount: 276, byteLength: 15804};
  assert.equal(parsedAssetRefusal(asset, parsed), null);
  assert.equal(parsedAssetRefusal(asset, {...parsed, primitiveCount: 2}), 'primitive_count_mismatch');
  assert.equal(parsedAssetRefusal(asset, {...parsed, triangleCount: 275}), 'triangle_count_mismatch');
  assert.equal(parsedAssetRefusal(asset, {...parsed, byteLength: 15805}), 'byte_length_mismatch');
  // A missing parse result is a parse failure, not a count mismatch.
  assert.equal(parsedAssetRefusal(asset, null), 'parse_failed');
  assert.equal(parsedAssetRefusal(asset, undefined), 'parse_failed');
});

// The measured cached star. The node scales uniformly and translates source y.
const NODE_SCALE = 0.0010000000475;
const NODE_TRANSLATE_Y = 0.0010000000475;
const ACCESSOR_CENTER_Z = -0.851047277;
const NODE_MATRIX = [
  NODE_SCALE, 0, 0, 0,
  0, NODE_SCALE, 0, 0,
  0, 0, NODE_SCALE, 0,
  0, NODE_TRANSLATE_Y, 0, 1,
];
const STAR_QUATERNION = [.70710678, .70710678, 0, 0];
// Accessor half extents that reproduce the measured source bounds after the node scale.
// Source y becomes engine z and source z becomes engine minus y under the correction.
const half = metres => metres / (2 * NODE_SCALE);
const [halfX, halfY, halfZ] = [half(.016949605942), half(.002), half(.016120851517)];
const STAR_POSITIONS = [
  -halfX, -halfY, ACCESSOR_CENTER_Z - halfZ,
  halfX, halfY, ACCESSOR_CENTER_Z + halfZ,
  0, 0, ACCESSOR_CENTER_Z,
];

test('the prepared geometry applies the node matrix, then the correction, then one recentre', () => {
  const {positions, center, size} = prepareGeometry(STAR_POSITIONS, NODE_MATRIX, STAR_QUATERNION);
  near(center, [0, .000851047318, .001000000047], 1e-9);
  // Metre dimensions survive. Only the origin moves.
  near(size.map(value => value * 1000), [16.949605942, 16.120851517, 2.0], 1e-6);
  // The accessor centre vertex lands on the origin, and so does the prepared bounds centre.
  near(positions.slice(6, 9), [0, 0, 0], 1e-12);
  near([0, 1, 2].map(axis => (positions[axis] + positions[3 + axis]) / 2), [0, 0, 0], 1e-12);
  // Correction before node matrix would put the centre 1 mm higher on y. Order is load bearing.
  assert.ok(Math.abs(center[1] - (0.851047277 * NODE_SCALE + NODE_TRANSLATE_Y)) > 1e-6);
});

test('the correction maps asset x to x, asset y to z, and asset z to minus y', () => {
  const identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
  const {positions} = prepareGeometry([1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0], identity, STAR_QUATERNION);
  // The registry quaternion is rounded to eight decimals, so allow its rounding.
  const centre = [.5, -.5, .5];
  near(positions.slice(0, 3), [1 - centre[0], -centre[1], -centre[2]], 1e-7);
  near(positions.slice(3, 6), [-centre[0], -centre[1], 1 - centre[2]], 1e-7);
  near(positions.slice(6, 9), [-centre[0], -1 - centre[1], -centre[2]], 1e-7);
});

test('a fixed-size generated object keeps the authored GLB dimensions at scale one', () => {
  const axes = [.008474803, .008060426, .001];
  assert.deepEqual(instanceScale('box', axes, axes), [1, 1, 1]);
  near(instanceScale('ellipsoid', [.004, .003, .002], [.002, .003, .001]), [2, 1, 2], 1e-12);
  assert.equal(instanceScale('box', [.004, .003], axes), null);
});

test('capsule extents derive the object and the reference bounds independently', () => {
  near(proxyExtents('capsule', [.004, .0015, .0015]), [.0015, .0015, .0055], 1e-15);
  near(instanceScale('capsule', [.003, .001, .001], [.001, .001, .001]), [1, 1, 2], 1e-12);
  assert.deepEqual(proxyExtents('box', [.004, .003, .002]), [.004, .003, .002]);
  assert.deepEqual(proxyExtents('half', [.00245, .0018, .00255]), [.00245, .0018, .00255]);
  assert.deepEqual(proxyExtents('ellipsoid', [.0049, .00355, .00255]), [.0049, .00355, .00255]);
});

test('asset choice runs only through the object identity and the accepted registry', () => {
  const id = idFor(STAR);
  const accepted = registryOf([renderAsset(STAR)]);
  const loaded = new Map([[id, {pool: 'generated'}]]);
  const empty = new Map();
  const builtIn = chooseObjectAsset({visual_asset_id: null, shape: 'ellipsoid', axes: [.0049, .00355, .00255]},
    {accepted, loaded, fallbacks: empty});
  assert.deepEqual([builtIn.source, builtIn.assetId, builtIn.reason], ['builtin', null, null]);

  const ready = chooseObjectAsset({visual_asset_id: id}, {accepted, loaded, fallbacks: empty});
  assert.deepEqual([ready.source, ready.reason], ['generated', null]);
  assert.equal(ready.asset.url, assetUrl(REVISION, STAR));

  const unknown = chooseObjectAsset({visual_asset_id: idFor(hashFor(9))}, {accepted, loaded, fallbacks: empty});
  assert.deepEqual([unknown.source, unknown.reason], ['proxy', 'unknown_asset']);

  const waiting = chooseObjectAsset({visual_asset_id: id}, {accepted, loaded: empty, fallbacks: empty});
  assert.deepEqual([waiting.source, waiting.reason], ['proxy', 'asset_loading']);

  const refused = chooseObjectAsset({visual_asset_id: id},
    {accepted, loaded, fallbacks: new Map([[id, 'over_byte_cap']])});
  assert.deepEqual([refused.source, refused.reason], ['proxy', 'over_byte_cap']);
});

test('generated pools clear on a session change and on a catalog revision change', () => {
  const previous = {session_id: 'session-a', catalog_revision: REVISION};
  assert.equal(mustResetGeneratedPools(previous, {...previous}), false);
  assert.equal(mustResetGeneratedPools(previous, {...previous, score_epoch_id: 'scores-2'}), false);
  assert.equal(mustResetGeneratedPools(previous, {...previous, session_id: 'session-b'}), true);
  assert.equal(mustResetGeneratedPools(previous, {...previous, catalog_revision: hashFor(7)}), true);
  assert.equal(mustResetGeneratedPools(null, previous), false);
  assert.equal(mustResetGeneratedPools(previous, null), true);
});

test('the metrics builder reports the revision and one record per requested asset', () => {
  const metrics = generatedAssetMetrics({
    catalogRevision: REVISION,
    records: [
      {visualAssetId: idFor(STAR), etag: `"${STAR}"`, byteLength: 15804, loaded: true, loadMs: 31.5, parseMs: 4.25},
      {visualAssetId: idFor(hashFor(2)), byteLength: 400000, fallbackReason: 'over_byte_cap'},
      {visualAssetId: idFor(hashFor(3)), byteLength: 2048},
    ],
  });
  assert.equal(metrics.catalogRevision, REVISION);
  assert.deepEqual(metrics.requested, [idFor(STAR), idFor(hashFor(2)), idFor(hashFor(3))]);
  assert.deepEqual(metrics.loaded, [idFor(STAR)]);
  assert.deepEqual(metrics.failed, [idFor(hashFor(2))]);
  assert.deepEqual(metrics.assets[0], {
    visualAssetId: idFor(STAR), etag: `"${STAR}"`, byteLength: 15804,
    loaded: true, fallbackReason: null, loadMs: 31.5, parseMs: 4.25,
  });
  assert.deepEqual([metrics.assets[1].etag, metrics.assets[1].loadMs, metrics.assets[1].parseMs], [null, null, null]);
  assert.equal(Object.isFrozen(metrics), true);
  assert.equal(Object.isFrozen(metrics.assets), true);
  assert.deepEqual(generatedAssetMetrics(), {catalogRevision: null, requested: [], loaded: [], failed: [], assets: []});
});
