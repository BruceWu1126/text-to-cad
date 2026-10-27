// Preview lifts its initial cover only after a traced picture of the loaded model is present.

/**
 * @param {{ presented: boolean, loading: boolean }} view
 * @returns {"wait" | "lift"}
 */
export function previewCoverStep({ presented, loading }) {
  return !presented || loading ? "wait" : "lift";
}
