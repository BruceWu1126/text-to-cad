import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { frameBoundsFor } from './headlessSnapshotSession.js';

test('animation framing includes each output pose and caches its bounds', () => {
  const root = new THREE.Group(), mesh = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshStandardMaterial());
  mesh.name = 'part'; root.add(mesh);
  const clip = new THREE.AnimationClip('slide', 1, [new THREE.VectorKeyframeTrack('part.position', [0, 1], [0,0,0, 10,0,0])]);
  const session = { family: { name: 'glb' }, scene: { object3D: root, document: { scene: root } }, job: { animation: { fps: 4 } },
    entries: [clip], routines: [{ id: 'glb:0', name: 'slide', duration: 1 }], frameBounds: new Map(), animations: new Map(), timelines: new Map() };
  const bounds = frameBoundsFor(session, 0);
  assert.equal(bounds.min[0], -0.5);
  assert.equal(bounds.max[0], 8, 'the last exported frame is at 0.75 seconds');
  assert.equal(frameBoundsFor(session, 0), bounds);
  session.animations.get(0).mixer.stopAllAction(); mesh.geometry.dispose(); mesh.material.dispose();
});
