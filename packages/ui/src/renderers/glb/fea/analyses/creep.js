/**
 * Creep (lite), Tier 3: the slow, permanent stretch of a part under a load held for a long time, usually
 * hot, by Norton's power law (secondary creep only). Its series is the hold in hours ("0 h" the instant the
 * load goes on), opening on the end, the stress relaxing where the creep sheds it. Creep goes as the stress
 * to a power, so no load control moves it, and a run whose creep ran away fails every check.
 */
import { heldRows, madeOfRows, pushedRows } from "../setup.js";
import { planned, playSeries } from "./stub.js";

export { PLAY } from "./stub.js";

/** The takeaway of a run whose creep ran away: the file's own sentence (in `analysis.warnings`); "" otherwise. */
export function runawayCaption(result) {
  return (result?.analysis?.warnings || []).find((line) => /^Creep runs away\b/.test(line)) || "";
}

/** Its setup: where it is held, what pushes it, what it is made of. */
export const CREEP_SETUP = Object.freeze([heldRows, pushedRows, madeOfRows]);

export default planned({
  name: "creep", tier: 3, word: "Creep",
  // Not linear in the load: every check is judged as solved, its takeaway its own sentence, never "OK up to N× this load".
  scaling: "none",
  checks: ["creep_strain", "stress", "displacement"],
  // Its controls are seriesControls': the time scrubber (an hours series is "Time"), the field, the deformation.
  setupGroups: CREEP_SETUP,
  routine: playSeries,
  // Secondary (steady) creep only; a runaway leads the takeaway, not the worst check's words.
  limitWord: "Steady creep",
  caption: runawayCaption,
});

