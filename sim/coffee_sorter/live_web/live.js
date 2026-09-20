import * as THREE from 'three';
import {OrbitControls} from '/vendor/OrbitControls.js';
import {RoomEnvironment} from '/vendor/RoomEnvironment.js';
import {GLTFLoader} from '/assets/vendor/loaders/GLTFLoader.js';
import {PolicyIntentBuffer, compareExpectedOutcome, emptyMetricState, formatEngineRate, freezeItemRequest, jobActionLabel, jobActionPath, jobErrorLabel, jobQueueSignature, jobStateLabel, jobStateNote, queueModeCue, normalizedClassPreview, normalizedCollectionSurfaces, normalizedJobSummary, profilePreviewScale, resolvePendingRequest, samePresentationTimeline} from './timeline.mjs';
import {acceptedAssetRegistry, chooseObjectAsset, generatedAssetMetrics, instanceScale, mustResetGeneratedPools, parsedAssetRefusal, planAssetLoads, prepareGeometry} from './generated_assets.mjs';

const $ = id => document.getElementById(id);
const canvas = $('scene');
const ctx = canvas.getContext('2d');
let state = null;
let liveState = null;
let socket;
let selected = null;
const requests = new Map();
let latestCommandId = null;
let activePolicyRequest = null;
const pendingPolicyIntents = new PolicyIntentBuffer();
let policyDispatchTimer = null;
let policyTransportClasses = new Set();
let policyTransportVersion = null;
let policyDesiredClasses = new Set();
let policyInitialized = false;
let pendingPolicyPresentation = null;
let policyError = '';
let itemCatalogSignature = '';
let currentView = '3d';
let itemPreview = null;
let selectedPreviewName = null;
let itemQueueSignature = null;
let generatedAssetContext = null;
let generatedAssetGeneration = 0;
// One frozen snapshot of the request in flight. It never rebuilds from the form.
let pendingItemRequest = null;
let wallEntries = [];
let wallOffset = 0;
let wallTotal = null;
let wallLoading = false;
let shownSession = null;
let frames = 0;
let fpsStart = performance.now();
let lastFrameAt = null;
let frameIntervals = [];
const packetArrivals = [];
let restartPending = false;
let reconnectTimer = null;
let heartbeatSeq = null;
let heartbeatSeenAt = null;
let staleConnection = false;
let telemetryFresh = false;
const selectedEvents = new Map();
const measurements = {fps: null, frame_ms_p50: null, frame_ms_p95: null, frame_ms_max: null, acknowledgment_ms: null, outcome_wall_s: null, pose_hz: null, click_dispatch_ms: null, buffer_delay_ms: 240, buffer_waiting: true, webgl: null};
window.coffeeMeasurements = measurements;
const HEARTBEAT_IDLE_MS = 4500;
const MAX_CLIENT_REQUESTS = 64;
const MAX_CLIENT_PENDING_REQUESTS = 16;
const DISPLAY_DELAY_MS = 240;
const SNAPSHOT_LIMIT = 32;
const SNAPSHOT_MAX_AGE_MS = 1500;
const POLICY_COALESCE_MS = 60;
const ITEM_VIEWS = ['active', 'queue', 'wall'];
const WALL_PAGE = 12;
const continuousMode = () => state?.mode === 'continuous';
const liveContinuousMode = () => liveState?.mode === 'continuous';
const latestRequest = () => latestCommandId ? requests.get(latestCommandId) : null;
const pendingRequests = () => [...requests.values()].filter(request => !request.acknowledged && !request.invalidated);
const pendingRequest = () => pendingRequests().length > 0;

function scheduleReconnect() {
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(connect, 1200);
}

function retryPending() {
  if ((!pendingRequest() && !activePolicyRequest) || !liveState || socket?.readyState !== WebSocket.OPEN) return;
  for (const request of pendingRequests()) {
    if (liveState.session_id !== request.payload.session_id) {
      request.invalidated = true;
      request.error = 'Engine session changed. The original request was not retried.';
      continue;
    }
    socket.send(JSON.stringify(request.payload));
    request.lastSend = performance.now();
    request.retryPending = false;
  }
  if (activePolicyRequest && performance.now() - activePolicyRequest.lastSend > 2000) {
    socket.send(JSON.stringify(activePolicyRequest.payload));
    activePolicyRequest.lastSend = performance.now();
    activePolicyRequest.serverPending = false;
  }
  updateLatestEvidence();
}

function connect() {
  telemetryFresh = false;
  heartbeatSeq = heartbeatSeenAt = null;
  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${scheme}//${location.host}/ws`);
  socket = ws;
  ws.onopen = () => {
    if (socket !== ws) return;
    $('error').textContent = '';
    staleConnection = false;
  };
  ws.onclose = () => {
    if (socket !== ws) return;
    telemetryFresh = false;
    for (const request of pendingRequests()) request.retryPending = true;
    $('status').textContent = 'Disconnected';
    $('inject').disabled = true;
    $('restart').disabled = true;
    $('notice').textContent = 'Connection lost. Reconnecting to the engine.';
    scheduleReconnect();
  };
  ws.onmessage = event => {
    if (socket !== ws) return;
    const packet = JSON.parse(event.data);
    if (packet.type === 'state') {
      if (shownSession && packet.session_id && shownSession !== packet.session_id) {
        heartbeatSeq = heartbeatSeenAt = null;
        invalidatePendingRequests();
        resetDisplayTimeline();
        resetPolicyControls('Engine session changed');
      }
      if (packet.session_id) shownSession = packet.session_id;
      const arrivedAt = performance.now();
      const nextHeartbeat = Number(packet.heartbeat_seq);
      if (Number.isFinite(nextHeartbeat) && nextHeartbeat !== heartbeatSeq) {
        heartbeatSeq = nextHeartbeat;
        heartbeatSeenAt = arrivedAt;
      } else if (!packet.mode || packet.mode !== 'continuous') {
        heartbeatSeenAt = arrivedAt;
      }
      packetArrivals.push(arrivedAt);
      while (packetArrivals.length > 2 && packetArrivals[0] < arrivedAt - 2000) packetArrivals.shift();
      if (packetArrivals.length > 1) {
        measurements.pose_hz = (packetArrivals.length - 1) * 1000 / (arrivedAt - packetArrivals[0]);
      }
      liveState = packet;
      telemetryFresh = true;
      window.cintaLiveState = liveState;
      syncPolicyTransport(packet.reject_policy);
      pendingPolicyIntents.setCatalog((packet.class_catalog || []).map(item => item.name));
      // Queue rows follow the server packet directly. Pose buffering must not delay them.
      updateItemQueue();
      enqueueSnapshot(packet, arrivedAt);
      processPolicyQueue();
      // Retry each retained command after a new connection or an acknowledgment timeout.
      if ((pendingRequest() || activePolicyRequest) && ['ready', 'running'].includes(liveState.status)) {
        const policyRetry = activePolicyRequest && performance.now() - activePolicyRequest.lastSend > 2000;
        if (policyRetry || pendingRequests().some(request => request.retryPending || performance.now() - request.lastSend > 2000)) retryPending();
      }
    } else if (packet.type === 'ack' && requests.has(packet.command_id)) {
      const request = requests.get(packet.command_id);
      if (request.acknowledged) return;
      request.acknowledged = true;
      request.ack = packet;
      if (!packet.ok) {
        request.error = packet.error || packet.error_code || 'Injection failed';
        $('error').textContent = `Injection ${packet.command_id.slice(0, 8)} failed: ${request.error}`;
      } else {
        request.object_id = packet.object_id;
        request.spawn_position = packet.spawn_position;
        request.spawnWall = performance.now();
        request.acknowledgment_ms = request.spawnWall - request.sent;
      }
      updateLatestEvidence();
    } else if (packet.type === 'ack' && activePolicyRequest?.payload.command_id === packet.command_id) {
      handlePolicyAck(packet);
    } else if (packet.type === 'pending' && requests.has(packet.command_id)) {
      requests.get(packet.command_id).serverPending = true;
      updateLatestEvidence();
    } else if (packet.type === 'pending' && activePolicyRequest?.payload.command_id === packet.command_id) {
      activePolicyRequest.serverPending = true;
      updateItems();
    }
  };
}

setInterval(() => {
  if (!liveContinuousMode() || !liveState || socket?.readyState !== WebSocket.OPEN || heartbeatSeenAt === null) return;
  if (performance.now() - heartbeatSeenAt < HEARTBEAT_IDLE_MS || staleConnection) return;
  staleConnection = true;
  for (const request of pendingRequests()) request.retryPending = true;
  $('error').textContent = 'No application heartbeat arrived. Reconnecting before retrying the original request.';
  $('notice').textContent = 'Connection became stale. Reconnecting to the engine.';
  socket.close(4000, 'application heartbeat timed out');
}, 1000);

function invalidatePendingRequests() {
  const invalidatedPending = pendingRequests().length;
  requests.clear();
  latestCommandId = null;
  selected = null;
  selectedEvents.clear();
  measurements.acknowledgment_ms = measurements.outcome_wall_s = null;
  if (invalidatedPending) {
    $('error').textContent = 'Engine session changed. Pending injections were not retried.';
  }
  updateLatestEvidence();
}

$('details-toggle').onclick = () => $('diagnostics').showModal();
$('details-close').onclick = () => $('diagnostics').close();

$('restart').onclick = async () => {
  if (!liveState?.session_id || !liveState.restart_supported || restartPending || socket.readyState !== WebSocket.OPEN) return;
  restartPending = true;
  $('error').textContent = '';
  update();
  try {
    const response = await fetch('/restart', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: liveState.session_id}),
    });
    if (!response.headers.get('Content-Type')?.includes('application/json')) {
      throw new Error(response.status === 404 ? 'Restart is unavailable on this server. The service needs the latest update.' : `Restart failed with server status ${response.status}. Try again.`);
    }
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'The session could not restart. Try again.');
  } catch (error) {
    $('error').textContent = error.message;
  } finally {
    restartPending = false;
    update();
  }
};

function injectStone() {
  const interactionStarted = performance.now();
  if (!liveState || restartPending || socket?.readyState !== WebSocket.OPEN || $('inject').disabled) return;
  for (const [commandId, request] of requests) {
    if (requests.size < MAX_CLIENT_REQUESTS) break;
    if (request.acknowledged || request.invalidated) requests.delete(commandId);
  }
  if (requests.size >= MAX_CLIENT_REQUESTS) {
    $('error').textContent = `Client request storage is full (${MAX_CLIENT_REQUESTS} active records). Wait for an injection result.`;
    update();
    return;
  }
  const payload = {type: 'inject', command_id: crypto.randomUUID(), session_id: liveState.session_id, class_name: 'stone'};
  if (requests.has(payload.command_id)) {
    $('error').textContent = 'The browser could not create a distinct command ID. Try again.';
    return;
  }
  if (liveContinuousMode()) {
    if (!liveState.command_epoch) return;
    payload.command_epoch = liveState.command_epoch;
  }
  const request = {payload, sent: performance.now(), lastSend: performance.now(), acknowledged: false, retryPending: false};
  requests.set(payload.command_id, request);
  latestCommandId = payload.command_id;
  selected = null;
  selectedEvents.clear();
  measurements.acknowledgment_ms = measurements.outcome_wall_s = null;
  $('error').textContent = '';
  if (pendingRequests().length > MAX_CLIENT_PENDING_REQUESTS) {
    request.acknowledged = true;
    request.error = `The browser already has ${MAX_CLIENT_PENDING_REQUESTS} pending injections. Wait for a result.`;
    $('error').textContent = request.error;
    update();
    return;
  }
  updateLatestEvidence();
  socket.send(JSON.stringify(payload));
  measurements.click_dispatch_ms = performance.now() - interactionStarted;
  update();
}
$('inject').onclick = injectStone;

function sameSet(a, b) {
  return a.size === b.size && [...a].every(value => b.has(value));
}

function normalizedPolicy(value) {
  if (!value || !Array.isArray(value.reject_classes) || typeof value.policy_version !== 'string') return null;
  return {
    classes: new Set(value.reject_classes),
    policy_version: value.policy_version,
    score_epoch_id: value.score_epoch_id || null,
  };
}

function syncPolicyTransport(value) {
  const policy = normalizedPolicy(value);
  if (!policy) return;
  policyTransportClasses = policy.classes;
  policyTransportVersion = policy.policy_version;
  if (!policyInitialized) {
    policyDesiredClasses = new Set(policy.classes);
    policyInitialized = true;
    policyError = '';
  }
}

function resetPolicyControls(message = '') {
  const invalidatedPolicy = Boolean(activePolicyRequest || pendingPolicyIntents.size);
  clearTimeout(policyDispatchTimer);
  policyDispatchTimer = null;
  activePolicyRequest = null;
  pendingPolicyIntents.clear();
  policyTransportClasses = new Set();
  policyDesiredClasses = new Set();
  policyTransportVersion = null;
  policyInitialized = false;
  pendingPolicyPresentation = null;
  policyError = invalidatedPolicy ? message : '';
  itemCatalogSignature = '';
  if ($('items-dialog').open) closeItems();
  updateItems();
}

function policyCatalogNames() {
  return new Set((liveState?.class_catalog || []).map(item => item.name));
}

function queuePolicyChange(className, reject) {
  if (!policyCatalogNames().has(className)) {
    policyError = `Unknown item class: ${className}`;
    updateItems();
    return;
  }
  policyError = '';
  if (reject) policyDesiredClasses.add(className);
  else policyDesiredClasses.delete(className);
  pendingPolicyIntents.setCatalog(policyCatalogNames());
  pendingPolicyIntents.record(className, reject);
  updateItems();
  schedulePolicyDispatch();
}

function schedulePolicyDispatch() {
  clearTimeout(policyDispatchTimer);
  policyDispatchTimer = setTimeout(() => {
    policyDispatchTimer = null;
    processPolicyQueue();
  }, POLICY_COALESCE_MS);
}

function queuePolicySet(reject) {
  const names = policyCatalogNames();
  if (!names.size) return;
  policyError = '';
  policyDesiredClasses = reject ? new Set(names) : new Set();
  pendingPolicyIntents.setCatalog(names);
  for (const name of names) pendingPolicyIntents.record(name, reject);
  updateItems();
  schedulePolicyDispatch();
}

function processPolicyQueue() {
  if (activePolicyRequest || !pendingPolicyIntents.size || socket?.readyState !== WebSocket.OPEN || !liveState?.command_epoch || !policyTransportVersion) return;
  if (!pendingPolicyIntents.canDispatch(liveState.command_epoch)) return;
  pendingPolicyIntents.acceptEpoch(liveState.command_epoch);
  const intents = pendingPolicyIntents.take();
  const target = pendingPolicyIntents.apply(policyTransportClasses, intents);
  if (sameSet(target, policyTransportClasses)) {
    updateItems();
    return;
  }
  const payload = {
    type: 'set_reject_policy',
    command_id: crypto.randomUUID(),
    command_epoch: liveState.command_epoch,
    session_id: liveState.session_id,
    expected_policy_version: policyTransportVersion,
    reject_classes: [...target].sort(),
  };
  activePolicyRequest = {intents, payload, lastSend: performance.now(), serverPending: false};
  socket.send(JSON.stringify(payload));
  updateItems();
}

function handlePolicyAck(packet) {
  const active = activePolicyRequest;
  if (!active) return;
  if (packet.ok) {
    syncPolicyTransport({
      reject_classes: packet.reject_classes,
      policy_version: packet.policy_version,
      score_epoch_id: packet.score_epoch_id,
    });
    pendingPolicyPresentation = {
      policy_version: packet.policy_version,
      score_epoch_id: packet.score_epoch_id || null,
    };
    activePolicyRequest = null;
    policyError = '';
    processPolicyQueue();
    updateItems();
    return;
  }
  const current = packet.reject_policy || packet.current_reject_policy;
  if (packet.error_code === 'policy_version_conflict' && normalizedPolicy(current)) {
    syncPolicyTransport(current);
    activePolicyRequest = null;
    pendingPolicyIntents.requeue(active.intents);
    policyDesiredClasses = pendingPolicyIntents.apply(policyTransportClasses);
    processPolicyQueue();
    return;
  }
  if (/epoch/i.test(packet.error_code || '')) {
    activePolicyRequest = null;
    pendingPolicyIntents.requeue(active.intents);
    pendingPolicyIntents.waitForFreshEpoch(active.payload.command_epoch);
    updateItems();
    return;
  }
  activePolicyRequest = null;
  pendingPolicyIntents.clear();
  policyDesiredClasses = new Set(policyTransportClasses);
  policyError = packet.error || packet.error_code || 'Policy change failed';
  updateItems();
}

function updateItems() {
  const catalog = state?.class_catalog || [];
  const policy = normalizedPolicy(state?.reject_policy);
  if (!catalog.length || !policy) {
    $('items').replaceChildren();
    $('items-quick').replaceChildren();
    itemCatalogSignature = '';
    $('policy-status').textContent = 'Waiting for catalog';
    $('items-dialog-status').textContent = 'Waiting for catalog';
    $('policy-summary').textContent = 'Waiting';
    return;
  }
  if (!policyInitialized) {
    policyDesiredClasses = new Set(policy.classes);
    policyInitialized = true;
  }
  if (pendingPolicyPresentation
      && policy.policy_version === pendingPolicyPresentation.policy_version
      && policy.score_epoch_id === pendingPolicyPresentation.score_epoch_id) {
    pendingPolicyPresentation = null;
  }
  if (!activePolicyRequest && !pendingPolicyIntents.size && !pendingPolicyPresentation) {
    policyDesiredClasses = new Set(policy.classes);
  }
  const signature = catalog.map(item => `${item.name}:${item.severity}:${JSON.stringify(item.preview || null)}:${JSON.stringify(item.render_asset || null)}`).join('|');
  if (signature !== itemCatalogSignature) {
    itemCatalogSignature = signature;
    $('items').replaceChildren(...catalog.map(item => {
      const row = document.createElement('div'); row.className = 'item-row'; row.dataset.className = item.name; row.tabIndex = 0; row.setAttribute('role', 'option'); row.setAttribute('aria-selected', 'false');
      row.dataset.visualSource = item.render_asset ? 'proxy' : 'builtin';
      const thumb = document.createElement('img'); thumb.className = 'item-thumb'; thumb.alt = ''; thumb.dataset.previewName = item.name;
      const name = document.createElement('span'); name.className = 'item-name';
      const dot = document.createElement('i'); dot.dataset.severity = item.severity;
      name.append(dot, document.createTextNode(item.name));
      const button = document.createElement('button'); button.type = 'button'; button.className = 'policy-toggle';
      button.dataset.policyClass = item.name;
      button.onclick = event => { event.stopPropagation(); queuePolicyChange(item.name, !policyDesiredClasses.has(item.name)); };
      row.onclick = () => selectItemPreview(item.name);
      row.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectItemPreview(item.name); } };
      row.append(thumb, name, button);
      return row;
    }));
    $('items-quick').replaceChildren(...catalog.map(item => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'quick-policy-toggle';
      button.dataset.policyClass = item.name;
      const name = document.createElement('span'); name.textContent = item.name;
      const action = document.createElement('strong');
      button.append(name, action);
      button.onclick = () => queuePolicyChange(item.name, !policyDesiredClasses.has(item.name));
      return button;
    }));
    if ($('items-dialog').open) refreshItemPreviews();
  }
  for (const row of $('items').children) {
    const className = row.dataset.className;
    const button = row.querySelector('button');
    const reject = policyDesiredClasses.has(className);
    const pending = reject !== policy.classes.has(className);
    button.textContent = reject ? 'Reject' : 'Keep';
    button.setAttribute('aria-label', `${className}: ${reject ? 'Reject' : 'Keep'}`);
    button.setAttribute('aria-pressed', String(reject));
    button.classList.toggle('pending', pending);
  }
  for (const button of $('items-quick').children) {
    const className = button.dataset.policyClass;
    const reject = policyDesiredClasses.has(className);
    const pending = reject !== policy.classes.has(className);
    button.querySelector('strong').textContent = reject ? 'Reject' : 'Keep';
    button.setAttribute('aria-label', `${className}: ${reject ? 'Reject' : 'Keep'}`);
    button.setAttribute('aria-pressed', String(reject));
    button.classList.toggle('pending', pending);
  }
  const pendingCount = pendingPolicyIntents.size + (activePolicyRequest ? 1 : 0);
  const keepCount = catalog.length - policyDesiredClasses.size;
  $('policy-summary').textContent = `${keepCount} Keep · ${policyDesiredClasses.size} Reject`;
  $('policy-status').classList.toggle('error', Boolean(policyError));
  const statusText = policyError || (pendingCount ? `${pendingCount} pending` : pendingPolicyPresentation ? 'Applying' : 'Synced');
  $('policy-status').textContent = statusText;
  $('items-dialog-status').textContent = statusText;
  $('items-dialog-status').classList.toggle('error', Boolean(policyError));
}

function previewItem(name) {
  return (state?.class_catalog || []).find(item => item.name === name) || null;
}

function disposePreviewMesh(mesh) {
  if (!mesh) return;
  mesh.geometry.dispose();
  for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) material?.dispose();
}

function setPreviewMesh(item) {
  if (!itemPreview) return false;
  const preview = normalizedClassPreview(item);
  const assetId = item?.render_asset?.visual_asset_id;
  const generated = assetId ? generatedAssetContext?.loaded.get(assetId) : null;
  const pool = generated?.pool || (item?.name === 'black' && three.pools?.black ? three.pools.black : three.pools?.[preview?.shape]);
  if (!preview || (preview.shape !== 'half' && !pool?.geometry)) return false;
  if (itemPreview.mesh) {
    itemPreview.scene.remove(itemPreview.mesh);
    disposePreviewMesh(itemPreview.mesh);
  }
  // The profile half shape is a hemisphere. Its z value is the full cut-half
  // thickness, while x and y are parent ellipsoid semi-axes.
  const geometry = generated ? pool.geometry.clone() : preview.shape === 'half'
    ? new THREE.SphereGeometry(1, 20, 10, 0, Math.PI * 2, 0, Math.PI / 2)
    : pool.geometry.clone();
  if (!generated && preview.shape === 'half') geometry.rotateX(Math.PI / 2);
  geometry.computeBoundingBox();
  if (!generated) geometry.center();
  const material = generated
    ? (Array.isArray(pool.material) ? pool.material.map(value => value.clone()) : pool.material.clone())
    : new THREE.MeshStandardMaterial({color: new THREE.Color().setRGB(...preview.rgb), roughness: .58, metalness: .03});
  const mesh = new THREE.Mesh(geometry, material);
  mesh.scale.fromArray(generated ? [1, 1, 1] : profilePreviewScale(preview));
  mesh.rotation.set(.38, 0, -.25);
  itemPreview.scene.add(mesh);
  itemPreview.mesh = mesh;
  itemPreview.visualSource = generated ? 'generated' : item?.render_asset ? 'proxy' : 'builtin';
  $('items-preview-stage').dataset.visualSource = itemPreview.visualSource;
  return itemPreview.visualSource;
}

function renderPreviewThumbnail(item) {
  if (!setPreviewMesh(item)) return '';
  const {renderer, scene, camera, mesh} = itemPreview;
  renderer.setSize(168, 116, false);
  camera.aspect = 168 / 116; camera.updateProjectionMatrix();
  mesh.rotation.set(.45, .15, -.35);
  renderer.render(scene, camera);
  return renderer.domElement.toDataURL('image/png');
}

function selectItemPreview(name) {
  const item = previewItem(name);
  if (!item || !itemPreview) return;
  const visualSource = setPreviewMesh(item);
  if (!visualSource) return;
  selectedPreviewName = name;
  for (const row of $('items').children) {
    row.setAttribute('aria-selected', String(row.dataset.className === name));
    if (row.dataset.className === name) row.dataset.visualSource = visualSource;
  }
  const preview = normalizedClassPreview(item);
  $('items-preview-name').textContent = item.name;
  const millimetres = preview.axes.map(value => (value * 1000).toFixed(1));
  const physical = preview.shape === 'half'
    ? `Profile semi-axes ${millimetres[0]} × ${millimetres[1]} mm. Cut-half thickness ${millimetres[2]} mm.`
    : preview.shape === 'capsule'
      ? `Profile half-length ${millimetres[0]} mm. Radius ${millimetres[1]} mm.`
      : `Profile half-extents ${millimetres.join(' × ')} mm.`;
  const source = visualSource === 'generated'
    ? `Rendered asset ${item.render_asset.visual_asset_id.slice(7, 19)}.`
    : visualSource === 'proxy' ? 'Physical proxy shown while the render asset is unavailable.' : 'Built-in visual.';
  $('items-preview-meta').textContent = `${source} ${physical}`;
  resizeItemPreview();
}

function resizeItemPreview() {
  if (!itemPreview) return;
  const host = $('items-preview-stage');
  const width = Math.max(1, host.clientWidth), height = Math.max(1, host.clientHeight);
  itemPreview.renderer.setSize(width, height, false);
  itemPreview.camera.aspect = width / height;
  itemPreview.camera.updateProjectionMatrix();
}

function refreshItemPreviews() {
  if (!itemPreview) return;
  for (const item of state?.class_catalog || []) {
    const row = $('items')?.querySelector(`[data-class-name="${CSS.escape(item.name)}"]`);
    const image = row?.querySelector('img');
    const dataUrl = renderPreviewThumbnail(item);
    if (image && dataUrl) image.src = dataUrl;
    if (row && itemPreview.visualSource) row.dataset.visualSource = itemPreview.visualSource;
  }
  const selected = previewItem(selectedPreviewName) ? selectedPreviewName : state?.class_catalog?.[0]?.name;
  if (selected) selectItemPreview(selected);
}

function openItems() {
  const dialog = $('items-dialog');
  dialog.showModal();
  const renderer = new THREE.WebGLRenderer({antialias: true, alpha: true, preserveDrawingBuffer: true, powerPreference: 'high-performance'});
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(28, 1, .001, 1);
  camera.position.set(.028, -.045, .026); camera.lookAt(0, 0, 0);
  scene.add(new THREE.HemisphereLight('#fffdf6', '#79877f', 2));
  const light = new THREE.DirectionalLight('#fff2dc', 3); light.position.set(-.03, -.04, .07); scene.add(light);
  $('items-preview-stage').replaceChildren(renderer.domElement);
  itemPreview = {renderer, scene, camera, mesh: null, raf: null, previous: performance.now()};
  reconcileGeneratedAssets();
  refreshItemPreviews();
  const animate = now => {
    if (!itemPreview || !$('items-dialog').open) return;
    const elapsed = Math.min(.05, (now - itemPreview.previous) / 1000); itemPreview.previous = now;
    if (itemPreview.mesh) itemPreview.mesh.rotation.z += elapsed * .75;
    itemPreview.renderer.render(itemPreview.scene, itemPreview.camera);
    itemPreview.raf = requestAnimationFrame(animate);
  };
  itemPreview.raf = requestAnimationFrame(animate);
}

function closeItems() {
  $('items-dialog').close();
  if (!itemPreview) return;
  cancelAnimationFrame(itemPreview.raf);
  if (itemPreview.mesh) {
    disposePreviewMesh(itemPreview.mesh);
  }
  itemPreview.renderer.dispose();
  itemPreview.renderer.forceContextLoss();
  $('items-preview-stage').replaceChildren();
  itemPreview = null;
}

// Queue and Wall of Fame views. The server owns queue truth; every client shows the same rows.
function itemJobsPacket() {
  return liveState?.item_jobs || state?.item_jobs || null;
}

function setItemsView(name) {
  for (const view of ITEM_VIEWS) {
    $(`items-tab-${view}`).setAttribute('aria-pressed', String(view === name));
    $(`items-view-${view}`).hidden = view !== name;
  }
  if (name === 'wall' && wallTotal === null) loadWallPage();
}

function setItemAddStatus(text, failed = false) {
  $('item-add-status').textContent = text;
  $('item-add-status').classList.toggle('error', failed);
}

function jobDetails(job) {
  const details = document.createElement('details'); details.className = 'job-details';
  const summary = document.createElement('summary'); summary.textContent = 'Details';
  const list = document.createElement('dl');
  const attempts = Object.entries(job.attempts).map(([stage, count]) => `${stage} ${count}`).join(' · ');
  for (const [term, value] of [
    ['Request', job.requestId], ['Description', job.description || 'Not recorded'],
    ['Requester', job.requester || 'Not given'], ['State', jobStateLabel(job.state)],
    ['Created', job.createdAt || 'Unknown'], ['Updated', job.updatedAt || 'Unknown'],
    ['Attempts', attempts || 'None'], ['Progress', job.progress || 'None reported'],
    ['Error', jobErrorLabel(job.error) || 'None'],
    ['Provider', job.providerMode ? `${job.providerMode}${job.cacheHit === true ? ', cache hit' : job.cacheHit === false ? ', live request' : ''}` : 'Unknown'],
    ['What happens next', jobStateNote(job.state) || 'The queue continues without an operator.'],
    ['Recovery', jobActionLabel(job.action) || 'None required'],
  ]) {
    const term_ = document.createElement('dt'); term_.textContent = term;
    const value_ = document.createElement('dd'); value_.textContent = value;
    list.append(term_, value_);
  }
  details.append(summary, list);
  // The compact row replays the cache. A billable call needs paid mode and this choice.
  if (job.action === 'resolve_provider' && itemJobsPacket()?.provider_mode === 'paid') {
    const billable = document.createElement('button');
    billable.type = 'button'; billable.className = 'job-action';
    billable.textContent = 'New paid request';
    billable.onclick = () => sendJobAction(job, job.action, 'new_request');
    details.append(billable);
  }
  return details;
}

// One head layout for queue rows and Wall of Fame rows. Wall of Fame preview images
// stay a Phase 4 item, so the helper accepts a null preview.
function buildJobHead({name, meta, preview = null, failed = false, action = null}) {
  const head = document.createElement('div'); head.className = 'job-head';
  const thumb = document.createElement('img'); thumb.className = 'job-thumb'; thumb.alt = '';
  if (preview) thumb.src = preview;
  const copy = document.createElement('div'); copy.className = 'job-name';
  copy.append(document.createTextNode(name));
  const line = document.createElement('small'); line.className = 'job-meta';
  line.textContent = meta;
  line.classList.toggle('error', failed);
  copy.append(line);
  const slot = document.createElement(action ? 'button' : 'span');
  if (action) {
    slot.type = 'button'; slot.className = 'job-action'; slot.textContent = action.label;
    slot.onclick = action.run;
  }
  head.append(thumb, copy, slot);
  return head;
}

function jobRow(job) {
  const row = document.createElement('div'); row.className = 'job-row'; row.dataset.requestId = job.requestId;
  const label = jobActionLabel(job.action);
  const head = buildJobHead({
    name: job.name,
    meta: [jobStateLabel(job.state), job.updatedAt, job.requester && `by ${job.requester}`,
           jobErrorLabel(job.error) || job.progress].filter(Boolean).join(' · '),
    preview: job.preview,
    failed: Boolean(job.error),
    action: label && {label, run: () => sendJobAction(job, job.action, job.action === 'resolve_provider' ? 'use_cache' : null)},
  });
  row.append(head, jobDetails(job));
  return row;
}

function updateItemQueue() {
  // Production never shows a fake successful job without saying so. The queue copy
  // follows the authoritative provider mode, never a default in the page.
  const packet = itemJobsPacket();
  $('item-fake-banner').hidden = packet?.provider_mode !== 'fake';
  $('item-mode-cue').textContent = queueModeCue(packet ? packet.provider_mode : null);
  const summaries = itemJobsPacket()?.summaries || [];
  if (pendingItemRequest) {
    // Only resolve here. A packet that does not hold the id must not restate a waiting
    // cue, otherwise every healthy submit flashes an error at packet rate.
    const known = summaries.map(item => item?.request_id);
    if (known.includes(pendingItemRequest.request_id)) {
      applyPendingOutcome({kind: 'queue', requestIds: known});
    }
  }
  const signature = jobQueueSignature(itemJobsPacket()?.summaries);
  if (signature === itemQueueSignature) return;
  itemQueueSignature = signature;
  const jobs = (itemJobsPacket()?.summaries || []).map(normalizedJobSummary).filter(Boolean);
  if (!jobs.length) {
    const empty = document.createElement('p');
    empty.textContent = 'No generated items are queued.';
    $('item-queue').replaceChildren(empty);
    return;
  }
  $('item-queue').replaceChildren(...jobs.map(jobRow));
}

async function postItemJob(path, body) {
  const response = await fetch(path, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
  });
  const result = await response.json().catch(() => ({}));
  return {ok: response.ok, result};
}

function applyPendingOutcome(outcome) {
  const decision = resolvePendingRequest(pendingItemRequest, outcome);
  if (decision.resolved) {
    pendingItemRequest = null;
    // Clear the form only when the request really landed. A rejection keeps the text,
    // so the user can edit it and send a NEW request with a new id.
    if (!decision.failed) $('item-description').value = '';
  }
  if (decision.cue) setItemAddStatus(decision.cue, decision.failed);
  updatePendingControls();
  return decision;
}

function updatePendingControls() {
  const waiting = Boolean(pendingItemRequest);
  // While a snapshot waits, the form cannot change what a retry would send.
  for (const id of ('item-description item-requester item-submit').split(' ')) $(id).disabled = waiting;
  $('item-retry').hidden = !waiting;
}

async function sendPendingItemRequest() {
  if (!pendingItemRequest) return;
  const snapshot = pendingItemRequest;
  $('item-submit').disabled = true;
  $('item-retry').disabled = true;
  setItemAddStatus('Submitting');
  try {
    // Exactly the frozen bytes. Never a payload rebuilt from the form or from state.
    const {ok, result} = await postItemJob('/item-jobs', snapshot);
    applyPendingOutcome(ok
      ? {kind: 'accepted', requestId: snapshot.request_id}
      : {kind: 'rejected', errorCode: result.error_code});
  } catch (error) {
    // A network error proves nothing. The server may already hold the job.
    applyPendingOutcome({kind: 'network'});
  } finally {
    $('item-retry').disabled = false;
    updatePendingControls();
  }
}

function submitItemJob(event) {
  event.preventDefault();
  if (pendingItemRequest) return sendPendingItemRequest();
  const description = $('item-description').value.trim();
  const revision = itemJobsPacket()?.catalog_revision;
  if (!description || !revision) {
    setItemAddStatus('Describe the item and wait for the catalog.', true);
    return;
  }
  pendingItemRequest = freezeItemRequest({
    requestId: crypto.randomUUID(), description, catalogRevision: revision,
    requesterName: $('item-requester').value.trim(),
  });
  updatePendingControls();
  return sendPendingItemRequest();
}

async function sendJobAction(job, action, choice) {
  const path = jobActionPath(job.requestId, action);
  if (!path) return;
  setItemAddStatus(`${jobActionLabel(action)} sent`);
  try {
    const {ok, result} = await postItemJob(path, choice ? {action: choice} : {});
    if (!ok) setItemAddStatus(jobErrorLabel(result.error_code) || 'The recovery action failed.', true);
    else setItemAddStatus(`${jobActionLabel(action)} accepted`);
  } catch (error) {
    setItemAddStatus('The service did not answer.', true);
  }
}

function wallRow(entry) {
  const row = document.createElement('div'); row.className = 'job-row';
  // The one shared preview renderer stays the single WebGL context. No card holds one.
  row.append(buildJobHead({
    name: String(entry?.display_name || entry?.object_type_id || 'Archived item'),
    meta: [`Retired ${entry?.retired_at || 'at an unknown time'}`,
           entry?.classifier_label, entry?.provenance?.kind].filter(Boolean).join(' · '),
  }));
  row.onclick = () => selectWallEntry(entry);
  return row;
}

function selectWallEntry(entry) {
  $('items-preview-name').textContent = String(entry?.display_name || entry?.object_type_id || 'Archived item');
  $('items-preview-meta').textContent = `Archived ${entry?.retired_at || 'at an unknown time'}. `
    + `Label ${entry?.classifier_label || 'unknown'}. Definition ${String(entry?.definition_sha256 || '').slice(0, 12)}. `
    + 'An archived definition stays read-only and never re-enters the active catalog.';
}

function renderWallPage(unreadable) {
  $('wall-rows').replaceChildren(...(wallEntries.length ? wallEntries.map(wallRow) : [(() => {
    const empty = document.createElement('p');
    empty.textContent = 'No replaced types are archived yet.';
    return empty;
  })()]));
  const total = wallTotal === null ? wallEntries.length : wallTotal;
  $('wall-status').textContent = `${wallEntries.length} of ${total} archived`
    + (unreadable ? ` · ${unreadable} unreadable` : '');
}

async function loadWallPage() {
  if (wallLoading) return;
  wallLoading = true;
  $('wall-more').disabled = true;
  $('wall-status').textContent = 'Loading';
  try {
    const response = await fetch(`/wall-of-fame?offset=${wallOffset}&limit=${WALL_PAGE}`);
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      $('wall-status').textContent = 'The archived page did not load.';
      return;
    }
    wallEntries = wallEntries.concat(Array.isArray(result.entries) ? result.entries : []);
    wallTotal = Number.isInteger(result.total) ? result.total : wallEntries.length;
    wallOffset = wallEntries.length;
    renderWallPage(result.unreadable);
  } catch (error) {
    $('wall-status').textContent = 'The service did not answer.';
  } finally {
    wallLoading = false;
    $('wall-more').disabled = wallTotal !== null && wallOffset >= wallTotal;
  }
}

$('items-open').onclick = openItems;
for (const view of ITEM_VIEWS) $(`items-tab-${view}`).onclick = () => setItemsView(view);
$('item-add').onsubmit = submitItemJob;
$('item-retry').onclick = sendPendingItemRequest;
$('wall-more').onclick = loadWallPage;
$('items-close').onclick = closeItems;
$('items-dialog').addEventListener('cancel', event => { event.preventDefault(); closeItems(); });
$('keep-all').onclick = () => queuePolicySet(false);
$('reject-all').onclick = () => queuePolicySet(true);

function updateLatestEvidence() {
  const request = latestRequest();
  const object = request?.object_id == null ? null
    : (state?.objects || []).find(item => item.object_id === request.object_id)
      || (state?.injected_objects || []).find(item => item.object_id === request.object_id);
  selected = request?.object_id ?? null;
  const decision = object?.decision;
  const expectedOutcome = request?.ack?.expected_outcome || object?.expected_outcome || null;
  const comparison = compareExpectedOutcome(expectedOutcome, object?.outcome);
  const values = {
    prediction: decision ? `${decision.predicted_class}${decision.association_approximate ? ' (approximate object match)' : ''}` : 'Not observed',
    decision: decision ? (decision.scheduled ? 'Pulse commanded' : decision.reject ? (decision.late ? 'Reject decision, too late' : 'Reject decision, no pulse') : 'No pulse commanded') : 'Not decided',
    hit: object?.jet_hits ? `${object.own_pulse_hit ? 'Own pulse' : 'Other pulse'}, ${object.jet_hits} force step${object.jet_hits === 1 ? '' : 's'}` : 'No force contact',
    outcome: object?.outcome ? ({accept:'Keep path', reject:'Reject path', spilled:'Spilled'})[object.outcome] : 'Unresolved',
    'outcome-time': 'Waiting',
  };
  if (object?.outcome && request && request.outcomeWall === undefined && request.spawnWall !== undefined) {
    request.outcomeWall = performance.now();
    request.outcome_wall_s = (request.outcomeWall - request.spawnWall) / 1000;
  }
  if (object?.spawn_to_outcome_wall_s != null) values['outcome-time'] = `${object.spawn_to_outcome_wall_s.toFixed(2)} wall seconds from physical spawn`;
  else if (request?.outcome_wall_s != null) values['outcome-time'] = `${request.outcome_wall_s.toFixed(2)} wall seconds from physical spawn`;
  for (const [id, text] of Object.entries(values)) {
    $(id).textContent = text;
    $(`card-${id}`).textContent = text;
  }
  $('card-expected').textContent = comparison.expectedLabel;
  $('card-actual').textContent = comparison.actualLabel;
  $('card-decision-summary').textContent = decision ? `${decision.predicted_class} · ${values.decision}` : 'Waiting';
  $('card-empty').hidden = Boolean(request);
  $('card-content').hidden = !request;
  const stateLabel = request?.error || request?.invalidated ? '[!] Command failed'
    : comparison.verdict === 'As expected' ? '[OK] As expected'
      : comparison.verdict === 'Unexpected' ? '[!] Unexpected'
        : '[...] In progress';
  $('card-state').textContent = stateLabel;
  $('card-state').classList.toggle('ok', comparison.verdict === 'As expected');
  $('card-state').classList.toggle('unexpected', comparison.verdict === 'Unexpected' || Boolean(request?.error || request?.invalidated));
  $('object-title').textContent = request?.object_id == null ? (request ? 'Following your stone' : 'Follow your stone') : `Your stone, object ${request.object_id}`;
  $('command').textContent = request ? request.payload.command_id : 'No injection yet';
  $('card-command').textContent = request
    ? `Command ${request.payload.command_id.slice(0, 8)}`
    : 'No injection yet';
  let acknowledgment = 'Waiting';
  if (request?.invalidated) acknowledgment = 'Engine session changed. The request was not retried.';
  else if (request?.error) acknowledgment = 'Injection failed';
  else if (request?.acknowledged) {
    const position = request.spawn_position;
    acknowledgment = `${request.acknowledgment_ms.toFixed(0)} ms${Array.isArray(position) ? ` at ${position.map(value => Number(value).toFixed(3)).join(', ')} m` : ''}`;
  }
  else if (request?.serverPending) acknowledgment = 'Server still has the original injection request';
  else if (request?.retryPending) acknowledgment = 'Retrying the original injection request';
  else if (request) acknowledgment = 'Waiting for physical spawn';
  $('ack').textContent = acknowledgment;
  const expectationPolicy = request?.ack?.expectation_policy_version || object?.expectation_policy_version;
  $('expectation-policy').textContent = expectationPolicy || 'Waiting for authoritative injection evidence';
  $('card-error').hidden = !request?.error;
  $('card-error').textContent = request?.error ? `Injection failed: ${request.error}` : '';
  measurements.acknowledgment_ms = request?.acknowledgment_ms ?? null;
  measurements.outcome_wall_s = request?.outcome_wall_s ?? null;
}

function formatScore(score, name, warmingUp) {
  if (Number.isFinite(score?.value)) return `${(score.value * 100).toFixed(1)}%`;
  if (score?.denominator === 0) return emptyMetricState({
    metric: name,
    warmingUp,
    catalogSize: state?.class_catalog?.length || 0,
    rejectSize: state?.reject_policy?.reject_classes?.length || 0,
  });
  return 'Computing';
}

function formatCount(score, emptyLabel, name, warmingUp) {
  return Number.isFinite(score?.denominator) && score.denominator > 0
    ? `${score.numerator ?? 0} / ${score.denominator}`
    : `0 / 0 · ${formatScore(score, name, warmingUp) === 'Computing' ? 'Computing' : emptyLabel}`;
}

function updateScoreboard() {
  const scores = state.rolling_scores;
  $('scoreboard').hidden = !continuousMode();
  if (!continuousMode()) return;
  if (!scores) {
    $('score-status').textContent = 'Waiting for server score aggregates';
    for (const [valueId, countId, emptyLabel] of [
      ['score-accuracy', 'score-accuracy-count', 'No eligible objects'],
      ['score-capture', 'score-capture-count', 'No reject items'],
      ['score-loss', 'score-loss-count', 'No keep items'],
      ['score-unresolved', 'score-unresolved-count', 'No eligible objects'],
    ]) {
      $(valueId).textContent = 'Computing';
      $(countId).textContent = `0 / 0 · ${emptyLabel}`;
    }
    $('score-context').textContent = 'Scores use a rolling simulated-time window.';
    $('score-versions').textContent = 'Version context is unavailable.';
    return;
  }
  const metricIds = [
    ['sorting_accuracy', 'score-accuracy', 'score-accuracy-count', 'No eligible objects'],
    [scores.reject_capture ? 'reject_capture' : 'defect_capture', 'score-capture', 'score-capture-count', 'No reject items'],
    [scores.keep_loss ? 'keep_loss' : 'good_loss', 'score-loss', 'score-loss-count', 'No keep items'],
    ['unresolved', 'score-unresolved', 'score-unresolved-count', 'No eligible objects'],
  ];
  for (const [name, valueId, countId, emptyLabel] of metricIds) {
    $(valueId).textContent = formatScore(scores[name], name, scores.warming_up);
    $(countId).textContent = formatCount(scores[name], emptyLabel, name, scores.warming_up);
  }
  const asOf = Number(scores.as_of_sim_time_s || 0);
  const available = Number(scores.available_seconds || 0);
  const window = Number(scores.window_seconds || 0);
  const settling = Number(scores.settling_seconds || 0);
  const start = Number(scores.window_start_exclusive_s || 0);
  const end = Number(scores.window_end_inclusive_s || 0);
  const windowLabel = Number.isInteger(window) ? String(window) : window.toFixed(1);
  const settlingLabel = Number.isInteger(settling) ? String(settling) : settling.toFixed(1);
  $('score-help').dataset.help = `The window covers ${windowLabel} simulated seconds and excludes the newest ${settlingLabel} seconds for settling. Manual drops are excluded. Policy changes start a new window.`;
  $('score-status').textContent = `${asOf.toFixed(1)} s · ${scores.warming_up ? 'warming' : 'ready'}`;
  $('score-context').textContent = `Window (${start.toFixed(1)}, ${end.toFixed(1)}] simulated seconds. Available ${available.toFixed(1)} / ${window.toFixed(1)} simulated seconds. ${scores.settling_objects ?? 0} settling object${scores.settling_objects === 1 ? '' : 's'}. Manual injections ${scores.manual_injections_excluded ? 'excluded' : 'not excluded'}. Settling delay ${settling.toFixed(1)} simulated seconds.`;
  const versions = scores.versions || {};
  $('score-versions').textContent = `Score epoch ${scores.score_epoch_id?.slice(0, 8) || 'unavailable'} · Source ${versions.source_revision?.slice(0, 7) || 'unavailable'} · Model ${versions.model?.slice(0, 12) || 'unavailable'} · Policy ${versions.policy?.slice(0, 12) || 'unavailable'}`;
}

function setMetric(id, text) {
  const element = $(id);
  if (element) element.textContent = text;
  const mirror = document.getElementById(`diag-${id}`);
  if (mirror) mirror.textContent = text;
}

function update() {
  if (!state) return;
  reconcileGeneratedAssets();
  syncCollectionSurfaces();
  const commandState = liveState || state;
  const status = commandState.status;
  const connected = socket?.readyState === WebSocket.OPEN;
  const heartbeatAge = heartbeatSeenAt === null ? null : performance.now() - heartbeatSeenAt;
  const statusLabel = ({starting:'Preparing', restarting:'Restarting', ready:'Ready', running:'Live', completed:'Session complete', failed:'Engine failed'})[status] || status;
  const heartbeatStale = continuousMode() && heartbeatAge !== null && heartbeatAge >= HEARTBEAT_IDLE_MS;
  $('status').textContent = !connected ? 'Disconnected' : heartbeatStale ? `${statusLabel} · stale` : statusLabel;
  $('status').dataset.state = status;
  const durationComplete = !liveContinuousMode() && commandState.sim_time_s >= (commandState.limits?.sim_seconds || 10) - 0.6;
  const awaitingHeartbeat = liveContinuousMode() && heartbeatSeenAt === null;
  $('inject').disabled = !connected || restartPending || awaitingHeartbeat || !['ready', 'running'].includes(status) || durationComplete || (liveContinuousMode() && !commandState.command_epoch);
  $('restart').hidden = !commandState.restart_supported;
  $('restart').disabled = !connected || !commandState.restart_supported || restartPending || !commandState.session_id || !['ready', 'running', 'completed', 'failed'].includes(status);
  $('restart').textContent = restartPending || status === 'restarting' ? 'Restarting…' : 'Restart session';
  const boundedNotice = {starting:'Preparing the engine', restarting:'Stopping the old session and preparing a new one', ready:'Ready for a physical injection', running:'The simulation clock shows the actual engine rate', completed:'Session complete. Select Restart session to inject again.', failed:'The engine stopped. Select Restart session to try again.'};
  const continuousNotice = {starting:'Preparing the continuous engine', restarting:'Restarting the engine session', ready:'Continuous engine ready', running:'Continuous engine runs without this page', completed:'Continuous engine completed unexpectedly', failed:'The continuous engine stopped'};
  $('notice').textContent = (continuousMode() ? continuousNotice : boundedNotice)[status] || 'Waiting for the engine';
  $('mode-note').textContent = continuousMode()
    ? 'The conveyor runs continuously. Injection adds one manual object to the shared stream and does not change feed scores.'
    : 'The first injection starts the conveyor. Restart resets the shared session for all browsers.';
  if (state.error) $('error').textContent = state.error;
  setMetric('sim-time', `${(state.sim_time_s || 0).toFixed(2)} s`);
  setMetric('engine-rate', formatEngineRate(state.engine_rate) || 'Waiting');
  setMetric('admitted', `${(state.admitted_rate || 0).toFixed(0)} /s`);
  const objects = state.objects || [];
  setMetric('active', String(objects.filter(o => o.active).length));
  setMetric('pose-hz', measurements.pose_hz ? `${measurements.pose_hz.toFixed(1)} Hz` : 'Waiting');
  setMetric('heartbeat', heartbeatAge === null ? 'Waiting' : `${(heartbeatAge / 1000).toFixed(1)} s ago`);
  updateScoreboard();
  updateLatestEvidence();
  updateItems();
  const scoreVersions = state.rolling_scores?.versions || {};
  const modelVersion = scoreVersions.model || state.model_version;
  const policyVersion = scoreVersions.policy || state.policy_version;
  const sourceRevision = scoreVersions.source_revision || state.source_revision;
  $('versions').textContent = modelVersion ? `Engine source ${sourceRevision?.slice(0,7) || 'unavailable'} · Session ${state.session_id?.slice(0,8) || 'unavailable'} · Model ${modelVersion.slice(0,12)} · Policy ${policyVersion?.slice(0,12) || 'unavailable'}${state.preset_version ? ` · Preset ${state.preset_version.slice(0,12)}` : ''}` : 'Source, model, and policy versions appear when the engine is ready.';
  for (const event of state.events || []) {
    if (selected !== null && (event.object_id === selected || event.object_ids?.includes(selected))) selectedEvents.set(event.event_id, event);
  }
  while (selectedEvents.size > 6) selectedEvents.delete(selectedEvents.keys().next().value);
  const relevant = (selected === null ? state.events || [] : [...selectedEvents.values()]).slice(-6).reverse();
  $('events').replaceChildren(...relevant.map(event => {
    const li = document.createElement('li');
    li.textContent = `${Number(event.sim_time_s || 0).toFixed(3)} s: ${event.type}${event.outcome ? `, ${event.outcome}` : ''}${event.predicted_class ? `, predicted ${event.predicted_class}` : ''}`;
    return li;
  }));
  if (!relevant.length) { const li = document.createElement('li'); li.textContent = 'Waiting for an associated camera decision.'; $('events').append(li); }
  const evicted = state.injection_history_evicted ?? state.retention?.injection_history_evicted ?? 0;
  $('limit-note').textContent = continuousMode()
    ? `Requested feed ${state.requested_rate || 500}/s. Manual injections stay outside rolling feed scores. Injection history evicted: ${evicted} completed record${evicted === 1 ? '' : 's'}.`
    : `Requested feed ${state.requested_rate || 500}/s. Session limit ${state.limits?.sim_seconds || 10} simulated seconds or ${state.limits?.wall_seconds || 300} wall seconds. Restart session resets the shared session for all browsers.`;
}

// Complete snapshots stay ordered in a short presentation buffer. The view
// interpolates only between known poses from the same session and policy epoch.
const snapshots = [];
let displayFrame = {before: null, after: null, afterById: new Map(), alpha: 0, waiting: true};
let indexedAfterPacket = null;
let indexedAfterById = new Map();
let lastEventId = -1;
const puffs = [];
const SUPPORTED_RENDER_SHAPES = new Set(['ellipsoid', 'half', 'box', 'capsule']);

function hasAuthoritativeRenderFields(object) {
  const finiteArray = (value, length) => (
    Array.isArray(value) && value.length === length && value.every(Number.isFinite)
  );
  return (
    Number.isInteger(object?.object_id) &&
    SUPPORTED_RENDER_SHAPES.has(object.shape) &&
    finiteArray(object.pos, 3) &&
    finiteArray(object.quat, 4) &&
    object.quat.some(value => value !== 0) &&
    finiteArray(object.axes, 3) &&
    object.axes.every(value => value > 0) &&
    finiteArray(object.rgb, 3) &&
    object.rgb.every(value => value >= 0 && value <= 1)
  );
}

function resetDisplayTimeline() {
  snapshots.length = 0;
  state = null;
  displayFrame = {before: null, after: null, afterById: new Map(), alpha: 0, waiting: true};
  indexedAfterPacket = null;
  indexedAfterById = new Map();
  lastEventId = -1;
  puffs.length = 0;
}

function enqueueSnapshot(packet, arrivedAt) {
  snapshots.push({packet, arrivedAt});
  while (snapshots.length > SNAPSHOT_LIMIT) snapshots.shift();
  while (snapshots.length > 2 && arrivedAt - snapshots[0].arrivedAt > SNAPSHOT_MAX_AGE_MS) snapshots.shift();
}

function presentSnapshot(packet, now) {
  state = packet;
  window.coffeeState = state;
  const L = packet.layout;
  let maxId = lastEventId;
  for (const event of packet.events || []) {
    if (event.event_id <= lastEventId) continue;
    maxId = Math.max(maxId, event.event_id);
    if (event.type !== 'valve_activated' || !L) continue;
    const target = (packet.objects || []).find(object => event.object_ids?.includes(object.object_id));
    const y = target?.pos ? target.pos[1] : 0;
    puffs.push({x: L.ej_x, y, z: L.belt_z + L.ej_z_offset, at: now});
  }
  if (lastEventId === -1) puffs.length = 0;
  lastEventId = maxId;
  while (puffs.length > 48) puffs.shift();
  update();
}

function advanceDisplayTimeline(now) {
  const target = now - DISPLAY_DELAY_MS;
  while (snapshots.length > 1 && snapshots[1].arrivedAt <= target) snapshots.shift();
  const before = snapshots[0];
  if (before && before.arrivedAt <= target && state !== before.packet) presentSnapshot(before.packet, now);
  const after = state === before?.packet ? snapshots[1] : null;
  const canBlend = after && samePresentationTimeline(before.packet, after.packet);
  if (canBlend && indexedAfterPacket !== after.packet) {
    indexedAfterPacket = after.packet;
    indexedAfterById = new Map((after.packet.objects || []).map(object => [object.object_id, object]));
  } else if (!canBlend && indexedAfterPacket !== null) {
    indexedAfterPacket = null;
    indexedAfterById = new Map();
  }
  const alpha = canBlend
    ? THREE.MathUtils.clamp((target - before.arrivedAt) / Math.max(1, after.arrivedAt - before.arrivedAt), 0, 1)
    : 0;
  const waiting = Boolean(state && !after && target > before.arrivedAt);
  displayFrame = {
    before,
    after: canBlend ? after : null,
    afterById: indexedAfterById,
    alpha,
    waiting,
  };
  measurements.buffer_waiting = waiting;
  updatePerformanceBadge(waiting);
}

function updatePerformanceBadge(waiting) {
  const output = $('performance-badge');
  const fpsLabel = Number.isFinite(measurements.fps) ? `${measurements.fps.toFixed(0)} FPS` : 'Measuring FPS';
  const connected = socket?.readyState === WebSocket.OPEN && !staleConnection;
  const connecting = !socket || socket.readyState === WebSocket.CONNECTING;
  const engineRate = formatEngineRate(liveState?.engine_rate);
  const hasEngineRate = connected && telemetryFresh && engineRate !== null;
  const simLabel = !connected
    ? connecting ? 'Sim connecting' : 'Sim disconnected'
    : hasEngineRate ? `Sim ${engineRate}` : 'Sim measuring';
  const viewWaiting = connected && telemetryFresh && waiting;
  const label = `${fpsLabel} · ${simLabel}${viewWaiting ? ' · View waiting' : ''}`;
  const badgeState = !connected && !connecting ? 'disconnected' : viewWaiting || !telemetryFresh ? 'waiting' : 'live';
  if (output.textContent !== label) output.textContent = label;
  if (output.dataset.state !== badgeState) output.dataset.state = badgeState;
}

function initHelpTooltips() {
  const tooltip = $('help-tooltip');
  let target = null;

  const position = () => {
    if (!target || tooltip.hidden) return;
    const anchor = target.getBoundingClientRect();
    const box = tooltip.getBoundingClientRect();
    const margin = 8;
    const left = Math.min(window.innerWidth - box.width - margin, Math.max(margin, anchor.left + (anchor.width - box.width) / 2));
    const below = anchor.bottom + margin;
    const top = below + box.height <= window.innerHeight - margin ? below : anchor.top - box.height - margin;
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${Math.max(margin, top)}px`;
  };
  const show = element => {
    if (!element?.dataset.help) return;
    if (target && target !== element) target.removeAttribute('aria-describedby');
    target = element;
    tooltip.textContent = element.dataset.help;
    tooltip.hidden = false;
    element.setAttribute('aria-describedby', tooltip.id);
    position();
  };
  const hide = element => {
    if (element && element !== target) return;
    target?.removeAttribute('aria-describedby');
    target = null;
    tooltip.hidden = true;
  };

  for (const element of document.querySelectorAll('[data-help]')) {
    element.addEventListener('pointerenter', event => { if (event.pointerType !== 'touch') show(element); });
    element.addEventListener('pointerleave', event => { if (event.pointerType !== 'touch' && document.activeElement !== element) hide(element); });
    element.addEventListener('focus', () => show(element));
    element.addEventListener('blur', () => hide(element));
    element.addEventListener('pointerup', event => { if (event.pointerType === 'touch') show(element); });
  }
  document.addEventListener('pointerdown', event => { if (!event.target.closest?.('[data-help]')) hide(); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') hide(); });
  window.addEventListener('resize', position);
  document.addEventListener('scroll', position, true);
}

function displayedPose(object, outPos, outQuat) {
  outPos.fromArray(object.pos);
  outQuat.set(object.quat[1], object.quat[2], object.quat[3], object.quat[0]).normalize();
  if (!displayFrame.after || displayFrame.alpha <= 0) return;
  const next = displayFrame.afterById.get(object.object_id);
  if (!hasAuthoritativeRenderFields(next) || next.appearance_key !== object.appearance_key
      || next.visual_asset_id !== object.visual_asset_id) return;
  const a = object.pos, b = next.pos, alpha = displayFrame.alpha;
  outPos.set(a[0] + (b[0] - a[0]) * alpha, a[1] + (b[1] - a[1]) * alpha, a[2] + (b[2] - a[2]) * alpha);
  _qa.set(object.quat[1], object.quat[2], object.quat[3], object.quat[0]).normalize();
  _qb.set(next.quat[1], next.quat[2], next.quat[3], next.quat[0]).normalize();
  outQuat.copy(_qa).slerp(_qb, alpha);
}
const _qa = new THREE.Quaternion(), _qb = new THREE.Quaternion();

// ---------------------------------------------------------------------------
// 3D stage: an engineering-drawing style view built from the engine layout.
// Conventions borrowed from technical drawings: matte fills, visible edges,
// dimension callouts in metres, and a title block with the machine dimensions.
// ---------------------------------------------------------------------------
const stage = $('stage');
const three = {ready: false, machineBuilt: false, labels: true, cameraPreset: 'overview', inspectionHousing: []};
const dimLines = [];
const annotations = [];
const clickTargets = [];
const presets = {
  overview: {position: [1.82, -2.62, 1.98], target: [-.22, 0, .47]},
  sorting: {position: [.20, 1.75, .58], target: [.12, 0, .48]},
  inspection: {position: [.82, 0, 1.65], target: [-.35, 0, .58]},
};
const INK = '#25342d', EDGE = '#34463d', EDGE_SOFT = '#829188', PAPER = '#eef0ea';
const REJECT_COLOR = new THREE.Color('#d26045'), SPILL_COLOR = new THREE.Color('#d49a27'), SELECT_COLOR = new THREE.Color('#d8781c');

function showWebglError(message) {
  let element = $('webgl-error');
  if (!element) {
    element = document.createElement('div');
    element.id = 'webgl-error';
    element.setAttribute('role', 'alert');
    stage.append(element);
  }
  element.textContent = message;
}

function initThree() {
  try {
    const renderer = new THREE.WebGLRenderer({antialias: true, alpha: false, powerPreference: 'high-performance'});
    const gl = renderer.getContext(), debug = gl.getExtension('WEBGL_debug_renderer_info');
    const gpuName = debug ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : 'unknown';
    const softwareRenderer = /swiftshader|llvmpipe|software/i.test(gpuName);
    renderer.setPixelRatio(softwareRenderer ? .6 : Math.min(window.devicePixelRatio, 1.5));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.08;
    renderer.domElement.className = 'webgl';
    renderer.domElement.setAttribute('aria-label', 'Live CINTA conveyor; drag to orbit, scroll to zoom, click the machine to drop a test stone');
    renderer.domElement.addEventListener('webglcontextlost', event => {
      event.preventDefault();
      three.restoreView = currentView;
      three.ready = false;
      measurements.webgl = 'context lost';
      clearGeneratedAssets();
      setView('2d');
      showWebglError('3D graphics paused. Showing the live 2D view while graphics recover.');
    });
    renderer.domElement.addEventListener('webglcontextrestored', () => {
      three.ready = true;
      measurements.webgl = gpuName;
      $('webgl-error')?.remove();
      reconcileGeneratedAssets();
      if (three.restoreView === '3d') setView('3d');
      three.restoreView = null;
    });
    stage.prepend(renderer.domElement);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(PAPER);
    const persp = new THREE.PerspectiveCamera(33, 1, .005, 30);
    persp.up.set(0, 0, 1);
    const controls = new OrbitControls(persp, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = .08;
    controls.minDistance = .12;
    controls.maxDistance = 5;
    controls.maxPolarAngle = Math.PI * .49;
    const pmrem = new THREE.PMREMGenerator(renderer); const room = new RoomEnvironment();
    scene.environment = pmrem.fromScene(room, .04).texture; scene.environmentIntensity = .62; room.dispose(); pmrem.dispose();
    scene.add(new THREE.HemisphereLight('#fffdf6', '#aeb8b1', 1.05));
    const key = new THREE.DirectionalLight('#fff6e6', 2.1); key.position.set(-1.8, -2.8, 4.5); scene.add(key);
    const fill = new THREE.DirectionalLight('#dbeeff', .75); fill.position.set(1.8, 2.4, 2.2); scene.add(fill);
    // 100 mm grid on the floor, 1 m major lines.
    const grid = new THREE.GridHelper(6, 60, '#b5bcb3', '#dfe3dc'); grid.rotation.x = Math.PI / 2; grid.position.z = .001; scene.add(grid);
    const major = new THREE.GridHelper(6, 6, '#9aa39a', '#9aa39a'); major.rotation.x = Math.PI / 2; major.position.z = .0015; scene.add(major);
    Object.assign(three, {renderer, scene, persp, camera: persp, controls, gpuName});
    function resize() {
      const w = stage.clientWidth, h = stage.clientHeight;
      renderer.setSize(w, h);
      persp.aspect = w / h;
      persp.fov = THREE.MathUtils.radToDeg(2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(33) / 2) * Math.max(1, 1.2 / persp.aspect)));
      persp.updateProjectionMatrix();
    }
    new ResizeObserver(resize).observe(stage); resize();
    setCamera('overview');
    // Click on the belt or table = inject a stone. A drag stays an orbit.
    const raycaster = new THREE.Raycaster(); const pointer = new THREE.Vector2(); let down = null;
    renderer.domElement.addEventListener('pointerdown', e => { down = {x: e.clientX, y: e.clientY, t: performance.now()}; });
    renderer.domElement.addEventListener('pointerup', e => {
      if (!down) return;
      const moved = Math.hypot(e.clientX - down.x, e.clientY - down.y), held = performance.now() - down.t; down = null;
      if (moved > 6 || held > 600 || !clickTargets.length) return;
      const r = renderer.domElement.getBoundingClientRect();
      pointer.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
      raycaster.setFromCamera(pointer, three.camera);
      if (raycaster.intersectObjects(clickTargets, false).length) injectStone();
    });
    three.ready = true;
    measurements.webgl = gpuName;
  } catch (error) {
    showWebglError(`The 3D view could not start: ${error.message}. The 2D view still follows the engine.`);
    setView('2d');
    console.error(error);
    measurements.webgl = 'unavailable';
  }
}

function setCamera(name) {
  const p = presets[name]; if (!p || !three.camera) return;
  three.camera.position.fromArray(p.position); three.controls.target.fromArray(p.target); three.controls.update();
  three.cameraPreset = name;
  updateMachineVisibility();
  document.querySelectorAll('[data-camera]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.camera === name)));
}

function updateMachineVisibility() {
  const hideHousing = three.cameraPreset === 'inspection' || currentView !== '3d';
  for (const mesh of three.inspectionHousing || []) mesh.visible = !hideHousing;
}

function setView(name) {
  if (!['3d', '2d'].includes(name)) return;
  if (name === '3d' && !three.ready) name = '2d';
  if (name === '2d' && currentView !== '2d') {
    measurements.generated_asset_instances = null;
    measurements.builtin_or_proxy_instances = null;
    measurements.generated_proxy_instances = null;
    measurements.render_omitted = null;
  }
  currentView = name;
  document.body.dataset.view = name;
  document.querySelectorAll('button[data-view]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.view === name)));
  updateMachineVisibility();
  resizeInset();
}

const matte = (color, opacity = 1, metalness = .04) => new THREE.MeshStandardMaterial({
  color,
  roughness: metalness > .3 ? .34 : .72,
  metalness,
  transparent: opacity < 1,
  opacity,
  depthWrite: opacity === 1,
  side: opacity < 1 ? THREE.DoubleSide : THREE.FrontSide,
});

function buildMachine(L) {
  const {scene} = three;
  const materials = {
    steel: matte('#c3cac6', 1, .55), frame: matte('#55635c', 1, .22), belt: matte('#557ba3'), dark: matte('#35423c', 1, .18),
    volume: matte('#b9c4bd', .18), accept: matte('#9bbf90', .2), reject: matte('#c99a78', .2),
  };
  const edgeMat = new THREE.LineBasicMaterial({color: EDGE}), softEdgeMat = new THREE.LineBasicMaterial({color: EDGE_SOFT});
  const machineGroup = new THREE.Group(); scene.add(machineGroup);
  const add = (geometry, material, pos, {rot = null, edges = true, soft = false, click = false} = {}) => {
    const mesh = new THREE.Mesh(geometry, material);
    mesh.position.set(...pos); if (rot) mesh.rotation.set(...rot);
    machineGroup.add(mesh);
    if (edges) { const line = new THREE.LineSegments(new THREE.EdgesGeometry(geometry, 20), soft ? softEdgeMat : edgeMat); mesh.add(line); }
    if (click) clickTargets.push(mesh);
    return mesh;
  };
  const beltX0 = -L.belt_len, beltX1 = 0, beltMid = -L.belt_len / 2;
  add(new THREE.BoxGeometry(L.belt_len, L.belt_w, .02), materials.belt, [beltMid, 0, L.belt_z - .01], {click: true});
  for (const x of [beltX0, beltX1]) add(new THREE.CylinderGeometry(.035, .035, L.belt_w + .02, 24), materials.steel, [x, 0, L.belt_z - .035], {soft: true, click: true});
  add(new THREE.BoxGeometry(L.belt_len + .1, L.belt_w + .08, .05), materials.frame, [beltMid, 0, L.belt_z - .095], {click: true});
  for (const x of [beltX0 + .05, beltX1 - .05]) for (const y of [-1, 1]) add(new THREE.BoxGeometry(.04, .04, L.belt_z - .12), materials.frame, [x, y * (L.belt_w / 2 + .02), (L.belt_z - .12) / 2], {click: true});
  for (const y of [-1, 1]) add(new THREE.BoxGeometry(L.belt_len, .01, .035), materials.steel, [beltMid, y * (L.belt_w / 2 + .005), L.belt_z + .017], {click: true});
  const spawn = state.spawn_region || {x_min: L.feed_x[0], x_max: L.feed_x[1], y_min: -L.belt_w / 2, y_max: L.belt_w / 2, belt_z: L.belt_z};
  const spawnCue = new THREE.Mesh(
    new THREE.BoxGeometry(spawn.x_max - spawn.x_min, spawn.y_max - spawn.y_min, .002),
    new THREE.MeshBasicMaterial({color: '#28a4c2', transparent: true, opacity: .38, depthWrite: false}),
  );
  spawnCue.position.set((spawn.x_min + spawn.x_max) / 2, (spawn.y_min + spawn.y_max) / 2, spawn.belt_z + .003);
  scene.add(spawnCue);
  // Feeder hopper over the spawn interval.
  const feedMid = (L.feed_x[0] + L.feed_x[1]) / 2;
  add(new THREE.BoxGeometry(L.feed_x[1] - L.feed_x[0] + .06, L.belt_w, .18), materials.volume, [feedMid, 0, L.belt_z + .22], {soft: true});
  add(new THREE.BoxGeometry(L.feed_x[1] - L.feed_x[0] + .06, L.belt_w + .04, .02), materials.frame, [feedMid, 0, L.belt_z + .32]);
  for (const y of [-1, 1]) add(new THREE.BoxGeometry(.03, .03, .34), materials.frame, [feedMid, y * (L.belt_w / 2 + .05), L.belt_z + .17]);
  // Camera bridge and line-scan footprint.
  add(new THREE.BoxGeometry(.08, L.belt_w + .14, .06), materials.frame, [L.cam_x, 0, L.belt_z + .42]);
  add(new THREE.BoxGeometry(.05, .09, .06), materials.dark, [L.cam_x, 0, L.belt_z + .36]);
  for (const y of [-1, 1]) add(new THREE.BoxGeometry(.026, .026, .43), materials.frame, [L.cam_x, y * (L.belt_w / 2 + .048), L.belt_z + .205]);
  const scan = new THREE.Mesh(new THREE.PlaneGeometry(L.cam_fov, L.belt_w), new THREE.MeshBasicMaterial({color: '#4fb3a0', transparent: true, opacity: .45, depthWrite: false, side: THREE.DoubleSide}));
  scan.position.set(L.cam_x, 0, L.belt_z + .0008); machineGroup.add(scan);
  // Ejector manifold with one nozzle per valve.
  const nozzleGeo = new THREE.CylinderGeometry(.0022, .0015, .016, 8); nozzleGeo.rotateX(Math.PI / 2);
  const nozzles = new THREE.InstancedMesh(nozzleGeo, materials.dark, L.n_nozzles);
  const dummy = new THREE.Object3D();
  for (let i = 0; i < L.n_nozzles; i++) {
    dummy.position.set(L.ej_x, -L.belt_w / 2 + L.belt_w / L.n_nozzles * (i + .5), L.belt_z + L.ej_z_offset);
    dummy.updateMatrix(); nozzles.setMatrixAt(i, dummy.matrix);
  }
  machineGroup.add(nozzles);
  add(new THREE.BoxGeometry(.03, L.belt_w + .04, .03), materials.steel, [L.ej_x, 0, L.belt_z + L.ej_z_offset + .022]);
  for (const y of [-1, 1]) add(new THREE.BoxGeometry(.03, .03, .12), materials.frame, [L.ej_x, y * (L.belt_w / 2 + .035), L.belt_z + .06]);
  // Splitter plate and the two collection volumes.
  const splitZ = L.belt_z - L.split_z_drop;
  const legacyCollectionMeshes = [
    add(new THREE.BoxGeometry(.15, L.belt_w + .05, .006), materials.steel, [L.split_x + .075, 0, splitZ]),
    add(new THREE.BoxGeometry(.37, L.belt_w + .05, .25), materials.reject, [L.split_x - .035, 0, splitZ - .13], {soft: true}),
    add(new THREE.BoxGeometry(.26, L.belt_w + .05, .28), materials.accept, [L.split_x + .28, 0, splitZ - .13], {soft: true}),
  ];
  // Dimension callouts (metres), drawn as thin lines with end ticks.
  const dimMat = new THREE.LineBasicMaterial({color: INK});
  const dimension = (a, b, text, tick) => {
    const A = new THREE.Vector3(...a), B = new THREE.Vector3(...b), T = new THREE.Vector3(...tick);
    const pts = [A, B, A.clone().sub(T), A.clone().add(T), B.clone().sub(T), B.clone().add(T)];
    const line = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(pts), dimMat); line.visible = three.labels; scene.add(line); dimLines.push(line);
    const el = document.createElement('div'); el.className = 'annotation dim'; el.textContent = text; stage.append(el);
    annotations.push({el, pos: A.clone().add(B).multiplyScalar(.5), essential: false});
  };
  const yd = -(L.belt_w / 2 + .16), t = [0, .02, 0], tz = [.02, 0, 0];
  dimension([beltX0, yd, L.belt_z], [beltX1, yd, L.belt_z], `BELT ${L.belt_len.toFixed(2)} m · ${L.belt_speed.toFixed(1)} m/s`, t);
  dimension([beltX0 - .16, -L.belt_w / 2, L.belt_z], [beltX0 - .16, L.belt_w / 2, L.belt_z], `${L.belt_w.toFixed(2)} m`, tz);
  dimension([beltX0 - .16, yd, 0], [beltX0 - .16, yd, L.belt_z], `H ${L.belt_z.toFixed(2)} m`, t);
  dimension([L.cam_x, yd + .06, L.belt_z], [L.ej_x, yd + .06, L.belt_z], `CAM→JETS ${(L.ej_x - L.cam_x).toFixed(2)} m`, t);
  dimension([L.split_x - .06, yd, L.belt_z], [L.split_x - .06, yd, splitZ], `DROP ${L.split_z_drop.toFixed(3)} m`, t);
  dimension([L.ej_x, L.belt_w / 2 + .09, L.belt_z + L.ej_z_offset], [L.ej_x, L.belt_w / 2 + .09, L.belt_z], `${L.n_nozzles} NOZZLES · PITCH ${(L.belt_w / L.n_nozzles * 1000).toFixed(1)} mm`, tz);
  // Object pools: one instanced draw call per shape, per-instance color from the engine.
  const beanGeo = new THREE.SphereGeometry(1, 12, 8);
  const pos = beanGeo.attributes.position;
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i), y = pos.getY(i), z = pos.getZ(i);
    const crease = z > 0 ? .2 * Math.exp(-y * y * 120) * (1 - x * x) : 0;
    pos.setXYZ(i, x, y, z - crease);
  }
  beanGeo.computeVertexNormals();
  const geometries = {
    ellipsoid: beanGeo,
    half: new THREE.SphereGeometry(1, 12, 6, 0, Math.PI * 2, 0, Math.PI / 2),
    box: new THREE.BoxGeometry(2, 2, 2),
    capsule: new THREE.CapsuleGeometry(1, 2, 4, 8).rotateX(Math.PI / 2).scale(1, 1, .5),
  };
  const capacity = {ellipsoid: (L.n_ellipsoid || 400) + 64, half: (L.n_half || 48) + 16, box: (L.n_box || 20) + 16, capsule: (L.n_capsule || 20) + 16};
  const pools = {};
  for (const [shape, geometry] of Object.entries(geometries)) {
    const mesh = new THREE.InstancedMesh(geometry, new THREE.MeshLambertMaterial({color: '#ffffff'}), capacity[shape]);
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage); mesh.frustumCulled = false; mesh.count = 0; mesh.userData.textured = false; scene.add(mesh);
    pools[shape] = mesh;
  }
  // Air pulses as short-lived translucent cones under the nozzle bank; a ring follows the injected object.
  const puffGeo = new THREE.ConeGeometry(1, 1, 10, 1, true); puffGeo.translate(0, -.5, 0); puffGeo.rotateX(Math.PI / 2);
  const puffMat = new THREE.MeshBasicMaterial({color: '#3aa0c8', transparent: true, opacity: .35, depthWrite: false, side: THREE.DoubleSide});
  const puffMesh = new THREE.InstancedMesh(puffGeo, puffMat, 48); puffMesh.frustumCulled = false; puffMesh.count = 0; scene.add(puffMesh);
  const ring = new THREE.Mesh(new THREE.TorusGeometry(.016, .0018, 8, 32), new THREE.MeshBasicMaterial({color: '#d8781c'}));
  ring.visible = false; scene.add(ring);
  const ring2 = ring.clone(); ring2.rotation.x = Math.PI / 2; scene.add(ring2);
  const selectedShadowMaterial = new THREE.MeshBasicMaterial({color: '#17231d', transparent: true, opacity: .14, depthWrite: false});
  const selectedShadow = new THREE.Mesh(new THREE.CircleGeometry(1, 24), selectedShadowMaterial);
  selectedShadow.visible = false; scene.add(selectedShadow);
  for (const [text, p] of [
    ['1 DROP ZONE', [(spawn.x_min + spawn.x_max) / 2, (spawn.y_min + spawn.y_max) / 2, L.belt_z + .42]], ['2 INSPECT', [L.cam_x, 0, L.belt_z + .52]],
    ['3 AIR JETS', [L.ej_x, .25, L.belt_z + .15]], ['KEEP', [L.split_x + .28, -.28, splitZ + .05]], ['REJECT', [L.split_x - .04, -.28, splitZ - .24]],
  ]) {
    const el = document.createElement('div'); el.className = 'annotation'; el.textContent = text; stage.append(el);
    annotations.push({el, pos: new THREE.Vector3(...p), essential: true});
  }
  $('title-rows').replaceChildren(...[
    ['CINTA', 'Class-agnostic INline Transport Analyzer'],
    ['BELT', `${L.belt_len.toFixed(2)} × ${L.belt_w.toFixed(2)} m at H ${L.belt_z.toFixed(2)} m, ${L.belt_speed.toFixed(1)} m/s`],
    ['FEED', `x ${L.feed_x[0].toFixed(2)}…${L.feed_x[1].toFixed(2)} m, ${state.requested_rate || 500} obj/s`],
    ['CAMERA', `x ${L.cam_x.toFixed(2)} m, strip ${(L.cam_fov * 1000).toFixed(0)} mm, ${L.cam_w}×${L.cam_h} px`],
    ['JETS', `x ${L.ej_x.toFixed(2)} m, ${L.n_nozzles} nozzles, ${(L.ej_z_offset * 1000).toFixed(0)} mm above belt`],
    ['SPLITTER', `x ${L.split_x.toFixed(2)} m, drop ${(L.split_z_drop * 1000).toFixed(0)} mm`],
    ['STEP', `${(L.timestep * 1000).toFixed(0)} ms physics · units m · rev ${(state.source_revision || '').slice(0, 7) || 'n/a'}`],
    ['ASSETS', 'primitives (loading Blender GLBs)'],
    ['RENDER', 'authoritative objects only'],
    ['INPUT', 'drag orbit · scroll zoom · click machine = drop stone · L labels · H panels'],
  ].map(([k, v]) => { const row = document.createElement('div'); const b = document.createElement('b'); b.textContent = k; row.append(b, document.createTextNode(v)); return row; }));
  Object.assign(three, {pools, puffMesh, ring, ring2, selectedShadow, selectedShadowMaterial, spawnCue, dummy, machineGroup, legacyCollectionMeshes});
  three.machineBuilt = true;
  syncCollectionSurfaces();
  loadBlenderAssets(L).catch(error => { console.error(error); setAssetsRow(`primitives (Blender GLBs failed: ${error.message})`); });
}

function setAssetsRow(text) {
  const row = [...$('title-rows').children].find(r => r.firstChild?.textContent === 'ASSETS');
  if (row) row.lastChild.textContent = text;
}

function setRenderRow(text) {
  const row = [...$('title-rows').children].find(r => r.firstChild?.textContent === 'RENDER');
  if (row && row.lastChild.textContent !== text) row.lastChild.textContent = text;
}

// Blender assets from visual_assets/browser: the machine (scene_machine.py via export_machine.py) and the
// baked bean LODs. GLB is Y up, so each root/geometry is rotated +90 degrees about X once. Bean geometry is
// normalised by the nominal semi-axes (regular) or AABB half-extents (broken) so the engine's per-object
// axes can be applied directly as the instance scale, as RECORDING.md prescribes.
const NOMINAL_REGULAR = [.0049, .00355, .00255], NOMINAL_BROKEN = [.005, .003591612, .001283763];
const GOOD_MEAN_RGB = [.50, .60, .47];
function disposeOwnedFallbackMachine(root) {
  const geometries = new Set();
  const materials = new Set();
  root.traverse(object => {
    if (object.geometry) geometries.add(object.geometry);
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
      if (material) materials.add(material);
    }
  });
  for (const geometry of geometries) geometry.dispose();
  for (const material of materials) material.dispose();
}

const OBSOLETE_COLLECTION_NAMES = new Set([
  'splitter', 'bin accept', 'bin reject', 'recorded surface 20', 'recorded surface 21',
  'accept tray bracket left', 'accept tray bracket right', 'accept tray underside',
  'reject tray bracket left', 'reject tray bracket right', 'reject tray underside',
  'splitter side trim left', 'splitter side trim right',
]);
const normalizedMeshName = name => String(name || '').toLowerCase().replace(/[ _]+/g, ' ').trim();

function clearCollectionOverlay() {
  if (three.collectionOverlay) {
    three.scene.remove(three.collectionOverlay);
    disposeOwnedFallbackMachine(three.collectionOverlay);
    three.collectionOverlay = null;
  }
  for (const [mesh, visible] of three.collectionHidden || []) mesh.visible = visible;
  three.collectionHidden = new Map();
}

function syncCollectionSurfaces() {
  if (!three.ready || !three.machineBuilt || !state) return;
  const surfaces = normalizedCollectionSurfaces(state.collection_surfaces);
  measurements.collection_surfaces = surfaces;
  const signature = surfaces ? JSON.stringify(surfaces) : null;
  if (signature === three.collectionSignature) return;
  clearCollectionOverlay();
  three.collectionSignature = signature;
  if (!surfaces) return;
  const hidden = new Map();
  for (const mesh of [...(three.legacyCollectionMeshes || []), ...(three.machineMeshes || [])]) {
    if ((three.legacyCollectionMeshes || []).includes(mesh)
        || OBSOLETE_COLLECTION_NAMES.has(normalizedMeshName(mesh.name))) {
      hidden.set(mesh, mesh.visible);
      mesh.visible = false;
    }
  }
  three.collectionHidden = hidden;
  const group = new THREE.Group();
  group.name = 'Authoritative collection surfaces';
  const materials = {
    splitter: matte('#c3cac6', 1, .55),
    accept: matte('#5d9766', .72, .12),
    reject: matte('#b45b45', .72, .12),
  };
  for (const surface of surfaces) {
    const material = surface.name === 'splitter' ? materials.splitter
      : surface.name.startsWith('bin_accept') ? materials.accept : materials.reject;
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(
      surface.halfSize[0] * 2, surface.halfSize[1] * 2, surface.halfSize[2] * 2,
    ), material);
    mesh.name = `Authoritative ${surface.name}`;
    mesh.position.fromArray(surface.center);
    mesh.quaternion.set(
      surface.quaternionWxyz[1], surface.quaternionWxyz[2],
      surface.quaternionWxyz[3], surface.quaternionWxyz[0],
    );
    mesh.userData.authoritativeCollectionSurface = true;
    group.add(mesh);
  }
  three.scene.add(group);
  three.collectionOverlay = group;
}

function disposeGeneratedPool(record) {
  if (!record?.pool) return;
  three.scene?.remove(record.pool);
  record.pool.geometry.dispose();
  for (const material of Array.isArray(record.pool.material) ? record.pool.material : [record.pool.material]) {
    if (!material) continue;
    for (const value of Object.values(material)) if (value?.isTexture) value.dispose();
    material.dispose();
  }
}

function clearGeneratedAssets() {
  generatedAssetGeneration++;
  if (generatedAssetContext) {
    for (const record of generatedAssetContext.loaded.values()) disposeGeneratedPool(record);
  }
  generatedAssetContext = null;
  measurements.generated_assets = generatedAssetMetrics();
  measurements.generated_asset_instances = null;
  measurements.builtin_or_proxy_instances = null;
  measurements.generated_proxy_instances = null;
  measurements.render_omitted = null;
  if (itemPreview && $('items-dialog').open) refreshItemPreviews();
}

function updateGeneratedAssetMetrics() {
  if (!generatedAssetContext) {
    measurements.generated_assets = generatedAssetMetrics();
    return;
  }
  measurements.generated_assets = generatedAssetMetrics({
    catalogRevision: generatedAssetContext.identity.catalog_revision,
    records: [...generatedAssetContext.records.values()],
  });
}

function generatedPoolCapacity() {
  const count = Object.entries(state?.layout || {})
    .filter(([key, value]) => key.startsWith('n_') && Number.isInteger(value))
    .reduce((sum, [, value]) => sum + value, 0);
  return Math.max(96, count, (state?.objects?.length || 0) + 64);
}

function disposeParsedScene(root, disposeTextures) {
  root?.traverse(object => {
    object.geometry?.dispose();
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
      if (!material) continue;
      if (disposeTextures) {
        for (const value of Object.values(material)) if (value?.isTexture) value.dispose();
      }
      material.dispose();
    }
  });
}

function parsedMeshEvidence(gltf, byteLength) {
  gltf.scene.updateMatrixWorld(true);
  const meshes = [];
  gltf.scene.traverse(object => { if (object.isMesh) meshes.push(object); });
  let primitiveCount = 0;
  let triangleCount = 0;
  for (const mesh of meshes) {
    const materials = Array.isArray(mesh.material) ? mesh.material.length : 1;
    primitiveCount += Math.max(1, materials);
    const count = mesh.geometry.index?.count || mesh.geometry.attributes.position?.count || 0;
    triangleCount += count / 3;
  }
  return {meshes, meshCount: meshes.length, primitiveCount, triangleCount, byteLength};
}

async function loadGeneratedAsset(asset, context, generation) {
  const record = context.records.get(asset.visualAssetId);
  const loadStarted = performance.now();
  let gltf = null;
  let ownedGeometry = null;
  let ownedMaterial = null;
  try {
    const response = await fetch(asset.url, {cache: 'force-cache'});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const bytes = await response.arrayBuffer();
    record.etag = response.headers.get('etag');
    record.byteLength = bytes.byteLength;
    record.loadMs = performance.now() - loadStarted;
    const parseStarted = performance.now();
    const basePath = asset.url.slice(0, asset.url.lastIndexOf('/') + 1);
    gltf = await new GLTFLoader().parseAsync(bytes, basePath);
    record.parseMs = performance.now() - parseStarted;
    const parsed = parsedMeshEvidence(gltf, bytes.byteLength);
    const refusal = parsedAssetRefusal(asset, parsed);
    const mesh = parsed.meshes[0];
    const positions = mesh?.geometry?.attributes?.position?.array;
    const prepared = refusal ? null : prepareGeometry(positions, mesh.matrixWorld.elements, asset.quaternionWxyz);
    if (refusal || !prepared) throw new Error(refusal || 'invalid_geometry');
    if (generation !== generatedAssetGeneration || context !== generatedAssetContext) {
      disposeParsedScene(gltf.scene, true);
      return;
    }
    const sourceMaterial = Array.isArray(mesh.material) ? mesh.material[0] : mesh.material;
    if (!sourceMaterial?.isMaterial) throw new Error('invalid_material');
    ownedGeometry = mesh.geometry.clone();
    ownedGeometry.applyMatrix4(mesh.matrixWorld);
    const correction = new THREE.Quaternion(asset.quaternionWxyz[1], asset.quaternionWxyz[2], asset.quaternionWxyz[3], asset.quaternionWxyz[0]);
    ownedGeometry.applyQuaternion(correction);
    ownedGeometry.translate(-prepared.center[0], -prepared.center[1], -prepared.center[2]);
    ownedGeometry.computeBoundingBox();
    ownedGeometry.computeBoundingSphere();
    ownedMaterial = sourceMaterial.clone();
    if ('envMapIntensity' in ownedMaterial) ownedMaterial.envMapIntensity = .72;
    const anisotropy = three.renderer.capabilities.getMaxAnisotropy();
    for (const mapName of ['map', 'normalMap', 'roughnessMap', 'metalnessMap']) {
      if (ownedMaterial[mapName]) ownedMaterial[mapName].anisotropy = anisotropy;
    }
    ownedMaterial.needsUpdate = true;
    const pool = new THREE.InstancedMesh(ownedGeometry, ownedMaterial, generatedPoolCapacity());
    pool.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    pool.frustumCulled = false;
    pool.count = 0;
    pool.userData.textured = true;
    pool.userData.visualAssetId = asset.visualAssetId;
    three.scene.add(pool);
    const loaded = {asset, pool, preparedSize: prepared.size};
    context.loaded.set(asset.visualAssetId, loaded);
    ownedGeometry = ownedMaterial = null;
    disposeParsedScene(gltf.scene, false);
    gltf = null;
    record.loaded = true;
    record.fallbackReason = null;
    updateGeneratedAssetMetrics();
    if ($('items-dialog').open) refreshItemPreviews();
  } catch (error) {
    ownedGeometry?.dispose();
    ownedMaterial?.dispose();
    if (gltf) disposeParsedScene(gltf.scene, true);
    if (generation !== generatedAssetGeneration || context !== generatedAssetContext) return;
    record.fallbackReason = error.message || 'parse_failed';
    context.fallbacks.set(asset.visualAssetId, record.fallbackReason);
    updateGeneratedAssetMetrics();
  }
}

function reconcileGeneratedAssets() {
  if (!three.ready || !three.machineBuilt || !state) return;
  const identity = {session_id: state.session_id, catalog_revision: state.catalog_revision};
  if (generatedAssetContext && !mustResetGeneratedPools(generatedAssetContext.identity, identity)) return;
  clearGeneratedAssets();
  const registry = acceptedAssetRegistry(state, state.catalog_revision);
  const plan = planAssetLoads(registry.accepted);
  const records = new Map();
  for (const [visualAssetId, reason] of registry.refusedAssets) {
    records.set(visualAssetId, {visualAssetId, loaded: false, fallbackReason: reason});
  }
  for (const [visualAssetId, reason] of plan.fallbacks) {
    records.set(visualAssetId, {visualAssetId, loaded: false, fallbackReason: reason});
  }
  for (const asset of plan.load) {
    records.set(asset.visualAssetId, {visualAssetId: asset.visualAssetId, loaded: false, fallbackReason: null});
  }
  const context = {
    identity,
    accepted: registry.accepted,
    refusedAssets: registry.refusedAssets,
    fallbacks: new Map(plan.fallbacks),
    loaded: new Map(),
    records,
  };
  generatedAssetContext = context;
  const generation = ++generatedAssetGeneration;
  updateGeneratedAssetMetrics();
  for (const asset of plan.load) loadGeneratedAsset(asset, context, generation);
}

async function loadBlenderAssets(L) {
  const loader = new GLTFLoader();
  const {scene} = three;
  const loaded = [];
  const machine = await loader.loadAsync('/assets/machine_lod.glb');
  machine.scene.rotation.x = Math.PI / 2;
  const meshes = []; machine.scene.traverse(o => { if (o.isMesh) meshes.push(o); });
  const anisotropy = three.renderer.capabilities.getMaxAnisotropy();
  for (const mesh of meshes) {
    for (const material of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
      if (!material) continue;
      if ('envMapIntensity' in material) material.envMapIntensity = .72;
      if ('roughness' in material) material.roughness = THREE.MathUtils.clamp(material.roughness, .28, .86);
      for (const mapName of ['map', 'normalMap', 'roughnessMap', 'metalnessMap']) {
        if (material[mapName]) material[mapName].anisotropy = anisotropy;
      }
      material.needsUpdate = true;
    }
  }
  scene.add(machine.scene);
  scene.remove(three.machineGroup);
  disposeOwnedFallbackMachine(three.machineGroup);
  three.legacyCollectionMeshes = [];
  three.machineMeshes = meshes;
  three.inspectionHousing = meshes.filter(mesh => /^(Camera_(?:shroud|gantry_bridge)|Inspection_light|Recorded_surface_(?:11|12|13))/.test(mesh.name));
  updateMachineVisibility();
  clickTargets.length = 0; clickTargets.push(...meshes.filter(m => m.visible));
  loaded.push(`machine ${meshes.length} parts`);
  // The recorded floor slab is a dark 8 x 6 m box; the drawing keeps the paper grid instead.
  const box = new THREE.Box3();
  for (const m of meshes) { box.setFromObject(m); if (box.max.x - box.min.x > 3) m.visible = false; }
  three.collectionSignature = undefined;
  syncCollectionSurfaces();
  setAssetsRow(`Blender ${loaded.join(', ')}; beans loading…`);
  const bean = async kind => {
    const gltf = await loader.loadAsync(`/assets/bean_${kind}_lod.glb`);
    gltf.scene.updateMatrixWorld(true);
    let part = null; gltf.scene.traverse(o => { if (o.isMesh && !part) part = o; });
    if (!part) throw new Error(`${kind}: no mesh primitive`);
    const nominal = kind === 'broken' ? NOMINAL_BROKEN : NOMINAL_REGULAR;
    const geometry = part.geometry.clone().applyMatrix4(part.matrixWorld).rotateX(Math.PI / 2).scale(1 / nominal[0], 1 / nominal[1], 1 / nominal[2]);
    if ('envMapIntensity' in part.material) part.material.envMapIntensity = .65;
    if (part.material.map) part.material.map.anisotropy = anisotropy;
    part.material.needsUpdate = true;
    return {geometry, material: part.material};
  };
  const [good, black, broken] = await Promise.all(['good', 'black', 'broken'].map(bean));
  const swap = (pool, asset) => { pool.geometry.dispose(); pool.material.dispose(); pool.geometry = asset.geometry; pool.material = asset.material; pool.userData.textured = true; };
  swap(three.pools.ellipsoid, good);
  swap(three.pools.half, broken);
  const blackPool = new THREE.InstancedMesh(black.geometry, black.material, 96);
  blackPool.instanceMatrix.setUsage(THREE.DynamicDrawUsage); blackPool.frustumCulled = false; blackPool.count = 0; blackPool.userData.textured = true;
  scene.add(blackPool); three.pools.black = blackPool;
  loaded.push('beans good/black/broken LODs (insect unused: true class not exposed)');
  setAssetsRow(`Blender ${loaded.join('; ')}`);
}

const _p = new THREE.Vector3(), _q = new THREE.Quaternion(), _c = new THREE.Color(), _fade = new THREE.Color(PAPER), _proj = new THREE.Vector3();

function render3d(now) {
  if (!three.ready) return;
  if (!three.machineBuilt) {
    if (!state?.layout) return;
    buildMachine(state.layout);
  }
  reconcileGeneratedAssets();
  const {pools, puffMesh, ring, ring2, selectedShadow, selectedShadowMaterial, dummy, camera, controls, renderer, scene} = three;
  const counts = {ellipsoid: 0, half: 0, box: 0, capsule: 0, black: 0};
  const generatedCounts = new Map();
  let ringShown = false;
  let shadowShown = false;
  let invalid = 0;
  let omitted = 0;
  let generatedVisible = 0;
  let proxyVisible = 0;
  for (const o of state?.objects || []) {
    if (!o.active && o.object_id !== selected) continue;
    if (!hasAuthoritativeRenderFields(o)) {
      invalid++;
      continue;
    }
    const rgb = o.rgb;
    const choice = chooseObjectAsset(o, generatedAssetContext || undefined);
    const generated = choice.source === 'generated' ? generatedAssetContext.loaded.get(choice.assetId) : null;
    let shape = o.shape;
    if (!generated && shape === 'ellipsoid' && pools.black && (rgb[0] + rgb[1] + rgb[2]) / 3 < .25) shape = 'black';
    const mesh = generated?.pool || pools[shape];
    const countKey = generated ? choice.assetId : shape;
    const count = generated ? (generatedCounts.get(countKey) || 0) : counts[countKey];
    if (!mesh || count >= mesh.instanceMatrix.count) {
      omitted++;
      continue;
    }
    displayedPose(o, _p, _q);
    dummy.position.copy(_p); dummy.quaternion.copy(_q);
    const ax = o.axes;
    if (generated) {
      const scale = instanceScale(o.shape, ax, choice.asset.referenceAxes);
      if (!scale) { invalid++; continue; }
      dummy.scale.fromArray(scale);
      generatedVisible++;
    } else {
      if (shape === 'capsule') dummy.scale.set(ax[1], ax[1], ax[0] + ax[1]);
      else dummy.scale.set(ax[0], ax[1], ax[2]);
      if (choice.source === 'proxy') proxyVisible++;
    }
    dummy.updateMatrix();
    const slot = count;
    if (generated) generatedCounts.set(countKey, count + 1);
    else counts[countKey]++;
    mesh.setMatrixAt(slot, dummy.matrix);
    if (mesh.userData.textured) {
      // Baked textures carry the colour; keep only the engine's per-object deviation from the mean good bean.
      if (generated || shape === 'black') _c.setRGB(1, 1, 1);
      else _c.setRGB(...rgb.slice(0, 3).map((v, i) => THREE.MathUtils.clamp(v / GOOD_MEAN_RGB[i], .55, 1.45)));
      if (o.outcome === 'accept') _c.multiplyScalar(.7);
    } else {
      _c.setRGB(rgb[0] <= 1 ? rgb[0] : rgb[0] / 255, rgb[1] <= 1 ? rgb[1] : rgb[1] / 255, rgb[2] <= 1 ? rgb[2] : rgb[2] / 255);
      if (o.outcome === 'accept') _c.lerp(_fade, .45);
    }
    if (o.outcome === 'reject') _c.copy(REJECT_COLOR);
    else if (o.outcome === 'spilled') _c.copy(SPILL_COLOR);
    mesh.setColorAt(slot, _c);
    if (o.object_id === selected) {
      ring.position.copy(_p); ring2.position.copy(_p); ring.lookAt(camera.position); ringShown = true;
      ring.material.color.copy(o.outcome === 'reject' ? REJECT_COLOR : o.outcome === 'spilled' ? SPILL_COLOR : SELECT_COLOR);
      const beltZ = state.layout?.belt_z;
      if (Number.isFinite(beltZ) && _p.z >= beltZ - .01) {
        const radius = Math.max(o.axes[0], o.axes[1]) * 1.8;
        selectedShadow.position.set(_p.x, _p.y, beltZ + .0035);
        selectedShadow.scale.set(radius, radius * .7, 1);
        selectedShadowMaterial.opacity = THREE.MathUtils.clamp(.18 - Math.max(0, _p.z - beltZ) * .3, .06, .18);
        shadowShown = true;
      }
    }
  }
  const visible = Object.values(counts).reduce((sum, count) => sum + count, 0)
    + [...generatedCounts.values()].reduce((sum, count) => sum + count, 0);
  setRenderRow(`${visible} visible · ${generatedVisible} generated · ${proxyVisible} proxy · ${invalid} invalid omitted · ${omitted} over cap`);
  measurements.generated_asset_instances = Object.fromEntries(generatedCounts);
  measurements.builtin_or_proxy_instances = visible - generatedVisible;
  measurements.generated_proxy_instances = proxyVisible;
  measurements.render_omitted = omitted;
  for (const [shape, mesh] of Object.entries(pools)) {
    mesh.count = counts[shape] || 0; mesh.instanceMatrix.needsUpdate = true; if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  }
  for (const [assetId, record] of generatedAssetContext?.loaded || []) {
    record.pool.count = generatedCounts.get(assetId) || 0;
    record.pool.instanceMatrix.needsUpdate = true;
    if (record.pool.instanceColor) record.pool.instanceColor.needsUpdate = true;
  }
  ring.visible = ring2.visible = ringShown;
  selectedShadow.visible = shadowShown;
  let used = 0;
  for (const puff of puffs) {
    const age = (now - puff.at) / 1000;
    if (age > .5 || used >= 48) continue;
    dummy.position.set(puff.x, puff.y, puff.z); dummy.quaternion.identity();
    const r = .004 + age * .05, len = .03 + age * .16;
    dummy.scale.set(r, r, len); dummy.updateMatrix(); puffMesh.setMatrixAt(used++, dummy.matrix);
  }
  puffMesh.count = used; puffMesh.instanceMatrix.needsUpdate = true;
  controls.update();
  for (const a of annotations) {
    _proj.copy(a.pos).project(camera);
    const show = (a.essential || three.labels) && _proj.z < 1 && Math.abs(_proj.x) < .95 && Math.abs(_proj.y) < .9;
    a.el.style.display = show ? 'block' : 'none';
    if (show) { a.el.style.left = `${(_proj.x * .5 + .5) * stage.clientWidth}px`; a.el.style.top = `${(-_proj.y * .5 + .5) * stage.clientHeight}px`; }
  }
  measurements.render3d_frames = (measurements.render3d_frames || 0) + 1;
  renderer.render(scene, camera);
}

function resizeInset() {
  requestAnimationFrame(() => {
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const ratio = Math.min(window.devicePixelRatio, 1.5);
    canvas.width = Math.round(rect.width * ratio);
    canvas.height = Math.round(rect.height * ratio);
  });
}

function drawInset() {
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const ratio = canvas.width / rect.width;
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  const width = rect.width, height = rect.height;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = '#f8f8f4'; ctx.fillRect(0, 0, width, height);
  const left = Math.max(42, width * .04), usable = width - left * 2;
  const expandedPanels = ['panel-left', 'panel-right']
    .map(id => $(id))
    .filter(panel => panel && !panel.classList.contains('collapsed'));
  const mobile = width <= 760;
  const topBlockBottom = expandedPanels
    .filter(panel => !mobile || panel.id === 'panel-left')
    .reduce((bottom, panel) => Math.max(bottom, panel.getBoundingClientRect().bottom), 0);
  const bottomBlockTop = expandedPanels
    .filter(panel => mobile && panel.id === 'panel-right')
    .reduce((top, panel) => Math.min(top, panel.getBoundingClientRect().top), height);
  let contentTop = Math.max(52, topBlockBottom + 18);
  let contentBottom = Math.min(height - 22, bottomBlockTop - 18);
  const minimumAvailableHeight = mobile ? 88 : 220;
  if (contentBottom - contentTop < minimumAvailableHeight) {
    contentTop = 52;
    contentBottom = height - 22;
  }
  const availableHeight = contentBottom - contentTop;
  const topCenter = contentTop + availableHeight * .20;
  const topHalfHeight = availableHeight * .12;
  const sideTop = contentTop + availableHeight * .48;
  const sideBottom = contentBottom - 4;
  const X = x => left + (x + 1.1) / 1.6 * usable;
  const Y = y => topCenter + y / .25 * topHalfHeight;
  const Z = z => sideBottom - (z - .30) / .65 * (sideBottom - sideTop);
  const topY = Y(-.25), topHeight = Y(.25) - topY;
  ctx.font = `${mobile ? 9 : 12}px ui-monospace, monospace`;
  ctx.fillStyle = '#586b7c';
  if (!mobile) ctx.fillText('TOP', 18, contentTop);
  ctx.fillText('SIDE', mobile ? 42 : 18, sideTop - 6);
  ctx.fillText(mobile ? 'DROP' : 'DROP ZONE', X(-1.05), contentTop);
  ctx.fillText(mobile ? 'INSP' : 'INSPECT', X(-.18), contentTop);
  ctx.fillText('AIR', X(.045), contentTop);
  ctx.fillText(mobile ? 'OUTCOME' : 'PHYSICAL OUTCOME', X(.28), contentTop);
  ctx.fillStyle = '#24548b';
  ctx.fillRect(X(-1.1), topY, X(0) - X(-1.1), topHeight);
  ctx.fillStyle = '#dfe8ed';
  ctx.fillRect(X(0), topY, X(.48) - X(0), topHeight);
  ctx.fillStyle = '#93d9e4';
  ctx.fillRect(X(-.144), topY, X(-.096) - X(-.144), topHeight);
  ctx.fillStyle = '#8a9ea9';
  ctx.fillRect(X(.1) - 2, topY, 4, topHeight);
  ctx.strokeStyle = '#34463d'; ctx.lineWidth = 1;
  ctx.strokeRect(X(-1.1), topY, X(.48) - X(-1.1), topHeight);
  ctx.fillStyle = '#b3c0c9';
  ctx.fillRect(X(-1.1), Z(.6), X(0) - X(-1.1), Math.max(5, height * .012));
  ctx.strokeStyle = '#91a2ad'; ctx.lineWidth = 3;
  ctx.beginPath(); ctx.moveTo(X(.34), Z(.475)); ctx.lineTo(X(.49), Z(.475)); ctx.stroke();
  ctx.font = '11px ui-monospace, monospace';
  ctx.fillStyle = '#466356'; ctx.fillText('KEEP', X(.36), mobile ? sideTop + 9 : Z(.56));
  ctx.fillStyle = '#825231'; ctx.fillText('REJECT', X(.36), mobile ? sideBottom - 3 : Z(.40));
  for (const o of state?.objects || []) {
    if ((!o.active && o.object_id !== selected) || !hasAuthoritativeRenderFields(o)) continue;
    displayedPose(o, _p, _q);
    const x = _p.x, y = _p.y, z = _p.z;
    if (x < -1.2 || x > .55) continue;
    const rgb = o.rgb;
    const color = o.outcome === 'reject' ? '#d26045' : o.outcome === 'spilled' ? '#d49a27'
      : `rgb(${rgb.slice(0,3).map(v => Math.round(v <= 1 ? v * 255 : v)).join(',')})`;
    const radius = Math.max(2, o.axes[0] / 1.6 * usable);
    const qw = _q.w, qx = _q.x, qy = _q.y, qz = _q.z;
    const yaw = Math.atan2(2 * (qx * qy + qw * qz), 1 - 2 * (qy * qy + qz * qz));
    const pitch = Math.atan2(-2 * (qx * qz - qw * qy), 1 - 2 * (qy * qy + qz * qz));
    ctx.globalAlpha = o.outcome ? .55 : 1;
    ctx.fillStyle = color;
    for (const [vertical, angle] of [[Y(y), yaw], [Z(z), pitch]]) {
      ctx.save(); ctx.translate(X(x), vertical); ctx.rotate(angle);
      if (o.shape === 'box') ctx.fillRect(-radius, -radius * .68, radius * 2, radius * 1.36);
      else { ctx.beginPath(); ctx.ellipse(0, 0, radius, Math.max(1.4, radius * .65), 0, 0, Math.PI * 2); ctx.fill(); }
      ctx.restore();
    }
    if (o.object_id === selected) {
      ctx.globalAlpha = 1; ctx.strokeStyle = '#e59a32'; ctx.lineWidth = 2;
      for (const vertical of [Y(y), Z(z)]) { ctx.beginPath(); ctx.arc(X(x), vertical, 10, 0, Math.PI * 2); ctx.stroke(); }
    }
  }
  ctx.globalAlpha = 1;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
}

function draw(now) {
  if (lastFrameAt !== null) frameIntervals.push(now - lastFrameAt);
  lastFrameAt = now;
  frames++;
  if (now - fpsStart >= 1000) {
    measurements.fps = frames * 1000 / (now - fpsStart);
    const sorted = frameIntervals.slice().sort((a, b) => a - b);
    const percentile = value => sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * value))] ?? null;
    measurements.frame_ms_p50 = percentile(.5);
    measurements.frame_ms_p95 = percentile(.95);
    measurements.frame_ms_max = sorted.at(-1) ?? null;
    fpsStart = now; frames = 0;
    frameIntervals = [];
  }
  advanceDisplayTimeline(now);
  if (currentView === '2d') drawInset();
  if (currentView === '3d' && !$('items-dialog').open) render3d(now);
  requestAnimationFrame(draw);
}

// Panels collapse toward their screen edge; only the dark tab stays. Collapsed state is a per-viewer convenience.
function setCollapsed(id, collapsed, persist = true) {
  const panel = $(id); if (!panel) return;
  panel.classList.toggle('collapsed', collapsed);
  panel.querySelector('.edge')?.setAttribute('aria-expanded', String(!collapsed));
  if (persist) { try { localStorage.setItem(`coffee.panel.${id}`, collapsed ? '1' : '0'); } catch {} }
}
function setLabels(on, persist = true) {
  three.labels = on;
  for (const line of dimLines) line.visible = on;
  $('labels').setAttribute('aria-pressed', String(on));
  if (persist) { try { localStorage.setItem('coffee.labels', on ? '1' : '0'); } catch {} }
}
$('labels').onclick = () => setLabels(!three.labels);
try {
  const storedLabels = localStorage.getItem('coffee.labels');
  setLabels(storedLabels === null ? false : storedLabels !== '0', false);
} catch { setLabels(false, false); }
const PANELS = ['panel-left', 'panel-right', 'title-block'];
document.addEventListener('keydown', event => {
  if (event.metaKey || event.ctrlKey || event.altKey || $('diagnostics').open) return;
  if (/^(input|textarea|select|button)$/i.test(event.target.tagName)) return;
  if (event.key === 'l' || event.key === 'L') setLabels(!three.labels);
  if (event.key === 'h' || event.key === 'H') {
    const anyOpen = PANELS.some(id => !$(id).classList.contains('collapsed'));
    for (const id of PANELS) setCollapsed(id, anyOpen);
  }
});
for (const button of document.querySelectorAll('[data-toggle]')) {
  const id = button.dataset.toggle;
  button.onclick = () => setCollapsed(id, !$(id).classList.contains('collapsed'));
  let stored = null; try { stored = localStorage.getItem(`coffee.panel.${id}`); } catch {}
  const defaultCollapsed = window.innerWidth < 760 || id === 'title-block' || id === 'inset';
  setCollapsed(id, stored === null ? defaultCollapsed : stored === '1', false);
}
new ResizeObserver(resizeInset).observe($('inset'));
document.querySelectorAll('button[data-camera]').forEach(button => button.onclick = () => setCamera(button.dataset.camera));
document.querySelectorAll('button[data-view]').forEach(button => button.onclick = () => setView(button.dataset.view));
setView('3d');
initHelpTooltips();
initThree();
connect();
requestAnimationFrame(draw);
