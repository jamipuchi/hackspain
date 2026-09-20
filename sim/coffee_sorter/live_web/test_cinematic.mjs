import assert from 'node:assert/strict';
import test from 'node:test';
import {PerspectiveCamera, Vector3} from '../web/vendor/three.module.js';
import {createCinematicMode, sampleCinematicPose} from './cinematic.mjs';

// Minimal DOM fixture for restoration checks. Browser checks cover layout and events.
class Element {
  constructor() {
    this.children = [];
    this.listeners = {};
    this.handlers = {};
    this.attributes = {};
    this.isConnected = true;
    this.classes = new Set();
    this.classList = {add: value => this.classes.add(value), remove: value => this.classes.delete(value)};
  }
  append(child) {
    if (child.parentNode) child.parentNode.children.splice(child.parentNode.children.indexOf(child), 1);
    child.parentNode = this;
    this.children.push(child);
  }
  insertBefore(child, next) {
    this.append(child);
    this.children.pop();
    this.children.splice(next ? this.children.indexOf(next) : this.children.length, 0, child);
  }
  get nextSibling() { return this.parentNode.children[this.parentNode.children.indexOf(this) + 1] || null; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, callback) {
    (this.handlers[name] ||= []).push(callback);
    this.listeners[name] = event => this.handlers[name].forEach(listener => listener(event));
  }
  focus() { document.activeElement = this; }
  closest() { return this.parentNode; }
}

function fixture() {
  const nav = new Element(), table = new Element(), row = new Element(), next = new Element(), accuracy = new Element();
  table.append(row); table.append(next); row.append(accuracy);
  const previousFocus = new Element();
  const doc = {
    head: new Element(), body: new Element(), activeElement: previousFocus, listeners: {},
    createElement: () => new Element(),
    querySelector: () => nav,
    getElementById: id => id === 'score-accuracy' ? accuracy : null,
    addEventListener(name, listener) { this.listeners[name] = listener; },
  };
  globalThis.document = doc;
  const camera = new PerspectiveCamera();
  camera.up.set(0, 0, 1);
  camera.position.set(1.7, -2, 1.5);
  camera.lookAt(-.2, 0, .4);
  const controls = {
    target: new Vector3(-.2, 0, .4), enabled: true, enableDamping: true,
    pendingOrbit: .01, updateCount: 0,
    update() { camera.position.x += this.pendingOrbit; this.pendingOrbit = 0; this.updateCount++; },
  };
  const three = {camera, controls, labels: true, cameraPreset: 'inspection', ready: true, renderer: {domElement: new Element()}};
  let view = '2d';
  const labelWrites = [];
  const mode = createCinematicMode({three, getView: () => view, setView: value => { view = value; }, setLabels: (value, persist) => { three.labels = value; labelWrites.push(persist); }});
  return {mode, three, doc, nav, table, row, next, previousFocus, labelWrites, get view() { return view; }};
}

function pose(seconds) {
  const position = new Vector3(), target = new Vector3();
  sampleCinematicPose(seconds, position, target);
  return {position, target};
}

test('camera loop joins smoothly and repeats without frame history', () => {
  const start = pose(0), end = pose(30);
  assert.deepEqual(start, end);
  const epsilon = .001;
  const before = pose(30 - epsilon).position, after = pose(epsilon).position;
  assert.ok(before.distanceTo(after) < .002);
  const incoming = start.position.clone().sub(before).divideScalar(epsilon);
  const outgoing = after.clone().sub(start.position).divideScalar(epsilon);
  assert.ok(incoming.distanceTo(outgoing) < .001);
  for (let t = 0; t < 30; t += .1) {
    const sample = pose(t);
    assert.ok(sample.position.z >= 1.1);
    assert.ok(sample.position.distanceTo(pose(t + 30).position) < 1e-12);
  }
});

test('exit restores camera, controls, view, labels and original accuracy DOM position', () => {
  const f = fixture();
  const {camera, controls} = f.three;
  const before = {position: camera.position.clone(), quaternion: camera.quaternion.clone(), up: camera.up.clone(), zoom: camera.zoom, target: controls.target.clone()};
  assert.equal(f.mode.enter(), true);
  assert.equal(f.mode.enter(), false);
  assert.equal(f.view, '3d');
  assert.equal(controls.enabled, false);
  assert.equal(f.three.labels, false);
  assert.notEqual(f.row.parentNode, f.table);
  assert.equal(f.mode.update(10000), true);
  assert.equal(f.mode.exit(), true);
  assert.equal(f.mode.exit(), false);
  assert.equal(f.mode.update(11000), false);
  assert.deepEqual(camera.position, before.position);
  assert.deepEqual(camera.quaternion.toArray(), before.quaternion.toArray());
  assert.deepEqual(camera.up, before.up);
  assert.equal(camera.zoom, before.zoom);
  assert.deepEqual(controls.target, before.target);
  assert.equal(controls.enabled, true);
  assert.equal(controls.enableDamping, true);
  assert.equal(controls.pendingOrbit, .01);
  assert.equal(controls.updateCount, 0);
  assert.equal(f.three.cameraPreset, 'inspection');
  assert.equal(f.three.labels, true);
  assert.equal(f.view, '2d');
  assert.deepEqual(f.table.children, [f.row, f.next]);
  assert.equal(f.doc.body.classes.has('cinematic-mode'), false);
  assert.deepEqual(f.labelWrites, [false, false]);
  assert.equal(f.doc.activeElement, f.previousFocus);
});

test('Escape exits and unavailable WebGL refuses entry', () => {
  const f = fixture();
  f.mode.enter();
  f.doc.listeners.keydown({key: 'Escape', preventDefault() {}, stopImmediatePropagation() {}});
  assert.equal(f.mode.active, false);
  f.three.ready = false;
  assert.equal(f.mode.enter(), false);
  assert.equal(f.nav.children[0].disabled, true);
});

test('cinematic input blocks machine clicks but keeps exit keyboard activation', () => {
  const f = fixture();
  f.three.controls.enabled = false;
  f.mode.enter();
  const close = f.doc.body.children.find(element => element.id === 'cinematic-exit');
  let blocked = 0;
  const event = {target: close, key: 'Enter', preventDefault() { blocked++; }, stopImmediatePropagation() { blocked++; }};
  f.doc.listeners.keydown(event);
  assert.equal(blocked, 0);
  f.three.renderer.domElement.listeners.pointerup(event);
  assert.equal(blocked, 2);
  close.listeners.click();
  assert.equal(f.mode.active, false);
  assert.equal(f.three.controls.enabled, false);
});

test('WebGL context loss exits before the renderer changes availability', () => {
  const f = fixture();
  f.mode.enter();
  f.three.renderer.domElement.listeners.webglcontextlost({});
  assert.equal(f.mode.active, false);
  assert.equal(f.three.controls.enabled, true);
  assert.equal(f.view, '2d');
  assert.deepEqual(f.table.children, [f.row, f.next]);
});
