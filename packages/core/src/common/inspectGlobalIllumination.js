// Global illumination for the Inspect look: the theme's lighting rig (hemisphere, ambient,
// key, fill and rim lights) with a progressive radiosity bake (`lib/viewer/radiosityStage.js`)
// over it. The hemisphere and ambient lights, which three applies unoccluded, hand over to
// a lightmap baked against a sky made from them, so the inside of a part and the gap under
// it darken with soft contact occlusion; the key, fill and rim keep their direction and
// highlights but reach each surface by the fraction of their (softbox-sized) cone it can
// see, baked the same way, so none of them lights an interior it cannot reach — which is what
// made an unshadowed rig look lit from one side. The floor under the model catches the baked
// shadow instead of the key's shadow map, whose pass is skipped while the bake runs.
//
// It needs a host that steps the bake every frame (`runtime.refineFrame()` before each draw,
// which the viewer's viewport does) and a renderer with float render targets. A snapshot
// renders the rig alone, as does a software renderer.

import { collectLitMeshes, RadiosityStage } from "../lib/viewer/radiosityStage.js";

// The rig's shadow cones (radians): a tight key, broad fills.
export const INSPECT_GLOBAL_ILLUMINATION_CONES = Object.freeze({ key: 0.08, fill: 0.16 });
// The floor's shadow strength: the rig's floor shadow is a tint, not a studio's.
export const INSPECT_GLOBAL_ILLUMINATION_FLOOR_SHADOW = 0.55;

function luminance(color) {
  return color ? 0.2126 * color.r + 0.7152 * color.g + 0.0722 * color.b : 0;
}

function lightDirection(THREE, light) {
  const direction = light.getWorldPosition(new THREE.Vector3());
  if (light.target?.isObject3D) direction.sub(light.target.getWorldPosition(new THREE.Vector3()));
  return direction.lengthSq() > 1e-12 ? direction.normalize() : null;
}

/**
 * Whether `runtime` can bake: hardware WebGL2 with float render targets.
 */
export function inspectGlobalIlluminationSupported(runtime) {
  return Boolean(runtime?.renderer) && runtime.softwareRendering !== true && RadiosityStage.supported(runtime.renderer);
}

/**
 * Bake global illumination for the meshes under `root`, lit by the rig, or update the bake
 * in place. Returns the state (`runtime.inspectRadiosity`), or null when the runtime cannot
 * bake (then any earlier bake is released).
 *
 * @param {object} THREE
 * @param {object} runtime  Scene, renderer and the rig's lights.
 * @param {object} options
 * @param {import("three").Object3D} options.root  Whose meshes the bake lights.
 * @param {{ hemisphere?: object, ambient?: object, directional?: Array<{ light: object, angle?: number }> }} options.lights
 *   The rig: three light objects (read each call: visibility, colour, intensity, placement).
 * @param {{ center: number[], level: number, extent: number, albedo?: object } | null} [options.floor]
 *   The floor the bake's shadow catcher stands on (world Z up), or null for a floating model.
 */
export function applyInspectGlobalIllumination(THREE, runtime, { root, lights = {}, floor = null }) {
  if (!root?.isObject3D || !inspectGlobalIlluminationSupported(runtime)) {
    disposeInspectGlobalIllumination(runtime);
    return null;
  }
  let state = runtime.inspectRadiosity;
  if (!state) {
    const stage = new RadiosityStage(runtime.renderer, { floorShadow: INSPECT_GLOBAL_ILLUMINATION_FLOOR_SHADOW });
    state = { stage, root: null, step: () => stage.step() === true, shadowLights: new Set() };
    runtime.inspectRadiosity = state;
    runtime.scene.add(stage.group);
  }
  const { stage } = state;
  runtime.refineFrame = state.step;
  if (state.root !== root) {
    stage.setMeshesProvider(() => collectLitMeshes(root));
    state.root = root;
  }
  stage.setFloor(floor ? {
    center: new THREE.Vector3(...floor.center),
    level: floor.level,
    extent: floor.extent,
    albedo: floor.albedo?.isColor ? floor.albedo : new THREE.Color(0.5, 0.5, 0.5)
  } : null);

  const hemisphere = lights.hemisphere;
  const ambient = lights.ambient;
  const environment = runtime.scene?.environment;
  stage.setSkyFromLights({
    hemisphere: hemisphere?.isHemisphereLight && hemisphere.visible && hemisphere.intensity > 0
      ? { skyColor: hemisphere.color, groundColor: hemisphere.groundColor, intensity: hemisphere.intensity } : null,
    ambient: ambient?.isAmbientLight && ambient.visible && ambient.intensity > 0
      ? { color: ambient.color, intensity: ambient.intensity } : null,
    environment: environment?.isTexture && environment.image?.data
      ? { texture: environment, intensity: runtime.scene.environmentIntensity ?? 1 } : null
  });

  const direct = [];
  for (const entry of lights.directional || []) {
    const light = entry?.light;
    if (!light?.isDirectionalLight || !light.visible || !(light.intensity > 0)) continue;
    const direction = lightDirection(THREE, light);
    if (!direction) continue;
    direct.push({
      direction,
      angle: entry.angle ?? INSPECT_GLOBAL_ILLUMINATION_CONES.fill,
      irradiance: light.intensity * luminance(light.color)
    });
    // The bake shadows it; its shadow map is not drawn while the bake runs.
    if (light.castShadow) {
      light.castShadow = false;
      state.shadowLights.add(light);
    }
  }
  stage.setDirectLights(direct);
  runtime.invalidateShadows?.();
  return state;
}

/** Release the bake and give the rig's lights their shadow maps back. */
export function disposeInspectGlobalIllumination(runtime) {
  const state = runtime?.inspectRadiosity;
  if (!state) return;
  for (const light of state.shadowLights) {
    light.castShadow = light.visible && runtime.softwareRendering !== true;
  }
  state.shadowLights.clear();
  state.stage.group.removeFromParent();
  if (runtime.refineFrame === state.step) runtime.refineFrame = null;
  state.stage.dispose();
  runtime.inspectRadiosity = null;
  runtime.invalidateShadows?.();
}
