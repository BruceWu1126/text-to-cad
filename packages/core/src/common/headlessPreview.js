import { createProgressivePreview } from '../lib/preview-tracing/progressivePreview.js';

// The headless page drives the very same controller as Preview. It owns a host
// for the controller's private canvases and refuses to encode a raster fallback.
export function createHeadlessPreview(viewport, { createController = createProgressivePreview, schedule = requestAnimationFrame, cancel = cancelAnimationFrame } = {}) {
  const host = document.createElement('div');
  const canvas = viewport.renderer.domElement;
  const oldParent = canvas.parentNode, oldNext = canvas.nextSibling;
  host.appendChild(canvas);
  const runtime = {
    THREE: viewport.THREE, renderer: viewport.renderer, scene: viewport.scene,
    modelGroup: viewport.model.root, photographicStudio: viewport.studioRuntime?.photographicStudio,
    previewConfiguration: viewport.studioConfiguration,
  };
  let pending = null, queued = null, disposed = false;
  const requestRender = () => { if (!disposed && pending && queued === null) queued = schedule(draw); };
  const controller = createController(runtime, { requestRender });
  function draw() {
    queued = null;
    if (!pending || disposed) return;
    try {
      controller.render(viewport.renderer, viewport.scene, pending.camera, { still: true, moving: false, sceneChanged: false });
      const progress = controller.progress;
      if (progress.state === 'fallback') throw new Error(`Preview tracing failed: ${progress.fallbackReason || 'unavailable'}`);
      if (progress.budgetExhausted) throw new Error('Preview tracing exhausted its frame budget before convergence');
      if (progress.denoisedSamples >= progress.samples && progress.samples > 0) {
        const result = controller.captureCanvas;
        if (!result) throw new Error('Preview tracing produced no completed image');
        const resolve = pending.resolve; pending = null; resolve(result);
      }
    } catch (error) { const reject = pending.reject; pending = null; reject(error); }
  }
  return {
    get progress() { return controller.progress; },
    renderFrame(camera, { sceneChanged = false } = {}) {
      if (disposed) return Promise.reject(new Error('Preview session is closed'));
      if (pending) return Promise.reject(new Error('A Preview frame is already rendering'));
      if (sceneChanged) controller.invalidate({ transformsChanged: true });
      return new Promise((resolve, reject) => { pending = { camera, resolve, reject }; requestRender(); });
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      if (queued !== null) cancel(queued);
      if (pending) { pending.reject(new Error('Preview session is closed')); pending = null; }
      controller.dispose();
      if (oldParent) oldParent.insertBefore(canvas, oldNext); else canvas.remove();
      host.remove();
    }
  };
}
