/**
 * Contact (lite), Tier 3: parts that press on each other or slide instead of being glued, and parts resting
 * on a rigid plane. Its series is the load steps as pseudo-time ("60 % load"); contact opens, closes and
 * slides, so nothing is proportional to the load. Study names each contact pair in plain words ("pin presses
 * on plate, friction 0.2"), what it did under the load its hint.
 */
import { plainNumber } from "../numbers.js";
import { group, heldRows, madeOfRows, pushedRows, rigidFloorRows, spaced } from "../setup.js";
import { stopsCaption } from "./nonlinear.js";
import { planned, playSeries } from "./stub.js";

export { PLAY } from "./stub.js";

export const list = (value) => (Array.isArray(value) ? value.filter((entry) => entry && typeof entry === "object") : []);

/** The refs a solved outcome names, for its row's choice. */
export const outcomeRefs = (outcome) => (Array.isArray(outcome?.refs) ? outcome.refs.filter((ref) => typeof ref === "string" && ref) : []);

/** How a contact came out under the load, for its row's hint: "1000 N, peak 10 MPa", "no longer touching". */
function outcomeWords(outcome) {
  if (!outcome) return "";
  if (outcome.touching === false) return "no longer touching";
  const force = Number(outcome.force_N);
  const peak = Number(outcome.peak_MPa);
  return [Number.isFinite(force) ? `${plainNumber(force)} N` : "", Number.isFinite(peak) ? `peak ${plainNumber(peak)} MPa` : ""]
    .filter(Boolean).join(", ");
}

/**
 * Study's "Pressed together": one row per contact pair as cadgen solved it ("pin presses on plate,
 * friction 0.2"), what it did under the load its hint, chosen with both parts. [] for none.
 */
export function contactRows(result) {
  const extras = result?.mesh?.userData || {};
  const pairs = list(result?.study?.contactPairs);
  const outcomes = list(extras.contacts).filter((entry) => entry.kind === "pair");
  const rows = pairs.map((pair, index) => {
    const outcome = outcomes[index];
    const between = Array.isArray(outcome?.between) && outcome.between.length === 2 ? outcome.between : pair.between;
    if (!Array.isArray(between) || between.length !== 2) return null;
    const friction = Number(pair.friction) || 0;
    const label = `${spaced(between[0])} presses on ${spaced(between[1])}, ${friction ? `friction ${plainNumber(friction)}` : "no friction"}`;
    const refs = outcomeRefs(outcome);
    const hint = outcomeWords(outcome);
    return { id: `contact:${index}`, label, detail: "", wrap: true, ...(hint ? { hint } : {}),
      ...(refs.length ? { refs, summary: `Contact: ${label}` } : {}) };
  }).filter(Boolean);
  return group("contacts", "Pressed together", "load", rows);
}

/** Its setup: where it is held, what pushes it, which parts press on which, the rigid floor, what it is made of. */
export const CONTACT_SETUP = Object.freeze([heldRows, pushedRows, contactRows, rigidFloorRows, madeOfRows]);

export default planned({
  name: "contact", tier: 3, word: "Contact",
  // Not linear in the load: every check is judged as solved, its takeaway its own sentence, never "OK up to 18× this load".
  scaling: "none",
  checks: ["stress", "displacement", "contact_pressure"],
  // The load-step scrubber, the field, the deformation (controls.seriesControls words a % series "Load step").
  setupGroups: CONTACT_SETUP,
  routine: playSeries,
  markers: ["load", "fixture", "rigid_plane"],
  limitWord: "Lite",
  // A load path that could not be followed to the full load leads the takeaway: "Stops at about 20 % of the load · ".
  stopCaption: stopsCaption,
});

