/**
 * Two fluids (lite), Tier 3: a liquid and a gas inside the part over time (sloshing in a shaken tank, filling
 * or draining, a rising bubble). Its fields are on the wetted walls, per frame: the water fraction (1 where
 * the liquid touches the wall) and the wall pressure; mapped onto the structure (one-way), the stress and
 * displacement join them. It judges the highest the liquid rises (against the brim or a limit) and the
 * hardest it presses on the walls. Liquid that left over the brim is the takeaway itself, and a run stopped
 * at its time limit says so first.
 */
import { fieldAndDeformation, mapsStructure } from "../controls.js";
import { plainNumber } from "../numbers.js";
import { frameControl } from "../series.js";
import { axisWords, group, heldRows, madeOfRows, wholeRefs } from "../setup.js";
import { planned, playSeries } from "./stub.js";
import { historyWords } from "./transient.js";

const record = (raw) => (raw && typeof raw === "object" && !Array.isArray(raw) ? raw : null);

/** What the file says of the two fluids: the study's `multiphase` (as the viewer read it) and its analysis's `fill`. */
function twoFluids(result) {
  const extras = result?.mesh?.userData || {};
  return { study: record(result?.study?.multiphase), fill: record(extras.analysis?.fill) };
}

/** How full it starts, in words: "Half full of water", "A quarter full of oil", "40 % full of water". */
export function fillWords(fraction, liquid = "water") {
  const name = liquid || "liquid";
  if (!Number.isFinite(fraction)) return `Partly ${name}`;
  const named = [[0.25, "A quarter full"], [0.5, "Half full"], [0.75, "Three quarters full"], [1, "Full"]];
  const near = named.find(([share]) => Math.abs(fraction - share) < 0.02);
  return near ? `${near[1]} of ${name}` : `${Math.round(100 * fraction)} % full of ${name}`;
}

/** How it is shaken, in words: "shaken 0.3 g along X". "" for a part held still. */
export function shakeWords(acceleration) {
  const shake = record(acceleration);
  if (!shake || !Number.isFinite(shake.amplitude_g) || !Array.isArray(shake.direction)) return "";
  const along = axisWords(shake.direction);
  return `shaken ${plainNumber(Math.abs(shake.amplitude_g))} g${along ? ` along ${along}` : ""}`;
}

const SINGLE_FLUID = /^Completely full: solved as single-fluid flow/;
const SPILL = /^Spills over the brim: /;
const TIME_LIMIT = /^Stopped at its time limit at t = (.+?) of /;

/** The takeaway of a tank that spilled: the file's own sentence ("Spills over the brim: 2.3 mL left the tank ..."); "" otherwise. */
export function spillCaption(result) {
  return (result?.analysis?.warnings || []).find((line) => SPILL.test(line)) || "";
}

/** What leads the takeaway of a run stopped at its time limit: "Stopped at its time limit at 0.42 s"; "" otherwise. */
export function timeLimitCaption(result) {
  const line = (result?.analysis?.warnings || []).find((entry) => TIME_LIMIT.test(entry));
  return line ? `Stopped at its time limit at ${TIME_LIMIT.exec(line)[1]}` : "";
}

/**
 * Study's "Filled": how full it starts and how it is shaken, its hint how the shake changes over time; a
 * completely full inside's hint says it was solved as the liquid's flow alone ("Completely full: solved as
 * single-fluid flow, no free surface").
 */
export function filledRows(result) {
  const { study, fill } = twoFluids(result);
  if (!study && !fill) return [];
  const words = fillWords(fill?.fraction, fill?.liquid || study?.liquid?.name);
  const shake = shakeWords(study?.acceleration);
  const label = shake ? `${words}, ${shake}` : words;
  const history = study?.acceleration ? historyWords(study.acceleration.history) : "";
  // A completely full inside has no free surface: cadgen solves the liquid's flow alone and says so.
  const single = (result.analysis?.warnings || []).find((line) => SINGLE_FLUID.test(line)) || "";
  const hint = [single, history ? `Over time: ${history}` : ""].filter(Boolean).join(". ");
  const refs = wholeRefs(result);
  return [{ id: "filled", label: "Filled", detail: "", glyph: "flow", children: [{ id: "filled:0", label, detail: "", wrap: true,
    ...(hint ? { hint } : {}),
    ...(refs.length ? { refs, summary: `Filled: ${label.charAt(0).toLowerCase()}${label.slice(1)}${history ? `, ${history}` : ""}` } : {}) }] }];
}

const sideWords = (opening) => {
  const [axis, end] = String(opening || "").split("_");
  return axis && end ? `the ${end === "min" ? "low" : "high"} ${axis.toUpperCase()} side` : "";
};

/** Study's "Flow in/out": each inlet's speed, side and fluid, each outlet's pressure and side. */
export function openingRows(result) {
  const { study } = twoFluids(result);
  if (!study) return [];
  const refs = wholeRefs(result);
  const row = (id, label) => ({ id, label, detail: "", wrap: true, ...(refs.length ? { refs, summary: label } : {}) });
  const fluidOf = (kind) => (kind === "gas" ? study.gas?.name : study.liquid?.name) || kind;
  const rows = [
    ...(Array.isArray(study.inlets) ? study.inlets : []).map((inlet, index) => row(`inlet:${index}`,
      `${fluidOf(inlet.fluid)} in${Number.isFinite(inlet.velocity_m_s) ? ` at ${plainNumber(inlet.velocity_m_s)} m/s` : ""}, ${sideWords(inlet.opening)}`)),
    ...(Array.isArray(study.outlets) ? study.outlets : []).map((outlet, index) => row(`outlet:${index}`,
      `Out${Number.isFinite(outlet.pressure_Pa) ? ` at ${plainNumber(outlet.pressure_Pa)} Pa` : ""}, ${sideWords(outlet.opening)}`)),
  ].map((entry) => ({ ...entry, label: entry.label.charAt(0).toUpperCase() + entry.label.slice(1) }));
  return group("flow", "Flow in/out", "flow", rows);
}

/** Two fluids' setup: filled (and shaken), flow in/out, then (mapped onto the structure) held and made of. */
export const TWO_FLUIDS_SETUP = Object.freeze([filledRows, openingRows, heldRows, madeOfRows]);

/**
 * The Time scrubber over its frames (opening on the series' default, the highest rise), the field (water
 * fraction first), and the deformation only when the fluids' push was mapped onto the structure.
 */
export function twoFluidControls(result) {
  const [field, deformation] = fieldAndDeformation(result);
  const time = frameControl(result, "frame");
  return [...(time ? [time] : []), field, ...(mapsStructure(result) ? [deformation] : [])];
}

export default planned({
  name: "multiphase", tier: 3, word: "Two fluids", noun: "this shake", limitWord: "Laminar",
  // The flow's wall stress grows with its speed squared and more: no check is a multiple of the load.
  scaling: "none",
  checks: ["fill_level", "wall_pressure", "stress", "displacement"],
  defaultControls: twoFluidControls,
  setupGroups: TWO_FLUIDS_SETUP,
  routine: playSeries,
  markers: ["inlet", "outlet", "fixture"], displayTitle: "Flow openings",
  // A spill is the takeaway, not the fill level's "Rises to 40 mm, limit: the brim" (the fluid ends at the brim).
  caption: spillCaption,
  // A run stopped at its time limit says so first: nothing after it was solved.
  stopCaption: timeLimitCaption,
});
