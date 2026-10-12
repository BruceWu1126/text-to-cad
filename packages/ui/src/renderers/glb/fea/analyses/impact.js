/**
 * Drop impact (lite): a part dropped onto a rigid floor, simulated through time, Tier 3. Its result
 * is a series of times after first contact (`39.6 µs`), evenly spaced plus the moment of the peak
 * stress, each frame's stress and displacement (relative to the part's travel) its own, with the
 * envelopes over the run beside them, and the plastic strain left at the end where plasticity was
 * given. Its verdict judges the peak stress, the peak g the floor gives it ("Peak g") and the
 * permanent strain, and choosing a check jumps the scrubber to when it peaked (`at.frame`); its
 * takeaway leads with "Rigid floor · " (the tier's limit word). A drop's stress grows with the
 * impact speed, not in step with the drop height, so there is no load control. What you see opens
 * on the Time scrubber, on the peak (the series' default), the field and the deformation. Where the file
 * says the fall, the Time row is the whole motion's (`../fall.js`, `../motion.js`): it falls from its
 * height, its solved frames slowed down where it meets the floor, and it rebounds and rests, with the
 * states Released, Contact, Peak and At rest; Preview plays it as Drop. Its setup says how it was dropped ("1 m drop onto a rigid floor",
 * the way it falls and any friction its hint, the faces that landed under it) and what it is made of.
 */
import { DROP, fallMotion } from "../fall.js";
import { plainNumber } from "../numbers.js";
import { droppedRows, forceDirection, madeOfRows } from "../setup.js";
import { PLAY, timeFieldAndDeformation } from "./transient.js";

export { PLAY };

/** How it falls and slides, in words: "Falling down, no friction", "Falling along +X, friction 0.3". */
export function fallWords(result) {
  const drop = result.study.drop;
  const way = drop?.direction ? forceDirection(drop.direction) : "";
  const friction = drop?.friction > 0 ? `friction ${plainNumber(drop.friction)}` : "no friction";
  return [way ? `Falling ${way}` : "", friction].filter(Boolean).join(", ");
}

/**
 * Study's "Dropped" for an impact: one row, "1 m drop onto a rigid floor", how it falls and slides
 * its hint, the faces that met the floor under it; chosen, it carries the drop into a prompt.
 */
export function impactDroppedRows(result) {
  const hint = fallWords(result);
  return droppedRows(result).map((group) => ({
    ...group,
    children: group.children.map((row) => {
      const label = `${row.label} onto a rigid floor`;
      const summary = row.summary ? row.summary.replace(row.label, label) : row.summary;
      return { ...row, label, ...(hint ? { hint } : {}), ...(summary ? { summary } : {}) };
    }),
  }));
}

export default Object.freeze({
  name: "impact",
  tier: 3,
  word: "Drop impact",
  noun: "this drop",
  family: null,
  estimate: false,
  scalesWithLoad: false,
  // Not linear in the load: every check is judged as solved, its takeaway its own sentence, never "OK up to N× this load".
  scaling: "none",
  checks: Object.freeze(["stress", "acceleration", "plastic_strain"]),
  checkLabels: Object.freeze({ acceleration: "Peak g" }),
  defaultControls: timeFieldAndDeformation,
  setupGroups: Object.freeze([impactDroppedRows, madeOfRows]),
  // Drop: the fall from its height, its solved frames slowed down where it meets the floor, its rebound and rest;
  // a file from before cadgen wrote the fall plays its frames.
  routine: (result) => (result?.series ? (result.analysis?.fall ? DROP : PLAY) : null),
  // Its motion (`../motion.js`): the Time row over the fall and its solved frames, the states, the routine.
  motion: fallMotion,
  markers: Object.freeze(["drop", "rigid_plane"]),
  displayTitle: "Drop and floor",
  limitWord: "Rigid floor",
});
