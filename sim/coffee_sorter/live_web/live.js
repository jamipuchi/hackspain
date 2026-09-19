const $ = id => document.getElementById(id);
const canvas = $('scene');
const ctx = canvas.getContext('2d');
let state = null;
let socket;
let selected = null;
let request = null;
let spawnWall = null;
let outcomeWall = null;
let shownSession = null;
let frames = 0;
let fpsStart = performance.now();
let previousPacket = null;
let restartPending = false;
const selectedEvents = new Map();
const measurements = {fps: null, acknowledgment_ms: null, outcome_wall_s: null, pose_hz: null};
window.coffeeMeasurements = measurements;

function connect() {
  socket = new WebSocket(`ws://${location.host}/ws`);
  socket.onopen = () => { $('error').textContent = ''; };
  socket.onclose = () => {
    $('status').textContent = 'Disconnected';
    $('inject').disabled = true;
    $('restart').disabled = true;
    $('notice').textContent = 'Connection lost. Reconnecting to the engine.';
    setTimeout(connect, 1200);
  };
  socket.onmessage = event => {
    const packet = JSON.parse(event.data);
    if (packet.type === 'state') {
      if (shownSession && packet.session_id && shownSession !== packet.session_id) {
        clearSelection();
      }
      if (packet.session_id) shownSession = packet.session_id;
      if (previousPacket !== null) measurements.pose_hz = 1000 / (performance.now() - previousPacket);
      previousPacket = performance.now();
      state = packet;
      window.coffeeState = state;
      update();
      // A lost acknowledgment can be requested again without another physical injection.
      if (request && !request.acknowledged && ['ready', 'running'].includes(state.status) && state.session_id === request.payload.session_id) {
        if (performance.now() - request.lastSend > 2000) {
          socket.send(JSON.stringify(request.payload));
          request.lastSend = performance.now();
        }
      }
    } else if (packet.type === 'ack' && request && packet.command_id === request.payload.command_id) {
      if (request.acknowledged) return;
      request.acknowledged = true;
      if (!packet.ok) {
        $('error').textContent = packet.error;
        $('ack').textContent = 'Injection failed';
      } else {
        selected = packet.object_id;
        spawnWall = performance.now();
        measurements.acknowledgment_ms = spawnWall - request.sent;
        $('ack').textContent = `${measurements.acknowledgment_ms.toFixed(0)} ms on this connection`;
        $('object-title').textContent = `Your stone, object ${selected}`;
        $('command').textContent = request.payload.command_id;
      }
      update();
    }
  };
}

function clearSelection() {
  selected = request = spawnWall = outcomeWall = null;
  selectedEvents.clear();
  previousPacket = null;
  measurements.acknowledgment_ms = measurements.outcome_wall_s = measurements.pose_hz = null;
  $('object-title').textContent = 'Follow your stone';
  $('command').textContent = 'No injection yet';
  $('ack').textContent = 'Waiting';
  $('error').textContent = '';
  for (const [id, text] of Object.entries({prediction:'Not observed', decision:'Not decided', hit:'Not recorded', outcome:'Unresolved', 'outcome-time':'Waiting'})) $(id).textContent = text;
}

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
  if (!state || restartPending || socket.readyState !== WebSocket.OPEN) return;
  const payload = {type: 'inject', command_id: crypto.randomUUID(), session_id: state.session_id, class_name: 'stone'};
  request = {payload, sent: performance.now(), lastSend: performance.now(), acknowledged: false};
  selected = null;
  selectedEvents.clear();
  spawnWall = outcomeWall = null;
  measurements.acknowledgment_ms = measurements.outcome_wall_s = null;
  $('error').textContent = '';
  $('ack').textContent = 'Waiting for physical spawn';
  $('object-title').textContent = 'Following your stone';
  $('command').textContent = payload.command_id;
  for (const [id, text] of Object.entries({prediction:'Not observed', decision:'Not decided', hit:'Not recorded', outcome:'Unresolved', 'outcome-time':'Waiting'})) $(id).textContent = text;
  socket.send(JSON.stringify(payload));
  $('inject').disabled = true;
};

function update() {
  if (!state) return;
  const status = state.status;
  const connected = socket?.readyState === WebSocket.OPEN;
  $('status').textContent = !connected ? 'Disconnected' : ({starting:'Preparing', restarting:'Restarting', ready:'Ready', running:'Live', completed:'Session complete', failed:'Engine failed'})[status] || status;
  $('status').dataset.state = status;
  $('inject').disabled = !connected || restartPending || !['ready', 'running'].includes(status) || (request && !request.acknowledged) || state.sim_time_s >= (state.limits?.sim_seconds || 10) - 0.6;
  $('restart').disabled = !connected || !state.restart_supported || restartPending || !state.session_id || !['ready', 'running', 'completed', 'failed'].includes(status);
  $('restart').textContent = restartPending || status === 'restarting' ? 'Restarting…' : 'Restart session';
  $('notice').textContent = ({starting:'Preparing the engine', restarting:'Stopping the old session and preparing a new one', ready:'Ready for a physical injection', running:'The simulation clock shows the actual engine rate', completed:'Session complete. Select Restart session to inject again.', failed:'The engine stopped. Select Restart session to try again.'})[status] || 'Waiting for the engine';
  if (state.error) $('error').textContent = state.error;
  $('sim-time').textContent = `${(state.sim_time_s || 0).toFixed(2)} s`;
  $('engine-rate').textContent = state.engine_rate ? `${state.engine_rate.toFixed(2)}×` : 'Waiting';
  $('admitted').textContent = `${(state.admitted_rate || 0).toFixed(0)} /s`;
  const objects = state.objects || [];
  $('active').textContent = objects.filter(o => o.active).length;
  const object = objects.find(o => o.object_id === selected) || (state.injected_objects || []).find(o => o.object_id === selected);
  if (object) {
    const decision = object.decision;
    $('prediction').textContent = decision ? `${decision.predicted_class}${decision.association_approximate ? ' (approximate object match)' : ''}` : 'Not observed';
    $('decision').textContent = decision ? `${decision.reject ? 'Reject' : 'Keep'}${decision.scheduled ? ', valves scheduled' : ''}${decision.late ? ', late' : ''}` : 'Not decided';
    $('hit').textContent = object.jet_hits ? `${object.own_pulse_hit ? 'Own pulse' : 'Other or unassociated pulse'}, ${object.jet_hits} nozzle contact steps` : 'No contact recorded';
    $('outcome').textContent = object.outcome ? ({accept:'Accept path', reject:'Reject path', spilled:'Spilled'})[object.outcome] : 'Unresolved';
    if (object.outcome && outcomeWall === null && spawnWall !== null) {
      outcomeWall = performance.now();
      measurements.outcome_wall_s = (outcomeWall - spawnWall) / 1000;
    }
    if (object.spawn_to_outcome_wall_s != null) $('outcome-time').textContent = `${object.spawn_to_outcome_wall_s.toFixed(2)} wall seconds from physical spawn`;
  }
  $('versions').textContent = state.model_version ? `Engine source ${state.source_revision?.slice(0,7) || 'unavailable'} · Session ${state.session_id.slice(0,8)} · Model ${state.model_version.slice(0,12)} · Policy ${state.policy_version.slice(0,12)} · Preset ${state.preset_version.slice(0,12)}` : 'Source, model, and policy versions appear when the engine is ready.';
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
  $('limit-note').textContent = `Requested feed ${state.requested_rate || 500}/s. Session limit ${state.limits?.sim_seconds || 10} simulated seconds or ${state.limits?.wall_seconds || 300} wall seconds. Restart session resets the shared session for all browsers.`;
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
