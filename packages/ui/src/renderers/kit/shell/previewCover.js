// Preview lifts its initial cover when the raster studio presents the loaded model.
// Refinement happens in view: a still picture never waits behind the cover for samples.

/**
 * @param {{ presented: boolean, loading: boolean }} view
 * @returns {"wait" | "lift"}
 */
export function previewCoverStep({ presented, loading }) {
  return !presented || loading ? "wait" : "lift";
}
