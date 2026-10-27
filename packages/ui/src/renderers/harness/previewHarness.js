// Test-only substitute for UI workflow specs. Real rendering, transmission and
// device failure are exercised by PreviewPathTracing.browser.test.mjs.
export function createProgressivePreview(runtime, { onFailure = () => {} } = {}) {
  let disposed = false, visible = false;
  if (globalThis.__cadTestPreviewUnavailable) queueMicrotask(() => { if (!disposed) onFailure(new Error('WebGPU unavailable')); });
  return {
    get visible() { return visible; },
    get captureCanvas() { return visible ? (runtime.renderer.drawingCanvas || runtime.renderer.domElement) : null; },
    get progress() { return { state: 'ready', sample: 1, samples: 1, denoisedSamples: 1 }; },
    render(renderer, scene, camera) {
      if (globalThis.__cadTestPreviewUnavailable) return { visible: false, refining: false };
      renderer.render(scene, camera); visible = true;
      return { visible: true, refining: false };
    },
    invalidate() {},
    dispose() { disposed = true; visible = false; },
  };
}
