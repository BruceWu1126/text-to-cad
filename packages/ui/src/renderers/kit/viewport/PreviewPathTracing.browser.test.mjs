import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { createServer } from 'node:http';
import { readFile, writeFile, mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { chromium } from 'playwright';
import { PNG } from 'pngjs';
import { harnessBundle } from '../../harness/bundle.mjs';
import { pressInPreview } from '../../harness/preview.mjs';

// A small, generated scene: face-painted and repeated parts, metal and translucent
// material. This exercises actual GPU output, without the sample-model corpus or a
// noisy screenshot golden. Unsupported browsers still exercise the error state.
const fixture = `
import * as THREE from 'three';
import { createProgressivePreview } from './packages/core/src/lib/preview-tracing/progressivePreview.js';
import { PreviewFastFilter } from './packages/core/src/lib/preview-tracing/previewFastFilter.js';
const host = document.querySelector('#view');
const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
renderer.setSize(256, 192); renderer.setPixelRatio(1);
host.append(renderer.domElement);
renderer.domElement.style.opacity = '0.8';
const scene = new THREE.Scene(); scene.background = new THREE.Color('#e7e7e5');
const modelGroup = new THREE.Group(); scene.add(modelGroup);
const red = new THREE.MeshStandardMaterial({ color: '#d22822', roughness: 0.55 });
const blue = new THREE.MeshStandardMaterial({ color: '#2167db', roughness: 0.45 });
const green = new THREE.MeshStandardMaterial({ color: '#2dab51', roughness: 0.5 });
const metal = new THREE.MeshStandardMaterial({ color: '#c3cbd4', metalness: 1, roughness: 0.14 });
const glass = new THREE.MeshPhysicalMaterial({ color: '#ffffff', opacity: 1, transmission: 1, thickness: 0.6, ior: 1.5, roughness: 0.06 });
const box = new THREE.BoxGeometry(1.1, 1.1, 1.1);
const painted = new THREE.Mesh(box, [red, blue, green, blue, red, green]);
painted.position.set(-1.15, 0, 0.55); modelGroup.add(painted);
const repeat = new THREE.Mesh(box, blue); repeat.position.set(1.15, 0, 0.55); modelGroup.add(repeat);
const sphere = new THREE.SphereGeometry(0.58, 16, 12);
const polished = new THREE.Mesh(sphere, metal); polished.position.set(-0.8, 1.5, 0.6); modelGroup.add(polished);
const transparent = new THREE.Mesh(sphere, glass); transparent.position.set(0.8, 1.5, 0.6); modelGroup.add(transparent);
const floor = new THREE.Mesh(new THREE.PlaneGeometry(20, 20), new THREE.MeshStandardMaterial({ color: '#e7e7e5', roughness: 0.8 }));
floor.position.z = -0.01; scene.add(floor);
const keyLight = new THREE.DirectionalLight(0xffffff, 3); keyLight.position.set(-3, -4, 8); scene.add(keyLight);
scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8a8a, 2));
const camera = new THREE.PerspectiveCamera(38, 256/192, 0.1, 100);
camera.up.set(0,0,1); camera.position.set(5,-7,5); camera.lookAt(0,0.6,0.4); camera.updateMatrixWorld();
const runtime = { THREE, renderer, scene, modelGroup, photographicStudio: { ground: floor, keyLight }, previewConfiguration: { exposure: 0, lighting: { size: 1, fill: 0.25, rotation: 0 } }, previewQuality: 'preview' };
let controller, queued = false, moving = false, draws = 0, preparationFrames = 0, changeQuality = false, qualityChanged = false;
const longTasks = [];
new PerformanceObserver(list => longTasks.push(...list.getEntries().map(e => e.duration))).observe({type:'longtask',buffered:true});
const requestRender = () => { if (queued) return; queued = true; requestAnimationFrame(draw); };
function draw() {
  queued = false; renderer.render(scene,camera); draws++;
  if (changeQuality && controller.progress.motion && controller.progress.stage === 'filter-start') {
    changeQuality = false; qualityChanged = true; runtime.previewConfiguration.performance = { mode: 'manual', value: 100 };
  }
  const result = controller.render(renderer,scene,camera,{still:!moving,moving,sceneChanged:false});
  if(result.refining)requestRender();
}
const create = () => {
  controller = createProgressivePreview(runtime,{requestRender}); requestRender();
  const preparing = () => { if(controller.progress.state === 'preparing') preparationFrames++; if(['idle','preparing'].includes(controller.progress.state)) requestAnimationFrame(preparing); };
  requestAnimationFrame(preparing);
};
window.previewTest = {
  async filterProbe() {
    const adapter = await navigator.gpu.requestAdapter(), device = await adapter.requestDevice();
    const width = 5, height = 3, row = 256;
    const source = { width, height }, albedo = {}, normal = {};
    const color = new Float32Array(width * height * 4), colors = new Uint8Array(width * height * 4), normals = new Uint8Array(width * height * 4);
    for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
      const i = (y * width + x) * 4;
      if (x === 0) { color.set([7, 7, 7, 0], i); continue; }
      const red = x < 3;
      color.set(red ? [4, 0, 0, x === 1 ? 0.25 : 1] : [0, 0, 4, 1], i);
      colors.set(red ? [255, 0, 0, 255] : [0, 0, 255, 255], i);
      normals.set([128, 128, 255, 255], i);
    }
    const texture = (format, data, bytesPerRow) => {
      const result = device.createTexture({ size: [width, height], format, usage: GPUTextureUsage.TEXTURE_BINDING | GPUTextureUsage.COPY_DST });
      device.queue.writeTexture({ texture: result }, data, { bytesPerRow }, [width, height]);
      return result;
    };
    const sourceTexture = texture('rgba32float', color, width * 16), albedoTexture = texture('rgba8unorm', colors, width * 4), normalTexture = texture('rgba8unorm', normals, width * 4);
    const mappings = new Map([[source, sourceTexture], [albedo, albedoTexture], [normal, normalTexture]]);
    const filter = new PreviewFastFilter({ initTexture() {}, backend: { device, get: value => ({ texture: mappings.get(value) }) } });
    const buffer = device.createBuffer({ size: row * height, usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ });
    const half = value => {
      const sign = value & 0x8000 ? -1 : 1, exponent = (value >> 10) & 31, fraction = value & 1023;
      if (exponent === 0) return sign * 2 ** -14 * fraction / 1024;
      if (exponent === 31) return fraction ? NaN : sign * Infinity;
      return sign * 2 ** (exponent - 15) * (1 + fraction / 1024);
    };
    try {
      device.pushErrorScope('validation');
      filter.apply(source, { textures: [albedo, normal] });
      const encoder = device.createCommandEncoder();
      encoder.copyTextureToBuffer({ texture: filter.raw }, { buffer, bytesPerRow: row }, [width, height]);
      device.queue.submit([encoder.finish()]);
      await buffer.mapAsync(GPUMapMode.READ);
      const data = new DataView(buffer.getMappedRange());
      const pixel = (x, y) => Array.from({ length: 4 }, (_, c) => half(data.getUint16(y * row + x * 8 + c * 2, true)));
      const result = { empty: pixel(0, 1), translucent: pixel(1, 1), red: pixel(2, 1), blue: pixel(3, 1) };
      buffer.unmap();
      const error = await device.popErrorScope();
      if (error) throw new Error(error.message);
      return result;
    } finally { filter.dispose(); buffer.destroy(); sourceTexture.destroy(); albedoTexture.destroy(); normalTexture.destroy(); device.destroy(); }
  },
  start: create,
  configurePreparing: () => { renderer.setSize(288,216); runtime.previewConfiguration.performance = { mode: 'manual', value: 100 }; requestRender(); },
  restoreAuto: () => { renderer.setSize(256,192); runtime.previewConfiguration.performance = { mode: 'auto', value: 50 }; requestRender(); },
  progress: () => ({ ...controller.progress, draws, preparationFrames, longTasks: [...longTasks] }),
  move: () => { moving=true; repeat.position.x=2; controller.invalidate({sceneChanged:true}); draw(); },
  orbit: () => { camera.position.set(6,-6,5); camera.lookAt(0,0.6,0.4); camera.updateMatrixWorld(); controller.invalidate({sceneChanged:false}); draw(); },
  stop: () => { moving=false; requestRender(); },
  releaseAtNewCamera: () => { moving=false; camera.position.x += 1; camera.lookAt(0,0.6,0.4); camera.updateMatrixWorld(); controller.invalidate({sceneChanged:false}); draw(); return camera.position.toArray(); },
  changeQualityDuringMotion: () => {
    changeQuality = true;
    const poll = () => { if (!changeQuality) return; draw(); if (changeQuality) setTimeout(poll, 0); };
    poll();
  },
  qualityChanged: () => qualityChanged,
  previewQuality: () => { runtime.previewConfiguration.performance = { mode: 'auto', value: 50 }; requestRender(); },
  sustainedOrbit: () => { moving=true; let n=0; const tick=() => { if (!moving) return; n++; camera.position.set(6+0.8*Math.sin(n*0.02),-6+0.8*Math.cos(n*0.02),5); camera.lookAt(0,0.6,0.4); camera.updateMatrixWorld(); controller.invalidate({sceneChanged:false}); draw(); requestAnimationFrame(tick); }; tick(); },
  transparent: () => { camera.position.set(0,-3,18); camera.lookAt(0,0,0); camera.updateMatrixWorld(); scene.background=null; renderer.setClearAlpha(0); floor.scale.setScalar(0.5); floor.material.opacity=0.6; floor.material.transparent=true; runtime.photographicStudio.floorFade={value:0.8}; controller.invalidate({sceneChanged:true}); requestRender(); },
  emptyPatch: () => {
    const ray = new THREE.Raycaster();
    scene.updateMatrixWorld(true);
    for (let y=0; y<176; y+=8) for (let x=0; x<240; x+=8) {
      let empty=true;
      for (const dy of [0,4,8,12,16]) for (const dx of [0,4,8,12,16]) {
        ray.setFromCamera(new THREE.Vector2((x+dx)/128-1, 1-(y+dy)/96), camera);
        if (ray.intersectObject(floor).length || ray.intersectObject(modelGroup,true).length) empty=false;
      }
      if (empty) return {x,y,size:16};
    }
    return null;
  },
  fadedFloorPatch: () => {
    const ray = new THREE.Raycaster();
    scene.updateMatrixWorld(true);
    for (let y=0; y<184; y+=4) for (let x=0; x<248; x+=4) {
      let empty=true;
      for (const dy of [0,4,8]) for (const dx of [0,4,8]) {
        ray.setFromCamera(new THREE.Vector2((x+dx)/128-1, 1-(y+dy)/96), camera);
        const hit=ray.intersectObject(floor)[0];
        if (!hit || Math.hypot(hit.uv.x*2-1,hit.uv.y*2-1)<1.08 || ray.intersectObject(modelGroup,true).length) empty=false;
      }
      if (empty) return {x,y,size:8};
    }
    return null;
  },
  floorPatch: () => {
    // Find an unobstructed patch inside the floor's constant-opacity region.
    const ray = new THREE.Raycaster();
    scene.updateMatrixWorld(true);
    for (let y=8; y<176; y+=8) for (let x=8; x<240; x+=8) {
      let valid=true;
      for (const dy of [0,8,15]) for (const dx of [0,8,15]) {
        ray.setFromCamera(new THREE.Vector2((x+dx+0.5)/128-1, 1-(y+dy+0.5)/96), camera);
        const hit=ray.intersectObject(floor)[0];
        if (!hit || Math.hypot(hit.uv.x*2-1,hit.uv.y*2-1)>0.65 || ray.intersectObject(modelGroup,true).length) valid=false;
      }
      if (valid) return {x,y,size:16};
    }
    return null;
  },
  dispose: () => { controller.dispose(); },
  source: () => ({ sameGeometry: painted.geometry===repeat.geometry, material: red.color.getHexString(), boundsTree: !!box.boundsTree }),
  canvases: () => host.querySelectorAll('canvas').length,
  rasterOpacity: () => renderer.domElement.style.opacity,
  capture: () => controller.captureCanvas?.toDataURL('image/png') || null,
};
renderer.render(scene,camera);
window.previewReady=true;
`;

// This is the actual launch branch below, not a guess about adapter capability.
const hardwareLaunch = process.platform === 'darwin' && process.env.CAD_TEST_SWIFTSHADER !== '1';
let browser, server, artifacts;
before(async () => {
  if (process.env.CAD_TRACE_ARTIFACTS) artifacts = await mkdtemp(join(tmpdir(), 'preview-tracing-review-'));
  const root = fileURLToPath(new URL('../../../../../../', import.meta.url));
  const result = await build({ stdin: { contents: fixture, resolveDir: root, sourcefile: 'preview-fixture.js' },
    bundle: true, write: false, format: 'esm', platform: 'browser', target: 'esnext' });
  const worker = await build({ entryPoints: [`${root}/packages/core/src/lib/preview-tracing/previewWorker.js`], bundle: true, write: false, format: 'esm', platform: 'browser' });
  const { bundle: uiBundle } = await harnessBundle();
  const css = await readFile(`${root}/packages/ui/dist/styles.css`);
  const glb = await readFile(`${root}/packages/core/src/lib/render/fixtures/pair.glb`);
  const weights = await readFile(`${root}/packages/core/src/lib/preview-tracing/weights/rt_hdr_calb_cnrm.tza`);
  const uiWorkers = new Map();
  for (const relative of ['packages/core/src/lib/render/glbMeshWorker.js', 'packages/core/src/lib/viewer/raycastBvhWorker.js', 'packages/ui/src/renderers/kit/look/studioEnvironment.worker.js']) {
    const output = await build({ entryPoints: [`${root}/${relative}`], bundle: true, write: false, format: 'esm', platform: 'browser' });
    uiWorkers.set(relative.split('/').pop(), output.outputFiles[0].contents);
  }
  server = createServer((request, response) => {
    const url = new URL(request.url, 'http://test');
    const name = url.pathname.split('/').pop();
    if (request.url === '/fixture.js') { response.setHeader('Content-Type', 'text/javascript'); response.end(result.outputFiles[0].contents); }
    else if (request.url === '/previewWorker.js') { response.setHeader('Content-Type', 'text/javascript'); response.end(worker.outputFiles[0].contents); }
    else if (uiWorkers.has(name)) { response.setHeader('Content-Type', 'text/javascript'); response.end(uiWorkers.get(name)); }
    else if (name === 'ui-harness.js') { response.setHeader('Content-Type', 'text/javascript'); response.end(uiBundle); }
    else if (name === 'styles.css') { response.setHeader('Content-Type', 'text/css'); response.end(css); }
    else if (name === 'catalog') { response.setHeader('Content-Type', 'application/json'); response.end(JSON.stringify({ entries: [{ kind: 'glb', file: '/models/pair.glb', url: '/pair.glb', hash: 'painted-pair', bytes: glb.length }] })); }
    else if (name === 'server') { response.setHeader('Content-Type', 'application/json'); response.end(JSON.stringify({ backend: 'cadgen' })); }
    else if (name === 'rt_hdr_calb_cnrm.tza') { response.setHeader('Content-Type', 'application/octet-stream'); response.end(weights); }
    else if (name === 'pair.glb') { response.setHeader('Content-Type', 'model/gltf-binary'); response.end(glb); }
    else if (url.pathname === '/ui/') { response.setHeader('Content-Type', 'text/html'); response.end('<!doctype html><link rel="stylesheet" href="/styles.css"><style>#root>div{width:1000px!important;height:700px!important}</style><div id="root"></div><script type="module" src="/ui-harness.js"></script>'); }
    else { response.setHeader('Content-Type', 'text/html'); response.end('<!doctype html><style>body{margin:0}#view{position:relative;width:256px;height:192px}</style><div id="view"></div><script type="module" src="/fixture.js"></script>'); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  browser = await chromium.launch({ headless: true, args: hardwareLaunch
    ? ['--use-angle=metal'] : ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
});
after(async () => { await browser?.close(); if (server) await new Promise(resolve => server.close(resolve)); });

async function open(t, fallback = false) {
  const page = await browser.newPage({ viewport: { width: 256, height: 192 } });
  t.after(() => page.close());
  const errors = [], logs = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', entry => { if (entry.type() === 'error' || entry.type() === 'warn') logs.push(entry.text()); });
  if (fallback) await page.addInitScript(() => Object.defineProperty(navigator, 'gpu', { value: undefined }));
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.waitForFunction(() => window.previewReady);
  return { page, errors, logs };
}

test('a browser without WebGPU reports an unavailable tracer and disposes cleanly', async t => {
  const { page, errors } = await open(t, true);
  await page.evaluate(() => window.previewTest.start());
  await page.waitForFunction(() => window.previewTest.progress().state === 'fallback');
  assert.equal(await page.evaluate(() => window.previewTest.capture()), null);
  assert.equal(await page.evaluate(() => window.previewTest.canvases()), 1);
  await page.evaluate(() => window.previewTest.dispose());
  assert.equal(await page.evaluate(() => window.previewTest.canvases()), 1);
  assert.deepEqual(errors, []);
});

test('Preview offers retry and exit when WebGPU is unavailable', async t => {
  const page = await browser.newPage({ viewport: { width: 1000, height: 700 } });
  t.after(() => page.close());
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => Object.defineProperty(navigator, 'gpu', { value: undefined }));
  await page.goto(`http://127.0.0.1:${server.address().port}/ui/?file=pair.glb`);
  const pane = page.getByTestId('one');
  await page.waitForFunction(() => window.cadHarness?.a.controller?.readState().loading === false);
  await page.evaluate(() => { const controller = window.cadHarness.a.controller; controller.setCamera(controller.readState().camera); });
  await page.waitForFunction(() => window.__cadCamera()?.untouched === false && !window.__cadCamera()?.settling);
  const editorCanvas = pane.locator('[aria-busy] > div > canvas').first();
  const editor = PNG.sync.read(await editorCanvas.screenshot({ animations: 'disabled' }));
  await pane.getByRole('button', { name: 'Preview', exact: true }).click();
  const cover = pane.locator('[data-preview-cover]');
  await cover.getByRole('alert').waitFor({ state: 'visible' });
  assert.match(await cover.innerText(), /WebGPU unavailable/);
  await cover.getByRole('button', { name: 'Retry', exact: true }).click();
  await cover.getByRole('alert').waitFor({ state: 'visible' });
  await cover.getByRole('button', { name: 'Exit preview', exact: true }).click();
  await pane.locator('[data-preview-view]').waitFor({ state: 'detached' });
  await page.mouse.move(0, 0);
  const restored = PNG.sync.read(await editorCanvas.screenshot({ animations: 'disabled' }));
  let maxDelta = 0, changed = 0, squared = 0;
  for (let i = 0; i < editor.data.length; i++) { const delta = Math.abs(restored.data[i] - editor.data[i]); maxDelta = Math.max(maxDelta, delta); squared += delta * delta; if (delta) changed++; }
  t.diagnostic(`Failed Preview editor pixel variation ${JSON.stringify({ maxDelta, changed, rms: Math.sqrt(squared / editor.data.length) })}`);
  assert.deepEqual([restored.width, restored.height], [editor.width, editor.height]);
  assert.ok(maxDelta <= 1, 'leaving failed Preview restores the editor pixels within one 8-bit level');
  assert.deepEqual(errors, []);
});

test('the real Preview viewport traces, captures and restores the editor on exit', async t => {
  if (!hardwareLaunch) return t.skip('This browser launch explicitly uses SwiftShader; real hardware tracing is tested on Metal.');
  const page = await browser.newPage({ viewport: { width: 1000, height: 700 }, deviceScaleFactor: 1 });
  t.after(() => page.close());
  await page.addInitScript(() => {
    window.previewResourceAudit = { workers: [], adapters: 0, devices: 0, probe: false };
    const NativeWorker = Worker;
    window.Worker = class extends NativeWorker {
      constructor(...args) { super(...args); this.record = { url: String(args[0]), terminated: false }; window.previewResourceAudit.workers.push(this.record); }
      terminate() { this.record.terminated = true; return super.terminate(); }
    };
    if (navigator.gpu) {
      const requestAdapter = navigator.gpu.requestAdapter.bind(navigator.gpu);
      navigator.gpu.requestAdapter = (...args) => { if (window.previewResourceAudit.probe) window.previewResourceAudit.probe = false; else window.previewResourceAudit.adapters++; return requestAdapter(...args); };
    }
    if (globalThis.GPUAdapter) {
      const requestDevice = GPUAdapter.prototype.requestDevice;
      GPUAdapter.prototype.requestDevice = function(...args) { window.previewResourceAudit.devices++; return requestDevice.apply(this, args); };
    }
  });
  await page.goto(`http://127.0.0.1:${server.address().port}/ui/?file=pair.glb`);
  const adapter = await page.evaluate(async () => { window.previewResourceAudit.probe = true; try { const a = await navigator.gpu?.requestAdapter(); return a && !a.info.isFallbackAdapter; } finally { window.previewResourceAudit.probe = false; } });
  if (!adapter) return t.skip('A hardware WebGPU adapter is unavailable.');
  const pane = page.getByTestId('one');
  await page.waitForFunction(() => window.cadHarness?.a.controller?.readState().loading === false);
  const resources = () => page.evaluate(() => ({
    previewWorkers: window.previewResourceAudit.workers.filter(worker => /previewWorker/i.test(worker.url)),
    adapters: window.previewResourceAudit.adapters, devices: window.previewResourceAudit.devices,
  }));
  assert.deepEqual(await resources(), { previewWorkers: [], adapters: 0, devices: 0 }, 'the editor starts no Preview worker or WebGPU device');
  // Make this the person's chosen camera, so the pending opening fit cannot
  // change the baseline while Preview is active.
  await page.evaluate(() => { const controller = window.cadHarness.a.controller; controller.setCamera(controller.readState().camera); });
  await page.waitForFunction(() => window.__cadCamera()?.untouched === false && !window.__cadCamera()?.settling);
  const editorCanvas = pane.locator('[aria-busy] > div > canvas').first();
  await pane.getByRole('group', { name: 'Interaction tools' }).getByRole('button', { name: 'Pencil', exact: true }).click();
  const editorRect = await editorCanvas.boundingBox();
  const inkX = editorRect.x + editorRect.width * 0.35, inkY = editorRect.y + editorRect.height * 0.5;
  await page.mouse.move(inkX, inkY); await page.mouse.down();
  await page.mouse.move(inkX + 35, inkY + 10, { steps: 8 }); await page.mouse.up();
  await pane.locator('[data-quick-edit-ref="annotation"]').waitFor({ state: 'visible' });
  await pane.getByRole('group', { name: 'Interaction tools' }).getByRole('button', { name: 'Select', exact: true }).click();
  await page.mouse.move(0, 0);
  const editorBeforeA = PNG.sync.read(await editorCanvas.screenshot({ animations: 'disabled' }));
  const editorBefore = PNG.sync.read(await editorCanvas.screenshot({ animations: 'disabled' }));
  const camera = await page.evaluate(() => window.__cadCamera());
  await page.evaluate(() => {
    window.previewTimings = { start: performance.now(), first: null, converged: null, longTasks: [] };
    new PerformanceObserver(list => window.previewTimings.longTasks.push(...list.getEntries().map(e => e.duration))).observe({type:'longtask'});
    const measure = () => {
      const p = window.__cadStage()?.pathTracing;
      if (p?.denoisedSamples > 0 && !window.previewTimings.first) window.previewTimings.first = performance.now() - window.previewTimings.start;
      if (p?.denoisedSamples >= p?.samples && p?.denoisedSamples > 0) window.previewTimings.converged = performance.now() - window.previewTimings.start;
      else requestAnimationFrame(measure);
    }; requestAnimationFrame(measure);
  });
  await pane.getByRole('button', { name: 'Preview', exact: true }).click();
  await page.waitForFunction(() => { const p = window.__cadStage()?.pathTracing; return p?.state === 'fallback' || (p?.denoisedSamples > 0 && p.denoisedSamples >= p.samples); }, null, { timeout: 120000 })
    .catch(async error => { throw new Error(`${error.message}; stage=${JSON.stringify(await page.evaluate(() => window.__cadStage()))}`); });
  const progress = await page.evaluate(() => window.__cadStage().pathTracing);
  assert.equal((await resources()).previewWorkers.filter(worker => !worker.terminated).length, 1, 'Preview owns one rendering worker');
  assert.notEqual(progress.state, 'fallback', JSON.stringify(progress));
  t.diagnostic(`Real Preview ${JSON.stringify(await page.evaluate(() => ({...window.previewTimings, progress: window.__cadStage().pathTracing})))}`);
  if (artifacts) await page.screenshot({path: join(artifacts, 'preview-ui-traced.png')});
  // The raster canvas remains the controls' event target even when the traced
  // picture covers it. Exercise a real wheel gesture after convergence.
  const rasterCanvas = pane.locator('[data-preview-view] canvas').first();
  const rect = await rasterCanvas.boundingBox();
  assert.ok(rect, 'the viewport canvas is present');
  const x = rect.x + rect.width / 2, y = rect.y + rect.height / 2;
  assert.equal(await rasterCanvas.evaluate((canvas, {x, y}) => document.elementFromPoint(x, y) === canvas, {x, y}), true,
    'the traced overlay keeps the controls canvas hit-testable');
  const beforeWheel = await page.evaluate(() => window.__cadCamera());
  await page.mouse.move(x, y); await page.mouse.wheel(0, -100);
  await page.waitForFunction(before => {
    const next = window.__cadCamera();
    return Math.abs(next.zoom - before.zoom) > 1e-6 || next.position.some((v, i) => Math.abs(v - before.position[i]) > 1e-6);
  }, beforeWheel, {timeout: 10000});
  await page.waitForFunction(() => window.__cadStage()?.pathTracing?.denoisedSamples > 0, null, {timeout: 120000});
  const beforeDrag = await page.evaluate(() => window.__cadCamera());
  await page.mouse.move(x, y); await page.mouse.down();
  await page.mouse.move(x + 45, y + 10, {steps: 4}); await page.mouse.up();
  await page.waitForFunction(before => {
    const next = window.__cadCamera();
    return next.position.some((v, i) => Math.abs(v - before.position[i]) > 1e-6);
  }, beforeDrag, {timeout: 10000});
  await page.waitForFunction(() => window.__cadStage()?.pathTracing?.denoisedSamples > 0, null, {timeout: 120000});
  const capture = await page.evaluate(async () => {
    const blob = await window.cadHarness.a.controller.capture();
    const bytes = new Uint8Array(await blob.arrayBuffer());
    let binary = ''; for (const byte of bytes) binary += String.fromCharCode(byte);
    return btoa(binary);
  });
  const image = PNG.sync.read(Buffer.from(capture, 'base64'));
  const colors = new Set();
  for (let i=0; i<image.data.length; i+=4) colors.add(`${image.data[i]>>4},${image.data[i+1]>>4},${image.data[i+2]>>4}`);
  assert.ok(colors.size > 20, 'the captured Preview contains shaded model pixels');
  await pressInPreview(page, pane, pane.getByRole('button', { name: 'Preview settings', exact: true }));
  const settings = pane.getByRole('region', { name: 'Preview settings', exact: true });
  await settings.getByRole('checkbox', { name: 'Auto quality', exact: true }).click();
  const finalStart = await page.evaluate(() => performance.now());
  const quality = settings.getByRole('textbox', { name: 'Quality value', exact: true });
  await quality.fill('100'); await quality.press('Enter');
  t.diagnostic(`Quality applied ${JSON.stringify(await page.evaluate(() => ({stage:window.__cadStage(), record:window.cadHarness.state})))}`);
  await pressInPreview(page, pane, pane.getByRole('button', { name: 'Preview settings', exact: true }));
  await page.waitForFunction(previousBudget => { const p = window.__cadStage()?.pathTracing; return p?.samples > previousBudget && p.denoisedSamples >= p.samples; }, progress.samples, {timeout: 180000})
    .catch(async error => { throw new Error(`${error.message}; ${JSON.stringify(await page.evaluate(() => ({stage:window.__cadStage(),record:window.cadHarness.state, body:document.body.innerText})))}`); });
  assert.equal((await resources()).previewWorkers.length, 1, 'quality-only edits preserve the existing worker and scene');
  t.diagnostic(`Final quality ${JSON.stringify(await page.evaluate(start => ({elapsed:performance.now()-start, progress:window.__cadStage().pathTracing}), finalStart))}`);
  if (artifacts) await page.screenshot({path:join(artifacts, 'preview-ui-final.png')});
  await pressInPreview(page, pane, pane.getByRole('button', { name: 'Exit preview', exact: true }));
  await pane.locator('[data-preview-view]').waitFor({state:'detached'});
  assert.equal(await page.evaluate(() => window.__cadStage().pathTracing), null, 'the editor owns no Preview tracer');
  const released = await resources();
  assert.ok(released.previewWorkers.every(worker => worker.terminated), 'exit terminates every Preview worker and its owned GPU context');
  assert.equal(released.devices, 0, 'Preview never requests a main-thread WebGPU device');
  await pane.locator('[data-quick-edit-ref="annotation"]').waitFor({ state: 'visible' });
  await page.mouse.move(0, 0);
  const editorAfter = PNG.sync.read(await editorCanvas.screenshot({ animations: 'disabled' }));

  const restored = await page.evaluate(() => window.__cadCamera());
  // An orthographic eye's distance is fitted to the current viewport/depth range;
  // the picture's pose is its direction, target and zoom.
  const pose = c => {
    const direction = c.position.map((v, i) => v-c.target[i]);
    const length = Math.hypot(...direction);
    return [...direction.map(v => v/length), ...c.target, c.zoom];
  };
  pose(camera).forEach((value, index) => assert.ok(Math.abs(value-pose(restored)[index]) < 1e-6, 'exit restores the editor camera pose'));
  const pixelDifference = (a,b) => {
    let changed = 0, squared = 0, max = 0;
    for (let i=0; i<a.data.length; i++) { const delta = Math.abs(a.data[i]-b.data[i]); if(delta)changed++; squared += delta*delta; max = Math.max(max,delta); }
    return { changed, rms: Math.sqrt(squared/a.data.length), max };
  };
  t.diagnostic(`Editor raster variation ${JSON.stringify({ baseline:pixelDifference(editorBeforeA,editorBefore), after:pixelDifference(editorBefore,editorAfter), camera, restored })}`);
  if (artifacts) {
    for (const [name,image] of [['before-a',editorBeforeA],['before-b',editorBefore],['after',editorAfter]]) await writeFile(join(artifacts, `editor-${name}.png`), PNG.sync.write(image));
    t.diagnostic(`Editor artifacts ${artifacts}`);
  }
  assert.deepEqual(editorAfter.data, editorBefore.data, 'Preview settings and exit preserve the editor pixels, including annotation ink');
});

test('hardware Preview converges on authored parts, resets for motion and releases its canvas on every exit', async t => {
  if (!hardwareLaunch) return t.skip('This browser launch explicitly uses SwiftShader; real hardware tracing is tested on Metal.');
  const { page, errors, logs } = await open(t);
  const adapter = await page.evaluate(async () => { const a = await navigator.gpu?.requestAdapter(); return a ? { vendor: a.info.vendor, architecture: a.info.architecture, fallback: a.info.isFallbackAdapter } : null; });
  if (!adapter || adapter.fallback) return t.skip('A hardware WebGPU adapter is unavailable; the unavailable-device state is tested separately.');
  t.diagnostic(`WebGPU ${JSON.stringify(adapter)}`);
  const filter = await page.evaluate(() => window.previewTest.filterProbe());
  assert.ok(Object.values(filter).flat().every(Number.isFinite), 'spatial-filter output is finite');
  assert.deepEqual(filter.empty, [0, 0, 0, 0], 'fully empty background remains empty');
  assert.ok(filter.translucent[3] > 0.25 && filter.translucent[3] < 1, 'fractional coverage survives filtering');
  assert.ok(filter.translucent[0] > 3.9 && filter.translucent[2] < 0.01, 'premultiplied filtering avoids a dark transparent fringe');
  assert.ok(filter.red[0] > 3.9 && filter.red[2] < 0.01 && filter.blue[2] > 3.9 && filter.blue[0] < 0.01, 'albedo guide preserves the material boundary');
  await page.evaluate(() => window.previewTest.start());
  await page.waitForFunction(() => window.previewTest.progress().state === 'preparing');
  await page.evaluate(() => window.previewTest.configurePreparing());
  await page.waitForFunction(() => ['ready', 'fallback'].includes(window.previewTest.progress().state), null, { timeout: 90000 });
  const firstReady = await page.evaluate(() => window.previewTest.progress());
  assert.notEqual(firstReady.state, 'fallback', JSON.stringify(firstReady));
  assert.deepEqual([firstReady.samples, firstReady.renderWidth, firstReady.renderHeight], [256, 288, 216], 'preparation reconciles the newest quality and viewport before its first picture');
  await page.evaluate(() => window.previewTest.restoreAuto());
  await page.waitForFunction(() => { const p = window.previewTest.progress(); return p.state === 'fallback' || (p.renderWidth === 256 && p.renderHeight === 192 && p.samples < 256 && p.denoisedSamples > 0 && p.denoisedSamples >= p.samples); }, null, { timeout: 90000 })
    .catch(async error => { throw new Error(`${error.message}; ${JSON.stringify(await page.evaluate(() => window.previewTest.progress()))}; ${logs.join('\n')}`); });
  const progress = await page.evaluate(() => window.previewTest.progress());
  if (progress.state === 'fallback') t.diagnostic(logs.join('\n'));
  assert.notEqual(progress.state, 'fallback', `hardware tracing must work: ${JSON.stringify(progress)}`);
  t.diagnostic(`Converged ${JSON.stringify(progress)}`);
  assert.ok(progress.preparationFrames > 0, 'preparation yields to browser frames while work is pending');
  const encoded = await page.evaluate(() => window.previewTest.capture());
  assert.ok(encoded, 'the converged picture is capturable');
  if (artifacts) {
    await page.locator('#view').screenshot({path: join(artifacts, 'preview-trace-visible.png')});
    await writeFile(join(artifacts, 'preview-trace-capture.png'), Buffer.from(encoded.split(',')[1], 'base64'));
  }
  const image = PNG.sync.read(Buffer.from(encoded.split(',')[1], 'base64'));
  let red = 0, blue = 0, dark = 0;
  for (let i = 0; i < image.data.length; i += 4) {
    const [r,g,b] = image.data.subarray(i,i+3);
    if (r > g * 1.3 && r > b * 1.3) red++;
    if (b > r * 1.25 && b > g * 1.1) blue++;
    if (r+g+b < 450) dark++;
  }
  assert.ok(red > 30 && blue > 30 && dark > 100, `authored face paints and shaded geometry survive: ${JSON.stringify({red,blue,dark})}`);
  assert.deepEqual(await page.evaluate(() => window.previewTest.source()), { sameGeometry: true, material: 'd22822', boundsTree: false }, 'tracing leaves shared source geometry and authored materials untouched');
  await page.evaluate(() => window.previewTest.orbit());
  assert.ok(await page.evaluate(() => window.previewTest.capture()), 'a new camera retains the last traced picture while it updates');
  await page.waitForFunction(() => { const p = window.previewTest.progress(); return p.denoisedSamples > 0 && window.previewTest.capture() !== null; }, null, { timeout: 120000 });
  const previousFrames = await page.evaluate(() => window.previewTest.progress().presentedFrames);
  await page.evaluate(() => window.previewTest.sustainedOrbit());
  await page.waitForFunction(count => window.previewTest.progress().presentedFrames >= count + 2, previousFrames, { timeout: 120000 });
  assert.ok(await page.evaluate(() => window.previewTest.progress().motion), 'continuous camera motion presents new traced frames');
  assert.equal((await page.evaluate(() => window.previewTest.progress())).filterMode, 'spatial', 'motion uses the full-resolution guided filter');
  const motion = await page.evaluate(() => window.previewTest.progress());
  assert.deepEqual([motion.traceWidth, motion.traceHeight], [motion.renderWidth, motion.renderHeight], 'motion keeps the admitted image resolution even when the ray pool is smaller');
  const finalPosition = await page.evaluate(() => window.previewTest.releaseAtNewCamera());
  await page.waitForFunction(position => {
    const p = window.previewTest.progress();
    return !p.motion && p.filterMode === 'spatial' && p.denoisedSamples >= p.motionSamples
      && p.presentedCamera?.position.every((value, i) => Math.abs(value - position[i]) < 1e-7);
  }, finalPosition, { timeout: 120000 });
  await page.evaluate(() => window.previewTest.sustainedOrbit());
  await page.evaluate(() => window.previewTest.changeQualityDuringMotion());
  await page.waitForFunction(() => window.previewTest.qualityChanged());
  await page.waitForFunction(() => { const p = window.previewTest.progress(); return (p.samples === 256 && p.motion) || p.state === 'fallback'; });
  assert.notEqual((await page.evaluate(() => window.previewTest.progress())).state, 'fallback', 'quality changes cancel an interactive denoise safely');
  const beforeRelease = await page.evaluate(() => ({ frames: window.previewTest.progress().presentedFrames, image: window.previewTest.capture() }));
  await page.evaluate(() => window.previewTest.stop());
  await page.waitForFunction(count => { const p = window.previewTest.progress(); return p.presentedFrames > count && !p.motion && p.denoisedSamples >= p.motionSamples; }, beforeRelease.frames, { timeout: 120000 });
  const released = await page.evaluate(() => ({ ...window.previewTest.progress(), image: window.previewTest.capture() }));
  assert.notEqual(released.image, beforeRelease.image, 'release presents the latest camera instead of the latched older view');
  await page.waitForFunction(count => { const p = window.previewTest.progress(); return p.sample > count && p.frames > 0; }, released.motionSamples, { timeout: 120000 });
  const refined = await page.evaluate(() => window.previewTest.progress());
  assert.ok(refined.frames >= released.frames, 'settled refinement continues from the release image without resetting accumulation');
  await page.evaluate(() => window.previewTest.previewQuality());
  await page.evaluate(() => window.previewTest.move());
  assert.ok(await page.evaluate(() => window.previewTest.capture()), 'scene motion retains a traced picture');
  await page.evaluate(() => window.previewTest.stop());
  await page.waitForFunction(() => { const p = window.previewTest.progress(); return p.denoisedSamples > 0; }, null, { timeout: 120000 });
  // A denoised image must keep the tracer's transparent background coverage.
  await page.evaluate(() => window.previewTest.transparent());
  await page.waitForFunction(() => window.previewTest.progress().denoisedSamples > 0 && window.previewTest.capture() !== null, null, { timeout: 120000 });
  const transparentEncoded = await page.evaluate(() => window.previewTest.capture());
  const transparentImage = PNG.sync.read(Buffer.from(transparentEncoded.split(',')[1], 'base64'));
  let clear = 0, covered = 0;
  for (let i = 3; i < transparentImage.data.length; i += 4) {
    if (transparentImage.data[i] < 5) clear++;
    if (transparentImage.data[i] > 200) covered++;
  }
  assert.ok(clear > 100 && covered > 100, `transparent background and model coverage survive denoising: ${JSON.stringify({clear, covered})}`);
  const empty = await page.evaluate(() => window.previewTest.emptyPatch());
  assert.ok(empty, 'fixture provides a clear background patch');
  for (let y=empty.y; y<empty.y+empty.size; y++) for (let x=empty.x; x<empty.x+empty.size; x++) {
    assert.equal(transparentImage.data[(y*transparentImage.width+x)*4+3], 0, 'empty background remains exactly transparent after denoising');
  }
  const faded = await page.evaluate(() => window.previewTest.fadedFloorPatch());
  assert.ok(faded, 'fixture provides a fully faded floor patch');
  for (let y=faded.y; y<faded.y+faded.size; y++) for (let x=faded.x; x<faded.x+faded.size; x++) {
    assert.equal(transparentImage.data[(y*transparentImage.width+x)*4+3], 0, 'fully faded floor remains exactly transparent');
  }
  const patch = await page.evaluate(() => window.previewTest.floorPatch());
  assert.ok(patch, 'fixture provides an unobstructed constant-opacity floor patch');
  const alpha = [];
  for (let y=patch.y; y<patch.y+patch.size; y++) for (let x=patch.x; x<patch.x+patch.size; x++) {
    alpha.push(transparentImage.data[(y*transparentImage.width+x)*4+3]);
  }
  const mean = alpha.reduce((sum, value) => sum+value, 0)/alpha.length;
  const deviation = Math.sqrt(alpha.reduce((sum, value) => sum+(value-mean)**2, 0)/alpha.length);
  assert.ok(Math.abs(mean/255-0.6)<0.02 && deviation<2, `guided floor coverage retains opacity without grain: ${JSON.stringify({mean, deviation, patch})}`);
  for (let visit = 0; visit < 2; visit++) {
    await page.evaluate(() => window.previewTest.dispose());
    assert.equal(await page.evaluate(() => window.previewTest.canvases()), 1, 'exit detaches the traced canvas');
    assert.equal(await page.evaluate(() => window.previewTest.rasterOpacity()), '0.8', 'exit restores the exact inline raster opacity');
    if (visit === 0) {
      await page.evaluate(() => window.previewTest.start());
      await page.waitForFunction(() => window.previewTest.capture() !== null, null, { timeout: 120000 });
    }
  }
  assert.deepEqual(errors, []);
});
