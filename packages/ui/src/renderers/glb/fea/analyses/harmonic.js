/**
 * Shaking: the part's steady response to a sine sweep, shaken where it is held (a base shake) or pushed by
 * loads that swing. Its series is frequencies (`490 Hz`): the response peaks and log-spaced frequencies
 * across the sweep, each frame's stress (its peak over a cycle) and displacement (relative to where it is
 * held) its own, the displacement's imaginary part (`displacement_im`) beside it, so Vibrate turns a frame
 * through its cycle (re·cos − im·sin).
 */
import { deforms, fieldAndDeformation } from "../controls.js";
import { plainNumber } from "../numbers.js";
import { frameControl } from "../series.js";
import { heldRows, madeOfRows, mapRows, pushedRows, shakenRows } from "../setup.js";
import { planned, routineOf } from "./stub.js";

/** Vibrate: a one-second loop of the chosen frequency's motion through its cycle, the real part then the imaginary. */
export const VIBRATE = routineOf("Vibrate", "vibrate");

/** The Frequency scrubber over the sweep's frames, then the deformation; the field select where there is nothing to scrub. */
export function frequencyAndDeformation(result) {
  const [field, deformation] = fieldAndDeformation(result);
  const frequency = frameControl(result, "frame");
  if (!frequency) return deforms(result) ? [field, deformation] : [field];
  return deforms(result) ? [frequency, deformation] : [frequency, field];
}

/** The sweep in words: "10 to 600 Hz sweep, 2% damping"; either half alone where the file says only that; "" for neither. */
export function sweepWords(study) {
  const sweep = study?.sweepHz ? `${plainNumber(study.sweepHz[0])} to ${plainNumber(study.sweepHz[1])} Hz sweep` : "";
  const damping = Number.isFinite(study?.dampingRatio) ? `${plainNumber(study.dampingRatio * 100)}% damping` : "";
  return [sweep, damping].filter(Boolean).join(", ");
}

/** Study's "Shaken" for a base shake: its size and line, the sweep and damping its hint ("10 to 600 Hz sweep, 2% damping"). */
export function shakenSweepRows(result) {
  const hint = sweepWords(result.study);
  return mapRows(shakenRows(result), (row) => ({ ...row, ...(hint ? { hint } : {}) }));
}

/** Study's "Pushed" for a force shake: each load row says it swings, back and forth at every frequency of the sweep. */
export function swingingRows(result) {
  if (result.study?.excitation?.kind !== "force") return [];
  return mapRows(pushedRows(result), (row) => ({ ...row, hint: "Swinging back and forth across the sweep" }));
}

export default planned({
  name: "harmonic", word: "Shaking", noun: "this shake", scalesWithLoad: true,
  checks: ["stress", "displacement", "acceleration"],
  defaultControls: frequencyAndDeformation,
  setupGroups: [heldRows, shakenSweepRows, swingingRows, madeOfRows],
  routine: () => VIBRATE,
  markers: ["fixture", "base_excitation", "load"], displayTitle: "Shaker and fixtures",
});

