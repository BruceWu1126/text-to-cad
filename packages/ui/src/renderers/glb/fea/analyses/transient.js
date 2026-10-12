/**
 * Over time: a held part's response to loads that change in time, a hammer blow or a sudden push. Its series
 * is times (`1.02 ms`), evenly spaced plus the moments of the peak stress and displacement, with the envelopes
 * over all time beside them; both are k times larger at k times this load, and choosing a check jumps the
 * scrubber to when it peaked (`at.frame`).
 */
import { fieldAndDeformation } from "../controls.js";
import { threeFigures } from "../numbers.js";
import { frameControl } from "../series.js";
import { heldRows, loadWords, madeOfRows, mapRows, pushedRows, shakenRows } from "../setup.js";
import { planned, playSeries } from "./stub.js";

export { PLAY } from "./stub.js";

/** A time as the scrubber says it: "0 s", "2 ms", "1.5 s", "5 min". */
export function timeWords(seconds) {
  const s = Number(seconds) || 0;
  if (s === 0) return "0 s";
  if (s < 1) return `${threeFigures(s * 1000)} ms`;
  if (s < 120) return `${threeFigures(s)} s`;
  return `${threeFigures(s / 60)} min`;
}

/**
 * A load's history as the file echoes it, in words: "step", "ramp over 2 ms", "half-sine, 2 ms",
 * "table of 4 points to 10 ms". "" for none.
 */
export function historyWords(history) {
  if (Array.isArray(history)) {
    const points = history.filter((point) => Array.isArray(point) && point.length === 2 && point.every(Number.isFinite));
    if (!points.length) return "";
    return `table of ${points.length} point${points.length === 1 ? "" : "s"} to ${timeWords(points[points.length - 1][0])}`;
  }
  if (!history || typeof history !== "object") return "";
  if (history.shape === "step") return "step";
  if (!(history.duration_s > 0)) return "";
  if (history.shape === "ramp") return `ramp over ${timeWords(history.duration_s)}`;
  if (history.shape === "half_sine") return `half-sine, ${timeWords(history.duration_s)}`;
  return "";
}

/** The Time scrubber over the run's frames, the field and the deformation; the field and the deformation with no frames. */
export function timeFieldAndDeformation(result) {
  const [field, deformation] = fieldAndDeformation(result);
  const time = frameControl(result, "frame");
  return [...(time ? [time] : []), field, deformation];
}

/**
 * Study's "Pushed" over time: each face load says how much and how it changes ("50 N half-sine,
 * 2 ms"), which way it points its hint, and a prompt carries the history too.
 */
export function pushedOverTimeRows(result) {
  const loads = result.study.loads.filter((load) => load.faces.length);
  return mapRows(pushedRows(result), (row, index) => {
    const history = historyWords(loads[index]?.history);
    if (!history || !loads[index]) return row;
    const words = loadWords(loads[index]);
    return {
      ...row,
      label: `${words.amount} ${history}`,
      ...(words.direction ? { hint: `Pointing ${words.direction}` } : {}),
      ...(row.summary ? { summary: `${row.summary}, ${history}` } : {}),
    };
  });
}

/** Study's "Shaken" over time: the shake's size and line, how it changes over time its hint ("half-sine, 2 ms"). */
export function shakenOverTimeRows(result) {
  const history = historyWords(result.study?.excitation?.history);
  return mapRows(shakenRows(result), (row) => ({
    ...row,
    ...(history ? { hint: `Over time: ${history}` } : {}),
    ...(row.summary && history ? { summary: `${row.summary}, ${history}` } : {}),
  }));
}

export default planned({
  name: "transient", word: "Over time", scalesWithLoad: true,
  checks: ["stress", "displacement"],
  defaultControls: timeFieldAndDeformation,
  setupGroups: [heldRows, pushedOverTimeRows, shakenOverTimeRows, madeOfRows],
  routine: playSeries,
  markers: ["load", "fixture", "base_excitation"],
});

