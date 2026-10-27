import assert from 'node:assert/strict';
import test from 'node:test';
import { previewStudioConfiguration, previewStudioScene } from './previewConfiguration.js';

test('Preview performance updates preserve studio identity, while actual lighting edits do not', () => {
  const configuration = { quality: 'preview', lighting: { exposure: 0, fill: 0.25 },
    backdrop: { groundOpacity: 0.6 }, performance: { mode: 'auto', value: 50 } };
  const studio = previewStudioConfiguration(configuration);
  assert.equal(Object.hasOwn(studio, 'performance'), false);
  assert.equal(previewStudioConfiguration({ ...configuration, performance: { mode: 'manual', value: 100 } }, studio), studio);
  const changed = previewStudioConfiguration({ ...configuration, lighting: { ...configuration.lighting, exposure: 1 } }, studio);
  assert.notEqual(changed, studio);
  assert.equal(changed.lighting.exposure, 1);
  assert.deepEqual(configuration.performance, { mode: 'auto', value: 50 }, 'the persisted preference is not modified');
  assert.equal(previewStudioConfiguration(null, studio), null);
});

test('quality-only edits do not enter the staged scene update coordinator', () => {
  const scene = { quality: { id: 'high' }, view: { quality: { mode: 'auto', value: 50 }, lighting: { exposure: 0 } },
    render: { enabled: true, configuration: { lighting: { exposure: 0 }, performance: { mode: 'auto', value: 50 } } } };
  const staged = previewStudioScene(scene);
  const changed = { ...scene, view: { ...scene.view, quality: { mode: 'manual', value: 80 } },
    render: { ...scene.render, configuration: { ...scene.render.configuration, performance: { mode: 'manual', value: 80 } } } };
  assert.equal(previewStudioScene(changed, staged), staged);
  assert.deepEqual(staged.quality, scene.quality, 'the shared scene quality remains part of staging');
  assert.equal(Object.hasOwn(staged.view, 'quality'), false);
  assert.notEqual(previewStudioScene({ ...changed, view: { ...changed.view, lighting: { exposure: 1 } } }, staged), staged);
});
