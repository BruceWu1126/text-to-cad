import { previewQualityProfile, withPreviewRayBudget } from './resourcePolicy.js';

// Freeze the admitted settled target during convergence. Adapt only path slots
// and their corresponding motion resolution; resizing the still on every slow
// batch would reset accumulation forever. Memory admission alone may lower it.
export function createPreviewQualityController({ performance = {}, viewport = {}, admittedProfile = null } = {}) {
  const profile = admittedProfile || previewQualityProfile(performance, viewport);
  // Latency is a cadence tradeoff, not permission to erase the model. Preserve
  // a quarter of each admitted still dimension, bounded by the admitted pool.
  const minimumBatch = Math.min(profile.frameBudget, Math.ceil(profile.width * 0.25) * Math.ceil(profile.height * 0.25));
  let batch = profile.frameBudget, quiet = 0, revision = 0;
  const current = () => ({ ...withPreviewRayBudget(profile, batch), revision });
  return {
    get profile() { return current(); },
    observeBatch(milliseconds) {
      if (!Number.isFinite(milliseconds) || milliseconds <= 0) return current();
      // Per tracer update: a four-update motion picture supplies elapsed / 4.
      const target = profile.targetBatchMs;
      if (milliseconds > target * 1.5) {
        batch = Math.max(minimumBatch, Math.floor(batch * Math.max(0.5, target / milliseconds)));
        quiet = 0; revision++;
      } else if (milliseconds < target * 0.6) {
        if (++quiet >= 8) { batch = Math.min(profile.frameBudget, Math.ceil(batch * 1.1)); quiet = 0; revision++; }
      } else quiet = 0;
      return current();
    },
  };
}
