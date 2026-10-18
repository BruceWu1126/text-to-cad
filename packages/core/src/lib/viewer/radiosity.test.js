/**
 * Unit tests for the radiosity pieces that run without a GPU: the display
 * material patch, the shadow-catching floor, the quality presets and the
 * stage's settings normalization.
 */
import assert from "node:assert/strict";
import test from "node:test";
import * as THREE from "three";

import {
  applyRadiosityPatch,
  coneDirection,
  createRadiosityDisplayUniforms,
  hasRadiosityPatch,
  integrateUpIrradiance,
  RADIOSITY_DIRECTION_OPTIONS,
  RADIOSITY_MAX_DIRECT_LIGHTS,
  RADIOSITY_QUALITY,
  removeRadiosityPatch
} from "./radiosityLightmap.js";
import { RadiosityFloor } from "./radiosityFloor.js";
import {
  DEFAULT_RADIOSITY_STAGE_SETTINGS,
  normalizeRadiosityStageSettings,
  skyRadianceFromLights
} from "./radiosityStage.js";

function uniforms() {
  const shared = createRadiosityDisplayUniforms();
  shared.uRadWeight.value = 1;
  shared.uRadShadow.value = 0.5;
  return shared;
}

function shaderStub() {
  return {
    uniforms: {},
    vertexShader: "#include <common>\n",
    fragmentShader:
      "#include <common>\n#include <lights_fragment_begin>\n#include <lights_fragment_maps>\n#include <aomap_fragment>\n"
  };
}

test("applyRadiosityPatch attaches the lightmap and chains the edge shader hook", () => {
  const material = new THREE.MeshPhysicalMaterial();
  const calls = [];
  material.onBeforeCompile = function edgeShader() {
    calls.push("edges");
  };
  const texture = new THREE.Texture();

  applyRadiosityPatch(material, texture, uniforms());

  assert.equal(hasRadiosityPatch(material), true);
  assert.equal(material.lightMap, texture);
  assert.equal(material.lightMapIntensity, 1);
  // the edge hook's source keys the program; the patch appends its own tag
  assert.match(material.customProgramCacheKey(), /edges[\s\S]*\|radiosity$/);

  const shader = shaderStub();
  material.onBeforeCompile(shader, null);
  assert.deepEqual(calls, ["edges"]);
  assert.ok(shader.uniforms.uRadWeight);
  assert.ok(shader.uniforms.uRadShadow);
  assert.ok(shader.uniforms.uRadIntensity);
  assert.ok(shader.uniforms.uRadLightMap);
  assert.ok(shader.uniforms.uRadLightDir);
  assert.ok(shader.uniforms.uRadLightCount);
  assert.ok(!shader.fragmentShader.includes("#include <lights_fragment_maps>"));
  assert.ok(shader.fragmentShader.includes("tcvRadVisibility"));
  assert.ok(shader.fragmentShader.includes("irradiance *= mix( tcvRadVisibility, 1.0 - uRadWeight, uRadReplaceAmbient )"));
  assert.ok(shader.fragmentShader.includes("#include <aomap_fragment>"));
  // The direct-light loop is three's own, with every directional and spot light scaled by
  // its baked visibility, read before the loop runs.
  assert.ok(!shader.fragmentShader.includes("#include <lights_fragment_begin>"));
  assert.ok(shader.fragmentShader.includes("texture2D( uRadLightMap, vLightMapUv )"));
  assert.ok(shader.fragmentShader.includes("getDirectionalLightInfo( directionalLight, directLight );\n\t\tdirectLight.color *= tcvRadDirectVisibility( directLight.direction );"));
  assert.ok(shader.fragmentShader.includes("getSpotLightInfo( spotLight, geometryPosition, directLight );\n\t\tdirectLight.color *= tcvRadDirectVisibility( directLight.direction );"));
  assert.ok(shader.fragmentShader.includes("RE_Direct( directLight, geometryPosition, geometryNormal, geometryViewDir, geometryClearcoatNormal, material, reflectedLight );"));
  assert.equal(RADIOSITY_MAX_DIRECT_LIGHTS, 3);
});

test("applyRadiosityPatch is idempotent and only recompiles on first attach", () => {
  const material = new THREE.MeshPhysicalMaterial();
  const a = new THREE.Texture();
  const b = new THREE.Texture();
  const v0 = material.version;
  applyRadiosityPatch(material, a, uniforms());
  const v1 = material.version;
  assert.ok(v1 > v0);
  const hook = material.onBeforeCompile;
  applyRadiosityPatch(material, b, uniforms());
  assert.equal(material.onBeforeCompile, hook);
  assert.equal(material.lightMap, b);
  assert.equal(material.version, v1, "texture swap must not recompile");
  assert.equal(material.customProgramCacheKey(), "radiosity");
});

test("removeRadiosityPatch restores the previous hooks", () => {
  const material = new THREE.MeshPhysicalMaterial();
  const previous = () => {};
  material.onBeforeCompile = previous;
  applyRadiosityPatch(material, new THREE.Texture(), uniforms());
  removeRadiosityPatch(material);
  assert.equal(hasRadiosityPatch(material), false);
  assert.equal(material.onBeforeCompile, previous);
  assert.equal(material.lightMap, null);
});

test("applyRadiosityPatch ignores non-materials", () => {
  assert.doesNotThrow(() => applyRadiosityPatch(null, new THREE.Texture(), uniforms()));
  assert.doesNotThrow(() => applyRadiosityPatch({}, new THREE.Texture(), uniforms()));
});

test("RadiosityFloor builds a uv1 plane centred under the model", () => {
  const floor = new RadiosityFloor();
  floor.configure(new THREE.Vector3(10, 20, 30), 5, 100);
  const mesh = floor.mesh;
  assert.notEqual(mesh, null);
  assert.equal(mesh.position.z, 5);
  assert.equal(floor.group.visible, true);
  const uv1 = floor.geometry.getAttribute("uv1");
  assert.equal(uv1.count, 16);
  // inner square = 4 × extent, centred on the model
  const pos = floor.geometry.getAttribute("position");
  const xs = new Set();
  for (let i = 0; i < pos.count; i++) xs.add(pos.getX(i));
  assert.ok(xs.has(10 - 200));
  assert.ok(xs.has(10 + 200));
  assert.equal(floor.innerSize, 400);
});

test("RadiosityFloor.setAtlasRect maps the inner square into the atlas, repeatably", () => {
  const floor = new RadiosityFloor();
  floor.configure(new THREE.Vector3(), 0, 10);
  floor.setAtlasRect(100, 200, 300, 300, 1024);
  let uv1 = floor.geometry.getAttribute("uv1");
  const us = new Set();
  const vs = new Set();
  for (let i = 0; i < uv1.count; i++) {
    us.add(uv1.getX(i));
    vs.add(uv1.getY(i));
  }
  assert.deepEqual([...us].sort(), [100 / 1024, 400 / 1024]);
  assert.deepEqual([...vs].sort(), [200 / 1024, 500 / 1024]);
  // Lookups clamp to the chart's baked texel centres (the horizon ring and
  // antialiased edge fragments sit on, or just past, the chart's edge).
  const u = floor._material.uniforms;
  assert.deepEqual(u.uUvMin.value.toArray(), [100.5 / 1024, 200.5 / 1024]);
  assert.deepEqual(u.uUvMax.value.toArray(), [399.5 / 1024, 499.5 / 1024]);
  assert.ok(floor._material.fragmentShader.includes("clamp( vUv1, uUvMin, uUvMax )"));
  // A second mapping (quality change → new atlas) must come from the original
  // corner flags, not from the already-mapped attribute.
  floor.setAtlasRect(2, 6, 1020, 1020, 2048);
  uv1 = floor.geometry.getAttribute("uv1");
  const us2 = new Set();
  for (let i = 0; i < uv1.count; i++) us2.add(uv1.getX(i));
  assert.deepEqual([...us2].sort(), [2 / 2048, 1022 / 2048]);
  floor.dispose();
  assert.equal(floor.mesh, null);
});

test("RadiosityFloor takes the direct lights' shadows by their share of its light", () => {
  const floor = new RadiosityFloor();
  const texture = new THREE.Texture();
  floor.setDirectLightmap(texture, [0.5, 0.25, 0]);
  const u = floor._material.uniforms;
  assert.equal(u.uDirectMap.value, texture);
  assert.deepEqual(u.uDirectShare.value.toArray(), [0.5, 0.25, 0]);
  // Shares beyond the whole are scaled back to it.
  floor.setDirectLightmap(texture, [1, 1, 0]);
  assert.deepEqual(u.uDirectShare.value.toArray(), [0.5, 0.5, 0]);
  // Without a map the floor is the environment's alone.
  floor.setDirectLightmap(null, [0.5, 0.5, 0]);
  assert.equal(u.uDirectMap.value, null);
  assert.deepEqual(u.uDirectShare.value.toArray(), [0, 0, 0]);
});

test("coneDirection stays inside the cone and covers it", () => {
  const axis = new THREE.Vector3(-0.35, -1, 1.5).normalize();
  const angle = 0.11;
  const out = new THREE.Vector3();
  let minCos = 1;
  let sumX = 0;
  for (let i = 0; i < 400; i++) {
    const u1 = (0.5 + 0.7548776662466927 * i) % 1;
    const u2 = (0.5 + 0.5698402909980532 * i) % 1;
    coneDirection(out, axis, angle, u1, u2);
    assert.ok(Math.abs(out.length() - 1) < 1e-9);
    const c = out.dot(axis);
    assert.ok(c >= Math.cos(angle) - 1e-9);
    minCos = Math.min(minCos, c);
    sumX += out.x;
  }
  // The whole cone is used (some samples at its rim) and it is centred on the axis.
  assert.ok(minCos < Math.cos(angle * 0.9));
  assert.ok(Math.abs(sumX / 400 - axis.x) < 0.01);
  // A zero angle is the axis itself.
  coneDirection(out, axis, 0, 0.3, 0.7);
  assert.ok(out.distanceTo(axis) < 1e-9);
});

test("skyRadianceFromLights reproduces the hemisphere and ambient lights' irradiance", () => {
  const radiance = skyRadianceFromLights({
    hemisphere: { skyColor: new THREE.Color(1, 1, 1), groundColor: new THREE.Color(0.5, 0.5, 0.5), intensity: 1.2 },
    ambient: { color: new THREE.Color(1, 1, 1), intensity: 0.4 }
  });
  // Tabulate the sky over directions (polar axis +Z, row = theta) and integrate.
  const w = 256;
  const h = 128;
  const lum = new Float32Array(w * h);
  const dir = new THREE.Vector3();
  const color = new THREE.Color();
  const table = (flip) => {
    for (let i = 0; i < h; i++) {
      const theta = (i + 0.5) * (Math.PI / h);
      for (let j = 0; j < w; j++) {
        const phi = (j + 0.5) * ((2 * Math.PI) / w);
        dir.set(Math.sin(theta) * Math.cos(phi), Math.sin(theta) * Math.sin(phi), (flip ? -1 : 1) * Math.cos(theta));
        radiance(dir, color);
        lum[i * w + j] = color.r;
      }
    }
    return integrateUpIrradiance(lum, w, h);
  };
  // three: an up-facing surface receives sky × intensity + ambient; a down-facing one ground × intensity + ambient.
  assert.ok(Math.abs(table(false) - (1 * 1.2 + 0.4)) < 0.01);
  assert.ok(Math.abs(table(true) - (0.5 * 1.2 + 0.4)) < 0.01);
  // Nothing: darkness.
  const dark = skyRadianceFromLights({});
  assert.deepEqual(dark(new THREE.Vector3(0, 0, 1), color).toArray(), [0, 0, 0]);
});

test("RadiosityFloor albedo follows the stage floor colour", () => {
  const floor = new RadiosityFloor();
  floor.setAlbedo(new THREE.Color(0.1, 0.2, 0.3));
  assert.ok(Math.abs(floor.albedo().g - 0.2) < 1e-6);
  floor.setShadowIntensity(1.7);
  assert.equal(floor._material.uniforms.uShadow.value, 1);
});

test("quality presets use square direction grids and sane sizes", () => {
  for (const preset of Object.values(RADIOSITY_QUALITY)) {
    const side = Math.sqrt(preset.directions);
    assert.ok(Number.isInteger(side));
    assert.equal(preset.lightmapResolution % 2, 0);
    assert.ok(preset.gatherRadius >= 1);
    assert.ok(preset.maxSamples >= 0);
  }
  assert.deepEqual([...RADIOSITY_DIRECTION_OPTIONS], [1, 4, 9, 16]);
});

test("normalizeRadiosityStageSettings clamps and falls back", () => {
  assert.deepEqual(normalizeRadiosityStageSettings(), { ...DEFAULT_RADIOSITY_STAGE_SETTINGS });
  const normalized = normalizeRadiosityStageSettings({
    quality: "ULTRA",
    directions: 5,
    maxSamples: -10,
    shadow: 3,
    bounce: "x",
    envSampling: "uniform"
  });
  assert.equal(normalized.quality, "medium");
  assert.equal(normalized.directions, 16);
  assert.equal(normalized.maxSamples, 0);
  assert.equal(normalized.shadow, 1);
  assert.equal(normalized.floorShadow, null);
  assert.equal(normalized.bounce, 0);
  assert.equal(normalized.envSampling, "uniform");
  assert.equal(normalizeRadiosityStageSettings({ floorShadow: 2 }).floorShadow, 1);
});

test("EnvImportanceTable samples with the density its pdf claims, even at a bright pole", async () => {
  const { EnvImportanceTable } = await import("./radiosityLightmap.js");
  const w = 32;
  const h = 16;
  // Dark sky with a bright overhead softbox: the whole top row and its neighbour.
  const lum = new Float32Array(w * h).fill(0.05);
  for (let j = 0; j < w; j++) { lum[j] = 20; lum[w + j] = 10; }
  const table = new EnvImportanceTable(lum, w, h);
  // Monte-Carlo estimates of ∫ 1 dω = 4π and ∫ max(d·z, 0) dω = π through 1/pdf
  // weights: unbiased only if the samples are drawn with the pdf the table reports.
  const dir = new THREE.Vector3();
  let sphere = 0;
  let up = 0;
  const n = 20000;
  for (let k = 0; k < n; k++) {
    const u1 = (0.5 + 0.7548776662466927 * k) % 1;
    const u2 = (0.5 + 0.5698402909980532 * k) % 1;
    const pdf = table.sample(u1, u2, dir);
    assert.ok(Math.abs(dir.length() - 1) < 1e-6);
    assert.ok(Math.abs(table.pdf(dir) - pdf) < 1e-6 * pdf, "pdf(dir) agrees with the sampled cell");
    sphere += 1 / pdf;
    up += Math.max(dir.z, 0) / pdf;
  }
  assert.ok(Math.abs(sphere / n / (4 * Math.PI) - 1) < 0.02, `sphere ${sphere / n}`);
  assert.ok(Math.abs(up / n / Math.PI - 1) < 0.02, `up ${up / n}`);
});

test("integrateUpIrradiance of a uniform unit sky is π", async () => {
  const { integrateUpIrradiance } = await import("./radiosityLightmap.js");
  const w = 128;
  const h = 64;
  const lum = new Float32Array(w * h).fill(1);
  assert.ok(Math.abs(integrateUpIrradiance(lum, w, h) - Math.PI) < 1e-2);
  // Light only below the horizon contributes nothing to an up-facing surface.
  for (let i = 0; i < h / 2; i++) for (let j = 0; j < w; j++) lum[i * w + j] = 0;
  assert.ok(Math.abs(integrateUpIrradiance(lum, w, h)) < 1e-6);
});

test("collectLitMeshes picks the PBR meshes under a root and nothing else", async () => {
  const { collectLitMeshes } = await import("./radiosityStage.js");
  const root = new THREE.Group();
  const lit = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshPhysicalMaterial());
  const standard = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshStandardMaterial());
  const unlit = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial());
  const line = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial());
  const multi = new THREE.Mesh(new THREE.BoxGeometry(), [new THREE.MeshStandardMaterial(), new THREE.MeshStandardMaterial()]);
  const nested = new THREE.Group();
  nested.add(standard);
  root.add(lit, unlit, line, multi, nested);
  assert.deepEqual(collectLitMeshes(root), [lit, standard]);
  assert.deepEqual(collectLitMeshes(null), []);
});

test("RadiosityStage rebuilds when a geometry is re-tessellated in place", async () => {
  const { RadiosityStage } = await import("./radiosityStage.js");
  const stage = new RadiosityStage({ capabilities: { isWebGL2: true }, extensions: { has: () => true } });
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshPhysicalMaterial());
  const h0 = stage._computeSceneHash([mesh]);
  assert.equal(stage._computeSceneHash([mesh]), h0, "stable for an unchanged scene");
  mesh.geometry.getAttribute("position").needsUpdate = true;
  const h1 = stage._computeSceneHash([mesh]);
  assert.notEqual(h1, h0, "rewritten positions change the hash");
  const finer = new THREE.SphereGeometry(1, 8, 8);
  mesh.geometry.setAttribute("position", finer.getAttribute("position"));
  mesh.geometry.setIndex(finer.getIndex());
  const h2 = stage._computeSceneHash([mesh]);
  assert.notEqual(h2, h1, "new vertex and index counts change the hash");
  // Face ids arrive a frame after the mesh first draws: charts built without
  // them fold a part's facing surfaces together, so they must trigger a re-chart.
  mesh.userData.faceIds = new Uint32Array(mesh.geometry.getIndex().count / 3);
  assert.notEqual(stage._computeSceneHash([mesh]), h2, "attached face ids change the hash");
  stage.dispose();
});
