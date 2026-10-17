import assert from 'node:assert/strict';
import test from 'node:test';
import { normalizeViewSettings, resolveViewSettings, viewModeChanged } from '@text-to-cad/core/common/viewSettings.js';
import { DISPLAY_MODE_OPTIONS } from './DisplayModeOptions.js';
import {
  cameraForViewSettings, mergeViewerDisplaySettings, previewDisplaySettings,
  viewerDisplaySettingsForCamera
} from './viewerDisplaySettings.js';

test('the viewer offers the working display modes in their intended order: Render is the Studio\'s, no display mode', () => {
  assert.deepEqual(DISPLAY_MODE_OPTIONS.map(({ value, label }) => [value, label]), [
    ['solid', 'Solid'], ['xray', 'X-ray'], ['hidden-line', 'Hidden line'], ['wireframe', 'Wireframe']
  ]);
});

test('live patches merge only supplied mode settings and preserve the shared camera and tools', () => {
  const current = { mode: 'render', camera: { focalLength: 80 }, modes: { render: { lighting: { exposure: 1, size: 2 } } },
    clip: { enabled: true, axis: 'y', at: 0.35 } };
  const next = mergeViewerDisplaySettings(current, { modes: { render: { lighting: { exposure: 2 } } } });
  assert.deepEqual(next, { ...current, modes: { render: { lighting: { exposure: 2, size: 2 } } } });
  assert.throws(() => mergeViewerDisplaySettings(current, { lighting: { exposure: 1 } }), /Unsupported/);
  assert.throws(() => normalizeViewSettings({ mode: 'grid' }), /mode/);
});

test('preset camera changes retain the actual camera position, target and zoom', () => {
  const camera = { position: [30, 40, 50], target: [3, 4, 5], up: [0, 0, 1], zoom: 1.4,
    projection: 'orthographic', focalLength: 120, orthographicHalfHeight: 24 };
  for (const mode of ['solid', 'render', 'xray', 'hidden-line', 'wireframe']) {
    const next = cameraForViewSettings(camera, { mode });
    const expected = resolveViewSettings({ mode }).camera;
    assert.deepEqual(next, { ...camera, projection: expected.projection, focalLength: expected.focalLength });
  }
  assert.equal(cameraForViewSettings(null, { mode: 'render' }), null);
});

test('the viewer Preview baseline is inherited without an override or Custom state', async () => {
  const { resolveViewSceneSettings } = await import('@text-to-cad/core/common/sceneSettings.js');
  const options = { lightingQuality: 'preview' };
  const scene = resolveViewSceneSettings({ display: { mode: 'render' }, ...options });
  assert.equal(scene.view.lighting.quality, 'preview');
  assert.equal(scene.quality.id, 'standard');
  assert.equal(viewModeChanged({ mode: 'render' }, 'render', options), false);
  assert.equal(viewModeChanged({ modes: { render: { lighting: { quality: 'preview' } } } }, 'render', options), false);
  assert.equal(viewModeChanged({ modes: { render: { lighting: { quality: 'final' } } } }, 'render', options), true);
});

test('camera commands activate explicit projection and lens while pose-only updates leave View unchanged', () => {
  const current = { mode: 'xray', camera: { projection: 'orthographic' }, clip: { enabled: true, axis: 'z', at: 0.2 } };
  const pose = { position: [30, 40, 50], target: [3, 4, 5], up: [0, 0, 1], zoom: 1.4 };
  assert.deepEqual(viewerDisplaySettingsForCamera(current, pose), current);
  const next = viewerDisplaySettingsForCamera(current, { ...pose, projection: 'perspective', focalLength: 85 });
  assert.deepEqual(next, { ...current, camera: { projection: 'perspective', focalLength: 85 } });
  assert.equal(resolveViewSettings(next).camera.projection, 'perspective');
  assert.equal(resolveViewSettings(next).camera.focalLength, 85);
  assert.deepEqual(current.camera, { projection: 'orthographic' });
  assert.throws(() => viewerDisplaySettingsForCamera(current, { ...pose, focalLength: 500 }), /focalLength/);
});

test('live clip patches place the plane in millimetres; a new axis without a place cuts through the middle', () => {
  const initial = { mode: 'solid', clip: { axis: 'x', at: 2 } };
  const moved = mergeViewerDisplaySettings(initial, { clip: { at: 4.5 } });
  assert.deepEqual(moved.clip, { enabled: true, axis: 'x', invert: false, at: 4.5 });
  assert.deepEqual(mergeViewerDisplaySettings(moved, { clip: { invert: true } }).clip,
    { enabled: true, axis: 'x', invert: true, at: 4.5 }, 'flipping never moves the plane');
  assert.deepEqual(mergeViewerDisplaySettings(moved, { clip: { axis: 'y' } }).clip, { enabled: true, axis: 'y', invert: false });
  assert.deepEqual(mergeViewerDisplaySettings(moved, { clip: { axis: 'y', at: -3 } }).clip,
    { enabled: true, axis: 'y', invert: false, at: -3 });
  assert.throws(() => mergeViewerDisplaySettings(moved, { clip: { offset: 0.5 } }), /Unsupported display\.clip fields: offset/);
});

test('presentation suspends model effects without mutating the saved settings', () => {
  const display = { mode: 'render', clip: { enabled: true, axis: 'y', at: 4 }, exploded: { enabled: true, amount: .7 } };
  const before = structuredClone(display);
  const preview = previewDisplaySettings(display);
  assert.equal(preview.clip.enabled, false);
  assert.equal(preview.exploded.enabled, false);
  assert.equal(preview.exploded.amount, 0);
  assert.equal(preview.mode, 'render');
  assert.deepEqual(display, before);
});
