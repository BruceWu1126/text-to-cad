import assert from "node:assert/strict";
import test from "node:test";
import { createViewSettingsStore } from "./viewSettingsStore.js";
import { createViewerRenderStateResolver } from "./renderState.js";

test("per-mode edits compose, survive mode switches and reset without changing shared state", () => {
  const store = createViewSettingsStore({ camera: { focalLength: 80 }, clip: { enabled: true, at: 4 }, exploded: { enabled: true, amount: 0.4 } });
  store.patchMode("solid", { grid: { enabled: true, color: "#abcdef" } });
  store.patchMode("solid", { grid: { opacity: 0.8 } });
  store.patchMode("render", { lighting: { exposure: 1 } });
  const before = store.getSnapshot();
  assert.equal(before.display.mode, "solid");
  assert.equal(before.scene.view.lighting.exposure, 0);
  store.selectMode("render");
  assert.equal(store.getSnapshot().scene.view.lighting.exposure, 1);
  store.selectMode("solid");
  assert.deepEqual(store.getSnapshot(), before);
  store.resetMode("render");
  assert.deepEqual(store.getSnapshot().display.modes, { solid: { grid: { enabled: true, color: "#abcdef", opacity: 0.8 } } });
  for (const key of ["camera", "clip", "exploded"]) assert.deepEqual(store.getSnapshot().display[key], before.display[key]);
  assert.deepEqual(createViewSettingsStore(before.display).getSnapshot(), before);
});

test("a present mode draws the scene in that mode, the file's own mode and settings untouched, and back again", () => {
  const store = createViewSettingsStore({});
  store.patchMode("render", { lighting: { exposure: 1 } });
  const before = store.getSnapshot();
  assert.equal(before.scene.view.mode, "solid");
  store.configure({ presentMode: "render" });
  const presented = store.getSnapshot();
  assert.deepEqual([presented.display, presented.scene.view.mode, presented.scene.view.lighting.exposure, presented.scene.render.enabled],
    [before.display, "render", 1, true]);
  store.configure({ presentMode: null });
  assert.deepEqual(store.getSnapshot(), before);
});

test("inactive mode edits preserve the scene and no-op mode/reset actions publish nothing", () => {
  const store = createViewSettingsStore();
  const scene = store.getSnapshot().scene;
  store.patchMode("xray", { opacity: 0.5 });
  assert.equal(store.getSnapshot().scene, scene);
  let updates = 0;
  store.subscribe(() => updates++);
  store.selectMode("solid");
  store.resetMode("render");
  store.patchMode("xray", { opacity: 0.5 });
  assert.equal(updates, 0);
});

test("Clip edits preserve all unrelated renderer inputs through both resolution stages", () => {
  for (const mode of ["solid", "render"]) {
    const store = createViewSettingsStore({ mode });
    const resolve = createViewerRenderStateResolver();
    const before = store.getSnapshot();
    const oldRuntime = resolve({ themeSettings: before.scene.theme, displaySettings: before.scene.display });
    store.patch({ clip: { enabled: true, at: 0.4 } });
    const after = store.getSnapshot();
    const newRuntime = resolve({ themeSettings: after.scene.theme, displaySettings: after.scene.display });
    for (const name of ["theme", "camera", "render", "quality"]) assert.equal(after.scene[name], before.scene[name], name);
    assert.equal(newRuntime.themeSettings, oldRuntime.themeSettings);
    assert.equal(newRuntime.edgeSettings, oldRuntime.edgeSettings);
    for (const name of ["surfaces", "guides", "partColor", "exploded"]) assert.equal(newRuntime.displaySettings[name], oldRuntime.displaySettings[name], name);
    assert.notEqual(newRuntime.clipSettings, oldRuntime.clipSettings);
  }
});

test("lighting and floor edits do not invalidate geometry settings, and no-op edits publish nothing", () => {
  const store = createViewSettingsStore({ mode: "render" });
  const before = store.getSnapshot();
  store.patchMode("render", { lighting: { exposure: 0.7 }, floor: { opacity: 0.8 } });
  const after = store.getSnapshot();
  assert.equal(after.scene.display, before.scene.display);
  assert.equal(after.scene.camera, before.scene.camera);
  let updates = 0;
  store.subscribe(() => updates++);
  store.patchMode("render", { lighting: { exposure: 0.7 } });
  store.configure({ appearance: "light" });
  assert.equal(updates, 0);
  assert.equal(store.getSnapshot(), after);
});


