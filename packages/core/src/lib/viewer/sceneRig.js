// A scene's own lighting (`sceneContract.js`'s `rig`), on whatever stage shows it: the viewer's
// viewport (`@text-to-cad/ui` renderers/kit, `ShellViewport.jsx`) and the snapshot's headless stage
// (`common/renderMeshScene.js`) both light a scene that brings a rig with THIS, in place of the
// theme's rig, outside photographic Render. A rig is plain data:
//
//   { id,                                   what it is, for a host deciding whether it changed
//     ambient,                              { color, intensity }: three's AmbientLight, or null
//     lights: [{ name, towards, color, intensity }],   DirectionalLights; `towards`, the unit
//                                           vector from a surface to the light, world frame
//     environment,                          the light every surface reflects, or null:
//                                           { width, height, data, intensity, rotation }, an
//                                           equirectangular image (RGBA floats, bottom row
//                                           first) the stage turns into its PMREM, lighting the
//                                           scene at `intensity`, turned by `rotation` (Euler
//                                           radians, three's `scene.environmentRotation`)
//     exposure,                             the renderer's toneMappingExposure
//     toneCurve }                           a curve of SCENE_RIG_TONE_CURVES, drawn as three's
//                                           CustomToneMapping
//
// Its lights cast no shadow. A rig put up keeps what the stage had (its renderer's tone mapping
// and exposure, its scene's environment, intensity and rotation) and the last one taken down gives
// it back; one stage's rigs that share an environment image share its PMREM.

// The tone curves a rig may name: the body of three's `CustomToneMapping( vec3 color )`, after
// exposure. `clamp` is the colour times the exposure, clamped to [0, 1]: what an sRGB frame
// buffer does to a linear sum and nothing more (OCCT's raster: `cadgen/engine/render/look.py`).
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
  const environment = rig?.environment;
  return Boolean(rig && typeof rig === "object" && Array.isArray(rig.lights)
    && (rig.ambient == null || Number.isFinite(Number(rig.ambient.intensity)))
    && (environment == null || (environment.data?.length === environment.width * environment.height * 4
      && Number.isFinite(Number(environment.intensity))))
    && Number.isFinite(Number(rig.exposure)) && Object.hasOwn(SCENE_RIG_TONE_CURVES, rig.toneCurve));
}

// A renderer's PMREMs of environment images, by the image's pixels: shared by the rigs that light
// with the same image, released with the last of them.
const environments = new WeakMap();

function acquireEnvironment(THREE, renderer, environment) {
  let held = environments.get(renderer);
  if (!held) environments.set(renderer, held = new Map());
  let found = held.get(environment.data);
  if (!found) {
    // Half floats: what WebGL2 filters linearly without an extension, as three's HDRLoader loads.
    const texture = new THREE.DataTexture(Uint16Array.from(environment.data, THREE.DataUtils.toHalfFloat),
      environment.width, environment.height, THREE.RGBAFormat, THREE.HalfFloatType);
    texture.mapping = THREE.EquirectangularReflectionMapping;
    texture.colorSpace = THREE.LinearSRGBColorSpace;
    texture.magFilter = texture.minFilter = THREE.LinearFilter;
    texture.generateMipmaps = false;
    texture.needsUpdate = true;
    const generator = new THREE.PMREMGenerator(renderer);
    try {
      found = { target: generator.fromEquirectangular(texture), refs: 0 };
    } finally {
      generator.dispose();
      texture.dispose();
    }
    held.set(environment.data, found);
  }
  found.refs += 1;
  return found.target;
}

function releaseEnvironment(renderer, environment) {
  const held = environments.get(renderer);
  const found = held?.get(environment.data);
  if (!found) return;
  found.refs -= 1;
  if (found.refs > 0) return;
  found.target.dispose();
  held.delete(environment.data);
}

/**
 * Put a scene's rig up on a stage: its lights in a group of their own under `stage.scene`, its
 * environment as the scene's, its exposure and tone curve on `stage.renderer`. The stage's own
 * lights are the caller's to hide. `stage` is the object the stage keeps for as long as it lives
 * (the viewer's runtime, the snapshot's viewport): the rig up on it is `stage.sceneRig`, and what
 * it found there is kept beside it until the last rig comes down.
 *
 * @param {typeof import("three")} THREE
 * @param {{ scene: import("three").Scene, renderer: object }} stage
 * @param {object} rig  A scene's `rig` (`sceneContract.js`).
 * @returns {{ rig: object, group: import("three").Group, restate(): void, dispose(): void }}
 *   `restate`: put the rig's renderer and environment state back after the stage set its own.
 */
export function applySceneRig(THREE, stage, rig) {
  if (!isSceneRig(rig)) throw new Error("a scene rig has directional lights, an exposure and a known tone curve");
  installSceneRigToneCurve(THREE, rig.toneCurve);
  const { scene, renderer } = stage;
  const group = new THREE.Group();
  group.name = "Scene rig";
  if (rig.ambient) group.add(new THREE.AmbientLight(rig.ambient.color || "#ffffff", Number(rig.ambient.intensity)));
  for (const light of rig.lights) {
    const directional = new THREE.DirectionalLight(light.color || "#ffffff", Number(light.intensity));
    directional.name = String(light.name || "rig light");
    // A directional light shines from its position towards its target, the origin.
    directional.position.fromArray(light.towards).normalize();
    directional.castShadow = false;
    group.add(directional);
  }
  scene.add(group);
  stage.sceneRigKept ??= {
    toneMapping: renderer.toneMapping, toneMappingExposure: renderer.toneMappingExposure, environment: scene.environment,
    environmentIntensity: scene.environmentIntensity, environmentRotation: scene.environmentRotation.clone()
  };
  const target = rig.environment ? acquireEnvironment(THREE, renderer, rig.environment) : null;
  let disposed = false;
  const handle = {
    rig,
    group,
    restate() {
      if (disposed) return;
      renderer.toneMapping = THREE.CustomToneMapping;
      renderer.toneMappingExposure = Number(rig.exposure);
      scene.environment = target ? target.texture : null;
      scene.environmentIntensity = target ? Number(rig.environment.intensity) : 0;
      scene.environmentRotation.set(...(rig.environment?.rotation || [0, 0, 0]));
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      group.removeFromParent();
      for (const light of group.children) light.dispose?.();
      if (stage.sceneRig === handle) {
        const kept = stage.sceneRigKept;
        renderer.toneMapping = kept.toneMapping;
        renderer.toneMappingExposure = kept.toneMappingExposure;
        scene.environment = kept.environment;
        scene.environmentIntensity = kept.environmentIntensity;
        scene.environmentRotation.copy(kept.environmentRotation);
        stage.sceneRig = null;
        stage.sceneRigKept = undefined;
      }
      if (rig.environment) releaseEnvironment(renderer, rig.environment);
    }
  };
  stage.sceneRig = handle;
  handle.restate();
  return handle;
}
