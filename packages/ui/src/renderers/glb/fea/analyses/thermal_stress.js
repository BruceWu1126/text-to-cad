/**
 * Heat stress: the stress and movement a temperature field puts in a part, with any mechanical loads on top.
 * The load control scales the temperature change and the loads together ("this heat"). Where the file finds
 * the peak on a clamped edge (`singular_clamped_edge`), cadgen judges strength a little away from it (the
 * check's value is where the colours stop, `edge_peak` the edge's own), and the stress row says so
 * (`checkHint`); an older file, judged at the edge, says to read away.
 */
import { fieldAndDeformation } from "../controls.js";
import { plainNumber } from "../numbers.js";
import { cooledRows, heatedRows, heldRows, keptAtRows, madeOfRows, pushedRows } from "../setup.js";
import { heatMotion, poseRoutine } from "../poses.js";
import { LOAD_RAMP } from "./static.js";
import { planned } from "./stub.js";

// A part held fast while heat makes it grow is singular at the clamped edge: its peak is the mesh's.
const SINGULAR_HINT = "Peak at the clamped edge: a singularity that grows as the mesh is refined. Read the stress a little away from the edge";

export default planned({
  name: "thermal_stress", word: "Heat stress", noun: "this heat", family: "static", scalesWithLoad: true,
  checks: ["stress", "displacement"],
  defaultControls: fieldAndDeformation,
  setupGroups: [heldRows, pushedRows, keptAtRows, heatedRows, cooledRows, madeOfRows],
  // Heat up: from the reference temperature to the solved one, on a Heat row (`../poses.js`); a file with no
  // temperature field ramps its load as static does.
  motion: heatMotion,
  routine: (result) => poseRoutine(heatMotion(result)) || LOAD_RAMP,
  markers: ["load", "fixture", "body_load", "temperature", "heat", "convection"], displayTitle: "Loads, fixtures and heat",
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

