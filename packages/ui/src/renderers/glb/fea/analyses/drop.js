/**
 * Drop (estimate): a drop as one equivalent steady load, Tier 2. The landing faces are held and the
 * whole part carries G g toward them, so it is a static result in every number: stress and
 * displacement, both k times larger at k times this drop, under the Load ramp. Its verdict's
 * takeaway leads with "Estimate · " (the tier says so). Its setup says how it was dropped (the
 * landing faces under it, how it stops and the g that became its hint) and what it is made of; the
 * equivalent load is that row, not a second "Pushed" one. Where the file says the fall, it moves as a drop does
 * (`../fall.js`, `../motion.js`): What you see's Time row scrubs it falling from its height onto the floor, the stop it
 * assumed (the stress and the deformation ramping to the estimated peak and back) and its rest, with the states
 * Released, Contact, Peak and At rest, and Preview plays it as Drop.
 */
import { fieldAndDeformation } from "../controls.js";
import { plainNumber } from "../numbers.js";
import { DROP, fallMotion } from "../fall.js";
import { droppedRows, madeOfRows } from "../setup.js";
import { LOAD_RAMP } from "./static.js";
import { planned } from "./stub.js";

const G0_MM_S2 = 9806.65;

/**
 * The equivalent load a drop became, in g: height over stopping distance, or a half-sine's peak
 * π v / (2 τ) with v = √(2 g h). null where the file does not say enough.
 */
export function dropG(drop) {
  if (!drop || !(drop.heightMm > 0)) return null;
  if (drop.stopMm > 0) return drop.heightMm / drop.stopMm;
  if (drop.impactMs > 0) return (Math.PI * Math.sqrt(2 * G0_MM_S2 * drop.heightMm)) / (2 * (drop.impactMs / 1000)) / G0_MM_S2;
  return null;
}

/** Study's "Dropped", its row's hint ending with the steady load it became: "stopping in 2 mm, about 500 g steady". */
export function droppedEstimateRows(result) {
  const groups = droppedRows(result);
  const g = dropG(result.study?.drop);
  if (!groups.length || g === null) return groups;
  const words = `about ${plainNumber(g)} g steady`;
  return groups.map((group) => ({
    ...group,
    children: group.children.map((row) => ({ ...row, hint: row.hint ? `${row.hint}, ${words}` : words })),
  }));
}

export default planned({
  name: "drop", tier: 2, word: "Drop (estimate)", noun: "this drop", family: "static", estimate: true, scalesWithLoad: true,
  checks: ["stress", "displacement"], checkLabels: { stress: "Drop" },
  // Drop: the fall from its height, the stop slowed down (the load ramping to its peak and back) and the rest; a
  // file from before cadgen wrote the fall ramps the load as static does.
  routine: (result) => (result?.analysis?.fall ? DROP : LOAD_RAMP),
  // Its motion (`../motion.js`): the Time row, the states and the routine over the fall.
  motion: fallMotion,
  defaultControls: fieldAndDeformation,
  setupGroups: [droppedEstimateRows, madeOfRows],
  markers: ["drop", "fixture"],
});
