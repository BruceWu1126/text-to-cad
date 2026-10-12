/**
 * Motion: a result whose part moves as a whole through time (a drop falling, meeting the floor and coming to rest),
 * shown the way a STEP's kinematics is. Study's What you see has a **Time** row, a scrubber over the whole motion
 * whose value field says the real time at each moment; the motion's named states join the Preset row (as a
 * kinematics pose does); and Preview's playbar plays the same timeline. Generic: an analysis supplies its motion
 * (`motion(result)`, null for none) and the viewer draws the row, the presets and the routine from it:
 *
 * - `kind` (what the renderer draws it as: "fall"; "pose", `./poses.js`, a pose at each moment) and `label` (the
 *   routine's word: "Drop"); `row`, the row's word where it is not "Time" ("Cycle", "Phase", "Load", "Heat");
 * - `duration`: the timeline, in seconds as it plays (a moment of milliseconds may be slowed on it);
 * - `defaultAt`: where the Time row opens; `states`: `[{ id, label, at }]`, its named moments;
 * - `timeAt(at)`: what the value field says there ("−0.32 s", "0.21 ms"); `moments`: moments the scrubber also
 *   stops at (a solved frame's), beside `MOTION_SAMPLES` evenly spaced; `hint`: the row's hint;
 * - `play`: `{ from, to }`, the span the playbar plays with reduced motion (the part kept where it is);
 * - `atFrame(index)`: the moment of a frame of its series, where a chosen check jumps.
 *
 * Pure: no three.js, no React.
 */

/** The scrubber's evenly spaced stops over the whole motion. */
export const MOTION_SAMPLES = 240;

/** The routine Preview plays a motion as: its timeline, as the Time row shows it (GlbRenderer plays it). */
export const motionRoutine = (motion) => Object.freeze({ id: `fea:${motion.kind}`, label: motion.label, kind: "motion" });

/** The Time row over a motion: a number control along its timeline that stops at its moments, its value field the real time there. */
export function motionControl(motion) {
  const evenly = Array.from({ length: MOTION_SAMPLES + 1 }, (_, index) => (motion.duration * index) / MOTION_SAMPLES);
  const stops = [...evenly, ...motion.states.map((state) => state.at), ...(motion.moments || []), motion.defaultAt]
    .filter((at) => Number.isFinite(at) && at >= 0 && at <= motion.duration);
  // Each moment once: one a hair from another (a state on an even stop) is that one.
  const snaps = stops.sort((a, b) => a - b).filter((at, index, all) => index === 0 || at - all[index - 1] > 1e-9 * motion.duration);
  return {
    id: "motion", drives: "motion", type: "number", label: motion.row || "Time", min: 0, max: motion.duration, defaultValue: motion.defaultAt, unit: "",
    snaps, frameLabels: snaps.map((at) => motion.timeAt(at)), ...(motion.hint ? { labelTitle: motion.hint } : {}),
  };
}

/** A motion's named states as Preset entries: every control at its default (`defaults`) but the Time row, at the state. */
export const motionPresets = (motion, defaults) => motion.states.map((state) => ({
  value: `motion:${state.id}`, label: state.label, values: { ...defaults, motion: state.at },
}));

/** What the playbar plays of a motion: the whole timeline, or with reduced motion only its `play` span. `{ from, duration }`. */
export function motionSpan(motion, reducedMotion = false) {
  if (!reducedMotion || !motion.play) return { from: 0, duration: motion.duration };
  return { from: motion.play.from, duration: Math.max(motion.play.to - motion.play.from, 0) };
}
