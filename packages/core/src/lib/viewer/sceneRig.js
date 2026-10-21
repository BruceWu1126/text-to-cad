// A scene's own lighting (`sceneContract.js`'s `rig`), on whatever stage shows it: the viewer's
// viewport (`@text-to-cad/ui` renderers/kit, `ShellViewport.jsx`) and the snapshot's headless stage
// (`common/renderMeshScene.js`) both light a scene that brings a rig with THIS, in place of the
// theme's rig, outside photographic Render. A rig is plain data:
//
//   { id,                                   what it is, for a host deciding whether it changed
//     ambient: { color, intensity },        three's AmbientLight
//     lights: [{ name, towards, color, intensity }],   DirectionalLights; `towards`, the unit
//                                           vector from a surface to the light, world frame
//     environment,                          `scene.environmentIntensity`: how strongly the stage's
//                                           environment lights the scene as a whole (0: not at
//                                           all; a material's own `envMap` is its own)
//     exposure,                             the renderer's toneMappingExposure
//     toneCurve }                           a curve of SCENE_RIG_TONE_CURVES, drawn as three's
//                                           CustomToneMapping
//
// Its lights cast no shadow. Putting it on a stage keeps what the stage's renderer had (its tone
// mapping, its exposure) and `dispose()` gives it back.

// The tone curves a rig may name: the body of three's `CustomToneMapping( vec3 color )`, after
// exposure. `clamp` is the colour times the exposure, clamped to [0, 1]: what an sRGB frame
// buffer does to a linear sum and nothing more (the OCCT raster's regular modes:
// `cadgen/engine/render/look.py`).
export const SCENE_RIG_TONE_CURVES = Object.freeze({
  clamp: "return saturate( toneMappingExposure * color );"
});

const CUSTOM_TONE_MAPPING = /vec3 CustomToneMapping\( vec3 color \) \{[^}]*\}/;

/**
 * Make three's `CustomToneMapping` the rig's curve, in the shader chunk every program that tone
 * maps includes (`ShaderChunk.tonemapping_pars_fragment`). One custom curve serves a page:
 * installing it again is a no-op, and another is refused, since programs already compiled keep
 * the curve they were compiled with.
 */
export function installSceneRigToneCurve(THREE, curve) {
  const body = SCENE_RIG_TONE_CURVES[curve];
  if (!body) throw new Error(`a scene rig's tone curve is one of ${Object.keys(SCENE_RIG_TONE_CURVES).join(", ")}, not ${JSON.stringify(curve)}`);
  const chunk = THREE.ShaderChunk.tonemapping_pars_fragment;
  const found = CUSTOM_TONE_MAPPING.exec(chunk);
  if (!found) throw new Error("three's tone mapping chunk has no CustomToneMapping to replace");
  const wanted = `vec3 CustomToneMapping( vec3 color ) { ${body} }`;
  if (found[0] === wanted) return;
  if (!/\{ return color; \}$/.test(found[0])) {
    throw new Error("three's CustomToneMapping already draws another curve: one custom tone curve serves a page");
  }
  THREE.ShaderChunk.tonemapping_pars_fragment = chunk.replace(CUSTOM_TONE_MAPPING, wanted);
}

/** Whether a value is a rig a stage can put up: its lights, its exposure and a curve it knows. */
export function isSceneRig(rig) {
  return Boolean(rig && typeof rig === "object" && Array.isArray(rig.lights)
    && Number.isFinite(Number(rig.ambient?.intensity)) && Number.isFinite(Number(rig.exposure))
    && Object.hasOwn(SCENE_RIG_TONE_CURVES, rig.toneCurve));
}

/**
 * Put a scene's rig up on a stage: its lights in a group of their own under `scene`, its exposure
 * and tone curve on `renderer`, and the scene's environment at the rig's intensity. The stage's
 * own lights are the caller's to hide.
 *
 * @param {typeof import("three")} THREE
 * @param {{ scene: import("three").Scene, renderer: { toneMapping: number, toneMappingExposure: number } }} stage
 * @param {object} rig  A scene's `rig` (`sceneContract.js`).
 * @returns {{ rig: object, group: import("three").Group, dispose(): void }}
 */
export function applySceneRig(THREE, { scene, renderer }, rig) {
  if (!isSceneRig(rig)) throw new Error("a scene rig has an ambient light, directional lights, an exposure and a known tone curve");
  installSceneRigToneCurve(THREE, rig.toneCurve);
  const group = new THREE.Group();
  group.name = "Scene rig";
  group.add(new THREE.AmbientLight(rig.ambient.color || "#ffffff", Number(rig.ambient.intensity)));
  for (const light of rig.lights) {
    const directional = new THREE.DirectionalLight(light.color || "#ffffff", Number(light.intensity));
    directional.name = String(light.name || "rig light");
    // A directional light shines from its position towards its target, the origin.
    directional.position.fromArray(light.towards).normalize();
    directional.castShadow = false;
    group.add(directional);
  }
  scene.add(group);
  const kept = { toneMapping: renderer.toneMapping, toneMappingExposure: renderer.toneMappingExposure,
    environmentIntensity: scene.environmentIntensity };
  renderer.toneMapping = THREE.CustomToneMapping;
  renderer.toneMappingExposure = Number(rig.exposure);
  scene.environmentIntensity = Math.max(Number(rig.environment) || 0, 0);
  let disposed = false;
  return {
    rig,
    group,
    dispose() {
      if (disposed) return;
      disposed = true;
      group.removeFromParent();
      for (const light of group.children) light.dispose?.();
      renderer.toneMapping = kept.toneMapping;
      renderer.toneMappingExposure = kept.toneMappingExposure;
      scene.environmentIntensity = kept.environmentIntensity;
    }
  };
}
