// Preview always presents its raster studio first. The optional tracer loads behind that
// picture; a late import or preparation can never attach itself to a closed viewport.
export function createPreviewRefinement(runtime, { requestRender, load = () => import("@text-to-cad/core/lib/preview-tracing/progressivePreview.js") }) {
  let controller = null;
  let loading = false;
  let disposed = false;
  let failed = false;
  const fail = () => {
    failed = true;
    controller?.dispose();
    controller = null;
  };
  return {
    get visible() { return controller?.visible === true; },
    get captureCanvas() {
      try { return controller?.captureCanvas || null; }
      catch { fail(); return null; }
    },
    get progress() {
      return controller?.progress || { state: failed ? "fallback" : loading ? "loading" : "idle", sample: 0, samples: 0 };
    },
    render(renderer, scene, camera, frame) {
      if (disposed || failed) return { refining: false, visible: false };
      if (!controller) {
        if (!loading && !frame.moving && frame.still && runtime.hasVisibleModel && runtime.environmentReady) {
          loading = true;
          Promise.resolve().then(load).then(module => {
            if (disposed || failed) return;
            controller = module.createProgressivePreview(runtime, { requestRender: () => {
              if (!disposed && !failed) requestRender();
            } });
            loading = false;
            requestRender();
          }).catch(() => { if (!disposed) fail(); });
        }
        return { refining: false, visible: false };
      }
      try {
        return controller.render(renderer, scene, camera, frame);
      } catch {
        fail();
        return { refining: false, visible: false };
      }
    },
    invalidate(options = {}) { controller?.invalidate(options); },
    dispose() {
      if (disposed) return;
      disposed = true;
      controller?.dispose();
      controller = null;
    }
  };
}

// Once the traced picture covers the canvas, its samples need no raster redraw. Any
// invalidation hides it first; if the tracer falls back during a sample, redraw underneath
// in the same task so the camera and the studio remain current.
export function renderPreviewFrame(refinement, renderer, scene, camera, frame, drawRaster) {
  if (refinement.visible && frame.still && !frame.moving) {
    const result = refinement.render(renderer, scene, camera, frame);
    if (!result.visible) return { ...result, refining: drawRaster().refining || result.refining };
    return result;
  }
  const raster = drawRaster();
  const result = refinement.render(renderer, scene, camera, frame);
  return { ...result, refining: raster.refining || result.refining };
}
