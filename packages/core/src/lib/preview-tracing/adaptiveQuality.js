import { previewQualityProfile, withPreviewRayBudget } from './resourcePolicy.js';

// Freeze the admitted settled target during convergence. Adapt only path slots
// without changing the selected image dimensions. Memory admission alone may
// lower the still or motion target.
export function createPreviewQualityController({ performance = {}, viewport = {}, admittedProfile = null } = {}) {
  const profile = admittedProfile || previewQualityProfile(performance, viewport);
  const minimumBatch = Math.min(profile.frameBudget, 4096);
  let batch = profile.frameBudget, quiet = 0, revision = 0;
  const current = () => ({ ...withPreviewRayBudget(profile, batch), revision });
  return {
    get profile() { return current(); },
    observeBatch(milliseconds) {
      if (!Number.isFinite(milliseconds) || milliseconds <= 0) return current();
      // Each observation measures one fenced wavefront update.
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
