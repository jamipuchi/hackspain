// Browser logic for the approved generated GLB display contract. It stays pure: no DOM,
// no three.js, plain arrays and numbers only, so node can test every rule.
// It decides which content-addressed assets may load, prepares one geometry in engine
// axes, and builds the read-only evidence the page shows beside its measurements.

// One catalog revision and one GLB hash are both lowercase sha256 digests.
const HEX64 = /^[0-9a-f]{64}$/;

const numbers = (value, count, positive = false) => Array.isArray(value) && value.length === count
  && value.every(item => Number.isFinite(item) && (!positive || item > 0));

const counted = value => Number.isInteger(value) && value >= 1;

// The safety caps from the contract. They bound download and draw cost against the
// cached asset. They are not a measured 60 FPS guarantee.
export const ASSET_CAPS = Object.freeze({
  assets: 10,
  bytesPerAsset: 256 * 1024,
  bytesTotal: 2 * 1024 * 1024,
  primitivesPerAsset: 1,
  trianglesPerAsset: 2000,
  primitivesTotal: 10,
  trianglesTotal: 20000,
});

// The only path this page may ever request. It is rebuilt, never copied.
export function assetUrl(catalogRevision, glbSha256) {
  return `/catalog-assets/${catalogRevision}/${glbSha256}.glb`;
}

function assetRefusal(asset, revision) {
  const hash = asset.glb_sha256;
  if (typeof hash !== 'string' || !HEX64.test(hash)) return 'invalid_hash';
  if (asset.visual_asset_id !== `sha256:${hash}`) return 'asset_id_mismatch';
  // A row that names any other path is refused, including one that only looks close.
  if (asset.url !== assetUrl(revision, hash)) return 'url_mismatch';
  if (!counted(asset.byte_length) || !counted(asset.primitive_count)
      || !counted(asset.triangle_count)) return 'invalid_declaration';
  // Without these the budget, the parsed check, and the transform would all read NaN.
  if (!numbers(asset.reference_axes_m, 3, true)) return 'invalid_declaration';
  if (!numbers(asset.sim_from_asset_quaternion_wxyz, 4)) return 'invalid_declaration';
  return null;
}

// Build the accepted registry from one state snapshot. `snapshotRevision` is the catalog
// revision of the frame being drawn, so a stale class packet can never paint it.
export function acceptedAssetRegistry(state, snapshotRevision) {
  const revision = typeof state?.catalog_revision === 'string' ? state.catalog_revision : null;
  const stale = revision === null || revision !== snapshotRevision
    ? 'revision_mismatch'
    : HEX64.test(revision) ? null : 'invalid_revision';
  const accepted = new Map();
  const refused = [];
  for (const row of Array.isArray(state?.class_catalog) ? state.class_catalog : []) {
    const asset = row?.render_asset;
    if (!asset) continue; // A built-in row keeps its current preview data and pools.
    const reason = stale || assetRefusal(asset, revision);
    const visualAssetId = typeof asset.visual_asset_id === 'string' ? asset.visual_asset_id : null;
    if (reason) {
      refused.push({objectTypeId: row.object_type_id ?? null, visualAssetId, reason});
      continue;
    }
    accepted.set(visualAssetId, Object.freeze({
      visualAssetId,
      glbSha256: asset.glb_sha256,
      url: asset.url,
      mediaType: asset.media_type ?? null,
      byteLength: asset.byte_length,
      primitiveCount: asset.primitive_count,
      triangleCount: asset.triangle_count,
      referenceAxes: Object.freeze(asset.reference_axes_m.slice()),
      quaternionWxyz: Object.freeze(asset.sim_from_asset_quaternion_wxyz.slice()),
    }));
  }
  return {catalogRevision: revision, accepted, refused};
}

// One asset can be too large on its own. That refuses only that asset.
function perAssetRefusal(asset, caps) {
  if (asset.byteLength > caps.bytesPerAsset) return 'over_byte_cap';
  if (asset.primitiveCount > caps.primitivesPerAsset) return 'over_primitive_cap';
  if (asset.triangleCount > caps.trianglesPerAsset) return 'over_triangle_cap';
  return null;
}

// A total is a budget, not a filter. Reaching one stops loading for good.
function totalRefusal(asset, caps, used) {
  if (used.count >= caps.assets) return 'over_asset_count_cap';
  if (used.bytes + asset.byteLength > caps.bytesTotal) return 'over_total_byte_cap';
  if (used.primitives + asset.primitiveCount > caps.primitivesTotal) return 'over_total_primitive_cap';
  if (used.triangles + asset.triangleCount > caps.trianglesTotal) return 'over_total_triangle_cap';
  return null;
}

// Order the accepted assets and keep only what fits the caps. Sorting by ID keeps the
// same assets winning the budget for every visitor and every reconnect. The loaded set
// is always a prefix of the assets that pass their own per asset caps.
export function planAssetLoads(accepted, caps = ASSET_CAPS) {
  const ordered = [...accepted.values()]
    .sort((left, right) => left.visualAssetId < right.visualAssetId ? -1
      : left.visualAssetId > right.visualAssetId ? 1 : 0);
  const load = [];
  const fallbacks = new Map();
  const used = {count: 0, bytes: 0, primitives: 0, triangles: 0};
  let stopped = null;
  for (const asset of ordered) {
    const perAsset = perAssetRefusal(asset, caps);
    if (!stopped && !perAsset) stopped = totalRefusal(asset, caps, used);
    // Once a total fires it holds for every later asset, small ones included.
    const reason = stopped || perAsset;
    if (reason) {
      fallbacks.set(asset.visualAssetId, reason);
      continue;
    }
    load.push(asset);
    used.count += 1;
    used.bytes += asset.byteLength;
    used.primitives += asset.primitiveCount;
    used.triangles += asset.triangleCount;
  }
  return {load, fallbacks};
}

// The parsed GLB must match its declared registry evidence exactly, or the proxy stays.
export function parsedAssetRefusal(asset, parsed) {
  if (parsed?.primitiveCount !== asset.primitiveCount) return 'primitive_count_mismatch';
  if (parsed?.triangleCount !== asset.triangleCount) return 'triangle_count_mismatch';
  if (parsed?.byteLength !== asset.byteLength) return 'byte_length_mismatch';
  return null;
}

// glTF and three.js both store a 4x4 as 16 column-major elements.
function applyMatrix(m, x, y, z) {
  return [
    m[0] * x + m[4] * y + m[8] * z + m[12],
    m[1] * x + m[5] * y + m[9] * z + m[13],
    m[2] * x + m[6] * y + m[10] * z + m[14],
  ];
}

// v + w * t + qv x t, with t = 2 * (qv x v).
function applyQuaternion([w, qx, qy, qz], x, y, z) {
  const tx = 2 * (qy * z - qz * y);
  const ty = 2 * (qz * x - qx * z);
  const tz = 2 * (qx * y - qy * x);
  return [
    x + w * tx + qy * tz - qz * ty,
    y + w * ty + qz * tx - qx * tz,
    z + w * tz + qx * ty - qy * tx,
  ];
}

// Prepare one mesh primitive for instancing: T(-center) * Q * M * vertex. The result
// stays in metres. Dimensions are never normalized, only the origin moves.
export function prepareGeometry(positions, nodeMatrix, quaternionWxyz) {
  const count = Math.floor(positions.length / 3);
  if (count === 0) return {positions: [], center: [0, 0, 0], size: [0, 0, 0]};
  const prepared = new Array(count * 3);
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  for (let index = 0; index < count; index++) {
    const base = index * 3;
    const moved = applyMatrix(nodeMatrix, positions[base], positions[base + 1], positions[base + 2]);
    const turned = applyQuaternion(quaternionWxyz, moved[0], moved[1], moved[2]);
    for (let axis = 0; axis < 3; axis++) {
      prepared[base + axis] = turned[axis];
      if (turned[axis] < min[axis]) min[axis] = turned[axis];
      if (turned[axis] > max[axis]) max[axis] = turned[axis];
    }
  }
  const center = [0, 1, 2].map(axis => (min[axis] + max[axis]) / 2);
  const size = [0, 1, 2].map(axis => max[axis] - min[axis]);
  // The export is grounded at its minimum height, so the visual centre has to move onto
  // the physical proxy centre exactly once.
  for (let index = 0; index < count; index++) {
    for (let axis = 0; axis < 3; axis++) prepared[index * 3 + axis] -= center[axis];
  }
  return {positions: prepared, center, size};
}

// Half extents in engine axis order. A capsule row carries its half length first, then
// its radius twice, so its bounds are the radius across and half length plus radius along z.
export function proxyExtents(shape, axes) {
  if (!numbers(axes, 3, true)) return null;
  if (shape === 'capsule') return [axes[1], axes[1], axes[0] + axes[1]];
  return [axes[0], axes[1], axes[2]];
}

// The correction is already baked, so r stays in engine axis order with no permutation.
// A fixed-size generated object divides equal extents and gets exactly [1, 1, 1].
export function instanceScale(shape, objectAxes, referenceAxes) {
  const object = proxyExtents(shape, objectAxes);
  const reference = proxyExtents(shape, referenceAxes);
  if (!object || !reference) return null;
  return [0, 1, 2].map(axis => object[axis] / reference[axis]);
}

// Identity is the only input. Dimensions, predictions, and names never choose an asset.
export function chooseObjectAsset(object, {accepted, loaded, fallbacks} = {}) {
  const assetId = object?.visual_asset_id;
  if (assetId === null || assetId === undefined) {
    return {source: 'builtin', assetId: null, asset: null, reason: null};
  }
  const asset = accepted?.get(assetId) || null;
  if (!asset) return {source: 'proxy', assetId, asset: null, reason: 'unknown_asset'};
  const refused = fallbacks?.get(assetId) || null;
  if (refused) return {source: 'proxy', assetId, asset, reason: refused};
  if (loaded?.has(assetId)) return {source: 'generated', assetId, asset, reason: null};
  // The physical object is never hidden. It shows its proxy until a complete frame.
  return {source: 'proxy', assetId, asset, reason: 'asset_loading'};
}

// Generated pools carry one session and one catalog revision. Anything else must clear.
export function mustResetGeneratedPools(previous, next) {
  if (!previous) return false;
  return previous.session_id !== next?.session_id
    || previous.catalog_revision !== next?.catalog_revision;
}

// The read-only evidence list from the contract. One record per requested asset.
export function generatedAssetMetrics({catalogRevision = null, records = []} = {}) {
  const assets = records.map(record => Object.freeze({
    visualAssetId: record.visualAssetId,
    etag: record.etag ?? null,
    byteLength: Number.isFinite(record.byteLength) ? record.byteLength : null,
    loaded: Boolean(record.loaded),
    fallbackReason: record.fallbackReason ?? null,
    loadMs: Number.isFinite(record.loadMs) ? record.loadMs : null,
    parseMs: Number.isFinite(record.parseMs) ? record.parseMs : null,
  }));
  return Object.freeze({
    catalogRevision,
    requested: assets.map(asset => asset.visualAssetId),
    loaded: assets.filter(asset => asset.loaded).map(asset => asset.visualAssetId),
    failed: assets.filter(asset => asset.fallbackReason).map(asset => asset.visualAssetId),
    assets: Object.freeze(assets),
  });
}
