/**
 * RadiosityStage — connects the meshes of a scene to the progressive
 * radiosity solver (`radiosityLightmap.js`).
 *
 * Responsibilities:
 * - Chart every lit mesh into one shared lightmap atlas
 *   (`lightmapAtlas.js`), writing a `uv1` attribute (in place when the mesh
 *   is de-indexed and its geometry is its own; through a split or cloned copy
 *   otherwise, restored on teardown).
 * - Reserve a large atlas region for the shadow-catching floor and feed its
 *   chart corner to the solver as the unoccluded-ground reference.
 * - Patch the PBR materials so they read the lightmap
 *   (`applyRadiosityPatch`) and keep their `lightMap` pointing at the
 *   solver's current ping-pong texture.
 * - Light the bake either from an environment map (`setEnvironment`) or from
 *   a sky synthesized from the scene's hemisphere and ambient lights
 *   (`setSkyFromLights`), and shadow the scene's direct lights
 *   (`setDirectLights`): the display keeps those lights, scaled per texel by
 *   their baked visibility, and the floor darkens by their share.
 * - Detect changes that invalidate the bake every frame: visibility,
 *   transforms and occluder-ness reset the accumulation; added/removed meshes
 *   or swapped geometries trigger a rebuild. Colour changes are picked up
 *   progressively (the bounce reads albedo live), so highlights do not
 *   restart the bake.
 *
 * The stage is driven from the render loop: `step()` accumulates one bounce
 * per frame and returns true while more frames are needed.
 */

import * as THREE from "three";
import { chartMesh, packCharts, writeAtlasUv } from "./lightmapAtlas.js";
import {
  applyRadiosityPatch,
  createRadiosityDisplayUniforms,
  normalizeRadiosityDirections,
  normalizeRadiosityQuality,
  RADIOSITY_LIGHT_ANGLE_RANGE,
  RADIOSITY_MAX_DIRECT_LIGHTS,
  RadiosityLightmap,
  radiositySupported,
  removeRadiosityPatch
} from "./radiosityLightmap.js";
import { RadiosityFloor } from "./radiosityFloor.js";

/** Equirectangular size of the sky synthesized from the scene's lights. */
const SKY_WIDTH = 128;
const SKY_HEIGHT = 64;

/** Fraction of the atlas side reserved for the floor's chart. */
const FLOOR_ATLAS_FRACTION = 0.5;

/** Chart gutter (texels) on each side. */
const PADDING = 2;

/** Pre-blur (PMREM roughness) applied to the environment for the bake. */
const ENVIRONMENT_BLUR = 0.25;

const UP = new THREE.Vector3(0, 0, 1);

function luminance(color) {
  return 0.2126 * color.r + 0.7152 * color.g + 0.0722 * color.b;
}

/**
 * Radiance of an equirectangular float texture (three's convention: +Y at the
 * top row, +X at the centre column) along a world direction, nearest texel.
 */
function equirectRadiance(image, dir, out) {
  const { width, height, data } = image;
  const u = Math.atan2(dir.z, dir.x) / (2 * Math.PI) + 0.5;
  const v = Math.asin(Math.min(Math.max(dir.y, -1), 1)) / Math.PI + 0.5;
  const x = Math.min(width - 1, Math.max(0, Math.floor(u * width)));
  const y = Math.min(height - 1, Math.max(0, Math.floor(v * height)));
  const offset = (y * width + x) * 4;
  return out.setRGB(data[offset], data[offset + 1], data[offset + 2]);
}

/**
 * The radiance field (linear RGB per world direction) whose diffuse
 * irradiance equals what three's hemisphere and ambient lights give a surface
 * of any orientation: a hemisphere light's irradiance is
 * `mix(ground, sky, 0.5 + 0.5 n·up) × intensity`, which the linear-in-`d·up`
 * sky `(a + b d·up) / π` reproduces exactly for `a = (sky + ground) / 2` and
 * `b = 3 (sky − ground) / 4`; an ambient light is a uniform `colour ×
 * intensity / π`; an equirectangular environment adds its own radiance
 * times its intensity. Negative radiance (a very dark ground under a bright
 * sky) is clamped, so such a floor reads a touch lighter than the light.
 *
 * @param {object} lights
 * @param {{ skyColor: THREE.Color, groundColor: THREE.Color, intensity: number }} [lights.hemisphere]
 * @param {{ color: THREE.Color, intensity: number }} [lights.ambient]
 * @param {{ texture: THREE.Texture, intensity: number }} [lights.environment]
 * @param {THREE.Vector3} [up]
 * @returns {(dir: THREE.Vector3, out: THREE.Color) => THREE.Color}
 */
export function skyRadianceFromLights({ hemisphere = null, ambient = null, environment = null } = {}, up = UP) {
  const a = new THREE.Color(0, 0, 0);
  const b = new THREE.Color(0, 0, 0);
  if (hemisphere && hemisphere.intensity > 0) {
    const sky = new THREE.Color(hemisphere.skyColor).multiplyScalar(hemisphere.intensity);
    const ground = new THREE.Color(hemisphere.groundColor).multiplyScalar(hemisphere.intensity);
    a.setRGB((sky.r + ground.r) / 2, (sky.g + ground.g) / 2, (sky.b + ground.b) / 2);
    b.setRGB((sky.r - ground.r) * 0.75, (sky.g - ground.g) * 0.75, (sky.b - ground.b) * 0.75);
  }
  if (ambient && ambient.intensity > 0) {
    a.add(new THREE.Color(ambient.color).multiplyScalar(ambient.intensity));
  }
  const envImage = environment?.texture?.image;
  const envData = envImage?.data instanceof Float32Array && envImage.width > 0 && envImage.height > 0 ? envImage : null;
  const envIntensity = envData ? Math.max(Number(environment.intensity) || 0, 0) : 0;
  const envColor = new THREE.Color();
  return (dir, out) => {
    const t = dir.dot(up);
    out.setRGB(
      Math.max(a.r + b.r * t, 0) / Math.PI,
      Math.max(a.g + b.g * t, 0) / Math.PI,
      Math.max(a.b + b.b * t, 0) / Math.PI
    );
    if (envIntensity > 0) out.add(equirectRadiance(envData, dir, envColor).multiplyScalar(envIntensity));
    return out;
  };
}

/** Bake a radiance field into an equirectangular float texture (three's convention). */
function createSkyTexture(radiance, width = SKY_WIDTH, height = SKY_HEIGHT) {
  const data = new Float32Array(width * height * 4);
  const dir = new THREE.Vector3();
  const color = new THREE.Color();
  for (let y = 0; y < height; y++) {
    const v = (y + 0.5) / height;
    const polar = (v - 0.5) * Math.PI;
    const ring = Math.cos(polar);
    for (let x = 0; x < width; x++) {
      const u = (x + 0.5) / width;
      const azimuth = (u - 0.5) * 2 * Math.PI;
      dir.set(ring * Math.cos(azimuth), Math.sin(polar), ring * Math.sin(azimuth));
      radiance(dir, color);
      const offset = (y * width + x) * 4;
      data[offset] = color.r;
      data[offset + 1] = color.g;
      data[offset + 2] = color.b;
      data[offset + 3] = 1;
    }
  }
  const texture = new THREE.DataTexture(data, width, height, THREE.RGBAFormat, THREE.FloatType);
  texture.name = "radiositySky";
  texture.mapping = THREE.EquirectangularReflectionMapping;
  texture.colorSpace = THREE.LinearSRGBColorSpace;
  texture.needsUpdate = true;
  return texture;
}

function skyKey({ hemisphere = null, ambient = null, environment = null } = {}) {
  const colorKey = (color) => (color ? new THREE.Color(color).getHex() : -1);
  return [
    hemisphere ? `${colorKey(hemisphere.skyColor)}/${colorKey(hemisphere.groundColor)}/${hemisphere.intensity}` : "",
    ambient ? `${colorKey(ambient.color)}/${ambient.intensity}` : "",
    environment?.texture ? `${environment.texture.uuid}/${environment.texture.version}/${environment.intensity}` : ""
  ].join("|");
}

function worldVisible(obj) {
  let o = obj;
  while (o) {
    if (!o.visible) return false;
    o = o.parent;
  }
  return true;
}

/** Surfaces that should not block light (glass, mostly transparent). */
function isOccluder(material) {
  if (!material) return false;
  if (material.isMeshPhysicalMaterial && material.transmission > 0.5) return false;
  if (material.transparent && material.opacity < 0.5) return false;
  return true;
}

/** Lit PBR materials get the lightmap; unlit/wireframe materials do not. */
function isLitMaterial(material) {
  return !!material && (material.isMeshStandardMaterial || material.isMeshPhysicalMaterial) === true;
}

/**
 * The lit meshes under `root`, in traversal order: every Mesh with one lit
 * material (multi-material meshes and instanced meshes are left alone).
 */
export function collectLitMeshes(root, out = []) {
  if (!root?.isObject3D) return out;
  root.traverse((object) => {
    if (object.isMesh && !object.isInstancedMesh && !Array.isArray(object.material) && isLitMaterial(object.material)) {
      out.push(object);
    }
  });
  return out;
}

/** Diffuse albedo of a display material for the bounce (linear). */
function materialAlbedo(material, out) {
  if (material?.isMeshStandardMaterial) {
    out.copy(material.color).multiplyScalar(1 - material.metalness * 0.9);
    if (material.map) out.multiplyScalar(0.7);
    return out;
  }
  if (material?.color?.isColor) return out.copy(material.color);
  return out.setRGB(0.5, 0.5, 0.5);
}

function materialEmissive(material, out) {
  if (material?.isMeshStandardMaterial && material.emissive) {
    return out.copy(material.emissive).multiplyScalar(material.emissiveIntensity);
  }
  return out.setRGB(0, 0, 0);
}

/** Build a vertex-split copy of `src` following the chart result. */
function buildSplitGeometry(src, charted, uv1) {
  const remap = charted.remap;
  const g = new THREE.BufferGeometry();
  for (const name of Object.keys(src.attributes)) {
    const attr = src.attributes[name];
    if (!attr?.isBufferAttribute) continue;
    const itemSize = attr.itemSize;
    const SrcArray = attr.array.constructor;
    const arr = new SrcArray(charted.vertexCount * itemSize);
    const srcArr = attr.array;
    for (let v = 0; v < charted.vertexCount; v++) {
      const o = remap[v] * itemSize;
      const d = v * itemSize;
      for (let k = 0; k < itemSize; k++) arr[d + k] = srcArr[o + k];
    }
    g.setAttribute(name, new THREE.BufferAttribute(arr, itemSize, attr.normalized));
  }
  g.setAttribute("uv1", new THREE.BufferAttribute(uv1, 2));
  g.setIndex(new THREE.BufferAttribute(charted.index, 1));
  g.userData = { ...src.userData, cadSceneCachedGeometry: false };
  g.computeBoundingBox();
  g.computeBoundingSphere();
  return g;
}

/** Order-independent 32-bit hash helpers for change detection. */
function hashInt(h, v) {
  return Math.imul(h ^ (v | 0), 16777619) >>> 0;
}
function hashFloat(h, v) {
  return hashInt(h, Math.round(v * 4096));
}

export const DEFAULT_RADIOSITY_STAGE_SETTINGS = Object.freeze({
  quality: "medium",
  directions: 16,
  maxSamples: 4000,
  shadow: 1,
  // The ground's shadow strength; null follows `shadow`.
  floorShadow: null,
  // Direct environment light only by default; the Bounce control adds
  // indirect light from the model and floor.
  bounce: 0,
  envSampling: "importance",
  selfBias: 1.5
});

export function normalizeRadiosityStageSettings(settings = {}) {
  const source = settings && typeof settings === "object" ? settings : {};
  const number = (value, fallback, min, max) => {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? Math.min(Math.max(numeric, min), max) : fallback;
  };
  return {
    quality: normalizeRadiosityQuality(source.quality, DEFAULT_RADIOSITY_STAGE_SETTINGS.quality),
    directions: normalizeRadiosityDirections(source.directions, DEFAULT_RADIOSITY_STAGE_SETTINGS.directions),
    maxSamples: Math.floor(number(source.maxSamples, DEFAULT_RADIOSITY_STAGE_SETTINGS.maxSamples, 0, 100000)),
    shadow: number(source.shadow, DEFAULT_RADIOSITY_STAGE_SETTINGS.shadow, 0, 1),
    floorShadow: source.floorShadow == null ? null : number(source.floorShadow, DEFAULT_RADIOSITY_STAGE_SETTINGS.shadow, 0, 1),
    bounce: number(source.bounce, DEFAULT_RADIOSITY_STAGE_SETTINGS.bounce, 0, 2),
    envSampling: source.envSampling === "uniform" ? "uniform" : "importance",
    selfBias: number(source.selfBias, DEFAULT_RADIOSITY_STAGE_SETTINGS.selfBias, 0, 8)
  };
}

export class RadiosityStage {
  /** Whether `renderer` can run the solver (WebGL2 + float render targets). */
  static supported(renderer) {
    return radiositySupported(renderer);
  }

  /**
   * @param {THREE.WebGLRenderer} renderer
   * @param {object} [settings] - see DEFAULT_RADIOSITY_STAGE_SETTINGS
   */
  constructor(renderer, settings = {}) {
    this._renderer = renderer;
    this._settings = normalizeRadiosityStageSettings(settings);
    this._displayUniforms = createRadiosityDisplayUniforms();
    this._displayUniforms.uRadShadow.value = this._settings.shadow;
    this._solver = null;
    this._floor = new RadiosityFloor();
    this._floorParams = null;
    this._meshesProvider = () => [];
    this._entries = [];
    this._patched = new Set();
    this._needsBuild = true;
    this._built = false;
    this._stateHash = 0;
    this._sceneHash = 0;
    this._info = null;
    this._disposed = false;
    this._environment = { texture: null, rotation: new THREE.Euler(), intensity: 1 };
    /** Direct lights: `{ direction, angle, irradiance }` (world, toward the light). */
    this._lights = [];
    this._sky = null;
    this._pmremGenerator = null;
    this._createSolver();
  }

  /** Shadow-catcher group to add to the scene's stage. */
  get group() {
    return this._floor.group;
  }

  get settings() {
    return this._settings;
  }

  /** Atlas/packing diagnostics of the last build (null before the first). */
  get info() {
    return this._info;
  }

  /** Accumulated directions since the last reset. */
  get samples() {
    return this._solver ? this._solver.samples : 0;
  }

  /** 0..1 progress toward the sample budget (1 when unlimited and running). */
  get progress() {
    if (!this._solver) return 0;
    const max = this._solver.maxSamples;
    if (max <= 0) return this._solver.samples > 0 ? 1 : 0;
    return Math.min(1, this._solver.samples / max);
  }

  get converged() {
    return !!this._solver?.converged;
  }

  /** True when the last build produced at least one lit surface. */
  get active() {
    return this._built && this._entries.length > 0;
  }

  /**
   * Apply new settings. Quality and direction changes recreate the solver
   * (new atlas size) and schedule a rebuild; the others apply in place.
   */
  configure(settings = {}) {
    const next = normalizeRadiosityStageSettings({ ...this._settings, ...settings });
    const previous = this._settings;
    this._settings = next;
    this._displayUniforms.uRadShadow.value = next.shadow;
    this._floor.setShadowIntensity(next.floorShadow ?? next.shadow);
    if (next.quality !== previous.quality || next.directions !== previous.directions) {
      this._createSolver();
      return;
    }
    const solver = this._solver;
    solver.maxSamples = next.maxSamples;
    if (next.bounce !== previous.bounce) solver.setBounce(next.bounce);
    if (next.envSampling !== previous.envSampling) solver.setEnvSampling(next.envSampling);
    if (next.selfBias !== previous.selfBias) solver.setSelfBias(next.selfBias);
  }

  /**
   * Function returning the live meshes to bake, each frame (`collectLitMeshes`
   * of a scene root is the usual one). Meshes whose material is not a lit PBR
   * material are ignored.
   */
  setMeshesProvider(provider) {
    this._meshesProvider = typeof provider === "function" ? provider : () => [];
    this._needsBuild = true;
  }

  /**
   * Configure the ground plane, or null for a floating model (no catcher,
   * no bounce from the ground).
   *
   * @param {object|null} params
   * @param {THREE.Vector3} params.center - World centre of the model bounds
   * @param {number} params.level - Floor height (world Z)
   * @param {number} params.extent - Max extent of the model bounds
   * @param {THREE.Color} [params.albedo] - Floor colour for the bounce (linear)
   */
  setFloor(params) {
    const previous = this._floorParams;
    if (!params) {
      if (previous) {
        this._floorParams = null;
        this._needsBuild = true;
      }
      return;
    }
    const next = {
      center: params.center.clone(),
      level: Number(params.level) || 0,
      extent: Math.max(Number(params.extent) || 0, 1e-6),
      albedo: params.albedo?.isColor ? params.albedo.clone() : new THREE.Color(0.35, 0.35, 0.35)
    };
    const same = previous &&
      previous.center.equals(next.center) &&
      previous.level === next.level &&
      previous.extent === next.extent;
    this._floorParams = next;
    this._floor.setAlbedo(next.albedo);
    if (!same) {
      this._needsBuild = true;
    }
  }

  /**
   * Set the light source: a PMREM environment texture (cube-UV), the rotation
   * the scene applies to it (`scene.environmentRotation`) and the intensity
   * the display materials scale the lightmap by when they carry no
   * environment map of their own.
   */
  setEnvironment(texture, rotation = null, intensity = 1) {
    const env = this._environment;
    const nextTexture = texture || null;
    const nextIntensity = Math.max(Number(intensity) || 0, 0);
    const nextRotation = rotation?.isEuler ? rotation : null;
    const sameRotation = nextRotation
      ? env.rotation.equals(nextRotation)
      : env.rotation.x === 0 && env.rotation.y === 0 && env.rotation.z === 0;
    if (env.texture === nextTexture && env.intensity === nextIntensity && sameRotation) {
      return;
    }
    env.texture = nextTexture;
    if (nextRotation) env.rotation.copy(nextRotation);
    else env.rotation.set(0, 0, 0);
    env.intensity = nextIntensity;
    this._displayUniforms.uRadIntensity.value = env.intensity;
    this._displayUniforms.uRadReplaceAmbient.value = 0;
    this._releaseSky();
    this._solver.setEnvironment(env.texture, env.rotation, ENVIRONMENT_BLUR);
  }

  /**
   * Light the bake from a sky synthesized from the scene's hemisphere and
   * ambient lights (and an equirectangular environment, when the scene has
   * one), see `skyRadianceFromLights`. The display materials then hand those
   * lights over to the lightmap as it fades in: it carries their occluded
   * version. `null` for every light bakes against darkness (the direct
   * lights alone).
   *
   * @param {Parameters<typeof skyRadianceFromLights>[0]} lights
   */
  setSkyFromLights(lights = {}) {
    const key = skyKey(lights);
    if (this._sky?.key === key) return;
    this._releaseSky();
    const equirect = createSkyTexture(skyRadianceFromLights(lights, UP));
    let target = null;
    try {
      if (!this._pmremGenerator) this._pmremGenerator = new THREE.PMREMGenerator(this._renderer);
      target = this._pmremGenerator.fromEquirectangular(equirect);
    } catch (error) {
      // Without the sky the bake sees darkness: the direct lights alone are shadowed.
      console.warn("radiosity: sky from lights failed; baking direct lights only", error);
      target = null;
    } finally {
      equirect.dispose();
    }
    this._sky = { key, target };
    const env = this._environment;
    env.texture = target?.texture || null;
    env.rotation.set(0, 0, 0);
    env.intensity = 1;
    this._displayUniforms.uRadIntensity.value = 1;
    this._displayUniforms.uRadReplaceAmbient.value = 1;
    // Already smooth: no pre-blur.
    this._solver.setEnvironment(env.texture, env.rotation, 0);
  }

  /** Whether the bake is lit by a sky made from the scene's lights. */
  get skyFromLights() {
    return this._sky !== null;
  }

  /**
   * The direct lights the bake shadows (at most `RADIOSITY_MAX_DIRECT_LIGHTS`),
   * `{ direction, angle, irradiance }` each: `direction` the world direction
   * toward the light, `angle` its cone half-angle in radians (softness) and
   * `irradiance` (luminance, three's units) what a surface facing it receives,
   * which sets its share of the floor's shadow. The display materials keep
   * these lights and scale them per texel by the baked visibility.
   */
  setDirectLights(lights = []) {
    const [minAngle, maxAngle] = RADIOSITY_LIGHT_ANGLE_RANGE;
    const next = [];
    for (const light of Array.isArray(lights) ? lights : []) {
      if (next.length >= RADIOSITY_MAX_DIRECT_LIGHTS) break;
      if (!light?.direction?.isVector3 || light.direction.lengthSq() < 1e-12) continue;
      next.push({
        direction: light.direction.clone().normalize(),
        angle: Math.min(Math.max(Number(light.angle) || minAngle, minAngle), maxAngle),
        irradiance: Math.max(Number(light.irradiance) || 0, 0)
      });
    }
    const same = next.length === this._lights.length && next.every((light, k) =>
      light.direction.distanceToSquared(this._lights[k].direction) < 1e-10
      && light.angle === this._lights[k].angle && light.irradiance === this._lights[k].irradiance);
    if (same) return;
    this._lights = next;
    this._solver.setDirectLights(next);
    const u = this._displayUniforms;
    u.uRadLightCount.value = next.length;
    next.forEach((light, k) => u.uRadLightDir.value[k].copy(light.direction));
  }

  /** The direct lights being shadowed. */
  get directLights() {
    return this._lights;
  }

  /** PMREM texture lighting the bake. */
  get environmentTexture() {
    return this._environment.texture;
  }

  /**
   * Irradiance an unoccluded up-facing surface receives from
   * `environmentTexture` at unit intensity (three's light units), or 0
   * without an environment.
   */
  get environmentUpIrradiance() {
    return this._solver?.environmentUpIrradiance || 0;
  }

  /** Restart the accumulation (e.g. after a context restore). */
  reset() {
    this._solver?.reset();
  }

  /** Force the next step to re-chart the scene. */
  invalidate() {
    this._needsBuild = true;
  }

  /**
   * Advance the bake by one bounce. Call once per rendered frame before
   * `renderer.render`. Returns true while the solution is still refining.
   */
  step() {
    if (this._disposed) return false;
    const meshes = this._meshesProvider() || [];
    if (this._needsBuild || this._computeSceneHash(meshes) !== this._sceneHash) {
      this._build(meshes);
    }
    if (!this._built || this._entries.length === 0) {
      return false;
    }
    const state = this._computeStateHash();
    if (state !== this._stateHash) {
      this._stateHash = state;
      this._solver.reset();
    }
    const bounced = this._solver.bounce();
    this._syncLightmap();
    return bounced && !this._solver.converged;
  }

  /** Restore geometries and materials; keeps the solver for a later rebuild. */
  teardown() {
    this._restoreGeometries();
    for (const material of this._patched) removeRadiosityPatch(material);
    this._patched.clear();
    this._floor.setLightmap(null, 0);
    this._floor.group.visible = false;
    this._built = false;
    this._needsBuild = true;
    this._info = null;
  }

  dispose() {
    if (this._disposed) return;
    this.teardown();
    this._disposed = true;
    this._solver?.dispose();
    this._solver = null;
    this._floor.dispose();
    this._releaseSky();
    this._pmremGenerator?.dispose();
    this._pmremGenerator = null;
  }

  _releaseSky() {
    if (!this._sky) return;
    const { target } = this._sky;
    this._sky = null;
    if (target && this._environment.texture === target.texture) this._environment.texture = null;
    target?.dispose();
  }

  // -------------------------------------------------------------------------

  _createSolver() {
    const previous = this._solver;
    const s = this._settings;
    this._solver = new RadiosityLightmap(this._renderer, s.quality, {
      directions: s.directions,
      maxSamples: s.maxSamples,
      selfBias: s.selfBias,
      bounce: s.bounce,
      envSampling: s.envSampling,
      displayUniforms: this._displayUniforms
    });
    this._solver.setEnvironment(this._environment.texture, this._environment.rotation, this._sky ? 0 : ENVIRONMENT_BLUR);
    this._solver.setDirectLights(this._lights);
    previous?.dispose();
    this._needsBuild = true;
  }

  _build(meshes) {
    this._restoreGeometries();
    this._needsBuild = false;
    this._built = false;
    this._info = null;
    this._sceneHash = this._computeSceneHash(meshes);

    const lit = meshes.filter((mesh) => mesh?.isMesh && mesh.geometry && isLitMaterial(mesh.material));
    if (!lit.length) {
      this._floor.group.visible = false;
      this._solver.setObjects([], null, UP, null);
      return;
    }
    // Fresh world matrices: the bake mirrors them every bounce, but the
    // charting below sizes charts from them right now.
    let root = lit[0];
    while (root.parent) root = root.parent;
    root.updateMatrixWorld(true);

    const resolution = this._solver.resolution;
    const charted = [];
    const allCharts = [];
    let uvFaces = 0;
    let planarFaces = 0;
    for (const mesh of lit) {
      if (!isOccluder(mesh.material)) continue;
      const geometry = mesh.geometry;
      const position = geometry.getAttribute("position");
      if (!position?.isBufferAttribute || position.count === 0) continue;
      const uvAttr = geometry.getAttribute("uv");
      const faceIds = mesh.userData?.faceIds;
      let result;
      try {
        result = chartMesh({
          positions: position.array,
          index: geometry.index ? geometry.index.array : null,
          uv: uvAttr?.isBufferAttribute ? uvAttr.array : null,
          faceIds: faceIds instanceof Uint32Array ? faceIds : null,
          trianglesPerFace: geometry.userData?.trianglesPerFace || null,
          matrixWorld: mesh.matrixWorld.elements
        });
      } catch (error) {
        console.warn("radiosity: charting failed for a mesh, skipped", error);
        continue;
      }
      charted.push({ mesh, result });
      for (const c of result.charts) allCharts.push(c);
      uvFaces += result.uvFaces;
      planarFaces += result.planarFaces;
    }

    let floorChart = null;
    const floorParams = this._floorParams;
    if (floorParams) {
      const side = Math.floor(resolution * FLOOR_ATLAS_FRACTION) - 2 * PADDING;
      floorChart = { w: side, h: side, fixed: true, x: 0, y: 0, pw: 0, ph: 0 };
      allCharts.push(floorChart);
    }

    let pack = packCharts(allCharts, resolution, PADDING);
    if (!pack) pack = packCharts(allCharts, resolution, 1);
    if (!pack) {
      console.warn(`radiosity: ${allCharts.length} charts do not fit a ${resolution}² lightmap; disabled`);
      this._floor.group.visible = false;
      this._solver.setObjects([], null, UP, null);
      return;
    }

    let split = 0;
    const objects = [];
    // A geometry shared by several meshes (instances of one part) gets one
    // chart per mesh, so every mesh after the first takes its own copy.
    const claimed = new Set();
    for (const { mesh, result } of charted) {
      const original = mesh.geometry;
      const uv1 = writeAtlasUv(result, resolution);
      let swapped = null;
      let geometry = original;
      if (result.remap) {
        swapped = buildSplitGeometry(original, result, uv1);
        mesh.geometry = swapped;
        geometry = swapped;
        split++;
      } else if (claimed.has(original)) {
        swapped = original.clone();
        swapped.userData = { ...original.userData, cadSceneCachedGeometry: false };
        swapped.setAttribute("uv1", new THREE.BufferAttribute(uv1, 2));
        mesh.geometry = swapped;
        geometry = swapped;
        split++;
      } else {
        original.setAttribute("uv1", new THREE.BufferAttribute(uv1, 2));
      }
      claimed.add(original);
      const entry = {
        mesh,
        originalGeometry: original,
        swapped,
        albedo: new THREE.Color(),
        emissive: new THREE.Color()
      };
      this._entries.push(entry);
      objects.push({
        mesh,
        geometry,
        albedo: () => materialAlbedo(mesh.material, entry.albedo),
        emissive: () => materialEmissive(mesh.material, entry.emissive)
      });
    }

    let floorObject = null;
    let floorRefUv = null;
    if (floorParams && floorChart) {
      this._floor.configure(floorParams.center, floorParams.level, floorParams.extent);
      this._floor.setAlbedo(floorParams.albedo);
      this._floor.setShadowIntensity(this._settings.floorShadow ?? this._settings.shadow);
      this._floor.setAtlasRect(floorChart.x, floorChart.y, floorChart.pw, floorChart.ph, resolution);
      floorObject = {
        mesh: this._floor.mesh,
        geometry: this._floor.geometry,
        albedo: () => this._floor.albedo(),
        emissive: () => this._floor.emissive()
      };
      // Corner texel of the floor chart: far from the model → unoccluded.
      floorRefUv = new THREE.Vector2((floorChart.x + 0.5) / resolution, (floorChart.y + 0.5) / resolution);
    } else {
      this._floor.group.visible = false;
    }

    this._solver.setObjects(objects, floorObject, UP, floorRefUv);
    for (const mesh of lit) this._patchMaterial(mesh.material);
    this._stateHash = this._computeStateHash();
    // Swapped (split or cloned) geometries changed the ids the hash reads;
    // take the hash after the swaps so the next step does not rebuild again.
    this._sceneHash = this._computeSceneHash(meshes);
    this._built = true;
    this._info = {
      objects: objects.length,
      charts: allCharts.length,
      uvFaces,
      planarFaces,
      split,
      fill: pack.fill,
      texelsPerUnit: pack.scale
    };
  }

  _patchMaterial(material) {
    applyRadiosityPatch(material, this._solver.texture, this._displayUniforms);
    this._patched.add(material);
  }

  /** Point every patched material at the solver's current lightmap. */
  _syncLightmap() {
    const texture = this._solver.texture;
    const weight = this._displayUniforms.uRadWeight.value;
    for (const entry of this._entries) {
      const material = entry.mesh.material;
      if (isLitMaterial(material)) this._patchMaterial(material);
    }
    for (const material of this._patched) {
      if (material.lightMap !== texture) material.lightMap = texture;
    }
    this._floor.setLightmap(texture, weight);
    const lightTexture = this._solver.lightTexture;
    this._displayUniforms.uRadLightMap.value = lightTexture;
    this._floor.setDirectLightmap(lightTexture, lightTexture ? this._floorLightShares() : null);
  }

  /**
   * Each direct light's share of the light an unoccluded, up-facing patch of
   * ground receives, against the environment's.
   */
  _floorLightShares() {
    const ups = this._lights.map((light) => light.irradiance * Math.max(light.direction.dot(UP), 0));
    const total = this.environmentUpIrradiance * this._environment.intensity + ups.reduce((sum, value) => sum + value, 0);
    return total > 1e-9 ? ups.map((value) => value / total) : ups.map(() => 0);
  }

  _restoreGeometries() {
    for (const entry of this._entries) {
      const { mesh, originalGeometry, swapped } = entry;
      if (swapped) {
        if (mesh.geometry === swapped) mesh.geometry = originalGeometry;
        swapped.dispose();
      } else if (originalGeometry.hasAttribute("uv1")) {
        originalGeometry.deleteAttribute("uv1");
      }
    }
    this._entries = [];
  }

  _computeStateHash() {
    let h = 2166136261;
    for (const entry of this._entries) {
      const mesh = entry.mesh;
      h = hashInt(h, worldVisible(mesh) ? 1 : 0);
      const m = mesh.matrixWorld.elements;
      for (let i = 0; i < 16; i++) h = hashFloat(h, m[i]);
      h = hashInt(h, isOccluder(mesh.material) ? 1 : 0);
    }
    if (this._floorParams) {
      h = hashFloat(h, this._floorParams.level);
    }
    return h;
  }

  _computeSceneHash(meshes) {
    let h = 2166136261;
    h = hashInt(h, meshes.length);
    for (const mesh of meshes) {
      if (!mesh?.isMesh) continue;
      h = hashInt(h, mesh.id);
      const geometry = mesh.geometry;
      h = hashInt(h, geometry?.id ?? 0);
      // A geometry re-tessellated in place (same object, new attributes)
      // needs new charts as much as a new geometry does.
      const position = geometry?.getAttribute?.("position");
      h = hashInt(h, position ? position.count : -1);
      h = hashInt(h, position ? position.version : -1);
      h = hashInt(h, geometry?.index ? geometry.index.count : -1);
      h = hashInt(h, geometry?.index ? geometry.index.version : -1);
      h = hashInt(h, isLitMaterial(mesh.material) ? 1 : 0);
    }
    h = hashInt(h, this._floorParams ? 1 : 0);
    return h;
  }
}
