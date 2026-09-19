import * as THREE from 'three';
import {OrbitControls} from '/three/OrbitControls.js';
import {RoomEnvironment} from '/three/RoomEnvironment.js';
import {GLTFLoader} from '/three/loaders/GLTFLoader.js';

const $ = id => document.getElementById(id);
const stage = $('stage');
const renderer = new THREE.WebGLRenderer({antialias: true, powerPreference: 'high-performance'});
const gl = renderer.getContext();
const debug = gl.getExtension('WEBGL_debug_renderer_info');
const gpu = debug ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) : 'Unavailable';
const softwareRenderer = /swiftshader|llvmpipe|software/i.test(gpu);
renderer.setPixelRatio(softwareRenderer ? 0.6 : Math.min(devicePixelRatio, 1.5));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.shadowMap.autoUpdate = false;
stage.prepend(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color('#101512');
scene.fog = new THREE.Fog('#101512', 2.2, 6);
const camera = new THREE.PerspectiveCamera(38, 1, .002, 20);
camera.up.set(0, 0, 1);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = .08;
controls.minDistance = .25;
controls.maxDistance = 5;
controls.maxPolarAngle = Math.PI * .52;

const room = new RoomEnvironment();
const pmrem = new THREE.PMREMGenerator(renderer);
const environment = pmrem.fromScene(room, .04);
scene.environment = environment.texture;
scene.environmentIntensity = .75;
room.dispose();
pmrem.dispose();

const key = new THREE.DirectionalLight('#fff2d8', 3.1);
key.position.set(-1.5, -2.2, 3.4);
key.castShadow = true;
key.shadow.mapSize.set(1024, 1024);
Object.assign(key.shadow.camera, {left: -1.8, right: 1.8, top: 1.8, bottom: -1.8, near: .1, far: 7});
key.shadow.normalBias = .002;
scene.add(key, new THREE.HemisphereLight('#dceee0', '#29342f', 1.5));
const rim = new THREE.DirectionalLight('#7dd5ef', 1.4);
rim.position.set(1.2, 1.8, 2.2);
scene.add(rim);

const machineGroup = new THREE.Group();
const liveGroup = new THREE.Group();
const assetGroup = new THREE.Group();
assetGroup.visible = false;
scene.add(machineGroup, liveGroup, assetGroup);

const pbr = (color, metalness = 0, roughness = .55, options = {}) =>
  new THREE.MeshStandardMaterial({color, metalness, roughness, ...options});
const materials = {
  steel: pbr('#a9b5b3', .82, .28),
  frame: pbr('#263a33', .58, .4),
  belt: pbr('#244a83', .08, .74),
  floor: pbr('#1d2823', 0, .95),
  accept: pbr('#568b62', .05, .58),
  reject: pbr('#9b5247', .05, .58),
  chute: pbr('#b2c0bb', .25, .25, {transparent: true, opacity: .22, depthWrite: false, side: THREE.DoubleSide}),
  fallback: pbr('#9a988f', .08, .75),
};

const machineMeshes = [];
let machineBuilt = false;
function addBox(size, position, material, rotationY = 0) {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(...size), material);
  mesh.position.set(...position);
  mesh.rotation.y = rotationY;
  mesh.castShadow = material !== materials.chute;
  mesh.receiveShadow = true;
  machineGroup.add(mesh);
  machineMeshes.push(mesh);
  return mesh;
}

function buildMachine(layout) {
  if (machineBuilt) return;
  machineBuilt = true;
  const L = layout;
  const beltCentre = -L.belt_len / 2;
  addBox([4, 3, .025], [0, 0, -.018], materials.floor);
  addBox([L.belt_len, L.belt_w, .008], [beltCentre, 0, L.belt_z - .004], materials.belt);
  addBox([L.belt_len, L.belt_w + .06, .08], [beltCentre, 0, L.belt_z - .05], materials.frame);
  addBox([L.belt_len, .008, .024], [beltCentre, L.belt_w / 2 + .004, L.belt_z + .012], materials.steel);
  addBox([L.belt_len, .008, .024], [beltCentre, -L.belt_w / 2 - .004, L.belt_z + .012], materials.steel);
  for (const x of [-.1, -L.belt_len + .1]) for (const side of [-1, 1]) {
    addBox([.04, .04, L.belt_z - .09], [x, side * (L.belt_w / 2 + .05), (L.belt_z - .09) / 2], materials.frame);
  }
  addBox([.1, L.belt_w + .12, .06], [L.cam_x, 0, L.belt_z + .42], materials.frame);
  addBox([.012, L.belt_w + .04, .012], [L.cam_x - .05, 0, L.belt_z + .25], pbr('#fff6cf', 0, .4, {emissive: '#fff4c7', emissiveIntensity: 1.2}));
  addBox([.012, L.belt_w + .04, .012], [L.cam_x + .05, 0, L.belt_z + .25], pbr('#fff6cf', 0, .4, {emissive: '#fff4c7', emissiveIntensity: 1.2}));
  addBox([.024, L.belt_w + .04, .024], [L.ej_x, 0, L.belt_z + L.ej_z_offset + .018], materials.steel);
  const nozzleGeometry = new THREE.CylinderGeometry(.002, .0015, .016, 10);
  nozzleGeometry.rotateX(Math.PI / 2);
  const nozzles = new THREE.InstancedMesh(nozzleGeometry, materials.steel, L.n_nozzles);
  const dummy = new THREE.Object3D();
  for (let i = 0; i < L.n_nozzles; i++) {
    dummy.position.set(L.ej_x, -L.belt_w / 2 + L.belt_w / L.n_nozzles * (i + .5), L.belt_z + L.ej_z_offset);
    dummy.updateMatrix();
    nozzles.setMatrixAt(i, dummy.matrix);
  }
  machineGroup.add(nozzles);
  addBox([.28, L.belt_w + .04, .004], [L.split_x + .14, 0, L.belt_z - L.split_z_drop], materials.steel, .25);
  addBox([.32, .004, .32], [L.split_x + .12, L.belt_w / 2 + .022, L.belt_z - .12], materials.chute);
  addBox([.32, .004, .32], [L.split_x + .12, -L.belt_w / 2 - .022, L.belt_z - .12], materials.chute);
  addBox([.24, L.belt_w + .04, .006], [L.split_x + .30, 0, L.belt_z - .30], materials.accept);
  addBox([.24, L.belt_w + .04, .006], [L.split_x + .06, 0, L.belt_z - .42], materials.reject);
  renderer.shadowMap.needsUpdate = true;
}

function normalizeGeometry(geometry) {
  geometry.computeBoundingBox();
  const box = geometry.boundingBox;
  const centre = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());
  geometry.translate(-centre.x, -centre.y, -centre.z);
  geometry.scale(2 / size.x, 2 / size.y, 2 / size.z);
  geometry.computeVertexNormals();
  return geometry;
}

const loader = new GLTFLoader();
const fallbackAssets = new Set();
async function loadPrototype(name, url, fallbackGeometry) {
  try {
    const gltf = await loader.loadAsync(url);
    gltf.scene.updateMatrixWorld(true);
    const meshes = [];
    gltf.scene.traverse(node => { if (node.isMesh) meshes.push(node); });
    if (meshes.length !== 1 || Array.isArray(meshes[0].material)) throw new Error('Expected one mesh and one material');
    const part = meshes[0];
    const geometry = part.geometry.clone().applyMatrix4(part.matrixWorld).rotateX(Math.PI / 2);
    return {name, geometry: normalizeGeometry(geometry), material: part.material.clone(), fallback: false};
  } catch (error) {
    fallbackAssets.add(name);
    console.warn(`${name} GLB fallback`, error);
    return {name, geometry: normalizeGeometry(fallbackGeometry), material: materials.fallback.clone(), fallback: true};
  }
}

const MAX_INSTANCES = 600;
const prototypes = new Map();
const liveMeshes = new Map();
const dummy = new THREE.Object3D();
async function loadAssets() {
  const definitions = [
    ['good', '/assets/bean_good_lod.glb', new THREE.SphereGeometry(1, 12, 8)],
    ['black', '/assets/bean_black_lod.glb', new THREE.SphereGeometry(1, 12, 8)],
    ['insect', '/assets/bean_insect_lod.glb', new THREE.SphereGeometry(1, 12, 8)],
    ['broken', '/assets/bean_broken_lod.glb', new THREE.SphereGeometry(1, 12, 8)],
  ];
  for (const definition of definitions) {
    const prototype = await loadPrototype(...definition);
    prototypes.set(prototype.name, prototype);
  }
  prototypes.set('box', {name: 'box', geometry: new THREE.BoxGeometry(2, 2, 2), material: materials.fallback.clone(), fallback: true});
  prototypes.set('capsule', {name: 'capsule', geometry: normalizeGeometry(new THREE.CapsuleGeometry(1, 2, 4, 10).rotateZ(Math.PI / 2)), material: pbr('#88725b', .02, .82), fallback: true});
  for (const [name, prototype] of prototypes) {
    const mesh = new THREE.InstancedMesh(prototype.geometry, prototype.material, MAX_INSTANCES);
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    mesh.frustumCulled = false;
    mesh.count = 0;
    liveGroup.add(mesh);
    liveMeshes.set(name, mesh);
  }
  await buildAssetPreview();
}

async function buildAssetPreview() {
  const names = ['good', 'black', 'insect', 'broken'];
  names.forEach((name, index) => {
    const prototype = prototypes.get(name);
    const mesh = new THREE.Mesh(prototype.geometry, prototype.material.clone());
    mesh.position.set((index - 2) * .18, 0, .12);
    mesh.scale.setScalar(.055);
    mesh.rotation.z = -.35;
    assetGroup.add(mesh);
  });
  try {
    const gltf = await loader.loadAsync('/assets/generated_earring.glb');
    const generated = gltf.scene;
    generated.rotation.x = Math.PI / 2;
    generated.updateMatrixWorld(true);
    const bounds = new THREE.Box3().setFromObject(generated);
    const centre = bounds.getCenter(new THREE.Vector3());
    const size = bounds.getSize(new THREE.Vector3());
    generated.position.sub(centre);
    const specimen = new THREE.Group();
    specimen.add(generated);
    specimen.scale.setScalar(.11 / Math.max(size.x, size.y, size.z));
    specimen.position.set(.46, 0, .13);
    assetGroup.add(specimen);
  } catch (error) {
    fallbackAssets.add('generated-earring');
    console.warn('Generated multipart GLB unavailable', error);
  }
  const shelf = new THREE.Mesh(new THREE.BoxGeometry(1.25, .28, .025), materials.frame);
  shelf.position.set(0, 0, .02);
  shelf.receiveShadow = true;
  assetGroup.add(shelf);
}

function liveAssetKey(object) {
  if (object.shape === 'half') return 'broken';
  if (object.shape === 'box') return 'box';
  if (object.shape === 'capsule') return 'capsule';
  return 'good';
}

const selectedMarker = new THREE.Mesh(
  new THREE.TorusGeometry(1, .08, 8, 32),
  new THREE.MeshBasicMaterial({color: '#79e6f5', transparent: true, opacity: .9, depthWrite: false}),
);
selectedMarker.visible = false;
liveGroup.add(selectedMarker);

let state = null;
let sessionId = null;
let latestInjectedId = null;
let lastEventId = -1;
let visibleObjects = 0;
function updateLiveObjects(packet) {
  const counts = new Map([...liveMeshes.keys()].map(key => [key, 0]));
  let selectedObject = null;
  for (const object of packet.objects || []) {
    if (object.object_id === latestInjectedId) selectedObject = object;
    if (!object.active || !object.pos || !object.quat) continue;
    const key = liveAssetKey(object);
    const mesh = liveMeshes.get(key) || liveMeshes.get('box');
    const index = counts.get(key) || 0;
    if (index >= MAX_INSTANCES) continue;
    dummy.position.fromArray(object.pos);
    dummy.quaternion.set(object.quat[1], object.quat[2], object.quat[3], object.quat[0]).normalize();
    const axes = object.axes || [.004, .003, .002];
    if (object.shape === 'capsule') dummy.scale.set(axes[0] + axes[1], axes[1], axes[1]);
    else dummy.scale.set(axes[0], axes[1], axes[2]);
    dummy.updateMatrix();
    mesh.setMatrixAt(index, dummy.matrix);
    mesh.setColorAt(index, new THREE.Color().setRGB(...(object.rgb || [.55, .55, .5])));
    counts.set(key, index + 1);
  }
  visibleObjects = [...counts.values()].reduce((total, value) => total + value, 0);
  for (const [key, mesh] of liveMeshes) {
    mesh.count = counts.get(key) || 0;
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  }
  if (selectedObject?.active && selectedObject.pos) {
    const radius = Math.max(...selectedObject.axes) * 1.65;
    selectedMarker.position.fromArray(selectedObject.pos);
    selectedMarker.scale.setScalar(radius);
    selectedMarker.quaternion.copy(camera.quaternion);
    selectedMarker.visible = true;
  } else {
    selectedMarker.visible = false;
  }
  updateEvidence(selectedObject);
}

function formatPercent(metric) {
  return metric?.value == null ? 'Warm-up' : `${(metric.value * 100).toFixed(1)}%`;
}
function formatCount(metric, empty) {
  return metric ? `${metric.numerator} / ${metric.denominator}` : empty;
}
function updateScores(scores) {
  $('accuracy').textContent = formatPercent(scores?.sorting_accuracy);
  $('capture').textContent = formatPercent(scores?.defect_capture);
  $('loss').textContent = formatPercent(scores?.good_loss);
  $('accuracy-count').textContent = formatCount(scores?.sorting_accuracy, '0 / 0');
  $('capture-count').textContent = formatCount(scores?.defect_capture, '0 / 0');
  $('loss-count').textContent = formatCount(scores?.good_loss, '0 / 0');
}

function updateEvidence(object) {
  $('object-id').textContent = latestInjectedId == null ? 'Waiting for an injection' : `Object ${latestInjectedId}`;
  const decision = object?.decision;
  $('prediction').textContent = decision ? `${decision.predicted_class}${decision.association_approximate ? ' (approximate association)' : ''}` : 'Not observed';
  $('decision').textContent = decision ? `${decision.reject ? 'Reject' : 'Keep'}${decision.scheduled ? ', valves scheduled' : ''}${decision.late ? ', late' : ''}` : 'Not decided';
  $('contact').textContent = object?.jet_hits ? `${object.own_pulse_hit ? 'Own targeted pulse' : 'Other or unassociated pulse'}, ${object.jet_hits} contact steps` : 'No contact recorded';
  const outcomeText = {accept: 'Accept path', reject: 'Reject path', spilled: 'Spilled'};
  $('physical-outcome').textContent = object?.outcome ? outcomeText[object.outcome] : 'Unresolved';
  const label = object?.outcome === 'accept' ? 'Passed' : object?.outcome === 'reject' ? 'Rejected' : object?.outcome === 'spilled' ? 'Spilled' : 'In progress';
  $('outcome').textContent = label;
  $('outcome').className = `outcome ${label.toLowerCase()}`;
}

function ingest(packet) {
  if (packet.type !== 'state') return;
  if (sessionId && packet.session_id !== sessionId) {
    latestInjectedId = null;
    lastEventId = -1;
  }
  sessionId = packet.session_id;
  for (const event of packet.events || []) {
    if (event.event_id <= lastEventId) continue;
    lastEventId = event.event_id;
    if (event.type === 'injected') latestInjectedId = event.object_id;
  }
  state = packet;
  buildMachine(packet.layout);
  updateLiveObjects(packet);
  updateScores(packet.rolling_scores);
  $('connection').className = `connection ${packet.status === 'running' ? 'online' : ''}`;
  $('connection').querySelector('span').textContent = `${packet.status} · ${packet.sim_time_s.toFixed(1)} sim s`;
}

let socket;
let reconnectTimer;
function connect() {
  clearTimeout(reconnectTimer);
  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
  socket = new WebSocket(`${scheme}//${location.host}/ws`);
  socket.onopen = () => {
    $('error').classList.add('hidden');
    $('connection').className = 'connection';
    $('connection').querySelector('span').textContent = 'Connected, waiting for state';
  };
  socket.onmessage = event => {
    const packet = JSON.parse(event.data);
    if (packet.type === 'preview_error') {
      $('error').textContent = `The live backend is unavailable: ${packet.error}`;
      $('error').classList.remove('hidden');
      return;
    }
    ingest(packet);
  };
  socket.onclose = () => {
    $('connection').className = 'connection failed';
    $('connection').querySelector('span').textContent = 'Disconnected';
    $('error').textContent = 'The read-only live connection closed. Retrying without changing the last authoritative state.';
    $('error').classList.remove('hidden');
    reconnectTimer = setTimeout(connect, 1400);
  };
}

const views = {
  overview: {position: [1.5, -2.05, 1.55], target: [-.3, 0, .44]},
  sorting: {position: [.82, -1.18, .82], target: [.18, 0, .48]},
  top: {position: [-.25, -.02, 2.45], target: [-.25, 0, .38]},
  assets: {position: [0, -1.18, .62], target: [0, 0, .11]},
};
let currentView;
function setView(name) {
  const view = views[name];
  currentView = name;
  const position = [...view.position];
  if (name === 'assets') {
    const aspect = stage.clientWidth / Math.max(stage.clientHeight, 1);
    const distance = Math.min(3.6, Math.max(1.18, .72 / (Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) * aspect)));
    position[1] = -distance;
    position[2] = view.target[2] + distance * .43;
  }
  camera.position.fromArray(position);
  controls.target.fromArray(view.target);
  controls.update();
  const assetMode = name === 'assets';
  stage.classList.toggle('asset-mode', assetMode);
  machineGroup.visible = !assetMode;
  liveGroup.visible = !assetMode;
  assetGroup.visible = assetMode;
  document.querySelector('.latest').classList.toggle('hidden', assetMode);
  $('asset-note').innerHTML = assetMode
    ? '<strong>Asset compatibility mode</strong>Four bean LODs and one multipart generated earring. No live physics, injection, or training support.'
    : '<strong>Live visual contract</strong>Server shape and RGB select appearance. Boxes and capsules use generic fallbacks. Prediction never changes the model.';
  document.querySelectorAll('[data-camera]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.camera === name)));
}
document.querySelectorAll('[data-camera]').forEach(button => button.onclick = () => setView(button.dataset.camera));
setView('overview');

function resize() {
  const width = stage.clientWidth;
  const height = stage.clientHeight;
  renderer.setSize(width, height, false);
  camera.aspect = width / Math.max(height, 1);
  camera.updateProjectionMatrix();
  if (currentView === 'assets') setView('assets');
}
new ResizeObserver(resize).observe(stage);
resize();

let assetBytes = 0;
fetch('/asset-manifest.json').then(response => response.json()).then(manifest => { assetBytes = manifest.total_bytes; });
const samples = [];
let previousFrame;
let frameCounter = 0;
let fpsStarted = performance.now();
let fps = 0;
function render(now) {
  if (previousFrame != null) {
    samples.push(now - previousFrame);
    if (samples.length > 180) samples.shift();
  }
  previousFrame = now;
  frameCounter++;
  if (now - fpsStarted >= 1000) {
    fps = frameCounter * 1000 / (now - fpsStarted);
    frameCounter = 0;
    fpsStarted = now;
  }
  controls.update();
  if (selectedMarker.visible) selectedMarker.quaternion.copy(camera.quaternion);
  renderer.render(scene, camera);
  const sorted = [...samples].sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length * .5)] || 0;
  const p95 = sorted[Math.floor(sorted.length * .95)] || 0;
  const telemetry = {
    ready: Boolean(state),
    fps,
    medianFrameMs: median,
    p95FrameMs: p95,
    drawCalls: renderer.info.render.calls,
    triangles: renderer.info.render.triangles,
    visibleObjects,
    assetBytes,
    fallbackAssets: [...fallbackAssets, 'box', 'capsule'],
    renderer: gpu,
    pixelRatio: renderer.getPixelRatio(),
    sessionId,
    simTime: state?.sim_time_s ?? null,
  };
  window.live3dTelemetry = telemetry;
  $('telemetry').textContent = `${fps.toFixed(0)} fps · ${median.toFixed(1)} ms median · ${p95.toFixed(1)} ms p95\n${telemetry.drawCalls} draws · ${visibleObjects} live objects · ${(assetBytes / 1024).toFixed(0)} KiB GLB`;
  requestAnimationFrame(render);
}

await loadAssets();
connect();
requestAnimationFrame(render);
