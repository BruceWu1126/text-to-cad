/**
 * Bolted joint (lite), Tier 3: bolts with a preload clamping parts that touch through frictional contact.
 * Its series opens on the preload ("Preload", 0 %) and follows the load steps ("60 % load"). It judges each
 * bolt's force after loading, whether the clamped faces open and whether they slip; the preload is there
 * before any load, so nothing is proportional to it and no load control moves them.
 */
import { forceWords } from "../checkKinds.js";
import { group, heldRows, madeOfRows, pushedRows, rigidFloorRows, spaced } from "../setup.js";
import { contactRows, list, outcomeRefs } from "./contact.js";
import { stopsCaption } from "./nonlinear.js";
import { boltMotion, poseRoutine } from "../poses.js";
import { planned } from "./stub.js";

/** A bolt as its row says it: "M6 bolt, 5 kN preload, clamps plate and bracket". */
export function boltWords(bolt) {
  const between = Array.isArray(bolt.between) && bolt.between.length === 2 ? bolt.between : null;
  const preload = Number(bolt.preload_N);
  if (!bolt.size || !between || !Number.isFinite(preload)) return typeof bolt.words === "string" ? bolt.words : "";
  return `${bolt.size} bolt, ${forceWords(preload)} preload, clamps ${spaced(between[0])} and ${spaced(between[1])}`;
}

/** What a bolt came to under the load, for its row's hint: "6.1 kN after loading", "goes slack". */
function outcomeWords(outcome) {
  if (!outcome) return "";
  if (outcome.slack === true) return "goes slack";
  const force = Number(outcome.force_N);
  return Number.isFinite(force) ? `${forceWords(force)} after loading` : "";
}

/**
 * Study's "Bolted": one row per bolt as cadgen solved it ("M6 bolt, 5 kN preload, clamps plate and
 * bracket"), its force after loading its hint, chosen with the two parts it clamps. [] for none.
 */
export function boltRows(result) {
  const extras = result?.mesh?.userData || {};
  const outcomes = list(extras.bolts);
  const rows = list(result?.study?.bolts).map((bolt, index) => {
    const label = boltWords(bolt);
    if (!label) return null;
    const outcome = outcomes[index];
    const refs = outcomeRefs(outcome);
    const hint = outcomeWords(outcome);
    return { id: `bolt:${index}`, label, detail: "", wrap: true, ...(hint ? { hint } : {}),
      ...(refs.length ? { refs, summary: `Bolt: ${label}` } : {}) };
  }).filter(Boolean);
  return group("bolts", "Bolted", "load", rows);
}

/** Its setup: where it is held, what pushes it, its bolts, any other contact, the rigid floor, what it is made of. */
export const BOLT_SETUP = Object.freeze([heldRows, pushedRows, boltRows, contactRows, rigidFloorRows, madeOfRows]);

export default planned({
  name: "bolt", tier: 3, word: "Bolted joint",
  // Not linear in the load: every check is judged as solved, its takeaway its own sentence, never "OK up to 18× this load".
  scaling: "none",
  checks: ["bolt_load", "joint_separation", "joint_slip", "stress", "displacement", "contact_pressure"],
  // The load-step scrubber (from the preload), the field, the deformation.
  setupGroups: BOLT_SETUP,
  // Tighten and load: the bolt tightened (ramped), then the service load's steps, on a Load row (`../poses.js`).
  motion: boltMotion,
  routine: (result) => poseRoutine(boltMotion(result)),
  markers: ["load", "fixture", "rigid_plane"],
  limitWord: "Lite",
  stopCaption: stopsCaption,
});

