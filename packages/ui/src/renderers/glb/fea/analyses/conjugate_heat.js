/**
 * Cooled by flow (lite), Tier 3: heat carried by a flowing fluid into and out of the part (conjugate heat
 * transfer). Its fields are on the part's surface: its temperature, the fluid's mixed temperature across the
 * passage at each wetted wall, the heat into the flow (W/m², signed), the heat transfer coefficient, the wall
 * pressure and the speed near the wall. The hottest point is measured from the coolest temperature the study
 * sets; nothing deforms.
 */
import { fieldOnly } from "./thermal.js";
import { coolantRows, cooledRows, flowRows, heatedRows, keptAtRows, madeOfRows } from "../setup.js";
import { tracersRoutine } from "../poses.js";
import { planned } from "./stub.js";

/** Cooled by flow's setup: flow in/out, the coolant, heated, kept at, cooled by air, made of. */
export const COOLED_SETUP = Object.freeze([flowRows, coolantRows, heatedRows, keptAtRows, cooledRows, madeOfRows]);

/** The takeaway's lead word: the flow model the file says it solved, else "Flow". */
export const cooledLimitWord = (result) => {
  const regime = result?.analysis?.regime || result?.study?.flow?.regime;
  return regime === "turbulent" ? "Turbulent" : regime === "laminar" ? "Laminar" : "Flow";
};

export default planned({
  name: "conjugate_heat", tier: 3, word: "Cooled by flow", noun: "this heat", limitWord: "Flow", limitWordOf: cooledLimitWord,
  checks: ["temperature", "pressure_drop", "velocity"],
  defaultControls: fieldOnly,
  setupGroups: COOLED_SETUP,
  // Tracers carried by the solved flow, in Preview (`../poses.js`, drawn by `../../feaTracers.js`).
  routine: tracersRoutine,
  markers: ["inlet", "outlet", "heat", "temperature", "convection"], displayTitle: "Flow openings and heat",
});
