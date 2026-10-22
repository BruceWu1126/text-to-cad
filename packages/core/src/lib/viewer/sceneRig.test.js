// A scene's own rig on a stage (`sceneRig.js`): its lights in place, its environment the scene's
// (one PMREM per image, shared by the rigs that light with it), its curve as three's
// CustomToneMapping, its exposure, and the stage given back as it was by the last rig to go.
import assert from "node:assert/strict";
import test from "node:test";
import * as THREE from "three";

import { SCENE_RIG_TONE_CURVES, applySceneRig, installSceneRigToneCurve, isSceneRig, sceneRigFor } from "./sceneRig.js";

const IMAGE = Object.freeze({ width: 2, height: 1, data: new Float32Array([1, 1, 1, 1, 2, 2, 2, 1]) });
const RIG = Object.freeze({
  id: "test", ambient: null,
  lights: [{ name: "key", towards: [0, 0, 2], color: "#ffffff", intensity: 3 }, { name: "fill", towards: [1, 0, 0], color: "#ffeedd", intensity: 2 }],
  environment: { ...IMAGE, intensity: 0.5, rotation: [Math.PI / 2, 0, 0] }, exposure: 1, toneCurve: "clamp"
});

/** THREE with a PMREM generator that needs no WebGL: it records the images it turns into targets. */
function headless() {
  const made = [];
  class PMREMGenerator {
    fromEquirectangular(texture) {
      const target = { texture: new THREE.Texture(), disposed: false, source: texture, dispose() { this.disposed = true; } };
      made.push(target);
      return target;
    }
    dispose() {}
  }
  return { three: { ...THREE, PMREMGenerator }, made };
}

function stage() {
  const scene = new THREE.Scene();
  scene.environmentIntensity = 1;
  return { scene, renderer: { toneMapping: THREE.ACESFilmicToneMapping, toneMappingExposure: 1.08 } };
}

test("a scene's rig for the look it wears: its rigFor's answer where it has one, else its rig, else none", () => {
  const other = { ...RIG, id: "other" };
  const look = { surface: { style: "shaded", opacity: 0.22 } };
  const asked = [];
  const scene = { rig: RIG, rigFor(found) { asked.push(found); return found === look ? other : RIG; } };
  assert.equal(sceneRigFor(scene, look), other);
  assert.equal(sceneRigFor(scene, null), RIG);
  assert.deepEqual(asked, [look, null]);
  assert.equal(sceneRigFor({ rig: RIG }, look), RIG);
  assert.equal(sceneRigFor({}, look), null);
  assert.equal(sceneRigFor(null, look), null);
});

test("the rig's curve is three's CustomToneMapping, once a page", () => {
  installSceneRigToneCurve(THREE, "clamp");
  const chunk = THREE.ShaderChunk.tonemapping_pars_fragment;
  assert.ok(chunk.includes(`vec3 CustomToneMapping( vec3 color ) { ${SCENE_RIG_TONE_CURVES.clamp} }`));
  installSceneRigToneCurve(THREE, "clamp");
  assert.equal(THREE.ShaderChunk.tonemapping_pars_fragment, chunk, "installing it again changes nothing");
  assert.throws(() => installSceneRigToneCurve(THREE, "filmic"), /tone curve is one of clamp/);
  assert.equal(isSceneRig({ ...RIG, toneCurve: "aces" }), false);
  assert.equal(isSceneRig({ ...RIG, environment: { ...RIG.environment, width: 3 } }), false, "an image its pixels do not fill");
});

test("a rig lights the stage in place of its own, its image the scene's environment, and gives the stage back", () => {
  const { three, made } = headless();
  const target = stage();
  const { scene, renderer } = target;
  const up = applySceneRig(three, target, RIG);
  assert.equal(target.sceneRig, up);
  assert.equal(up.group.parent, scene);
  const [key, fill] = up.group.children;
  assert.deepEqual([key.intensity, fill.intensity], [3, 2]);
  assert.deepEqual(key.position.toArray(), [0, 0, 1], "it shines from where `towards` points, its target the origin");
  assert.equal(fill.color.getHexString(), "ffeedd");
  assert.equal(key.castShadow || fill.castShadow, false);
  assert.equal(made.length, 1);
  assert.equal(made[0].source.mapping, THREE.EquirectangularReflectionMapping);
  assert.equal(made[0].source.type, THREE.HalfFloatType, "half floats, which WebGL filters");
  assert.deepEqual([scene.environment, scene.environmentIntensity], [made[0].texture, 0.5]);
  assert.deepEqual(scene.environmentRotation.toArray().slice(0, 3), [Math.PI / 2, 0, 0]);
  assert.deepEqual([renderer.toneMapping, renderer.toneMappingExposure], [THREE.CustomToneMapping, 1]);
  // The stage sets its own exposure; the rig puts its own back.
  renderer.toneMappingExposure = 1.2;
  up.restate();
  assert.equal(renderer.toneMappingExposure, 1);
  // The next version of the scene brings a rig of the same image: up before the first comes down,
  // it shares the first's PMREM, and the first going leaves the stage to it.
  const next = applySceneRig(three, target, { ...RIG, id: "next" });
  up.dispose();
  assert.equal(made.length, 1, "one PMREM for one image");
  assert.equal(made[0].disposed, false);
  assert.equal(up.group.parent, null);
  assert.deepEqual([scene.environment, renderer.toneMapping, target.sceneRig], [made[0].texture, THREE.CustomToneMapping, next]);
  next.dispose();
  assert.equal(made[0].disposed, true, "released with the last rig that lit with it");
  assert.deepEqual([scene.environment, scene.environmentIntensity, renderer.toneMapping, renderer.toneMappingExposure],
    [null, 1, THREE.ACESFilmicToneMapping, 1.08]);
  assert.deepEqual(scene.environmentRotation.toArray().slice(0, 3), [0, 0, 0]);
  assert.throws(() => applySceneRig(three, target, { ...RIG, lights: null }), /a scene rig has/);
});
