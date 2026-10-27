import * as THREE from 'three';
import { WebGPURenderer } from 'three/webgpu';
import { WebGPUPathTracer } from 'three-gpu-pathtracer/webgpu';
import { RenderToScreenNodeMaterial } from 'three-gpu-pathtracer/src/webgpu/materials/RenderToScreenMaterial.js';
import { FullScreenQuad } from 'three/examples/jsm/postprocessing/Pass.js';
import { initUNetFromURL } from 'oidn-webgpu/dist/oidn.js';
import { PreviewDenoiser } from './previewDenoiser.js';
import { OIDN_WEIGHTS_URL } from './weights/index.js';
import { drawPreviewBackdrop } from './backdrop.js';
import { inspectPreviewScene, preparePreviewScene, disposePreviewScene, createPreviewGeometryCache, previewSceneBudget, syncPreviewSceneTransforms, PREVIEW_LIMITS } from './scene.js';

const FRAME_PATH_BUDGET = 250000;
// Upstream low-res mode advances five wavefront steps per update. Four bounded
// updates let paths traverse the full 12 surface plus 5 transparent bounces.
const MOTION_UPDATES = 4, MOTION_SCALE = 0.3;
const RAY_STATE_BYTES = 224;
const MAX_RENDER_PIXELS = 1000000;
const FIRST_DENOISE_SAMPLES = 32, PREVIEW_SAMPLES = 128, FINAL_SAMPLES = 256;
const MAX_BOUNCES = 12, MAX_TRANSPARENT_BOUNCES = 5;
const MIN_FRAME_LIMIT = 2048, MAX_FRAME_LIMIT = 32768, MAX_REFERENCE_FRAMES = 65536;

// Preview presents only this tracer; the caller owns loading and error UI.
export function createProgressivePreview(runtime, { requestRender = () => {}, onFailure = () => {}, diagnostic = null } = {}) {
  // For reproducible visual comparisons only: a bounded raw reference rendered
  // with this same scene and tracer. There is no viewer control for this profile.
  const rawReferenceSamples = diagnostic?.rawReferenceSamples ?? null;
  if (rawReferenceSamples !== null && (!Number.isInteger(rawReferenceSamples) || rawReferenceSamples < 1 || rawReferenceSamples > 1024)) {
    throw new RangeError('rawReferenceSamples must be an integer between 1 and 1024');
  }
  const rawReference = rawReferenceSamples !== null;
  let state = 'idle', reason = null, generation = 0, cameraEpoch = 0, abort = null, tracer = null;
  let gpu = null, scene = null, camera = null, canvas = null, background = null, presented = null, capture = null, frames = 0, visible = false, renderWidth = 0, renderHeight = 0;
  let rasterOpacity = null;
  let started = false, disposed = false, cameraDirty = false, transformsDirty = false, measuring = null, sample = 0, frameBudget = FRAME_PATH_BUDGET;
  let inFlight = null, denoiser = null, denoisedSamples = 0, denoisingSamples = 0, budgetExhausted = false, motionRemaining = 0, motionActive = false, presentedFrames = 0, shownSamples = 0;
  let motionReady = false, motionDenoising = false, motionQuad = null;
  const cache = createPreviewGeometryCache();
  let maxSamples = rawReferenceSamples ?? (runtime.previewConfiguration?.quality === 'final' ? FINAL_SAMPLES : PREVIEW_SAMPLES);
  const hide = (restoreRaster = true) => { visible = false; if (canvas) canvas.style.visibility = 'hidden'; if (presented) presented.style.visibility = 'hidden'; if (background) background.style.visibility = 'hidden'; if (restoreRaster && rasterOpacity !== null) { runtime.renderer.domElement.style.opacity = rasterOpacity; rasterOpacity = null; } };
  // The GPU canvas can be rewritten while tracing. Hold a completed traced frame
  // on a separate canvas until its next complete low-resolution update is ready.
  const show = (newFrame = true) => {
    if (presented.width !== canvas.width || presented.height !== canvas.height) { presented.width = canvas.width; presented.height = canvas.height; }
    const context = presented.getContext('2d');
    if (!context) throw new Error('Preview presentation unavailable');
    context.clearRect(0, 0, presented.width, presented.height);
    context.drawImage(canvas, 0, 0);
    presented.style.visibility = 'visible';
    if (!visible) { rasterOpacity = runtime.renderer.domElement.style.opacity; runtime.renderer.domElement.style.opacity = '0'; }
    visible = true; background.style.visibility = 'visible';
    if (newFrame) presentedFrames++;
  };
  const release = (restoreRaster = true) => {
    hide(restoreRaster); inFlight = null;
    tracer?.dispose(); tracer = null; denoiser = null; denoisedSamples = 0; denoisingSamples = 0;
    motionQuad?.material.dispose(); motionQuad?.dispose(); motionQuad = null; motionReady = false; motionDenoising = false;
    disposePreviewScene(scene); scene = null;
    gpu?.dispose(); gpu = null;
    canvas?.remove(); canvas = null; presented?.remove(); presented = null; background?.remove(); background = null; capture = null;
    motionRemaining = 0; motionActive = false;
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
  const invalidate = ({ sceneChanged = false, transformsChanged = false } = {}) => {
    cameraEpoch++; measuring = null;
    const wasDenoising = denoiser?.running;
    // Keep an in-progress interactive picture latched despite newer input.
    if ((!motionReady && !motionDenoising) || sceneChanged || globalThis.document?.visibilityState === 'hidden') {
      denoiser?.reset(); denoisedSamples = 0; denoisingSamples = 0;
      motionDenoising = false; motionReady = false;
      if (wasDenoising) fenceDenoise();
    }
    if (sceneChanged) {
      generation++;
      // Abort also rejects an in-flight worker task, so its private geometry is released.
      abort?.abort(); abort = null;
      motionRemaining = 0; motionActive = false; transformsDirty = false; started = false; state = 'idle'; reason = null; frames = 0; sample = 0; measuring = null; budgetExhausted = false;
    } else {
      cameraDirty = true; transformsDirty ||= transformsChanged;
    }
  };
  const fail = (error, token) => {
    if (disposed || token !== generation || state === 'fallback') return;
    state = 'fallback'; reason = error?.message || String(error);
    generation++; cameraEpoch++; measuring = null; abort?.abort(); abort = null;
    try { onFailure(error); } catch { /* Reporting must not prevent cleanup. */ }
    release(false); cache.dispose();
    requestRender();
  };
  const prepare = async (sourceCamera) => {
    const token = ++generation;
    started = true;
    state = 'preparing'; reason = null;
    maxSamples = rawReferenceSamples ?? (runtime.previewConfiguration?.quality === 'final' ? FINAL_SAMPLES : PREVIEW_SAMPLES);
    let localGpu = gpu, localScene = null, localCanvas = null, localBackdrop = null, localPresented = null;
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
        localPresented = document.createElement('canvas');
        Object.assign(localPresented.style, { position: 'absolute', inset: '0', width: '100%', height: '100%', pointerEvents: 'none', visibility: 'hidden' });
        runtime.renderer.domElement.parentElement.appendChild(localPresented);
        presented = localPresented;
        localGpu = new WebGPURenderer({ canvas: localCanvas, alpha: true, antialias: false, powerPreference: 'high-performance', requiredLimits });
        await localGpu.init();
        if (!localGpu.backend?.isWebGPUBackend) throw new Error('WebGPU unavailable');
        if (token !== generation || disposed) { localGpu.dispose(); localCanvas.remove(); backdrop.remove(); localPresented?.remove(); return; }
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
      if (token !== generation || disposed) { if (localScene && localScene !== scene) disposePreviewScene(localScene); if (localGpu && localGpu !== gpu) localGpu.dispose(); localCanvas?.remove(); localBackdrop?.remove(); localPresented?.remove(); return; }
      if (localScene && localScene !== scene) disposePreviewScene(localScene);
      if (localGpu && localGpu !== gpu) localGpu.dispose();
      fail(error, token);
    }
  };
  const render = (_renderer, _scene, sourceCamera, { still = true, moving = false, sceneChanged = false } = {}) => {
    if (disposed) return { refining: false, visible: false };
    if (globalThis.document?.visibilityState === 'hidden') return { refining: false, visible };
    if (sceneChanged && started) invalidate({ sceneChanged: true });
    if (!started) { void prepare(sourceCamera); return { refining: false, visible: false }; }
    if (state !== 'ready') return { refining: false, visible: false };
    // A quality-only setting can extend a converged image without rebuilding
    // geometry or discarding the accumulated samples.
    const desiredSamples = rawReferenceSamples ?? (runtime.previewConfiguration?.quality === 'final' ? FINAL_SAMPLES : PREVIEW_SAMPLES);
    if (desiredSamples !== maxSamples) {
      const wasDenoising = denoiser?.running;
      maxSamples = desiredSamples; tracer.maxSamples = maxSamples; denoisedSamples = 0; denoisingSamples = 0; budgetExhausted = false;
      if (motionReady || motionDenoising) { motionReady = false; motionDenoising = false; cameraDirty = true; }
      if (wasDenoising) fenceDenoise();
    }
    // Do not queue more than one GPU batch; camera changes can then take effect
    // after the current batch without draining a backlog.
    if (inFlight) return { refining: false, visible };
    try {
      if (motionDenoising) {
        if (denoiser.running) return { refining: false, visible };
        if (!denoiser.texture) throw new Error('Interactive denoising did not produce an image');
        if (!motionQuad) motionQuad = new FullScreenQuad(new RenderToScreenNodeMaterial());
        const material = motionQuad.material;
        material.texture = denoiser.texture; material.transition = 1;
        const previousTarget = gpu.getRenderTarget(), previousAutoClear = gpu.autoClear;
        try { gpu.setRenderTarget(null); gpu.autoClear = true; motionQuad.render(gpu); }
        finally { gpu.autoClear = previousAutoClear; gpu.setRenderTarget(previousTarget); }
        show(); shownSamples = 0; motionDenoising = false; denoiser.reset();
      }
      if (motionReady) {
        motionReady = false; motionDenoising = true;
        void denoiser.run(tracer.target, { scheduling: 'event-loop' });
        return { refining: false, visible };
      }
      const wantsMotion = moving || !still;
      // Finish the current latched motion picture before sampling a newer pose;
      // otherwise a continuous stream of pointer changes would reset every path.
      if (motionRemaining === 0 && transformsDirty) {
        transformsDirty = false;
        if (!scene || !syncPreviewSceneTransforms(scene, runtime.modelGroup, runtime.photographicStudio?.ground)) {
          invalidate({ sceneChanged: true });
          void prepare(sourceCamera);
          return { refining: false, visible: false };
        }
        tracer.updateTransforms();
        // Guide meshes are ordinary private copies, so rebuild them over the new matrices.
        denoiser.setScene(scene, camera);
      }
      const rasterCanvas = runtime.renderer.domElement;
      const width = Math.max(1, rasterCanvas.width || rasterCanvas.clientWidth);
      const height = Math.max(1, rasterCanvas.height || rasterCanvas.clientHeight);
      const scale = Math.min(1, Math.sqrt(MAX_RENDER_PIXELS / (width * height)));
      const w = Math.max(1, Math.floor(width * scale)), h = Math.max(1, Math.floor(height * scale));
      renderWidth = w; renderHeight = h;
      if (canvas.width !== w || canvas.height !== h) { gpu.setSize(w, h, false); cameraDirty = true; motionRemaining = 0; }
      if (motionRemaining === 0 && wantsMotion && (cameraDirty || !motionActive)) {
        tracer.dynamicLowRes = true; tracer.lowResScale = MOTION_SCALE; tracer.renderDelay = 60000000; tracer.fadeDuration = 0;
        motionActive = true; motionRemaining = MOTION_UPDATES;
      }
      if (motionRemaining === 0 && !wantsMotion && motionActive) {
        tracer.dynamicLowRes = false; tracer.renderDelay = 0; motionActive = false; cameraDirty = true;
      }
      if (cameraDirty && motionRemaining !== MOTION_UPDATES && motionRemaining > 0) {
        // A newer camera waits until the latched low-resolution image completes.
      } else if (cameraDirty) {
        camera.copy(sourceCamera, false); camera.coordinateSystem = THREE.WebGPUCoordinateSystem;
        tracer.updateCamera(); cameraDirty = false; frames = 0; sample = 0; budgetExhausted = false; denoisedSamples = 0; denoisingSamples = 0; cameraEpoch++; measuring = null;
        drawPreviewBackdrop(background, sourceCamera, runtime.photographicStudio, w, h, runtime.renderer.getClearAlpha());
      }
      if (motionRemaining > 0) {
        // Submit one latched picture as one bounded GPU batch. Low-resolution
        // updates each advance five wavefront steps, and are not tied to vsync.
        while (motionRemaining > 0) { tracer.renderSample(); motionRemaining--; }
        motionReady = true;
        const fence = gpu.backend.device.queue.onSubmittedWorkDone();
        inFlight = fence;
        const token = generation;
        fence.then(() => {
          if (inFlight !== fence) return;
          inFlight = null;
          if (disposed || state !== 'ready') return;
          // Start the latched guide/network work as soon as the trace batch
          // finishes; waiting for another animation frame would add a whole
          // display interval to every interactive picture.
          if (token === generation && motionReady && globalThis.document?.visibilityState !== 'hidden') {
            motionReady = false; motionDenoising = true;
            void denoiser.run(tracer.target, { scheduling: 'event-loop' });
          } else requestRender();
        }).catch(error => { if (inFlight !== fence) return; inFlight = null; if (token === generation) fail(error, token); });
        return { refining: false, visible };
      }
      if (wantsMotion) return { refining: false, visible };
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
      if (denoiser.texture || (rawReference && sample >= 1)) {
        const newFrame = rawReference || denoisedSamples !== shownSamples || !visible;
        if (newFrame) show(newFrame);
        shownSamples = denoisedSamples;
      }
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
      capture ||= document.createElement('canvas');
      capture.width = canvas.width; capture.height = canvas.height;
      const context = capture.getContext('2d');
      context.drawImage(background, 0, 0, capture.width, capture.height);
      context.drawImage(presented, 0, 0);
      return capture;
    },
    get progress() { return { state, motion: motionActive, motionRemaining, presentedFrames, traceWidth: motionActive ? Math.ceil(renderWidth * MOTION_SCALE) : renderWidth, traceHeight: motionActive ? Math.ceil(renderHeight * MOTION_SCALE) : renderHeight, sample, samples: maxSamples, frames, renderWidth, renderHeight, denoise: denoiser?.status || 'idle', denoisedSamples, budgetExhausted, denoiseTimings: denoiser?.timings || null, fallbackReason: reason }; },
  };
}
