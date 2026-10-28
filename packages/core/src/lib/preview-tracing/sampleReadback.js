import { SAMPLE_COUNTER_MIN, SAMPLE_COUNTER_PIXEL_COUNT } from 'three-gpu-pathtracer/src/webgpu/compute/TallySampleCountsKernel.js';

// Upstream's tally intentionally skips undispatched pixels. Perspective and
// orthographic Preview cameras cover the entire target, so require both a full
// pixel count and the minimum sample count before publishing a motion picture.
async function readFullCoverage(tracer) {
  const path = tracer._pathTracer;
  const { renderer, sampleCountTarget, sampleCountersStorage, primeSampleCountersKernel: prime, tallySampleCountsKernel: tally } = path;
  prime.counters = sampleCountersStorage;
  renderer.compute(prime.kernel, [1, 1, 1]);
  tally.counters = sampleCountersStorage;
  tally.sampleCountTarget = sampleCountTarget;
  renderer.compute(tally.kernel, tally.getDispatchSize(sampleCountTarget.width, sampleCountTarget.height));
  const counts = new Uint32Array(await renderer.getArrayBufferAsync(sampleCountersStorage));
  const pixels = sampleCountTarget.width * sampleCountTarget.height;
  return { min: counts[SAMPLE_COUNTER_PIXEL_COUNT] === pixels ? counts[SAMPLE_COUNTER_MIN] : 0,
    coveredPixels: counts[SAMPLE_COUNTER_PIXEL_COUNT], pixels };
}

// A GPU fence can complete while a map/readback promise never resolves. Bound
// that operation separately from trace-batch progress; failure ends the session.
export function readPreviewSamples(tracer, { timeoutMs = 45000, setTimer = setTimeout, clearTimer = clearTimeout, fullCoverage = false } = {}) {
  let timer;
  const deadline = new Promise((_, reject) => { timer = setTimer(() => reject(new Error('Preview sample readback stopped making progress')), timeoutMs); });
  return Promise.race([Promise.resolve().then(() => fullCoverage ? readFullCoverage(tracer) : tracer.getSampleCountsAsync()), deadline]).finally(() => clearTimer(timer));
}
