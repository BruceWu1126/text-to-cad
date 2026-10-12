/**
 * Fatigue life: how many cycles of its load the part lasts, from a static load case repeated. Its fields are
 * the life (log10 cycles, the colour bar in powers of ten) and the fatigue safety factor, the displacement at
 * the load's peak. A Goodman factor is not linear in the load, so it has no load control. Its Load cycle
 * (`../poses.js`) swings the stress (the cycle's peak, `_cycle_stress`) and the deformation between the
 * cycle's ends.
 */
import { fieldAndDeformation } from "../controls.js";
import { cycleMotion, poseRoutine } from "../poses.js";
import { heldRows, madeOfRows, pushedRows } from "../setup.js";
import { planned } from "./stub.js";

const FATIGUE_FACTOR = "_fatigue_factor";

/** Whether the life is the same everywhere: the part outlasts the material's data (the life is capped) on every face. */
export function lifeCapped(result) {
  const life = result.fields.find((entry) => entry.attribute === "_life");
  return Boolean(life) && !(life.max - life.min > 1e-6);
}

/** The field (life first, the fatigue margin where the life is capped everywhere) and the deformation. */
export function fatigueControls(result) {
  const [field, deformation] = fieldAndDeformation(result);
  const margin = lifeCapped(result) && field.options.some((option) => option.value === FATIGUE_FACTOR);
  return [margin ? { ...field, defaultValue: FATIGUE_FACTOR } : field, deformation];
}

export default planned({
  name: "fatigue", word: "Fatigue life",
  checks: ["fatigue"],
  // The field (life first, then the fatigue factor and the displacement) and the deformation.
  defaultControls: fatigueControls,
  setupGroups: [heldRows, pushedRows, madeOfRows],
  // Load cycle: the stress and the deformation swinging between the cycle's ends, on a Cycle row (`../poses.js`).
  motion: cycleMotion,
  routine: (result) => poseRoutine(cycleMotion(result)),
  markers: ["load", "fixture", "body_load"],
});
