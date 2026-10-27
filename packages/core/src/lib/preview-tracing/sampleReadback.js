// A GPU fence can complete while a map/readback promise never resolves. Bound
// that operation separately from trace-batch progress; failure ends the session.
export function readPreviewSamples(tracer, { timeoutMs = 45000, setTimer = setTimeout, clearTimer = clearTimeout } = {}) {
  let timer;
  const deadline = new Promise((_, reject) => { timer = setTimer(() => reject(new Error('Preview sample readback stopped making progress')), timeoutMs); });
  return Promise.race([Promise.resolve().then(() => tracer.getSampleCountsAsync()), deadline]).finally(() => clearTimer(timer));
}
