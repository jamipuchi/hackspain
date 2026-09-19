const $ = id => document.getElementById(id);
const canvas = $('scene');
const ctx = canvas.getContext('2d');
let state = null;
let socket;
let selected = null;
const requests = new Map();
let latestCommandId = null;
let shownSession = null;
let frames = 0;
let fpsStart = performance.now();
let previousPacket = null;
let restartPending = false;
let reconnectTimer = null;
let heartbeatSeq = null;
let heartbeatSeenAt = null;
let staleConnection = false;
const selectedEvents = new Map();
const measurements = {fps: null, acknowledgment_ms: null, outcome_wall_s: null, pose_hz: null};
window.coffeeMeasurements = measurements;
const HEARTBEAT_IDLE_MS = 4500;
const MAX_CLIENT_REQUESTS = 64;
const MAX_CLIENT_PENDING_REQUESTS = 16;

const continuousMode = () => state?.mode === 'continuous';
const latestRequest = () => latestCommandId ? requests.get(latestCommandId) : null;
const pendingRequests = () => [...requests.values()].filter(request => !request.acknowledged && !request.invalidated);
const pendingRequest = () => pendingRequests().length > 0;

function scheduleReconnect() {
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(connect, 1200);
}

function retryPending() {
  if (!pendingRequest() || !state || socket?.readyState !== WebSocket.OPEN) return;
  for (const request of pendingRequests()) {
    if (state.session_id !== request.payload.session_id) {
      request.invalidated = true;
      request.error = 'Engine session changed. The original request was not retried.';
      continue;
    }
    socket.send(JSON.stringify(request.payload));
    request.lastSend = performance.now();
    request.retryPending = false;
  }
  updateLatestEvidence();
}

function connect() {
  heartbeatSeq = heartbeatSeenAt = null;
  const ws = new WebSocket(`ws://${location.host}/ws`);
  socket = ws;
  ws.onopen = () => {
    if (socket !== ws) return;
    $('error').textContent = '';
    staleConnection = false;
  };
  ws.onclose = () => {
    if (socket !== ws) return;
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
      }
      if (packet.session_id) shownSession = packet.session_id;
      const nextHeartbeat = Number(packet.heartbeat_seq);
      if (Number.isFinite(nextHeartbeat) && nextHeartbeat !== heartbeatSeq) {
        heartbeatSeq = nextHeartbeat;
        heartbeatSeenAt = performance.now();
      } else if (!packet.mode || packet.mode !== 'continuous') {
        heartbeatSeenAt = performance.now();
      }
      if (previousPacket !== null) measurements.pose_hz = 1000 / (performance.now() - previousPacket);
      previousPacket = performance.now();
      state = packet;
      window.coffeeState = state;
      update();
      // Retry each retained command after a new connection or an acknowledgment timeout.
      if (pendingRequest() && ['ready', 'running'].includes(state.status)) {
        if (pendingRequests().some(request => request.retryPending || performance.now() - request.lastSend > 2000)) retryPending();
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
        request.spawnWall = performance.now();
        request.acknowledgment_ms = request.spawnWall - request.sent;
      }
      update();
    } else if (packet.type === 'pending' && requests.has(packet.command_id)) {
      requests.get(packet.command_id).serverPending = true;
      updateLatestEvidence();
    }
  };
}

setInterval(() => {
  if (!continuousMode() || !state || socket?.readyState !== WebSocket.OPEN || heartbeatSeenAt === null) return;
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
  if (!state?.session_id || !state.restart_supported || restartPending || socket.readyState !== WebSocket.OPEN) return;
  restartPending = true;
  $('error').textContent = '';
  update();
  try {
    const response = await fetch('/restart', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: state.session_id}),
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

$('inject').onclick = () => {
  if (!state || restartPending || socket?.readyState !== WebSocket.OPEN) return;
  for (const [commandId, request] of requests) {
    if (requests.size < MAX_CLIENT_REQUESTS) break;
    if (request.acknowledged || request.invalidated) requests.delete(commandId);
  }
  if (requests.size >= MAX_CLIENT_REQUESTS) {
    $('error').textContent = `Client request storage is full (${MAX_CLIENT_REQUESTS} active records). Wait for an injection result.`;
    update();
    return;
  }
  const payload = {type: 'inject', command_id: crypto.randomUUID(), session_id: state.session_id, class_name: 'stone'};
  if (requests.has(payload.command_id)) {
    $('error').textContent = 'The browser could not create a distinct command ID. Try again.';
    return;
  }
  if (continuousMode()) {
    if (!state.command_epoch) return;
    payload.command_epoch = state.command_epoch;
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
  update();
};

function updateLatestEvidence() {
  const request = latestRequest();
  const object = request?.object_id == null ? null
    : (state?.objects || []).find(item => item.object_id === request.object_id)
      || (state?.injected_objects || []).find(item => item.object_id === request.object_id);
  selected = request?.object_id ?? null;
  const decision = object?.decision;
  const values = {
    prediction: decision ? `${decision.predicted_class}${decision.association_approximate ? ' (approximate object match)' : ''}` : 'Not observed',
    decision: decision ? `${decision.reject ? 'Reject' : 'Keep'}${decision.scheduled ? ', valves scheduled' : ''}${decision.late ? ', late' : ''}` : 'Not decided',
    hit: object?.jet_hits ? `${object.own_pulse_hit ? 'Own pulse' : 'Other or unassociated pulse'}, ${object.jet_hits} nozzle contact steps` : 'No contact recorded',
    outcome: object?.outcome ? ({accept:'Accept path', reject:'Reject path', spilled:'Spilled'})[object.outcome] : 'Unresolved',
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
  const stateLabel = request?.error || request?.invalidated ? 'Command failed'
    : object?.outcome === 'reject' ? 'Rejected'
    : object?.outcome === 'spilled' ? 'Spilled'
      : object?.outcome === 'accept' ? 'Passed' : 'In progress';
  $('card-state').textContent = stateLabel;
  $('object-title').textContent = request?.object_id == null ? (request ? 'Following your stone' : 'Follow your stone') : `Your stone, object ${request.object_id}`;
  $('command').textContent = request ? request.payload.command_id : 'No injection yet';
  $('card-command').textContent = request
    ? `Command ${request.payload.command_id}${request.payload.command_epoch ? ` · Epoch ${request.payload.command_epoch}` : ''}`
    : 'No injection yet';
  let acknowledgment = 'Waiting';
  if (request?.invalidated) acknowledgment = 'Engine session changed. The request was not retried.';
  else if (request?.error) acknowledgment = 'Injection failed';
  else if (request?.acknowledged) acknowledgment = `${request.acknowledgment_ms.toFixed(0)} ms on this connection`;
  else if (request?.serverPending) acknowledgment = 'Server still has the original injection request';
  else if (request?.retryPending) acknowledgment = 'Retrying the original injection request';
  else if (request) acknowledgment = 'Waiting for physical spawn';
  $('ack').textContent = acknowledgment;
  $('card-error').hidden = !request?.error;
  $('card-error').textContent = request?.error ? `Injection failed: ${request.error}` : '';
  measurements.acknowledgment_ms = request?.acknowledgment_ms ?? null;
  measurements.outcome_wall_s = request?.outcome_wall_s ?? null;
}

function formatScore(score) {
  return Number.isFinite(score?.value) ? `${(score.value * 100).toFixed(1)}%` : 'Unavailable';
}

function formatCount(score, emptyLabel) {
  return Number.isFinite(score?.denominator) && score.denominator > 0
    ? `${score.numerator ?? 0} / ${score.denominator}`
    : `0 / 0 · ${emptyLabel}`;
}

function updateScoreboard() {
  const scores = state.rolling_scores;
  $('scoreboard').hidden = !continuousMode();
  if (!continuousMode()) return;
  if (!scores) {
    $('score-status').textContent = 'Waiting for server score aggregates';
    for (const [valueId, countId, emptyLabel] of [
      ['score-accuracy', 'score-accuracy-count', 'No eligible objects'],
      ['score-capture', 'score-capture-count', 'No required defects'],
      ['score-loss', 'score-loss-count', 'No keep objects'],
      ['score-unresolved', 'score-unresolved-count', 'No eligible objects'],
    ]) {
      $(valueId).textContent = 'Unavailable';
      $(countId).textContent = `0 / 0 · ${emptyLabel}`;
    }
    $('score-context').textContent = 'Scores use a rolling simulated-time window.';
    $('score-versions').textContent = 'Version context is unavailable.';
    return;
  }
  const metricIds = [
    ['sorting_accuracy', 'score-accuracy', 'score-accuracy-count', 'No eligible objects'],
    ['defect_capture', 'score-capture', 'score-capture-count', 'No required defects'],
    ['good_loss', 'score-loss', 'score-loss-count', 'No keep objects'],
    ['unresolved', 'score-unresolved', 'score-unresolved-count', 'No eligible objects'],
  ];
  for (const [name, valueId, countId, emptyLabel] of metricIds) {
    $(valueId).textContent = formatScore(scores[name]);
    $(countId).textContent = formatCount(scores[name], emptyLabel);
  }
  const asOf = Number(scores.as_of_sim_time_s || 0);
  const available = Number(scores.available_seconds || 0);
  const window = Number(scores.window_seconds || 0);
  const settling = Number(scores.settling_seconds || 0);
  const start = Number(scores.window_start_exclusive_s || 0);
  const end = Number(scores.window_end_inclusive_s || 0);
  $('score-status').textContent = `As of ${asOf.toFixed(1)} simulated seconds${scores.warming_up ? ' · warming up' : ' · full window'}`;
  $('score-context').textContent = `Window (${start.toFixed(1)}, ${end.toFixed(1)}] simulated seconds. Available ${available.toFixed(1)} / ${window.toFixed(1)} simulated seconds. ${scores.settling_objects ?? 0} settling object${scores.settling_objects === 1 ? '' : 's'}. Manual injections ${scores.manual_injections_excluded ? 'excluded' : 'not excluded'}. Settling delay ${settling.toFixed(1)} simulated seconds.`;
  const versions = scores.versions || {};
  $('score-versions').textContent = `Score epoch ${scores.score_epoch_id?.slice(0, 8) || 'unavailable'} · Source ${versions.source_revision?.slice(0, 7) || 'unavailable'} · Model ${versions.model?.slice(0, 12) || 'unavailable'} · Policy ${versions.policy?.slice(0, 12) || 'unavailable'}`;
}

function update() {
  if (!state) return;
  const status = state.status;
  const connected = socket?.readyState === WebSocket.OPEN;
  const heartbeatAge = heartbeatSeenAt === null ? null : performance.now() - heartbeatSeenAt;
  const heartbeatText = continuousMode() ? (heartbeatAge === null ? 'waiting for heartbeat' : `heartbeat ${(heartbeatAge / 1000).toFixed(1)} s ago`) : '';
  const statusLabel = ({starting:'Preparing', restarting:'Restarting', ready:'Ready', running:'Live', completed:'Session complete', failed:'Engine failed'})[status] || status;
  $('status').textContent = !connected ? 'Disconnected' : `${statusLabel}${heartbeatText ? ` · ${heartbeatText}` : ''}`;
  $('status').dataset.state = status;
  const durationComplete = !continuousMode() && state.sim_time_s >= (state.limits?.sim_seconds || 10) - 0.6;
  const awaitingHeartbeat = continuousMode() && heartbeatSeenAt === null;
  $('inject').disabled = !connected || restartPending || awaitingHeartbeat || !['ready', 'running'].includes(status) || durationComplete || (continuousMode() && !state.command_epoch);
  $('restart').hidden = !state.restart_supported;
  $('restart').disabled = !connected || !state.restart_supported || restartPending || !state.session_id || !['ready', 'running', 'completed', 'failed'].includes(status);
  $('restart').textContent = restartPending || status === 'restarting' ? 'Restarting…' : 'Restart session';
  const boundedNotice = {starting:'Preparing the engine', restarting:'Stopping the old session and preparing a new one', ready:'Ready for a physical injection', running:'The simulation clock shows the actual engine rate', completed:'Session complete. Select Restart session to inject again.', failed:'The engine stopped. Select Restart session to try again.'};
  const continuousNotice = {starting:'Preparing the continuous engine', restarting:'Restarting the engine session', ready:'Continuous engine ready', running:'Continuous engine runs without this page', completed:'Continuous engine completed unexpectedly', failed:'The continuous engine stopped'};
  $('notice').textContent = (continuousMode() ? continuousNotice : boundedNotice)[status] || 'Waiting for the engine';
  $('mode-note').textContent = continuousMode()
    ? 'The conveyor runs continuously. Injection adds one manual object to the shared stream and does not change feed scores.'
    : 'The first injection starts the conveyor. Restart resets the shared session for all browsers.';
  if (state.error) $('error').textContent = state.error;
  $('sim-time').textContent = `${(state.sim_time_s || 0).toFixed(2)} s`;
  $('engine-rate').textContent = state.engine_rate ? `${state.engine_rate.toFixed(2)}×` : 'Waiting';
  $('admitted').textContent = `${(state.admitted_rate || 0).toFixed(0)} /s`;
  const objects = state.objects || [];
  $('active').textContent = objects.filter(o => o.active).length;
  updateScoreboard();
  updateLatestEvidence();
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

function draw() {
  frames++;
  const now = performance.now();
  if (now - fpsStart >= 1000) {
    measurements.fps = frames * 1000 / (now - fpsStart);
    $('fps').textContent = measurements.fps.toFixed(0);
    fpsStart = now; frames = 0;
  }
  ctx.clearRect(0,0,1250,430);
  const X = x => 100 + (x + 1.1) / 1.6 * 1060;
  const Y = y => 138 + y * 270;
  const Z = z => 360 - (z - .35) * 260;
  ctx.font = '13px Avenir Next, sans-serif';
  ctx.fillStyle = '#586b7c';
  ctx.fillText('Top view', 22, 30);
  ctx.fillText('Side view', 22, 270);
  ctx.fillText('Feed', X(-1.05), 30);
  ctx.fillText('Inspection', X(-.18), 30);
  ctx.fillText('Air jets', X(.045), 30);
  ctx.fillText('Physical outcome', X(.28), 30);
  ctx.fillStyle = '#24548b';
  ctx.fillRect(X(-1.1),Y(-.25),X(0)-X(-1.1),135);
  ctx.fillStyle = '#dfe8ed';
  ctx.fillRect(X(0),Y(-.25),X(.48)-X(0),135);
  ctx.fillStyle = '#93d9e4';
  ctx.fillRect(X(-.144),Y(-.25),X(-.096)-X(-.144),135);
  ctx.fillStyle = '#8a9ea9';
  ctx.fillRect(X(.1)-2,Y(-.26),4,140);
  ctx.fillStyle = '#b3c0c9';
  ctx.fillRect(X(-1.1),Z(.6),X(0)-X(-1.1),8);
  ctx.strokeStyle = '#91a2ad';
  ctx.lineWidth = 3;
  ctx.beginPath();ctx.moveTo(X(.34),Z(.475));ctx.lineTo(X(.49),Z(.475));ctx.stroke();
  ctx.font = '12px Avenir Next, sans-serif';
  ctx.fillStyle = '#466356';ctx.fillText('Accept',X(.36),Z(.56));
  ctx.fillStyle = '#825231';ctx.fillText('Reject',X(.36),Z(.40));
  for (const o of state?.objects || []) {
    if (!o.pos || (!o.active && o.object_id !== selected)) continue;
    const [x,y,z] = o.pos;
    if (x < -1.2 || x > .55) continue;
    const rgb = o.rgb || [.5,.55,.42];
    const color = `rgb(${rgb.slice(0,3).map(v => Math.round(v <= 1 ? v * 255 : v)).join(',')})`;
    const radius = Math.max(2, (o.axes?.[0] || .004) * 500);
    const [qw,qx,qy,qz] = o.quat || [1,0,0,0];
    const yaw = Math.atan2(2*(qx*qy+qw*qz),1-2*(qy*qy+qz*qz));
    const pitch = Math.atan2(-2*(qx*qz-qw*qy),1-2*(qy*qy+qz*qz));
    ctx.globalAlpha = o.outcome ? .55 : 1;
    ctx.fillStyle = color;
    for (const [oy,angle] of [[Y(y),yaw],[Z(z),pitch]]) {
      ctx.save();ctx.translate(X(x),oy);ctx.rotate(angle);
      if (o.shape === 'box') ctx.fillRect(-radius,-radius*.68,radius*2,radius*1.36);
      else {ctx.beginPath();ctx.ellipse(0,0,radius,Math.max(1.4,radius*.65),0,0,Math.PI*2);ctx.fill();}
      ctx.restore();
    }
    if (o.object_id === selected) {
      ctx.globalAlpha = 1;ctx.strokeStyle = '#e59a32';ctx.lineWidth = 2;
      for (const oy of [Y(y),Z(z)]) {ctx.beginPath();ctx.arc(X(x),oy,10,0,Math.PI*2);ctx.stroke();}
    }
  }
  ctx.globalAlpha = 1;
  requestAnimationFrame(draw);
}
connect();
requestAnimationFrame(draw);
