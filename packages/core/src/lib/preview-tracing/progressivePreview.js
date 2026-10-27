import { snapshotPreviewScene } from './previewSnapshot.js';
import { previewResourcePool, PREVIEW_RESOURCE_LIMITS } from './resourcePolicy.js';

const STALL_MS = 45000;
const createWorker = () => new Worker(new URL('./previewWorker.js', import.meta.url), { type: 'module' });
const cameraRecord = camera => ({ type: camera.type, position: camera.position.toArray(), quaternion: camera.quaternion.toArray(), scale: camera.scale.toArray(),
  projection: camera.projectionMatrix.toArray(), projectionInverse: camera.projectionMatrixInverse.toArray(),
  near: camera.near, far: camera.far, zoom: camera.zoom, fov: camera.fov, aspect: camera.aspect,
  left: camera.left, right: camera.right, top: camera.top, bottom: camera.bottom, filmGauge: camera.filmGauge, filmOffset: camera.filmOffset });

// OrbitControls can change a camera's last bits even when it reports no motion.
// Compare against the last sent view, so small intended movement accumulates.
function sameCamera(a, b, viewDistance) {
  if (!a || !b) return false;
  const close = (x, y, absolute, relative) => x === y || (Number.isFinite(x) && Number.isFinite(y)
    && Math.abs(x - y) <= Math.max(absolute, Math.abs(x) * relative, Math.abs(y) * relative));
  return Object.keys(a).every(key => {
    const x = a[key], y = b[key];
    const absolute = key === 'position' ? Math.max(1e-10, viewDistance * 1e-6) : key === 'quaternion' ? 1e-6 : 1e-9;
    const relative = key === 'projection' || key === 'projectionInverse' || key === 'near' || key === 'far' ? 2e-6 : 1e-13;
    return Array.isArray(x) ? Array.isArray(y) && x.length === y.length && x.every((value, index) => close(value, y[index], absolute, relative)) : close(x, y, absolute, relative);
  });
}

// Owns only browser presentation and worker lifecycle. All trace scenes, GPU
// objects, BVHs and denoisers live in the dedicated worker.
export function createProgressivePreview(runtime, { requestRender = () => {}, onFailure = () => {}, diagnostic = null,
  workerFactory = createWorker, stallMs = STALL_MS, snapshotScene = snapshotPreviewScene,
  setTimer = globalThis.setTimeout, clearTimer = globalThis.clearTimeout } = {}) {
  const raw = diagnostic?.rawReferenceSamples ?? null;
  if (raw !== null && (!Number.isInteger(raw) || raw < 1 || raw > 1024)) throw new RangeError('rawReferenceSamples must be an integer between 1 and 1024');
  let generation = 0, worker = null, abort = null, timer = null, canvas = null, visible = false, disposed = false, started = false;
  let rasterOpacity = null, inputBusy = false, pending = null, sources = null, dirtyTransforms = false, paused = false, inputDirty = false, lastSentFrame = null, lastPerformanceKey = null, lease = null, initialized = false, sourceGround = null, sourceNodes = null;
  let progress = { state: 'idle', presentedFrames: 0, sample: 0, samples: 0, fallbackReason: null };
  const cancelTimer = () => { if (timer !== null) clearTimer(timer); timer = null; };
  const arm = token => { cancelTimer(); timer = setTimer(() => fail(new Error('Preview worker stopped making progress'), token), stallMs); };
  const beginWatch = token => { if (timer === null) arm(token); };
  const release = (restore = true, retainFrame = false) => {
    cancelTimer(); abort?.abort(); abort = null;
    if (worker) { worker.onmessage = null; worker.onerror = null; worker.onmessageerror = null; worker.terminate(); }
    worker = null; lease?.release(); lease = null; inputBusy = false; pending = null; sources = null; sourceGround = null; sourceNodes = null; initialized = false;
    if (!retainFrame && canvas) { canvas.remove(); canvas = null; visible = false; }
    lastSentFrame = null; lastPerformanceKey = null; inputDirty = false;
    if (restore && rasterOpacity !== null) { runtime.renderer.domElement.style.opacity = rasterOpacity; rasterOpacity = null; }
  };
  const fail = (error, token) => {
    if (disposed || token !== generation || progress.state === 'fallback') return;
    generation++; release(false);
    progress = { ...progress, state: 'fallback', fallbackReason: error?.message || String(error) };
    try { onFailure(error); } finally { requestRender(); }
  };
  const sendInput = async () => {
    if (!worker || !initialized || inputBusy || !pending || paused) return;
    const input = pending, local = worker, token = generation; pending = null; inputBusy = true;
    const takeTransforms = dirtyTransforms; dirtyTransforms = false;
    try {
      if (takeTransforms) {
        if (!sources) throw new Error('Preview placements are unavailable');
        // Update the admitted hierarchy in parent-first order, without another
        // recursive traversal, and latch all placements in the same event turn.
        runtime.modelGroup.parent?.updateWorldMatrix(true, false);
        for (const node of sourceNodes || []) {
          if (node.object.parent !== node.parent || node.object.visible !== node.visible) { invalidate({ sceneChanged: true }); requestRender(); return; }
          if (node.visible) node.object.updateWorldMatrix(false, false);
        }
        input.transforms = new Float32Array(sources.length * 16);
        sources.forEach((source, index) => source.matrixWorld.toArray(input.transforms, index * 16));
        const ground = runtime.photographicStudio?.ground;
        if ((ground?.visible ? ground : null) !== sourceGround) { invalidate({ sceneChanged: true }); requestRender(); return; }
        if (sourceGround) input.floorMatrix = sourceGround.matrixWorld.toArray();
      }
      beginWatch(token);
      if (input.transforms) local.postMessage(input, [input.transforms.buffer]);
      else local.postMessage(input);
      if (input.frameChanged) lastSentFrame = input;
      lastPerformanceKey = JSON.stringify(input.performance || {});
    } catch (error) { if (token === generation && error?.name !== 'AbortError') fail(error, token); }
  };
  const start = camera => {
    const token = ++generation; started = true; progress = { ...progress, state: 'preparing', fallbackReason: null, stage: 'probe' };
    abort = new AbortController(); arm(token);
    let outgoing = null, snapshotStarted = false;
    try {
      if (!globalThis.navigator?.gpu) throw new Error('WebGPU unavailable');
      if (typeof OffscreenCanvas !== 'function' || typeof Worker !== 'function') throw new Error('Preview worker unavailable');
      lease = previewResourcePool.reserve(PREVIEW_RESOURCE_LIMITS.driverReserveBytes);
      if (!lease) throw new Error('Preview resource budget is in use');
      const local = workerFactory(); worker = local;
      const view = runtime.renderer.domElement;
      const snapshotAfterProbe = async limits => {
        if (snapshotStarted) return; snapshotStarted = true; arm(token);
        progress = { ...progress, stage: 'snapshot' };
        try {
          const snapshot = await snapshotScene(runtime, { signal: abort.signal, deviceLimits: limits, lease, yieldWork: async () => {
            await new Promise(resolve => setTimeout(resolve, 0)); if (token === generation && !abort?.signal.aborted) arm(token);
          } });
          if (disposed || token !== generation || worker !== local) { snapshot.lease?.release(); for (const item of snapshot.transfers) item.close?.(); return; }
          if (snapshot.lease && snapshot.lease !== lease) { lease?.release(); lease = snapshot.lease; }
          sources = snapshot.sourceMeshes; sourceNodes = snapshot.sourceNodes || []; sourceGround = snapshot.sourceGround || null; outgoing = snapshot.transfers;
          if (!canvas) {
            canvas = document.createElement('canvas');
            Object.assign(canvas.style, { position: 'absolute', inset: '0', width: '100%', height: '100%', pointerEvents: 'none', visibility: 'hidden' });
            view.parentElement.appendChild(canvas);
          }
          local.postMessage({ type: 'init', scene: snapshot.payload, width: view.width || view.clientWidth, height: view.height || view.clientHeight,
            camera: cameraRecord(camera), diagnostic }, snapshot.transfers);
          outgoing = null; initialized = true; sendInput();
        } catch (error) { for (const item of outgoing || []) item.close?.(); outgoing = null; if (token === generation && error?.name !== 'AbortError') fail(error, token); }
      };
      local.onmessage = event => {
        const msg = event.data;
        if (disposed || token !== generation || worker !== local) { msg.bitmap?.close(); return; }
        if (msg.type === 'probe') { void snapshotAfterProbe(msg.limits); return; }
        if (msg.type === 'error') { fail(new Error(msg.message), token); return; }
        if (msg.type === 'progress') {
          progress = { ...progress, ...msg.progress, presentedFrames: progress.presentedFrames, state: msg.progress?.state === 'ready' && visible ? 'ready' : 'preparing', stage: msg.stage, stageMs: msg.stageMs };
          const complete = (msg.progress?.budgetExhausted && msg.progress.sample > 0 && msg.progress.denoisedSamples >= msg.progress.sample) || (msg.progress?.samples > 0 && msg.progress.sample >= msg.progress.samples && msg.progress.denoisedSamples >= msg.progress.samples);
          if (complete) cancelTimer(); else arm(token);
          requestRender(); return;
        }
        if (msg.type === 'resource-diagnostic') { progress = { ...progress, memory: msg.memory, effectiveQuality: msg.profile, resourceReason: msg.reason }; return; }
        if (msg.type === 'resource') {
          const ok = Boolean(lease?.resize(Math.max(lease.bytes, msg.memory.peakBytes)));
          progress = { ...progress, memory: msg.memory, effectiveQuality: msg.profile };
          local.postMessage({ type: 'resource-ack', id: msg.id, ok });
          return;
        }
        if (msg.type === 'input-ack') { inputBusy = false; sendInput(); return; }
        if (msg.type === 'frame') {
          try {
            const bitmap = msg.bitmap;
            if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) { canvas.width = bitmap.width; canvas.height = bitmap.height; }
            const context = canvas.getContext('2d');
            if (!context) throw new Error('Preview presentation unavailable');
            context.clearRect(0, 0, canvas.width, canvas.height); context.drawImage(bitmap, 0, 0); bitmap.close();
            if (!visible) { rasterOpacity = view.style.opacity; view.style.opacity = '0'; }
            canvas.style.visibility = 'visible'; visible = true;
            progress = { ...progress, ...msg.progress, presentedFrames: progress.presentedFrames + 1, state: 'ready', fallbackReason: null };
            if (msg.progress?.motion || (msg.progress?.samples > 0 && msg.progress.sample >= msg.progress.samples && msg.progress.denoisedSamples >= msg.progress.samples)) cancelTimer();
            else arm(token);
            local.postMessage({ type: 'frame-ack' }); requestRender();
          } catch (error) { msg.bitmap?.close(); fail(error, token); }
        }
      };
      local.onerror = event => { event.preventDefault?.(); fail(new Error(event.message || 'Preview worker failed'), token); };
      local.onmessageerror = () => fail(new Error('Preview worker sent an invalid message'), token);
      local.postMessage({ type: 'probe' });
    } catch (error) { for (const item of outgoing || []) item.close?.(); if (token === generation) fail(error, token); }
  };
  const invalidate = ({ sceneChanged = false, transformsChanged = false } = {}) => {
    if (disposed) return;
    if (sceneChanged) { generation++; release(false, true); started = false; progress = { ...progress, state: 'idle', sample: 0, samples: 0, denoisedSamples: 0, fallbackReason: null }; }
    else { dirtyTransforms ||= transformsChanged; inputDirty = true; }
  };
  const onVisibilityChange = () => {
    if (disposed) return;
    const hidden = globalThis.document?.visibilityState === 'hidden';
    if (hidden === paused) return;
    paused = hidden;
    if (paused) {
      cancelTimer();
      if (progress.state === 'preparing') invalidate({ sceneChanged: true });
      else worker?.postMessage({ type: 'pause', paused: true });
    } else {
      worker?.postMessage({ type: 'pause', paused: false });
      if (worker) beginWatch(generation);
      requestRender();
    }
  };
  globalThis.document?.addEventListener?.('visibilitychange', onVisibilityChange);
  return {
    invalidate,
    render(_renderer, _scene, camera, { still = true, moving = false, sceneChanged = false } = {}) {
      if (disposed) return { visible: false, refining: false };
      if (sceneChanged && started) invalidate({ sceneChanged: true });
      const hidden = globalThis.document?.visibilityState === 'hidden';
      if (hidden !== paused) onVisibilityChange();
      if (hidden || progress.state === 'fallback') return { visible, refining: false };
      if (!started) { void start(camera); return { visible, refining: false }; }
      if (worker) {
        const view = runtime.renderer.domElement;
        const next = { type: 'input', camera: cameraRecord(camera), width: view.width || view.clientWidth, height: view.height || view.clientHeight,
          moving: moving || !still, quality: runtime.previewConfiguration?.quality, performance: runtime.previewConfiguration?.performance };
        const target = runtime.controls?.target;
        const viewDistance = target && camera.position?.distanceTo ? camera.position.distanceTo(target) : 1;
        const frameChanged = inputDirty || dirtyTransforms || !lastSentFrame || !sameCamera(next.camera, lastSentFrame.camera, viewDistance)
          || next.width !== lastSentFrame.width || next.height !== lastSentFrame.height || next.moving !== lastSentFrame.moving || next.quality !== lastSentFrame.quality;
        const performanceKey = JSON.stringify(next.performance || {});
        if (pending || frameChanged || performanceKey !== lastPerformanceKey) {
          next.frameChanged = Boolean(pending?.frameChanged || frameChanged);
          pending = next; inputDirty = false; sendInput();
        }
      }
      return { visible, refining: false };
    },
    dispose() { if (disposed) return; disposed = true; globalThis.document?.removeEventListener?.('visibilitychange', onVisibilityChange); generation++; release(); progress = { ...progress, state: 'disposed' }; },
    get visible() { return visible; },
    get captureCanvas() { return visible ? canvas : null; },
    get progress() { return progress; },
  };
}
