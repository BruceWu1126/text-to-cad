import * as THREE from "three";

import { clamp, finiteOr } from "./numbers.js";
import { DEFAULT_RENDER_LIGHTING } from "./sceneSettings.js";
import {
  PHOTOGRAPHIC_STUDIO_BOUNCE_DIRECTION,
  PHOTOGRAPHIC_STUDIO_CARD_DISTANCE,
  PHOTOGRAPHIC_STUDIO_CARD_RADIANCE,
  PHOTOGRAPHIC_STUDIO_FILL_DIRECTION,
  PHOTOGRAPHIC_STUDIO_KEY_CARD,
  PHOTOGRAPHIC_STUDIO_KEY_DIRECTION,
  PHOTOGRAPHIC_STUDIO_PANELS,
  PHOTOGRAPHIC_STUDIO_ROOM_RADIANCE
} from "./photographicStudioRig.js";

export const PROCEDURAL_STUDIO_ENVIRONMENT_ID = "photographic-softbox";

function proceduralEnvironmentSize(value) {
  const numeric = finiteOr(value, 256);
  return Math.min(Math.max(2 ** Math.round(Math.log2(Math.max(numeric, 1))), 64), 1024);
}

function lightingConfiguration(configuration = {}) {
  const lighting = configuration?.lighting || {};
  return {
    size: clamp(finiteOr(lighting.size, DEFAULT_RENDER_LIGHTING.size), 0.25, 3),
    fill: clamp(finiteOr(lighting.fill, DEFAULT_RENDER_LIGHTING.fill), 0, 1)
  };
}

export function environmentResourceIdentity(configuration = {}, { size = 256 } = {}) {
  const lighting = lightingConfiguration(configuration);
  return `${PROCEDURAL_STUDIO_ENVIRONMENT_ID}:${JSON.stringify([
    lighting.size,
    lighting.fill,
    proceduralEnvironmentSize(size)
  ])}`;
}

function card(scene, {
  name,
  direction,
  width,
  height,
  intensity,
  up = null,
  falloff = null
}) {
  if (!(intensity > 0)) return null;
  // A soft source with `falloff` is brightest along its centre and falls toward
  // its edges, so polished metal shows a gradient across it, not a flat rectangle.
  const [across, along] = falloff || [0, 0];
  const segments = falloff ? 12 : 1;
  const geometry = new THREE.PlaneGeometry(width, height, segments, segments);
  const material = new THREE.MeshBasicMaterial({
    color: new THREE.Color(intensity, intensity, intensity),
    side: THREE.DoubleSide,
    vertexColors: Boolean(falloff),
    toneMapped: false
  });
  if (falloff) {
    const positions = geometry.getAttribute("position");
    const colors = new Float32Array(positions.count * 3);
    for (let index = 0; index < positions.count; index += 1) {
      const u = (2 * positions.getX(index)) / width;
      const v = (2 * positions.getY(index)) / height;
      colors.fill((1 - across * u * u) * (1 - along * v * v), index * 3, index * 3 + 3);
    }
    geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  }
  const mesh = new THREE.Mesh(geometry, material);
  mesh.name = name;
  mesh.position.copy(direction).normalize().multiplyScalar(PHOTOGRAPHIC_STUDIO_CARD_DISTANCE);
  if (up) mesh.up.set(...up);
  mesh.lookAt(0, 0, 0);
  mesh.updateMatrixWorld(true);
  scene.add(mesh);
  return mesh;
}

const ROOM_RADIUS = 15;

/**
 * The enclosure the sources hang in: a seamless studio sweep, one radiance per
 * elevation. It eases from the darkest band at the horizon up to the ceiling
 * and down to the floor, so polished metal reflects a room with a horizon line,
 * and faces turned away from every source still receive a soft fill.
 */
function addStudioSweep(scene) {
  const { zenith, horizon, nadir } = PHOTOGRAPHIC_STUDIO_ROOM_RADIANCE;
  // Three's sphere is Y-up: read each vertex's elevation, then turn the
  // sphere onto the studio's Z axis.
  const geometry = new THREE.SphereGeometry(ROOM_RADIUS, 64, 32);
  const positions = geometry.getAttribute("position");
  const colors = new Float32Array(positions.count * 3);
  for (let index = 0; index < positions.count; index += 1) {
    const elevation = positions.getY(index) / ROOM_RADIUS;
    const radiance = horizon
      + ((elevation >= 0 ? zenith : nadir) - horizon) * Math.sqrt(Math.abs(elevation));
    colors.fill(radiance, index * 3, index * 3 + 3);
  }
  geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  geometry.rotateX(Math.PI / 2);
  const room = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({
    vertexColors: true,
    side: THREE.BackSide,
    toneMapped: false
  }));
  room.name = "studio-room";
  scene.add(room);
  return room;
}

/**
 * Build the normalized HDR source scene used by PMREM: a bright neutral key
 * card, a rear fill card, a side bounce and the studio's fixed soft sources (an
 * overhead softbox and two strip boxes) inside a studio sweep. Their directions
 * match the photographic direct-light rig; scene.environmentRotation rotates them
 * together at runtime without rebuilding this resource.
 */
export function createStudioEnvironmentScene(configuration = {}) {
  const lighting = lightingConfiguration(configuration);
  // Emissive radiance is inversely proportional to source area, so changing the
  // apparent softbox size changes highlight width without changing total flux.
  const area = lighting.size * lighting.size;
  const cardRadiance = PHOTOGRAPHIC_STUDIO_CARD_RADIANCE / area;
  const scene = new THREE.Scene();
  scene.name = "cadgen-photographic-environment";
  scene.background = new THREE.Color().setScalar(PHOTOGRAPHIC_STUDIO_ROOM_RADIANCE.horizon);
  addStudioSweep(scene);

  card(scene, {
    name: "studio-key-card",
    direction: new THREE.Vector3(...PHOTOGRAPHIC_STUDIO_KEY_DIRECTION),
    width: PHOTOGRAPHIC_STUDIO_KEY_CARD.width * lighting.size,
    height: PHOTOGRAPHIC_STUDIO_KEY_CARD.height * lighting.size,
    intensity: cardRadiance
  });
  // Fill sets both fill cards: the rear one for horizontal reflections and the
  // bounce on the key's far side, which lifts the faces the key cannot reach.
  card(scene, {
    name: "studio-fill-card",
    direction: new THREE.Vector3(...PHOTOGRAPHIC_STUDIO_FILL_DIRECTION),
    width: 3.2 * lighting.size,
    height: 4.2 * lighting.size,
    intensity: cardRadiance * lighting.fill
  });
  card(scene, {
    name: "studio-bounce-card",
    direction: new THREE.Vector3(...PHOTOGRAPHIC_STUDIO_BOUNCE_DIRECTION),
    width: 4 * lighting.size,
    height: 5 * lighting.size,
    intensity: cardRadiance * lighting.fill
  });
  for (const panel of PHOTOGRAPHIC_STUDIO_PANELS) {
    card(scene, {
      name: panel.name,
      direction: new THREE.Vector3(...panel.direction),
      width: panel.width * lighting.size,
      height: panel.height * lighting.size,
      intensity: panel.radiance / area,
      up: panel.up || null,
      falloff: panel.falloff || null
    });
  }
  return scene;
}

/**
 * The same studio as a CPU-owned equirectangular HDR image, for renderers that cannot
 * share the WebGL PMREM target. Sample the source cards in their own frames; their
 * vertex-colour gradients and the sweep use the same recipe as the raster studio.
 * The texture is caller-owned. Rotation is still applied by the consuming scene.
 */
export function createStudioEnvironmentTexture(configuration = {}, { size = 256 } = {}) {
  const height = proceduralEnvironmentSize(size), width = height * 2;
  const scene = createStudioEnvironmentScene(configuration);
  const cards = scene.children.filter(mesh => mesh.geometry?.type === "PlaneGeometry").map(mesh => ({
    inverse: mesh.matrixWorld.clone().invert().elements,
    normal: new THREE.Vector3(0, 0, 1).applyQuaternion(mesh.quaternion),
    position: mesh.position,
    width: mesh.geometry.parameters.width,
    height: mesh.geometry.parameters.height,
    segments: mesh.geometry.parameters.widthSegments,
    colors: mesh.geometry.getAttribute("color"),
    color: mesh.material.color
  }));
  const data = new Float32Array(width * height * 4);
  const { zenith, horizon, nadir } = PHOTOGRAPHIC_STUDIO_ROOM_RADIANCE;
  try {
    for (let y = 0; y < height; y++) {
      // DataTexture rows run from v=0 at -Y to v=1 at +Y (unlike a flipped image).
      const elevation = ((y + 0.5) / height - 0.5) * Math.PI;
      const dy = Math.sin(elevation), ring = Math.cos(elevation);
      for (let x = 0; x < width; x++) {
        const azimuth = ((x + 0.5) / width - 0.5) * 2 * Math.PI;
        const dx = ring * Math.cos(azimuth), dz = ring * Math.sin(azimuth);
        const sweep = horizon + ((dz >= 0 ? zenith : nadir) - horizon) * Math.sqrt(Math.abs(dz));
        let r = sweep, g = sweep, b = sweep, nearest = ROOM_RADIUS;
        for (const card of cards) {
          const denominator = card.normal.x * dx + card.normal.y * dy + card.normal.z * dz;
          if (Math.abs(denominator) < 1e-10) continue;
          const distance = card.normal.dot(card.position) / denominator;
          if (distance <= 0 || distance >= nearest) continue;
          const m = card.inverse, px = dx * distance, py = dy * distance, pz = dz * distance;
          const u = (m[0] * px + m[4] * py + m[8] * pz + m[12]) / card.width + 0.5;
          const v = 0.5 - (m[1] * px + m[5] * py + m[9] * pz + m[13]) / card.height;
          if (u < 0 || u > 1 || v < 0 || v > 1) continue;
          let weight = 1;
          if (card.colors) {
            // Match PlaneGeometry's two triangles per cell, including their diagonal.
            const n = card.segments, col = Math.min(n - 1, Math.floor(u * n)), row = Math.min(n - 1, Math.floor(v * n));
            const fx = u * n - col, fy = v * n - row, a = row * (n + 1) + col;
            const c = card.colors;
            weight = fx + fy <= 1
              ? c.getX(a) * (1 - fx - fy) + c.getX(a + 1) * fx + c.getX(a + n + 1) * fy
              : c.getX(a + n + 2) * (fx + fy - 1) + c.getX(a + n + 1) * (1 - fx) + c.getX(a + 1) * (1 - fy);
          }
          nearest = distance;
          r = card.color.r * weight; g = card.color.g * weight; b = card.color.b * weight;
        }
        const offset = (y * width + x) * 4;
        data[offset] = r; data[offset + 1] = g; data[offset + 2] = b; data[offset + 3] = 1;
      }
    }
  } finally {
    disposeScene(scene);
  }
  const texture = new THREE.DataTexture(data, width, height, THREE.RGBAFormat, THREE.FloatType);
  texture.name = PROCEDURAL_STUDIO_ENVIRONMENT_ID;
  texture.mapping = THREE.EquirectangularReflectionMapping;
  texture.minFilter = THREE.LinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.colorSpace = THREE.LinearSRGBColorSpace;
  texture.needsUpdate = true;
  return texture;
}

function disposeScene(scene) {
  scene?.traverse?.((object) => {
    object.geometry?.dispose?.();
    const materials = Array.isArray(object.material) ? object.material : [object.material];
    for (const material of materials) material?.dispose?.();
  });
}

function ownedEnvironmentResource(identity, target, renderer) {
  let disposed = false;
  return {
    identity,
    texture: target.texture,
    async readPixels() {
      const data = new Uint16Array(target.width * target.height * 4);
      await renderer.readRenderTargetPixelsAsync(target, 0, 0, target.width, target.height, data);
      return { data, width: target.width, height: target.height };
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      target.dispose?.();
    }
  };
}

/**
 * Create a caller-owned PMREM resource. PMREM generation is a synchronous
 * sequence of GPU passes, so this returns the resource itself rather than a
 * promise. Rotation is intentionally absent from its identity: callers apply
 * it through scene.environmentRotation so rotating the studio remains a cheap
 * live update.
 */
export function createEnvironmentResource(renderer, configuration = {}, {
  size = 256
} = {}) {
  if (!renderer) {
    throw new Error("A WebGL renderer is required for the photographic studio environment");
  }
  const identity = environmentResourceIdentity(configuration, { size });
  const environmentScene = createStudioEnvironmentScene(configuration);
  const generator = new THREE.PMREMGenerator(renderer);
  try {
    const target = generator.fromScene(environmentScene, 0, 0.1, 100, {
      size: proceduralEnvironmentSize(size)
    });
    target.texture.name = PROCEDURAL_STUDIO_ENVIRONMENT_ID;
    return ownedEnvironmentResource(identity, target, renderer);
  } finally {
    disposeScene(environmentScene);
    generator.dispose();
  }
}

export function disposeEnvironmentResource(resource) {
  resource?.dispose?.();
}
