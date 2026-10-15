import assert from "node:assert/strict";
import test from "node:test";
import * as THREE from "three";

import {
  applyInspectGlobalIllumination,
  disposeInspectGlobalIllumination,
  INSPECT_GLOBAL_ILLUMINATION_CONES,
  INSPECT_GLOBAL_ILLUMINATION_FLOOR_SHADOW,
  inspectGlobalIlluminationSupported
} from "./inspectGlobalIllumination.js";

function rig() {
  const scene = new THREE.Scene();
  const hemisphereLight = new THREE.HemisphereLight("#ffffff", "#d6e2ee", 1.12);
  const ambientLight = new THREE.AmbientLight("#ffffff", 0.4);
  const keyLight = new THREE.DirectionalLight("#ffffff", 1.16);
  keyLight.position.set(-210, 260, 270);
  keyLight.castShadow = true;
  const fillLight = new THREE.DirectionalLight("#6b7f95", 0.46);
  fillLight.position.set(120, 80, 210);
  const rimLight = new THREE.DirectionalLight("#6db6e8", 0);
  rimLight.position.set(-260, 240, 180);
  scene.add(hemisphereLight, ambientLight, keyLight, fillLight, rimLight);
  const modelGroup = new THREE.Group();
  modelGroup.add(new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshPhysicalMaterial()));
  scene.add(modelGroup);
  return {
    scene, hemisphereLight, ambientLight, keyLight, fillLight, rimLight, modelGroup,
    renderer: { capabilities: { isWebGL2: true }, extensions: { has: () => true } },
    invalidations: 0,
    invalidateShadows() { this.invalidations += 1; }
  };
}

function lights(runtime) {
  return {
    hemisphere: runtime.hemisphereLight, ambient: runtime.ambientLight,
    directional: [
      { light: runtime.keyLight, angle: INSPECT_GLOBAL_ILLUMINATION_CONES.key },
      { light: runtime.fillLight, angle: INSPECT_GLOBAL_ILLUMINATION_CONES.fill },
      { light: runtime.rimLight, angle: INSPECT_GLOBAL_ILLUMINATION_CONES.fill }
    ]
  };
}

test("the rig's bake needs hardware WebGL2 with float render targets", () => {
  assert.equal(inspectGlobalIlluminationSupported(null), false);
  assert.equal(inspectGlobalIlluminationSupported({ renderer: {} }), false);
  assert.equal(inspectGlobalIlluminationSupported({ renderer: { capabilities: { isWebGL2: true }, extensions: { has: () => false } } }), false);
  assert.equal(inspectGlobalIlluminationSupported(rig()), true);
  assert.equal(inspectGlobalIlluminationSupported({ ...rig(), softwareRendering: true }), false);
  const software = { ...rig(), softwareRendering: true };
  assert.equal(applyInspectGlobalIllumination(THREE, software, { root: software.modelGroup, lights: lights(software) }), null);
  assert.equal(software.inspectRadiosity ?? null, null);
});

test("the rig's bake shadows the lit directional lights, takes over the key's shadow map and steps each frame", () => {
  const runtime = rig();
  const originalWarn = console.warn;
  console.warn = () => {};
  try {
    const state = applyInspectGlobalIllumination(THREE, runtime, {
      root: runtime.modelGroup, lights: lights(runtime),
      floor: { center: [1, 2, 3], level: -5, extent: 40 }
    });
    assert.ok(state);
    assert.equal(runtime.inspectRadiosity, state);
    assert.equal(typeof runtime.refineFrame, "function");
    assert.equal(runtime.scene.children.includes(state.stage.group), true);
    assert.equal(state.stage.settings.floorShadow, INSPECT_GLOBAL_ILLUMINATION_FLOOR_SHADOW);
    assert.equal(state.stage.skyFromLights, true);
    // The key and fill are lit; the rim, at zero intensity, is not baked.
    const direct = state.stage.directLights;
    assert.equal(direct.length, 2);
    assert.ok(direct[0].direction.distanceTo(runtime.keyLight.position.clone().normalize()) < 1e-9);
    assert.equal(direct[0].angle, INSPECT_GLOBAL_ILLUMINATION_CONES.key);
    assert.ok(Math.abs(direct[0].irradiance - 1.16) < 1e-9);
    assert.equal(direct[1].angle, INSPECT_GLOBAL_ILLUMINATION_CONES.fill);
    assert.ok(direct[1].irradiance < 0.46 && direct[1].irradiance > 0);
    assert.equal(state.stage._floorParams.level, -5);
    assert.equal(state.stage._floorParams.center.x, 1);
    // The key's shadow map is not drawn while the bake shadows it.
    assert.equal(runtime.keyLight.castShadow, false);
    assert.ok(runtime.invalidations > 0);

    // Applying again updates in place: the same stage, the same step.
    const step = runtime.refineFrame;
    runtime.rimLight.intensity = 0.3;
    assert.equal(applyInspectGlobalIllumination(THREE, runtime, { root: runtime.modelGroup, lights: lights(runtime), floor: null }), state);
    assert.equal(runtime.refineFrame, step);
    assert.equal(state.stage.directLights.length, 3);
    assert.equal(state.stage._floorParams, null);

    // Disposal gives the key its shadow map back and removes the step.
    disposeInspectGlobalIllumination(runtime);
    assert.equal(runtime.inspectRadiosity, null);
    assert.equal(runtime.refineFrame, null);
    assert.equal(runtime.keyLight.castShadow, true);
    assert.equal(runtime.scene.children.includes(state.stage.group), false);
    assert.doesNotThrow(() => disposeInspectGlobalIllumination(runtime));
  } finally {
    console.warn = originalWarn;
  }
});
