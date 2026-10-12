/**
 * Magnetic / electric (lite), Tier 3, by the study's `mode`: a static electric field, a steady (DC) current,
 * a static magnetic field or an AC one at a frequency (`ac_magnetic`: eddy currents, skin effect, induction
 * heating). Its fields are on the parts' surface: the voltage and the electric field, the current density or
 * the magnetic field (an AC field's as amplitudes); the Joule heat handed to a thermal solve adds the
 * temperature, and the magnetic force handed to a static solve adds the stress and displacement, so it
 * deforms. A field grows with the voltage and a force with the current squared, so no load control moves
 * them. A magnetic result also states the force on each part (`facts`), with no limit.
 */
import { fieldAndMappedDeformation } from "../controls.js";
import { plainNumber, threeFigures } from "../numbers.js";
import { cooledRows, faceEntryRows, facesWords, forceDirection, group, heldRows, keptAtRows, madeOfRows, spaced, wholeRefs } from "../setup.js";
import { acMotion, poseRoutine } from "../poses.js";
import { planned } from "./stub.js";

/** The study as the viewer read it (`readStudy`: its voltages, currents, coils, AC field and heat out); {} for none. */
const studyOf = (result) => result?.study || {};
const list = (value) => (Array.isArray(value) ? value : []);

/** An AC study's frequency, null for a static or DC one. */
const frequencyOf = (result) => {
  const study = studyOf(result);
  return study.mode === "ac_magnetic" && Number.isFinite(study.frequencyHz) ? study.frequencyHz : null;
};

/** A frequency as a person says it: "50 Hz", "15.8 kHz", "2 MHz". */
export function hertz(value) {
  for (const [scale, unit] of [[1e6, "MHz"], [1e3, "kHz"]]) {
    if (value >= scale) return `${threeFigures(value / scale)} ${unit}`;
  }
  return `${threeFigures(value)} Hz`;
}

/** " at 50 kHz" for an AC study, "" otherwise. */
const atFrequency = (result) => {
  const hz = frequencyOf(result);
  return hz === null ? "" : ` at ${hertz(hz)}`;
};

/** Study's "Electrodes": "Held at 1000 V" per held voltage, "2 A in" ("2 A in at 50 kHz") per current put in, each its faces under it. */
export function electrodeRows(result) {
  const study = studyOf(result);
  const at = atFrequency(result);
  const held = list(study.voltages).map((entry) => {
    const volts = entry.value;
    const label = volts === null ? "Held at a voltage" : `Held at ${plainNumber(volts)} V`;
    const named = typeof entry.name === "string" && entry.name && !/^-?[\d.e+]+ V$/.test(entry.name) ? entry.name : "";
    return { faces: entry.faces, label, hint: named, summary: (faces) => `${label} on ${facesWords(faces)}` };
  });
  const fed = list(study.currents).map((entry) => {
    const amps = entry.value;
    const label = amps === null ? `A current in${at}` : `${plainNumber(amps)} A in${at}`;
    return { faces: entry.faces, label, summary: (faces) => `${label} through ${facesWords(faces)}` };
  });
  return group("electrodes", "Electrodes", "fixture", faceEntryRows(result, "electrode", [...held, ...fed].filter((entry) => entry.faces.length)));
}

/**
 * Study's "Driven by": "Coil 2 A × 100 turns" per coil, the part it is wound on as its hint; at a frequency
 * "Coil 2 A at 50 kHz" (its turns in the hint), and a uniform field the air carries, "Field 10 mT at 500 Hz".
 */
export function coilRows(result) {
  const study = studyOf(result);
  const at = atFrequency(result);
  const rows = list(study.coils).map((coil, index) => {
    const { amps, turns } = coil;
    const ampsWords = amps === null ? "" : ` ${plainNumber(amps)} A`;
    const turnsWords = turns === null ? "" : `${plainNumber(turns)} turns`;
    const whole = `Coil${ampsWords}${turnsWords ? ` × ${turnsWords}` : ""}${at}`;
    const label = at ? `Coil${ampsWords}${at}` : whole;
    const part = coil.part ? spaced(coil.part) : "";
    const wound = part ? `wound on ${part}` : "";
    // At a frequency the row has no room for the turns, so they lead the hint.
    const hint = at && turnsWords ? [turnsWords, wound].filter(Boolean).join(", ") : wound && `Wound on ${part}`;
    return { id: `coil:${index}`, label, detail: "", ...(hint ? { hint } : {}), summary: `${whole}${wound ? ` ${wound}` : ""}` };
  });
  const field = study.appliedField || null;
  if (field && at) {
    const mT = field.mT;
    const label = `Field${mT === null ? "" : ` ${plainNumber(mT)} mT`}${at}`;
    const axis = field.direction || [];
    const along = axis.length === 3 && axis.filter((c) => c !== 0).length === 1 ? ` along ${"xyz"[axis.findIndex((c) => c !== 0)]}` : "";
    rows.push({ id: "field", label, detail: "", summary: `A uniform${mT === null ? "" : ` ${plainNumber(mT)} mT`} field${along}${at}` });
  }
  return group("coils", "Driven by", "wave", rows);
}

/** What leads the takeaway: "AC" for a field at a frequency, else the static word. */
export const electromagneticLimitWord = (result) => (frequencyOf(result) === null ? "Static" : "AC");

/** The Joule heat's way out (electro_thermal), as a thermal study's: kept at, cooled by air. */
function heatOutRows(result) {
  const block = studyOf(result).heatOut;
  if (!block) return [];
  const study = { ...result.study, temperatures: block.temperatures, convection: block.convection };
  const view = { ...result, study };
  return [...keptAtRows(view), ...cooledRows(view)];
}

export const ELECTROMAGNETIC_SETUP = Object.freeze([electrodeRows, coilRows, heatOutRows, heldRows, madeOfRows]);

// What makes a part's force, in plain words.
const FORCE_BY = Object.freeze({ "J x B": "the current in the field", "Maxwell stress": "its magnetic pull" });

/** Which way a force points: "down", "along +X", near enough to an axis (within about 18°) to say so, else as it is. */
function forceWay(vector) {
  const size = Math.hypot(...vector);
  if (!(size > 0)) return "";
  const axis = vector.findIndex((value) => Math.abs(value) / size >= 0.95);
  return forceDirection(axis >= 0 ? vector.map((value, k) => (k === axis ? Math.sign(value) : 0)) : vector);
}

/** A force in newtons as the verdict says it, three figures: "0.424 N", "12.5 N". */
const newtons = (value) => `${threeFigures(value)} N`;

/**
 * A magnetic result's force on each part, as the verdict's rows with no limit: "Force on the plunger",
 * "0.424 N down, its magnetic pull", chosen with the whole result. With no checks they are its verdict,
 * headed by the largest ("Pulls the plunger with 0.424 N", "0.424 N down"). null with none written.
 */
export function electromagneticFacts(result) {
  const forces = result?.analysis?.forces;
  if (!forces?.length) return null;
  const refs = wholeRefs(result);
  const named = (force) => (force.part ? `the ${spaced(force.part)}` : "the part");
  const rows = forces.map((force, index) => {
    const size = Math.hypot(...force.vector);
    const way = forceWay(force.vector);
    const by = FORCE_BY[force.by] || "";
    const line = [`${newtons(size)}${way ? ` ${way}` : ""}`, by].filter(Boolean).join(", ");
    const label = `Force on ${named(force)}`;
    return { id: `fact:force:${index}`, kind: "force", status: "none", label, part: "", line, use: null, margin: null,
      choice: { id: `fact:force:${index}`, ...(refs.length ? { refs } : {}), summary: `${label}: ${line}` } };
  });
  const largest = forces.reduce((best, force) => (Math.hypot(...force.vector) > Math.hypot(...best.vector) ? force : best));
  const size = Math.hypot(...largest.vector);
  return { title: `Pulls ${named(largest)} with ${newtons(size)}`, caption: `${newtons(size)} ${forceWay(largest.vector)} on ${named(largest)}`.replace("  ", " "), rows };
}

export default planned({
  name: "electromagnetic", tier: 3, word: "Magnetic / electric", noun: "this voltage", limitWord: "Static",
  checks: ["electric_field", "temperature", "stress", "displacement"],
  limitWordOf: electromagneticLimitWord,
  // The field select (every field it carries), and the deformation only when the force was mapped onto the structure.
  defaultControls: fieldAndMappedDeformation,
  setupGroups: ELECTROMAGNETIC_SETUP,
  // An AC field turning through its cycle, on a Phase row (`../poses.js`); a static field has none.
  motion: acMotion,
  routine: (result) => poseRoutine(acMotion(result)),
  facts: electromagneticFacts,
  markers: ["fixture"], displayTitle: "Electrodes and coils",
});
