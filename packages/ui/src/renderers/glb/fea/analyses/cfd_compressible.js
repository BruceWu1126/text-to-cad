/**
 * Fast gas flow (lite): steady compressible flow of an ideal gas through or around the part, Tier 3: the
 * step up from Flow (`cfd`) for a gas past about Mach 0.3, where its density changes with its speed. Its
 * fields are on the part's wetted surface: the wall pressure (Pa above the outlet's, signed), the Mach
 * number (the flow's speed over the local speed of sound), the gas temperature and the speed near the wall
 * (m/s, the fastest flow within a passage width); mapped onto the
 * structure (one-way), the part's stress and displacement join them and it deforms. Its verdict judges
 * the pressure drop, the fastest flow and the highest Mach number (and the stress and displacement when
 * mapped), none of which a load control moves, and its takeaway leads with "Ideal gas · ". What you see
 * opens on the field (Pressure first, then Mach number and Temperature) and, mapped, the deformation; its
 * setup and markers are Flow's. Its routine moves tracers along the solved flow.
 */
import { FLOW_SETUP, flowControls } from "./cfd.js";
import { tracersRoutine } from "../poses.js";
import { planned } from "./stub.js";

export default planned({
  name: "cfd_compressible", tier: 3, word: "Fast gas flow", noun: "this flow", limitWord: "Ideal gas", family: null, scalesWithLoad: false,
  // The flow's wall stress grows with its speed squared and more: no check is a multiple of the load.
  scaling: "none",
  checks: ["pressure_drop", "velocity", "mach", "stress", "displacement"],
  defaultControls: flowControls,
  setupGroups: FLOW_SETUP,
  // Tracers carried by the solved flow, in Preview (`../poses.js`, drawn by `../../feaTracers.js`).
  routine: tracersRoutine,
  markers: ["inlet", "outlet", "fixture"], displayTitle: "Flow openings",
});
