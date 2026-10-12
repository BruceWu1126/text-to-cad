/**
 * Heat stress: the stress and movement a temperature field puts in a part, with any mechanical
 * loads on top. It is of the static family (its verdict needs a stress field), its stress and
 * displacement checks follow the load control, which scales the temperature change and the loads
 * together ("this heat"), and it plays the Load ramp. What you see opens on today's field and
 * deformation, the field over stress, displacement and temperature; its setup says where it is held,
 * what pushes it, where it is kept at a temperature, heated and cooled, and what it is made of.
 * Where the file finds the peak on a clamped edge (`singular_clamped_edge`), cadgen judges strength a
 * little away from it (the check's value is where the colours stop, `edge_peak` the edge's own), and
 * the stress check's row says so (`checkHint`); an older file, judged at the edge, says to read away.
 */
import { fieldAndDeformation } from "../controls.js";
import { plainNumber } from "../numbers.js";
import { cooledRows, heatedRows, heldRows, keptAtRows, madeOfRows, pushedRows } from "../setup.js";
import { heatMotion, poseRoutine } from "../poses.js";
import { LOAD_RAMP } from "./static.js";

// A part held fast while heat makes it grow is singular at the clamped edge: its peak is the mesh's.
const SINGULAR_HINT = "Peak at the clamped edge: a singularity that grows as the mesh is refined. Read the stress a little away from the edge";

export default Object.freeze({
  name: "thermal_stress",
  tier: 1,
  word: "Heat stress",
  noun: "this heat",
  family: "static",
  estimate: false,
  scalesWithLoad: true,
  checks: Object.freeze(["stress", "displacement"]),
  checkLabels: Object.freeze({}),
  defaultControls: fieldAndDeformation,
  setupGroups: Object.freeze([heldRows, pushedRows, keptAtRows, heatedRows, cooledRows, madeOfRows]),
  // Heat up: from the reference temperature to the solved one, on a Heat row (`../poses.js`); a file with no
  // temperature field ramps its load as static does.
  motion: heatMotion,
  routine: (result) => poseRoutine(heatMotion(result)) || LOAD_RAMP,
  markers: Object.freeze(["load", "fixture", "body_load", "temperature", "heat", "convection"]),
  displayTitle: "Loads, fixtures and heat",
  limitWord: "",
  /** The stress check's hint where the file finds its peak on a clamped edge; "" for anything else. */
  checkHint: (result, check) => {
    if (check.kind !== "stress") return "";
    if (check.edgePeak) {
      // The edge's peak at the load shown, as the check's own value is.
      const peak = check.edgePeak * (Number.isFinite(check.shown) && check.value > 0 ? check.shown / check.value : 1);
      return `Judged a little away from the clamped edge: the peak there, ${plainNumber(peak)} MPa, is a singularity that grows as the mesh is refined`;
    }
    return result.findings.some((finding) => finding.type === "singular_clamped_edge") ? SINGULAR_HINT : "";
  },
});
