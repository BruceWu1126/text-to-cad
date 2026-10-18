import assert from "node:assert/strict";
import test from "node:test";
import * as THREE from "three";
import { buildModel } from "./cadScene.js";
import { buildStepClipPlane } from "../lib/viewer/clipPlane.js";
import { resolveCadEdgeSettings } from "./cadInk.js";
import { applyMaterialSettingsToRecord as applyViewerMaterialSettings } from "../lib/viewer/surfaceMaterials.js";
import { resolveViewSceneSettings } from "./sceneSettings.js";
import { renderJobContext, modelOptionsForRenderJob } from "./renderMeshScene.js";
import { ALL_VIEW_FEATURES, EDGELESS_VIEW_FEATURES, RENDER_FLOOR_PLACEMENT, normalizeViewSettings, resolveViewSettings, resetViewMode, viewModeChanged, VIEW_MODE_VALUES } from "./viewSettings.js";

const RENDER = { render: true };

test("input stays sparse, canonicalizes colors and refuses a setting its mode does not have", () => {
  assert.deepEqual(normalizeViewSettings(), { mode: "solid" });
  assert.deepEqual(normalizeViewSettings({ modes: { render: { floor: { color: "#AbC" } } } }),
    { mode: "solid", modes: { render: { floor: { color: "#aabbcc" } } } });
  for (const [input, message] of [
    [{ mode: "grid" }, /display\.mode must be one of/],
    // Render is no display mode: it is asked for on its own (`render`).
    [{ mode: "render" }, /display\.mode must be one of: solid, xray, hidden-line, wireframe/],
    [{ surfaces: {} }, /Unsupported display fields: surfaces/],
    [{ modes: { solid: { lighting: {} } } }, /Unsupported display\.modes\.solid fields: lighting/],
    [{ modes: { solid: { grid: { opacity: 2 } } } }, /display\.modes\.solid\.grid\.opacity must be a finite number between 0 and 1/],
    [{ modes: { xray: { edges: { enabled: false } } } }, /Unsupported display\.modes\.xray\.edges fields: enabled/],
    [{ modes: { render: { background: { enabled: true } } } }, /Unsupported display\.modes\.render\.background fields: enabled/],
    [{ camera: { enabled: true } }, /Unsupported display\.camera fields: enabled/],
    [{ camera: { focalLength: 19 } }, /display\.camera\.focalLength/],
    [{ clip: null }, /clip/]
  ]) assert.throws(() => normalizeViewSettings(input), message);
});

test("each mode draws its own settings; Render, asked for on its own, alone is in perspective, whatever the camera says", () => {
  assert.deepEqual(VIEW_MODE_VALUES, ["solid", "xray", "hidden-line", "wireframe"]);
  for (const mode of VIEW_MODE_VALUES) {
    const view = resolveViewSettings({ mode, camera: { projection: "orthographic" } });
    assert.equal(view.camera.projection, "orthographic");
    assert.equal(viewModeChanged({ mode }, mode), false);
    const rendered = resolveViewSettings({ mode, camera: { projection: "orthographic" } }, RENDER);
    assert.deepEqual([rendered.mode, rendered.camera.projection], ["render", "perspective"], `Render over ${mode}`);
  }
  assert.equal(viewModeChanged({}, "render"), false);
  const solid = resolveViewSettings({ modes: { solid: { flat: true, edges: { enabled: false } } } });
  assert.deepEqual([solid.surfaces.style, solid.edges.enabled], ["flat", false]);
  const xray = resolveViewSettings({ mode: "xray", modes: { xray: { opacity: 0.5 } } });
  assert.deepEqual([xray.surfaces.opacity, xray.edges.visibility, xray.edges.enabled], [0.5, "all", true]);
  assert.equal(resolveViewSettings({ mode: "xray" }).surfaces.opacity, 0.22);
  assert.deepEqual([resolveViewSettings({ mode: "hidden-line" }).surfaces.style, resolveViewSettings({ mode: "wireframe" }).surfaces.style], ["hidden", "off"]);
  const render = resolveViewSettings({}, RENDER);
  assert.deepEqual([render.lighting.enabled, render.background.enabled, render.floor.enabled, render.edges.enabled], [true, true, true, false]);
  assert.equal(resolveViewSettings({ modes: { render: { floor: { enabled: false } } } }, RENDER).floor.enabled, false);
  assert.equal(resolveViewSettings({ mode: "solid" }).lighting.enabled, false);
});

test("every mode keeps its own settings, and Render its own: another's change is not the one on screen's", () => {
  const display = { mode: "solid", modes: { solid: { grid: { enabled: true } }, render: { lighting: { exposure: 1 } } } };
  assert.equal(resolveViewSettings(display, RENDER).grid.enabled, false, "Render has no grid");
  assert.equal(resolveViewSettings(display, RENDER).lighting.exposure, 1);
  assert.equal(resolveViewSettings(display).grid.enabled, true);
  assert.equal(resolveViewSettings({ ...display, mode: "xray" }).grid.enabled, false, "X-ray's grid is its own");
  assert.deepEqual(resetViewMode(display, "render"), { mode: "solid", modes: { solid: { grid: { enabled: true } } } });
  assert.deepEqual(resetViewMode(resetViewMode(display, "render"), "solid"), { mode: "solid" });
});

test("viewer preview and snapshot final are environment defaults, not changes", () => {
  assert.equal(resolveViewSettings({}, RENDER).lighting.quality, "final");
  const options = { lightingQuality: "preview" };
  assert.equal(resolveViewSettings({}, { ...options, ...RENDER }).lighting.quality, "preview");
  assert.equal(viewModeChanged({ modes: { render: { lighting: { quality: "preview" } } } }, "render", options), false);
  assert.equal(viewModeChanged({ modes: { render: { lighting: { quality: "final" } } } }, "render", options), true);
  assert.equal(resolveViewSceneSettings({ display: {}, ...options, ...RENDER }).quality.id, "standard");
});

test("a mode's change compares effective values and excludes what modes share", () => {
  const shared = { clip: { enabled: true, at: 0.2 }, exploded: { enabled: true, amount: 0.5 }, camera: { projection: "perspective" } };
  assert.equal(viewModeChanged({ ...shared }, "solid"), false);
  assert.equal(viewModeChanged({ modes: { solid: { flat: false } } }, "solid"), false);
  assert.equal(viewModeChanged({ modes: { solid: { grid: { enabled: true } } } }, "solid"), true);
  assert.equal(viewModeChanged({ modes: { solid: { grid: { enabled: true } } } }, "xray"), false);
  // An offered setting alone counts: a mesh's view has no edges to have changed.
  assert.equal(viewModeChanged({ modes: { solid: { edges: { color: "#ff0000" } } } }, "solid", { features: EDGELESS_VIEW_FEATURES }), false);
});

test("a grid turned on is the measuring grid, and its axes are a toggle of their own", () => {
  for (const appearance of ["light", "dark"]) {
    const dark = appearance === "dark";
    const view = resolveViewSettings({ modes: { solid: { grid: { enabled: true } } } }, { appearance });
    assert.deepEqual(view.grid, { enabled: true, color: dark ? "#64768a" : "#94a3b8", opacity: dark ? 0.42 : 0.38, density: 2 });
    assert.equal(view.axes.enabled, false);
    for (const mode of VIEW_MODE_VALUES) {
      const plain = resolveViewSettings({ mode }, { appearance });
      assert.deepEqual([plain.grid.enabled, plain.axes.enabled], [false, false], `${mode} (${appearance}) draws no grid and no axes`);
    }
  }
  assert.equal(resolveViewSettings({ modes: { solid: { axes: { enabled: true } } } }).axes.enabled, true);
});

test("hidden-line and wireframe edges are light on a dark backdrop", () => {
  for (const mode of ["hidden-line", "wireframe"]) {
    assert.equal(resolveViewSettings({ mode }, { appearance: "dark" }).edges.color, "#c9d3de");
    assert.equal(resolveViewSettings({ mode }).edges.color, "#253443");
  }
  assert.equal(resolveViewSettings({ mode: "solid" }, { appearance: "dark" }).edges.color, "#253443");
});

test("Render's custom lighting and background keep CAD edges off", () => {
  const custom = resolveViewSceneSettings({ display: { modes: { render: { background: { opacity: 0.35 } } } }, ...RENDER });
  assert.equal(custom.camera.projection, "perspective");
  assert.equal(custom.render.configuration.backdrop.opacity, 0.35);
  assert.equal(custom.display.edges.enabled, false);
  assert.equal(custom.theme, null);
});

test("snapshot public boundary rejects removed camera fields and explicit null display", () => {
  for (const job of [
    { display: null }, { display: { mode: "shaded" } }, { display: { mode: "render" } }, { render: "yes" },
    { camera: { projection: "perspective" } },
    { outputs: [{ camera: { focalLength: 85 } }] }
  ]) assert.throws(() => renderJobContext(mesh(), job));
});

test("a clip is placed at its coordinate in millimetres, else through the middle", () => {
  const bounds = { min: [-10, 2, 100], max: [30, 12, 200] };
  const modelOffset = new THREE.Vector3(5, 5, -150);
  for (const invert of [false, true]) {
    const view = resolveViewSettings({ clip: { axis: "z", at: 137.5, invert } });
    assert.equal(view.clip.enabled, true, "a supplied clip cuts");
    const plane = buildStepClipPlane(THREE, view.clip, bounds, modelOffset);
    assert.equal(plane.distanceToPoint(new THREE.Vector3(0, 0, 137.5 - 150)), 0);
    assert.equal(Math.sign(plane.distanceToPoint(new THREE.Vector3(0, 0, 160 - 150))), invert ? 1 : -1,
      "the lower side is kept unless inverted");
    const middle = buildStepClipPlane(THREE, resolveViewSettings({ clip: { axis: "z", invert } }).clip, bounds, modelOffset);
    assert.equal(middle.distanceToPoint(new THREE.Vector3(0, 0, 150 - 150)), 0);
  }
  const middleX = resolveViewSettings({ clip: {} }).clip;
  assert.equal(buildStepClipPlane(THREE, middleX, bounds).distanceToPoint(new THREE.Vector3(10, 0, 0)), 0, "{} halves it along x");
  assert.equal(resolveViewSettings({}).clip.enabled, false, "no clip at all is no clip");
  assert.equal(buildStepClipPlane(THREE, { axis: "z", at: 120 }, null).distanceToPoint(new THREE.Vector3(0, 0, 120)), 0,
    "a placed plane needs no bounds");
  assert.equal(buildStepClipPlane(THREE, { axis: "z", at: 0 }, bounds).distanceToPoint(new THREE.Vector3(0, 0, 150)), -150,
    "below the model it cuts everything away");
});

test("a plane that cuts nothing activates no clipping", () => {
  const bounds = { min: [-10, 2, 100], max: [30, 12, 200] };
  assert.equal(buildStepClipPlane(THREE, { axis: "z", at: 200 }, bounds), null, "at the top, keeping the lower side");
  assert.equal(buildStepClipPlane(THREE, { axis: "z", at: 250 }, bounds), null, "past the top");
  assert.equal(buildStepClipPlane(THREE, { axis: "z", at: 100, invert: true }, bounds), null, "at the bottom, inverted");
  assert.ok(buildStepClipPlane(THREE, { axis: "z", at: 200, invert: true }, bounds));
  assert.equal(buildStepClipPlane(THREE, { enabled: false, axis: "z", at: 150 }, bounds), null);
});

test("default grouped edges preserve the original class palette in both appearances", () => {
  for (const appearance of ["light", "dark"]) {
    const scene = resolveViewSceneSettings({ appearance });
    assert.deepEqual(resolveCadEdgeSettings(scene.display.edges).classes, resolveCadEdgeSettings().classes);
  }
});

function mesh() {
  return {
    vertices: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]), indices: new Uint32Array([0, 1, 2]),
    normals: new Float32Array([0, 0, 1, 0, 0, 1, 0, 0, 1]), bounds: { min: [0, 0, 0], max: [1, 1, 0] },
    parts: [{ id: "part", vertexOffset: 0, vertexCount: 3, triangleOffset: 0, triangleCount: 1, bounds: { min: [0, 0, 0], max: [1, 1, 0] } }]
  };
}

test("draw bridge keeps surface opacity, edge visibility and edge color independent", () => {
  // Edges belong to a CAD model, so the job says it is one.
  const context = renderJobContext(mesh(), { kind: "step", display: { mode: "xray", modes: { xray: { opacity: 0.63, edges: { color: "#ff0000" } } } } });
  const model = buildModel(THREE, mesh(), { ...modelOptionsForRenderJob(context), edgeRendering: { mode: "basic" } });
  try {
    const record = model.displayRecords[0];
    assert.equal(record.material.opacity, 0.63);
    assert.equal(record.edgeMaterials[0].depthTest, false);
    assert.equal(record.edgeMaterials[0].color.getHexString(), "ff0000");
    const next = resolveViewSceneSettings({ display: { mode: "xray", modes: { xray: { opacity: 0.4, edges: { color: "#0000ff" } } } } });
    model.update({ displayMode: next.display.mode, surfaceSettings: next.view.surfaces, edgeSettings: next.display.edges });
    assert.equal(model.displayRecords[0].material.opacity, 0.4);
    assert.equal(model.displayRecords[0].edgeMaterials[0].color.getHexString(), "0000ff");
    applyViewerMaterialSettings(THREE, model.displayRecords[0], model.runtime.materialSettings, {
      displayMode: next.display.mode, surfaceSettings: next.view.surfaces
    });
    assert.equal(model.displayRecords[0].material.opacity, 0.4, "viewer fallback follows the same grouped opacity");
  } finally { model.dispose(); }
});

test("hidden-line uses a depth-only surface; wireframe has none, and Solid's edges turn off", () => {
  for (const [display, colorWrite, depthWrite, edges] of [
    [{ mode: "hidden-line" }, false, true, true],
    [{ mode: "wireframe" }, false, false, true],
    [{ mode: "solid", modes: { solid: { edges: { enabled: false } } } }, true, true, false]
  ]) {
    const context = renderJobContext(mesh(), { kind: "step", display });
    const model = buildModel(THREE, mesh(), { ...modelOptionsForRenderJob(context), edgeRendering: { mode: "basic" } });
    try {
      assert.equal(model.displayRecords[0].material.colorWrite, colorWrite);
      assert.equal(model.displayRecords[0].material.depthWrite, depthWrite);
      assert.equal(Boolean(model.displayRecords[0].edges), edges);
    } finally { model.dispose(); }
  }
});

test("a file that is not a CAD model has no edges, explode or section, and shows an edge-made mode as Solid, without rewriting what was saved", () => {
  const saved = { mode: "wireframe", modes: { solid: { edges: { color: "#ff0000" } } },
    clip: { enabled: true, axis: "x" }, exploded: { enabled: true, amount: 0.8 } };
  const step = resolveViewSettings(saved, { features: ALL_VIEW_FEATURES });
  assert.equal(step.mode, "wireframe");
  assert.equal(step.edges.enabled, true);
  const mesh = resolveViewSettings(saved, { features: EDGELESS_VIEW_FEATURES });
  assert.equal(mesh.mode, "solid");
  assert.equal(mesh.surfaces.style, "shaded");
  assert.equal(mesh.edges.enabled, false);
  assert.deepEqual([step.clip.enabled, step.exploded.enabled, mesh.clip.enabled, mesh.exploded.enabled], [true, true, false, false]);
  assert.equal(resolveViewSettings({ modes: { solid: { flat: true } } }, { features: EDGELESS_VIEW_FEATURES }).surfaces.style, "flat");
  // Render is not made of edges, and stays available.
  assert.equal(resolveViewSettings({}, { features: EDGELESS_VIEW_FEATURES, ...RENDER }).mode, "render");
  for (const mode of ["xray", "hidden-line"]) assert.equal(resolveViewSettings({ mode }, { features: EDGELESS_VIEW_FEATURES }).mode, "solid");
  assert.equal(normalizeViewSettings(saved).mode, "wireframe", "the saved settings still mean Wireframe on the next STEP file");
});

test("a view is resolved from the lists it opts into, one feature at a time", () => {
  const saved = { mode: "xray", clip: { enabled: true, axis: "x" }, exploded: { enabled: true, amount: 0.5 } };
  const everything = resolveViewSettings(saved);
  assert.deepEqual(resolveViewSettings(saved, { features: ALL_VIEW_FEATURES }), everything);
  assert.deepEqual(resolveViewSettings(saved, { features: {} }), everything, "a list left out stands for the full set");
  const noClip = resolveViewSettings(saved, { features: { settings: ALL_VIEW_FEATURES.settings.filter(id => id !== "clip") } });
  assert.deepEqual([noClip.clip.enabled, noClip.exploded.enabled, noClip.edges.enabled, noClip.mode], [false, true, true, "xray"]);
  assert.equal(resolveViewSettings(saved, { features: { modes: ["solid"] } }).mode, "solid");
  assert.deepEqual(EDGELESS_VIEW_FEATURES.modes, ["solid"]);
});

test("light Render separates a white background from the gray floor", () => {
  const view = resolveViewSettings({ appearance: "light" }, RENDER);
  assert.equal(view.background.color, "#ffffff");
  assert.equal(view.floor.color, "#e7e7e5");
  const scene = resolveViewSceneSettings({ display: { appearance: "light" }, ...RENDER });
  assert.equal(scene.render.configuration.backdrop.color, "#ffffff");
  assert.equal(scene.render.configuration.backdrop.groundColor, "#e7e7e5");
  assert.equal(resolveViewSettings({ modes: { render: { background: { color: "#abc123" } } } }, RENDER).background.color, "#abc123");
});

test("the floor is matte until Glossy is chosen, and a snapshot gives the studio the finish the viewer does", () => {
  for (const finish of ["shiny", "Glossy", true, null]) assert.throws(() => normalizeViewSettings({ modes: { render: { floor: { finish } } } }));
  const glossy = { modes: { render: { floor: { finish: "glossy" } } } };
  assert.equal(resolveViewSettings({}, RENDER).floor.finish, "matte");
  assert.equal(resolveViewSettings(glossy, RENDER).floor.finish, "glossy");
  assert.equal(viewModeChanged(glossy, "render"), true);
  // The viewer's store and a snapshot's job resolve one display through the same recipe.
  for (const display of [{}, glossy]) {
    const viewer = resolveViewSceneSettings({ display, ...RENDER }).render.configuration.backdrop;
    const snapshot = renderJobContext(mesh(), { kind: "step", display, ...RENDER }).sceneSettings.render.configuration.backdrop;
    assert.equal(snapshot.groundFinish, display.modes?.render.floor.finish ?? "matte");
    assert.deepEqual(snapshot, viewer);
  }
});

test("Render's floor stands at the model's lowest point until Model origin is chosen, and a snapshot's stands where the viewer's does", () => {
  assert.equal(RENDER_FLOOR_PLACEMENT, "lowest");
  for (const appearance of ["light", "dark"]) assert.equal(resolveViewSettings({}, { appearance, ...RENDER }).floor.placement, "lowest");
  const origin = { modes: { render: { floor: { placement: "origin" } } } };
  assert.equal(viewModeChanged(origin, "render"), true);
  for (const [display, placement] of [[{}, "lowest"], [origin, "origin"]]) {
    const viewer = resolveViewSceneSettings({ display, ...RENDER }).render.configuration.backdrop;
    const snapshot = renderJobContext(mesh(), { kind: "step", display, ...RENDER }).sceneSettings.render.configuration.backdrop;
    assert.deepEqual([viewer.groundPlacement, snapshot.groundPlacement], [placement, placement]);
  }
});
