/**
 * Permanent bend / Stretch (lite), Tier 3: metal past yield (J2 plasticity) or rubber stretched far
 * (Neo-Hookean), the load applied in steps. Its series is the load steps as pseudo-time ("60 % load"),
 * opening on the last load the part carried. The response is not proportional to the load, so no load
 * control moves it, and a part that collapses fails every check, its takeaway leading with the collapse.
 */
import { heldRows, madeOfRows, pushedRows } from "../setup.js";
import { planned, playSeries } from "./stub.js";

export { PLAY } from "./stub.js";

/**
 * What leads the takeaway of a load path that stopped short of the full load (`collapsed`): the
 * file's own sentence (in `analysis.warnings`, starting with `verb`: "Collapses at about 70 % of the
 * load", "Stops at about 20 % of the load"), else one from the last load it carried; "" for a run that
 * carried it all.
 */
export function stopSentence(result, verb) {
  if (!result?.collapsed) return "";
  const said = (result.analysis?.warnings || []).find((line) => new RegExp(`^${verb}\\b`).test(line));
  if (said) return said;
  const percent = result.loadPercent;
  if (!Number.isFinite(percent)) return `${verb} before the full load`;
  return percent < 1 ? `${verb} before 1 % of the load` : `${verb} at about ${Math.round(percent)} % of the load`;
}

/** A plastic or stretched part's: "Collapses at about 70 % of the load" (no equilibrium past it). */
export const collapseCaption = (result) => stopSentence(result, "Collapses");

/** A contact load path that could not be followed to the full load: "Stops at about 20 % of the load". */
export const stopsCaption = (result) => stopSentence(result, "Stops");

/** Its setup: where it is held, what pushes it, what it is made of. */
export const NONLINEAR_SETUP = Object.freeze([heldRows, pushedRows, madeOfRows]);

export default planned({
  name: "nonlinear", tier: 3, word: "Permanent bend / Stretch",
  // Not linear in the load: every check is judged as solved, its takeaway its own sentence, never "OK up to 18× this load".
  scaling: "none",
  checks: ["plastic_strain", "stress", "displacement"],
  // The load-step scrubber, the field, the deformation (controls.seriesControls words a % series "Load step").
  setupGroups: NONLINEAR_SETUP,
  routine: playSeries,
  limitWord: "Lite",
  // A collapse leads the takeaway, before its limit word and the worst check's words.
  stopCaption: collapseCaption,
});

