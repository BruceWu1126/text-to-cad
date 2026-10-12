/**
 * Flow (lite), Tier 3: steady laminar incompressible flow through or around the part. Its fields are on the
 * part's wetted surface: the wall pressure (Pa, signed), the wall shear and the speed near the wall (m/s, the
 * fastest flow within a passage width, so a velocity check's peak shows); mapped onto the structure (one-way),
 * the part's stress and displacement join them and it deforms. Past the laminar range its takeaway says
 * "Laminar · unreliable above Re 2000 · ".
 */
import { fieldAndMappedDeformation } from "../controls.js";
import { flowRows, heldRows, madeOfRows } from "../setup.js";
import { tracersRoutine } from "../poses.js";
import { planned } from "./stub.js";

/** Flow's setup: flow in/out, then (mapped onto the structure) held and made of. */
export const FLOW_SETUP = Object.freeze([flowRows, heldRows, madeOfRows]);

/** What every flow shares: its noun, controls, setup, tracers and markers, none of its checks a multiple of the load. */
export const FLOW = Object.freeze({
  tier: 3, noun: "this flow",
  // The flow's wall stress grows with its speed squared and more: no check is a multiple of the load.
  scaling: "none",
  // The field select (pressure first), and the deformation only when the flow was mapped onto the structure.
  defaultControls: fieldAndMappedDeformation,
  setupGroups: FLOW_SETUP,
  // Tracers carried by the solved flow, in Preview (`../poses.js`, drawn by `../../feaTracers.js`).
  routine: tracersRoutine,
  markers: ["inlet", "outlet", "fixture"], displayTitle: "Flow openings",
});

export default planned({ ...FLOW, name: "cfd", word: "Flow", limitWord: "Laminar", checks: ["pressure_drop", "velocity", "stress", "displacement"] });
