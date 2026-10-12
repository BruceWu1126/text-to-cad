/**
 * Heat over time: temperature against time from a start, opening on the hottest frame; choosing its check
 * jumps the scrubber to when it happened (`at.frame`). Its setup and markers are the steady analysis's.
 */
import { frameControl } from "../series.js";
import { planned, playSeries } from "./stub.js";
import { HEAT_MARKERS, HEAT_SETUP, fieldOnly } from "./thermal.js";

export { PLAY } from "./stub.js";

/** The field, then the time scrubber where the result has frames to scrub. */
export function fieldAndTime(result) {
  const time = frameControl(result, "frame");
  return [...fieldOnly(result), ...(time ? [time] : [])];
}

export default planned({
  name: "thermal_transient", word: "Heat over time", noun: "this heat",
  checks: ["temperature"],
  defaultControls: fieldAndTime,
  setupGroups: HEAT_SETUP,
  routine: playSeries,
  markers: HEAT_MARKERS, displayTitle: "Heat inputs and temperatures",
});

