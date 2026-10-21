// A scene's own rig on a stage (`sceneRig.js`): its lights in place, its curve as three's
// CustomToneMapping, its exposure, and the stage's renderer given back as it was.
import assert from "node:assert/strict";
import test from "node:test";
import * as THREE from "three";

import { SCENE_RIG_TONE_CURVES, applySceneRig, installSceneRigToneCurve, isSceneRig } from "./sceneRig.js";

const RIG = Object.freeze({
  id: "test", ambient: { color: "#ffffff", intensity: 1.5 },
  lights: [{ name: "key", towards: [0, 0, 2], color: "#ffffff", intensity: 3 }, { name: "fill", towards: [1, 0, 0], color: "#ffeedd", intensity: 2 }],
  environment: 0, exposure: 1, toneCurve: "clamp"
});

test("the rig's curve is three's CustomToneMapping, once a page", () => {
  installSceneRigToneCurve(THREE, "clamp");
  const chunk = THREE.ShaderChunk.tonemapping_pars_fragment;
  assert.ok(chunk.includes(`vec3 CustomToneMapping( vec3 color ) { ${SCENE_RIG_TONE_CURVES.clamp} }`));
  installSceneRigToneCurve(THREE, "clamp");
  assert.equal(THREE.ShaderChunk.tonemapping_pars_fragment, chunk, "installing it again changes nothing");
  assert.throws(() => installSceneRigToneCurve(THREE, "filmic"), /tone curve is one of clamp/);
  assert.equal(isSceneRig({ ...RIG, toneCurve: "aces" }), false);
});

test("a rig lights the stage in place of its own and gives the renderer back", () => {
  const scene = new THREE.Scene();
  scene.environmentIntensity = 1;
  const renderer = { toneMapping: THREE.ACESFilmicToneMapping, toneMappingExposure: 1.08 };
  const up = applySceneRig(THREE, { scene, renderer }, RIG);
  assert.equal(up.group.parent, scene);
  const [ambient, key, fill] = up.group.children;
  assert.ok(ambient.isAmbientLight);
  assert.equal(ambient.intensity, 1.5);
  assert.deepEqual([key.intensity, fill.intensity], [3, 2]);
  assert.deepEqual(key.position.toArray(), [0, 0, 1], "it shines from where `towards` points, its target the origin");
  assert.equal(fill.color.getHexString(), "ffeedd");
  assert.equal(key.castShadow || fill.castShadow, false);
  assert.equal(renderer.toneMapping, THREE.CustomToneMapping);
  assert.equal(renderer.toneMappingExposure, 1);
  assert.equal(scene.environmentIntensity, 0, "no environment lights the scene as a whole");
  up.dispose();
  assert.equal(up.group.parent, null);
  assert.equal(renderer.toneMapping, THREE.ACESFilmicToneMapping);
  assert.equal(renderer.toneMappingExposure, 1.08);
  assert.equal(scene.environmentIntensity, 1);
  assert.throws(() => applySceneRig(THREE, { scene, renderer }, { ...RIG, lights: null }), /a scene rig has/);
});
