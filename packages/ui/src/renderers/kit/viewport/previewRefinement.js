// Preview has one renderer: preparation is covered until a traced picture is
// ready, and a device or scene failure is reported rather than shown as raster.
export function createPreviewRefinement(runtime, { requestRender, onStatus = () => {}, load = () => import("@text-to-cad/core/lib/preview-tracing/progressivePreview.js"),
  loadTimeoutMs = 45000, setTimer = globalThis.setTimeout, clearTimer = globalThis.clearTimeout }) {
  let controller = null, loading = false, disposed = false, failed = null, lastStatus = "", loadTimer = null;
  const clearLoadTimer = () => { if (loadTimer !== null) clearTimer(loadTimer); loadTimer = null; };
  const report = (state, reason = "", phase = "preparing") => {
    const key = `${state}:${reason}:${phase}`;
    if (key !== lastStatus) { lastStatus = key; onStatus({ state, reason, phase }); }
  };
  const notifyFailure = error => {
    if (failed || disposed) return;
    clearLoadTimer(); loading = false;
    failed = String(error?.message || error || "Preview tracing unavailable");
    report("failed", failed);
    requestRender();
  };
  const fail = error => {
    controller?.dispose(); controller = null;
    notifyFailure(error);
  };
  return {
    get visible() { return controller?.visible === true; },
    get captureCanvas() {
      try { return controller?.captureCanvas || null; }
      catch (error) { fail(error); return null; }
    },
    get progress() {
      return controller?.progress || { state: failed ? "fallback" : loading ? "loading" : "idle", fallbackReason: failed, sample: 0, samples: 0 };
    },
    render(renderer, scene, camera, frame) {
      if (disposed || failed) return { refining: false, visible: false };
      if (!controller) {
        if (!loading && runtime.hasVisibleModel && runtime.environmentReady) {
          loading = true; report("loading");
          loadTimer = setTimer(() => fail(new Error("Preview renderer did not load")), loadTimeoutMs);
          Promise.resolve().then(load).then(module => {
            if (disposed || failed) return;
            clearLoadTimer();
            controller = module.createProgressivePreview(runtime, { onFailure: notifyFailure, requestRender: () => {
              if (!disposed && !failed) requestRender();
            } });
            loading = false;
            requestRender();
          }).catch(error => { if (!disposed) fail(error); });
        }
        return { refining: false, visible: false };
      }
      try {
        const result = controller.render(renderer, scene, camera, frame);
        const progress = controller.progress;
        if (progress?.state === "fallback") fail(progress.fallbackReason || "Preview tracing unavailable");
        else if (result.visible) report("ready");
        else report("loading", "", progress?.stage?.startsWith("denoise") ? "refining" : progress?.stage === "trace" || progress?.stage === "tracing" ? "rendering" : "preparing");
        return result;
      } catch (error) {
        fail(error);
        return { refining: false, visible: false };
      }
    },
    invalidate(options = {}) { controller?.invalidate(options); },
    dispose() {
      if (disposed) return;
      disposed = true; clearLoadTimer();
      controller?.dispose(); controller = null;
    }
  };
}

export function renderPreviewFrame(refinement, renderer, scene, camera, frame) {
  return refinement.render(renderer, scene, camera, frame);
}
