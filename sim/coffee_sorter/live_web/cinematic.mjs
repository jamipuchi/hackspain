const CYCLE_SECONDS = 30;
const TARGET_X = -.22;
const TARGET_Y = 0;
const TARGET_Z = .47;

/**
 * Writes the looping camera pose into supplied Three.js vectors without making
 * allocations. The position joins exactly at each 30 second boundary.
 */
export function sampleCinematicPose(nowSeconds, outPosition, outTarget) {
  const phase = (nowSeconds / CYCLE_SECONDS - Math.floor(nowSeconds / CYCLE_SECONDS)) * Math.PI * 2;
  const radius = 2.3 + .18 * Math.sin(phase * 2);
  outTarget.set(TARGET_X, TARGET_Y, TARGET_Z);
  outPosition.set(
    TARGET_X + Math.cos(phase) * radius,
    TARGET_Y + Math.sin(phase) * radius,
    1.3 + .22 * (1 + Math.sin(phase - .7)),
  );
}

/**
 * Adds the CINTA cinematic controls around the existing live WebGL scene.
 */
export function createCinematicMode({three, getView, setView, setLabels}) {
  const nav = document.querySelector('nav[aria-label="View"]');
  const button = document.createElement('button');
  const closeButton = document.createElement('button');
  const overlay = document.createElement('aside');
  const style = document.createElement('style');
  const saved = {};
  let scoreRow = null;
  let scoreParent = null;
  let scoreNextSibling = null;
  let active = false;
  let startedAt = 0;

  style.textContent = `
    body.cinematic-mode .panel,
    body.cinematic-mode .annotation,
    body.cinematic-mode .help-tooltip,
    body.cinematic-mode #inset { display: none !important; }
    #cinematic-exit { display: none; position: fixed; z-index: 30; top: calc(env(safe-area-inset-top) + 12px); right: calc(env(safe-area-inset-right) + 12px); width: 44px; height: 44px; min-width: 44px; min-height: 44px; padding: 0; font-size: 22px; line-height: 1; }
    body.cinematic-mode #cinematic-exit { display: block; }
    body.cinematic-mode #performance-badge { max-width: calc(100vw - 128px); white-space: normal; }
    #cinematic-score-overlay { display: none; position: fixed; z-index: 5; left: calc(env(safe-area-inset-left) + 12px); bottom: calc(env(safe-area-inset-bottom) + 12px); width: min(238px, calc(100vw - 24px)); padding: 8px 10px; border: 1px solid var(--rule); background: #f8f8f4e8; backdrop-filter: blur(5px); }
    body.cinematic-mode #cinematic-score-overlay { display: block; }
    #cinematic-score-overlay .row { padding: 0; border: 0; }
    #cinematic-score-overlay .row b { font-size: 18px; }
  `;
  document.head.append(style);

  button.type = 'button';
  button.id = 'cinematic-toggle';
  button.textContent = 'Cinematic';
  button.setAttribute('aria-pressed', 'false');
  button.setAttribute('aria-label', 'Enter cinematic view');
  if (nav) nav.append(button);

  closeButton.type = 'button';
  closeButton.id = 'cinematic-exit';
  closeButton.textContent = '×';
  closeButton.setAttribute('aria-label', 'Exit cinematic view');
  document.body.append(closeButton);

  overlay.id = 'cinematic-score-overlay';
  overlay.setAttribute('aria-label', 'Sorting accuracy');
  document.body.append(overlay);

  const canvas = () => three?.renderer?.domElement;
  const available = () => Boolean(three?.ready && three?.camera && three?.controls && canvas());
  const syncAvailability = () => { button.disabled = !available(); };

  function moveScoreRow() {
    scoreRow = document.getElementById('score-accuracy')?.closest('.row') || null;
    if (!scoreRow) return;
    scoreParent = scoreRow.parentNode;
    scoreNextSibling = scoreRow.nextSibling;
    overlay.append(scoreRow);
  }

  function restoreScoreRow() {
    if (scoreRow && scoreParent) scoreParent.insertBefore(scoreRow, scoreNextSibling);
    scoreRow = scoreParent = scoreNextSibling = null;
  }

  function enter() {
    if (active || !available()) {
      syncAvailability();
      return false;
    }
    const camera = three.camera;
    const controls = three.controls;
    saved.view = getView();
    saved.focusedElement = document.activeElement;
    saved.labels = Boolean(three.labels);
    saved.cameraPreset = three.cameraPreset;
    saved.positionX = camera.position.x; saved.positionY = camera.position.y; saved.positionZ = camera.position.z;
    saved.quaternionX = camera.quaternion.x; saved.quaternionY = camera.quaternion.y; saved.quaternionZ = camera.quaternion.z; saved.quaternionW = camera.quaternion.w;
    saved.upX = camera.up.x; saved.upY = camera.up.y; saved.upZ = camera.up.z;
    saved.zoom = camera.zoom;
    saved.targetX = controls.target.x; saved.targetY = controls.target.y; saved.targetZ = controls.target.z;
    saved.controlsEnabled = controls.enabled;

    controls.enabled = false;
    three.cameraPreset = 'overview';
    setView('3d');
    setLabels(false, false);
    moveScoreRow();
    document.body.classList.add('cinematic-mode');
    button.setAttribute('aria-pressed', 'true');
    button.setAttribute('aria-label', 'Exit cinematic view');
    active = true;
    startedAt = performance.now();
    update(startedAt);
    closeButton.focus({preventScroll: true});
    return true;
  }

  function exit() {
    if (!active) return false;
    const camera = three.camera;
    const controls = three.controls;
    active = false;
    camera.position.set(saved.positionX, saved.positionY, saved.positionZ);
    camera.quaternion.set(saved.quaternionX, saved.quaternionY, saved.quaternionZ, saved.quaternionW);
    camera.up.set(saved.upX, saved.upY, saved.upZ);
    camera.zoom = saved.zoom;
    camera.updateProjectionMatrix();
    camera.updateMatrixWorld();
    controls.target.set(saved.targetX, saved.targetY, saved.targetZ);
    controls.enabled = saved.controlsEnabled;
    three.cameraPreset = saved.cameraPreset;
    setView(saved.view);
    setLabels(saved.labels, false);
    restoreScoreRow();
    document.body.classList.remove('cinematic-mode');
    button.setAttribute('aria-pressed', 'false');
    button.setAttribute('aria-label', 'Enter cinematic view');
    if (saved.focusedElement?.isConnected && typeof saved.focusedElement.focus === 'function') saved.focusedElement.focus({preventScroll: true});
    else button.focus({preventScroll: true});
    return true;
  }

  function update(now) {
    if (!active) return false;
    if (!available()) {
      exit();
      syncAvailability();
      return false;
    }
    sampleCinematicPose((now - startedAt) / 1000, three.camera.position, three.controls.target);
    three.camera.lookAt(three.controls.target);
    three.camera.updateMatrixWorld();
    return true;
  }

  function blockKeyboard(event) {
    if (!active) return;
    if (event.key === 'Escape') {
      exit();
      event.preventDefault();
      event.stopImmediatePropagation();
      return;
    }
    if (event.key === 'Tab' || event.target === closeButton) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  }

  function blockCanvasInput(event) {
    if (!active) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  }

  button.addEventListener('click', () => { if (active) exit(); else enter(); });
  closeButton.addEventListener('click', exit);
  document.addEventListener('keydown', blockKeyboard, true);
  for (const type of ['pointerdown', 'pointermove', 'pointerup', 'pointercancel', 'wheel', 'contextmenu']) {
    canvas()?.addEventListener(type, blockCanvasInput, {capture: true, passive: false});
  }
  canvas()?.addEventListener('webglcontextlost', () => { if (active) exit(); }, true);
  canvas()?.addEventListener('webglcontextlost', syncAvailability);
  canvas()?.addEventListener('webglcontextrestored', syncAvailability);
  syncAvailability();

  return {update, enter, exit, get active() { return active; }};
}
