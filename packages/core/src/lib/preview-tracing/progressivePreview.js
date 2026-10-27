import * as THREE from 'three';
import { WebGPURenderer } from 'three/webgpu';
import { WebGPUPathTracer } from 'three-gpu-pathtracer/webgpu';
import { initUNetFromURL } from 'oidn-webgpu/dist/oidn.js';
import { PreviewDenoiser } from './previewDenoiser.js';
import { OIDN_WEIGHTS_URL } from './weights/index.js';
import { drawPreviewBackdrop } from './backdrop.js';
import { inspectPreviewScene, preparePreviewScene, disposePreviewScene, createPreviewGeometryCache, previewSceneBudget, PREVIEW_LIMITS } from './scene.js';

const FRAME_PATH_BUDGET = 250000;
const RAY_STATE_BYTES = 224;
const MAX_RENDER_PIXELS = 1000000;
const FIRST_DENOISE_SAMPLES = 32, PREVIEW_SAMPLES = 128, FINAL_SAMPLES = 256;
const MAX_BOUNCES = 12, MAX_TRANSPARENT_BOUNCES = 5;
const MIN_FRAME_LIMIT = 2048, MAX_FRAME_LIMIT = 32768, MAX_REFERENCE_FRAMES = 65536;

// The raster remains underneath this optional canvas at every stage. A failed device,
// oversized or unsupported scene is a normal fallback, not a blank Preview.
export function createProgressivePreview(runtime, { requestRender = () => {}, diagnostic = null } = {}) {
  // For reproducible visual comparisons only: a bounded raw reference rendered
  // with this same scene and tracer. There is no viewer control for this profile.
  const rawReferenceSamples = diagnostic?.rawReferenceSamples ?? null;
  if (rawReferenceSamples !== null && (!Number.isInteger(rawReferenceSamples) || rawReferenceSamples < 1 || rawReferenceSamples > 1024)) {
    throw new RangeError('rawReferenceSamples must be an integer between 1 and 1024');
  }
  const rawReference = rawReferenceSamples !== null;
  let state = 'idle', reason = null, generation = 0, cameraEpoch = 0, abort = null, tracer = null;
  let gpu = null, scene = null, camera = null, canvas = null, background = null, capture = null, frames = 0, visible = false;
  let rasterOpacity = null;
  let started = false, disposed = false, cameraDirty = false, measuring = null, sample = 0, frameBudget = FRAME_PATH_BUDGET;
  let inFlight = null, denoiser = null, denoisedSamples = 0, denoisingSamples = 0, budgetExhausted = false;
  const cache = createPreviewGeometryCache();
  let maxSamples = rawReferenceSamples ?? (runtime.previewConfiguration?.quality === 'final' ? FINAL_SAMPLES : PREVIEW_SAMPLES);
  const hide = () => { visible = false; if (canvas) canvas.style.visibility = 'hidden'; if (background) background.style.visibility = 'hidden'; if (rasterOpacity !== null) { runtime.renderer.domElement.style.opacity = rasterOpacity; rasterOpacity = null; } };
  const release = () => {
    hide(); inFlight = null;
    tracer?.dispose(); tracer = null; denoiser = null; denoisedSamples = 0; denoisingSamples = 0;
    disposePreviewScene(scene); scene = null;
    gpu?.dispose(); gpu = null;
    canvas?.remove(); canvas = null; background?.remove(); background = null; capture = null;
    camera = null;
    frames = 0; sample = 0; budgetExhausted = false;
  };
  // OIDN aborts between tiles. Wait for its already-submitted tile before the
  // next trace batch, keeping the GPU queue bounded across gesture and setting changes.
  const fenceDenoise = () => {
    if (!gpu || inFlight) return;
    const token = generation;
    const fence = gpu.backend.device.queue.onSubmittedWorkDone();
    inFlight = fence;
    fence.then(() => {
      if (inFlight !== fence) return;
      inFlight = null;
      if (!disposed && state === 'ready') requestRender();
    }).catch(error => {
      if (inFlight !== fence) return;
      inFlight = null;
      if (token === generation && !disposed && state === 'ready') fail(error, token);
      else if (!disposed && state === 'ready') requestRender();
    });
  };
  const invalidate = ({ sceneChanged = false } = {}) => {
    hide(); cameraEpoch++; measuring = null;
    const wasDenoising = denoiser?.running;
    denoiser?.reset(); denoisedSamples = 0; denoisingSamples = 0;
    if (wasDenoising) fenceDenoise();
    if (sceneChanged) {
      generation++;
      // Abort also rejects an in-flight worker task, so its private geometry is released.
      abort?.abort(); abort = null;
      started = false; state = 'idle'; reason = null; frames = 0; sample = 0; measuring = null; budgetExhausted = false;
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
    maxSamples = rawReferenceSamples ?? (runtime.previewConfiguration?.quality === 'final' ? FINAL_SAMPLES : PREVIEW_SAMPLES);
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
        // The default device exposes only 128 MiB per storage binding even
        // when its adapter can support more. Request no more than our explicit
        // cap; requesting a limit does not allocate the buffer.
        const adapter = await navigator.gpu.requestAdapter({ powerPreference: 'high-performance', featureLevel: 'compatibility' });
        if (!adapter) throw new Error('WebGPU unavailable');
        if (token !== generation || disposed) return;
        const maxBufferSize = Math.min(adapter.limits.maxBufferSize, PREVIEW_LIMITS.bindingBytes);
        const maxStorageBufferBindingSize = Math.min(adapter.limits.maxStorageBufferBindingSize, maxBufferSize);
        const requiredLimits = { maxBufferSize, maxStorageBufferBindingSize };
        localCanvas = document.createElement('canvas');
        const backdrop = document.createElement('canvas');
        localBackdrop = backdrop;
        Object.assign(backdrop.style, { position: 'absolute', inset: '0', width: '100%', height: '100%', pointerEvents: 'none', visibility: 'hidden' });
        runtime.renderer.domElement.parentElement.appendChild(backdrop);
        background = backdrop;
        Object.assign(localCanvas.style, { position: 'absolute', inset: '0', width: '100%', height: '100%', pointerEvents: 'none', visibility: 'hidden' });
        runtime.renderer.domElement.parentElement.appendChild(localCanvas);
        canvas = localCanvas;
        localGpu = new WebGPURenderer({ canvas: localCanvas, alpha: true, antialias: false, powerPreference: 'high-performance', requiredLimits });
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
      const deviceLimits = localGpu.backend.device.limits;
      const deviceBudget = previewSceneBudget(admission, undefined, deviceLimits);
      if (deviceBudget) throw new Error(deviceBudget);
      localScene = await preparePreviewScene(runtime, admission, taskAbort.signal, cache, deviceLimits);
      if (token !== generation || disposed) { disposePreviewScene(localScene); return; }
      camera = sourceCamera.clone();
      if (!tracer) {
        tracer = new WebGPUPathTracer(localGpu);
        denoiser = new PreviewDenoiser({ initUNetFromURL, auxWeightsUrl: OIDN_WEIGHTS_URL,
          maxTileSize: 256, dynamicTile: false }, {
          onComplete: () => { denoisedSamples = denoisingSamples; requestRender(); },
          onError: error => fail(error, generation),
        });
        tracer.setDenoiser(denoiser);
      }
      tracer.generateMissingAttributes = false;
      tracer.maxSamples = maxSamples;
      tracer.maxBounces = MAX_BOUNCES;
      tracer.maxTransparentBounces = MAX_TRANSPARENT_BOUNCES;
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
    if (moving || !still) { if (denoiser?.running || denoiser?.texture) invalidate({ sceneChanged: false }); else hide(); cameraDirty = true; return { refining: false, visible: false }; }
    if (!started) { void prepare(sourceCamera); return { refining: false, visible: false }; }
    if (state !== 'ready') return { refining: false, visible: false };
    // A quality-only setting can extend a converged image without rebuilding
    // geometry or discarding the accumulated samples.
    const desiredSamples = rawReferenceSamples ?? (runtime.previewConfiguration?.quality === 'final' ? FINAL_SAMPLES : PREVIEW_SAMPLES);
    if (desiredSamples !== maxSamples) {
      const wasDenoising = denoiser?.running;
      maxSamples = desiredSamples; tracer.maxSamples = maxSamples; denoisedSamples = 0; denoisingSamples = 0; budgetExhausted = false; hide();
      if (wasDenoising) fenceDenoise();
    }
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
        tracer.updateCamera(); cameraDirty = false; frames = 0; sample = 0; budgetExhausted = false; denoisedSamples = 0; denoisingSamples = 0; cameraEpoch++; measuring = null; hide();
        drawPreviewBackdrop(background, sourceCamera, runtime.photographicStudio, w, h, runtime.renderer.getClearAlpha());
      }
      if (denoiser.running) return { refining: false, visible };
      const estimatedFrames = Math.ceil(w * h / frameBudget * maxSamples * (MAX_BOUNCES + MAX_TRANSPARENT_BOUNCES + 4));
      const limit = Math.min(rawReference ? MAX_REFERENCE_FRAMES : MAX_FRAME_LIMIT, Math.max(MIN_FRAME_LIMIT, estimatedFrames));
      if (frames >= limit && !budgetExhausted) {
        // Wait for an in-flight count before reporting the budget result; then
        // denoise the best available image instead of leaving the first pass up.
        if (measuring) return { refining: false, visible };
        budgetExhausted = true;
      }
      const nextDenoise = budgetExhausted ? sample : (denoisedSamples ? maxSamples : FIRST_DENOISE_SAMPLES);
      if (!rawReference && sample >= nextDenoise && denoisedSamples < nextDenoise) {
        denoisingSamples = sample;
        void denoiser.run(tracer.target);
        return { refining: false, visible };
      }
      const wasPaused = tracer.pause;
      tracer.pause = budgetExhausted;
      try { tracer.renderSample(); } finally { tracer.pause = wasPaused; }
      if (!budgetExhausted) frames++;
      if (denoiser.texture || (rawReference && sample >= 1)) { if (!visible) { rasterOpacity = rasterCanvas.style.opacity; rasterCanvas.style.opacity = '0'; } visible = true; background.style.visibility = 'visible'; canvas.style.visibility = 'visible'; }
      // Readback only occasionally: it is asynchronous and only used to stop the loop.
      if (!budgetExhausted && !measuring && frames % 16 === 0) {
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
      const token = generation;
      const fence = gpu.backend.device.queue.onSubmittedWorkDone();
      inFlight = fence;
      fence.then(() => {
        if (inFlight !== fence) return;
        inFlight = null;
        if (!disposed && state === 'ready' && (token !== generation || cameraDirty || (!budgetExhausted && (frames >= limit || sample < maxSamples || (!rawReference && denoisedSamples < maxSamples))))) requestRender();
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
    get progress() { return { state, sample, samples: maxSamples, frames, denoise: denoiser?.status || 'idle', denoisedSamples, budgetExhausted, denoiseTimings: denoiser?.timings || null, fallbackReason: reason }; },
  };
}
