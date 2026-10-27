import * as THREE from 'three';
import { WebGPURenderer } from 'three/webgpu';
import { WebGPUPathTracer } from 'three-gpu-pathtracer/webgpu';
import { drawPreviewBackdrop } from './backdrop.js';
import { inspectPreviewScene, preparePreviewScene, disposePreviewScene, createPreviewGeometryCache } from './scene.js';

const FRAME_PATH_BUDGET = 250000;
const RAY_STATE_BYTES = 224;
const MAX_RENDER_PIXELS = 1000000;
const PREVIEW_SAMPLES = 32, FINAL_SAMPLES = 64;
const MIN_FRAME_LIMIT = 2048, MAX_FRAME_LIMIT = 4096;

// The raster remains underneath this optional canvas at every stage. A failed device,
// oversized or unsupported scene is a normal fallback, not a blank Preview.
export function createProgressivePreview(runtime, { requestRender = () => {} } = {}) {
  let state = 'idle', reason = null, generation = 0, cameraEpoch = 0, abort = null, tracer = null;
  let gpu = null, scene = null, camera = null, canvas = null, background = null, capture = null, frames = 0, visible = false;
  let rasterVisibility = null;
  let started = false, disposed = false, cameraDirty = false, measuring = null, sample = 0, frameBudget = FRAME_PATH_BUDGET;
  let inFlight = null;
  const cache = createPreviewGeometryCache();
  let maxSamples = runtime.previewConfiguration?.quality === 'final' ? FINAL_SAMPLES : PREVIEW_SAMPLES;
  const hide = () => { visible = false; if (canvas) canvas.style.visibility = 'hidden'; if (background) background.style.visibility = 'hidden'; if (rasterVisibility !== null) { runtime.renderer.domElement.style.visibility = rasterVisibility; rasterVisibility = null; } };
  const release = () => {
    hide(); inFlight = null;
    tracer?.dispose(); tracer = null;
    disposePreviewScene(scene); scene = null;
    gpu?.dispose(); gpu = null;
    canvas?.remove(); canvas = null; background?.remove(); background = null; capture = null;
    camera = null;
    frames = 0; sample = 0;
  };
  const invalidate = ({ sceneChanged = false } = {}) => {
    hide(); cameraEpoch++; measuring = null;
    if (sceneChanged) {
      generation++;
      // Abort also rejects an in-flight worker task, so its private geometry is released.
      abort?.abort(); abort = null;
      started = false; state = 'idle'; reason = null; frames = 0; sample = 0; measuring = null;
    } else {
      cameraDirty = true;
    }
  };
  const fail = (error, token) => {
    if (disposed || token !== generation || state === 'fallback') return;
    state = 'fallback'; reason = error?.message || String(error);
    generation++; cameraEpoch++; measuring = null; abort?.abort(); abort = null;
    release(); cache.dispose();
    requestRender();
  };
  const prepare = async (sourceCamera) => {
    const token = ++generation;
    started = true;
    state = 'preparing'; reason = null;
    maxSamples = runtime.previewConfiguration?.quality === 'final' ? FINAL_SAMPLES : PREVIEW_SAMPLES;
    let localGpu = gpu, localScene = null, localCanvas = null, localBackdrop = null;
    const taskAbort = new AbortController();
    abort = taskAbort;
    try {
      if (!globalThis.navigator?.gpu) throw new Error('WebGPU unavailable');
      if (typeof Worker !== 'function') throw new Error('Worker unavailable');
      if (runtime.previewConfiguration?.lighting?.enabled === false) throw new Error('studio lighting disabled');
      const admission = inspectPreviewScene(runtime.modelGroup);
      if (admission.reason) throw new Error(admission.reason);
      if (!localGpu) {
        localCanvas = document.createElement('canvas');
        const backdrop = document.createElement('canvas');
        localBackdrop = backdrop;
        Object.assign(backdrop.style, { position: 'absolute', inset: '0', width: '100%', height: '100%', pointerEvents: 'none', visibility: 'hidden' });
        runtime.renderer.domElement.parentElement.appendChild(backdrop);
        background = backdrop;
        Object.assign(localCanvas.style, { position: 'absolute', inset: '0', width: '100%', height: '100%', pointerEvents: 'none', visibility: 'hidden' });
        runtime.renderer.domElement.parentElement.appendChild(localCanvas);
        canvas = localCanvas;
        localGpu = new WebGPURenderer({ canvas: localCanvas, alpha: true, antialias: false });
        await localGpu.init();
        if (!localGpu.backend?.isWebGPUBackend) throw new Error('WebGPU unavailable');
        if (token !== generation || disposed) { localGpu.dispose(); localCanvas.remove(); backdrop.remove(); return; }
        gpu = localGpu;
        const device = localGpu.backend.device;
        device.addEventListener('uncapturederror', event => { if (gpu === localGpu) fail(event.error || new Error('WebGPU error'), generation); });
        device.lost.then(info => { if (gpu === localGpu) fail(new Error(info.message || 'WebGPU device lost'), generation); });
      }
      localGpu.toneMapping = runtime.renderer.toneMapping;
      localGpu.toneMappingExposure = runtime.renderer.toneMappingExposure;
      localGpu.setClearColor(0x000000, 0);
      localScene = await preparePreviewScene(runtime, admission, taskAbort.signal, cache);
      if (token !== generation || disposed) { disposePreviewScene(localScene); return; }
      camera = sourceCamera.clone();
      if (!tracer) tracer = new WebGPUPathTracer(localGpu);
      tracer.generateMissingAttributes = false;
      tracer.maxSamples = maxSamples;
      tracer.maxBounces = 5;
      // Upstream's wavefront pool costs 448 bytes per slot across five buffers. The
      // largest is ray state (224 bytes/slot), so respect the device's binding and
      // buffer limits.
      const limits = localGpu.backend.device.limits;
      const perBinding = Math.min(limits.maxStorageBufferBindingSize, limits.maxBufferSize);
      frameBudget = Math.max(1, Math.min(FRAME_PATH_BUDGET, Math.floor(perBinding / RAY_STATE_BYTES)));
      tracer.frameBudget = frameBudget;
      tracer.dynamicLowRes = false;
      tracer.renderDelay = 0;
      tracer.minSamples = 0;
      tracer.setScene(localScene, camera);
      disposePreviewScene(scene);
      scene = localScene;
      state = 'ready'; cameraDirty = true;
      requestRender();
    } catch (error) {
      if (token !== generation || disposed) { if (localScene && localScene !== scene) disposePreviewScene(localScene); if (localGpu && localGpu !== gpu) localGpu.dispose(); localCanvas?.remove(); localBackdrop?.remove(); return; }
      if (localScene && localScene !== scene) disposePreviewScene(localScene);
      if (localGpu && localGpu !== gpu) localGpu.dispose();
      fail(error, token);
    }
  };
  const render = (_renderer, _scene, sourceCamera, { still = true, moving = false, sceneChanged = false } = {}) => {
    if (disposed) return { refining: false, visible: false };
    if (sceneChanged && started) invalidate({ sceneChanged: true });
    if (moving || !still) { hide(); cameraDirty = true; return { refining: false, visible: false }; }
    if (!started) { void prepare(sourceCamera); return { refining: false, visible: false }; }
    if (state !== 'ready') return { refining: false, visible: false };
    // A quality-only setting can extend a converged image without rebuilding
    // geometry or discarding the accumulated samples.
    const desiredSamples = runtime.previewConfiguration?.quality === 'final' ? FINAL_SAMPLES : PREVIEW_SAMPLES;
    if (desiredSamples !== maxSamples) { maxSamples = desiredSamples; tracer.maxSamples = maxSamples; }
    // Do not queue more GPU work than one batch; a slow device can then return to
    // raster as soon as the current batch finishes instead of draining a backlog.
    if (inFlight) return { refining: false, visible };
    try {
      const rasterCanvas = runtime.renderer.domElement;
      const width = Math.max(1, rasterCanvas.width || rasterCanvas.clientWidth);
      const height = Math.max(1, rasterCanvas.height || rasterCanvas.clientHeight);
      const scale = Math.min(1, Math.sqrt(MAX_RENDER_PIXELS / (width * height)));
      const w = Math.max(1, Math.floor(width * scale)), h = Math.max(1, Math.floor(height * scale));
      if (canvas.width !== w || canvas.height !== h) { gpu.setSize(w, h, false); cameraDirty = true; }
      if (cameraDirty) {
        camera.copy(sourceCamera, false); camera.coordinateSystem = THREE.WebGPUCoordinateSystem;
        tracer.updateCamera(); cameraDirty = false; frames = 0; sample = 0; cameraEpoch++; measuring = null; hide();
        drawPreviewBackdrop(background, sourceCamera, runtime.photographicStudio, w, h, runtime.renderer.getClearAlpha());
      }
      tracer.renderSample();
      frames++;
      if (sample >= 1) { if (!visible) { rasterVisibility = rasterCanvas.style.visibility; rasterCanvas.style.visibility = 'hidden'; } visible = true; background.style.visibility = 'visible'; canvas.style.visibility = 'visible'; }
      // Readback only occasionally: it is asynchronous and only used to stop the loop.
      if (!measuring && frames % 16 === 0) {
        const measurement = {};
        measuring = measurement;
        const token = generation, epoch = cameraEpoch;
        tracer.getSampleCountsAsync().then(counts => {
          if (token === generation && epoch === cameraEpoch && !disposed) { sample = counts.min; requestRender(); }
        }).catch(error => { if (epoch === cameraEpoch) fail(error, token); }).finally(() => { if (measuring === measurement) measuring = null; });
      }
      // The fence closes the loop only after the submitted work has completed.
      // A completion from an older scene can wake a new ready scene, but cannot
      // fail it or present its result.
      const limit = Math.min(MAX_FRAME_LIMIT, Math.max(MIN_FRAME_LIMIT, Math.ceil(w * h / frameBudget * maxSamples * 10)));
      const token = generation;
      const fence = gpu.backend.device.queue.onSubmittedWorkDone();
      inFlight = fence;
      fence.then(() => {
        if (inFlight !== fence) return;
        inFlight = null;
        if (!disposed && state === 'ready' && (token !== generation || cameraDirty || (sample < maxSamples && frames < limit))) requestRender();
      }).catch(error => {
        if (inFlight !== fence) return;
        inFlight = null;
        if (token === generation) fail(error, token);
        else if (!disposed && state === 'ready') requestRender();
      });
      return { refining: false, visible };
    } catch (error) { fail(error, generation); return { refining: false, visible: false }; }
  };
  return {
    render, invalidate,
    dispose() { if (disposed) return; disposed = true; generation++; abort?.abort(); abort = null; release(); cache.dispose(); state = 'disposed'; },
    get visible() { return visible; },
    get captureCanvas() {
      if (!visible) return null;
      // WebGPU's presentation texture expires after the frame. Redraw the settled
      // accumulation into a fresh one before the synchronous canvas copy.
      const paused = tracer.pause;
      tracer.pause = true;
      try { tracer.renderSample(); } finally { tracer.pause = paused; }
      capture ||= document.createElement('canvas');
      capture.width = canvas.width; capture.height = canvas.height;
      const context = capture.getContext('2d');
      context.drawImage(background, 0, 0, capture.width, capture.height);
      context.drawImage(canvas, 0, 0);
      return capture;
    },
    get progress() { return { state, sample, samples: maxSamples, frames, fallbackReason: reason }; },
  };
}
