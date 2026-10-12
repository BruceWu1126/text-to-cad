/**
 * A drop's motion (`motion.js`), for a drop (estimate) and a drop impact, from the fall cadgen writes
 * (`extras.analysis.fall`: the height, the way it falls in the part's own axes, the impact speed,
 * the floor's point and normal, how long the impact lasts, and for an impact its rebound and its
 * centre of mass's travel at each frame). Its timeline: the part waits at its height, falls under
 * gravity at real time (a fall too short to see is slowed to `MIN_FALL_S`), meets the floor, and the
 * impact, a matter of milliseconds, is slowed down by a round factor to last about `IMPACT_S`; an
 * impact that rebounds hops at real time, and it settles at rest. The Time row scrubs it, its value
 * field the real time from when it lands ("−0.32 s" falling, "0.21 ms" after); its states are
 * Released, Contact, Peak and At rest; Preview plays it as Drop, and with reduced motion only the
 * impact, where it lands. Pure: no three.js, no React.
 */
import { threeFigures } from "./numbers.js";
import { motionRoutine } from "./motion.js";

/** Standard gravity, mm/s². */
export const G_MM_S2 = 9806.65;
/** Drop: the fall, the impact slowed down, and the rest, as Preview plays a drop's motion. */
export const DROP = motionRoutine({ kind: "fall", label: "Drop" });

/** How long it waits at its height before it falls, s. */
export const HOLD_S = 0.5;
/** A fall shorter than this (a small height) is slowed to last this long, so it is seen, s. */
export const MIN_FALL_S = 0.4;
/** About how long the impact lasts on screen, slowed down by a round factor, s. */
export const IMPACT_S = 2.5;
/** How long it rests at the end, s. */
export const REST_S = 1;

const vector = (raw) => (Array.isArray(raw) && raw.length === 3 && raw.every(Number.isFinite) ? raw.map(Number) : null);
const positive = (raw) => (Number.isFinite(raw) && raw > 0 ? Number(raw) : null);

/**
 * The fall the file says (`analysis.fall`): `heightMm`, its `direction` (unit, the part's own axes), the impact
 * `speedMs` (m/s), the `floor` (`point` in mm, `normal` facing up), `impactMs` (how long the impact lasts: an estimate's
 * stop, an impact's contact), and an impact's `endMs` (its run), `reboundMs` (m/s away from the floor at its end) and
 * `centres` (its centre of mass's travel at each frame, mm). null for a file that carries none.
 */
export function readFall(raw) {
  if (!raw || typeof raw !== "object") return null;
  const heightMm = positive(raw.height_mm);
  const direction = vector(raw.direction);
  const point = vector(raw.floor?.point_mm);
  if (heightMm === null || !direction || !point) return null;
  const centres = Array.isArray(raw.centre_mm) ? raw.centre_mm.map(vector) : null;
  return {
    heightMm, direction, speedMs: positive(raw.speed_m_s) ?? Math.sqrt(2 * G_MM_S2 * heightMm) / 1000,
    floor: { point, normal: vector(raw.floor?.normal) || direction.map((c) => 0 - c) },
    impactMs: positive(raw.impact_ms), endMs: positive(raw.end_ms),
    reboundMs: Number.isFinite(raw.rebound_m_s) ? Number(raw.rebound_m_s) : 0,
    centres: centres && centres.every(Boolean) ? centres : null,
  };
}

/** A slow-motion factor as a round number: 1, 2 or 5 times a power of ten, the nearest; 1 for none. */
export function roundFactor(factor) {
  if (!(factor > 1)) return 1;
  const power = 10 ** Math.floor(Math.log10(factor));
  const best = [1, 2, 5, 10].reduce((a, b) => (Math.abs(Math.log(factor / (b * power))) < Math.abs(Math.log(factor / (a * power))) ? b : a));
  return best * power;
}

/** How long it takes to fall `heightMm` from rest, s. */
export const fallSeconds = (heightMm) => Math.sqrt((2 * heightMm) / G_MM_S2);

/**
 * The routine's timeline for `fall` whose impact phase spans `impactS` real seconds (an impact's run, an estimate's
 * stop): its `phases` in order, each `{ name, start, end }` on screen and `real` (its real seconds), `realStart` (real
 * seconds since it was let go) and `slow` (screen time over real time), and its whole `duration` on screen.
 * The phases: `hold` (at its height), `fall`, `impact`, `hop` (an impact that rebounds) and `rest`.
 */
export function fallTimeline(fall, { impactS } = {}) {
  const fallReal = fallSeconds(fall.heightMm);
  const fallSlow = Math.max(1, MIN_FALL_S / fallReal);
  const impactReal = impactS > 0 ? impactS : (fall.impactMs || 1) / 1000;
  const impactSlow = roundFactor(IMPACT_S / impactReal);
  const rebound = Math.max(0, fall.reboundMs || 0) * 1000;
  const hopReal = (2 * rebound) / G_MM_S2;
  const steps = [
    ["hold", HOLD_S, 0, 1], ["fall", fallReal * fallSlow, fallReal, fallSlow],
    ["impact", impactReal * impactSlow, impactReal, impactSlow],
    ...(hopReal > 0 ? [["hop", hopReal * fallSlow, hopReal, fallSlow]] : []),
    ["rest", REST_S, 0, 1],
  ];
  const phases = [];
  let start = 0;
  let realStart = 0;
  for (const [name, shown, real, slow] of steps) {
    phases.push(Object.freeze({ name, start, end: start + shown, real, realStart, slow }));
    start += shown;
    realStart += real;
  }
  return Object.freeze({ phases: Object.freeze(phases), duration: start, fallReal, fallSlow, impactSlow, rebound });
}

/**
 * Where the routine is at `elapsed` seconds on screen: its `phase`, the `share` of the way through it, the `lift` (how
 * far above the floor it is drawn, mm), `realS` (real seconds since it was let go), `sinceContactS` (real seconds since
 * it met the floor, during and after the impact) and `slow` (the phase's slow motion).
 */
export function fallAt(timeline, fall, elapsed) {
  const t = Math.min(Math.max(Number(elapsed) || 0, 0), timeline.duration);
  const phase = timeline.phases.find((entry) => t < entry.end) || timeline.phases[timeline.phases.length - 1];
  const span = phase.end - phase.start;
  const share = span > 0 ? Math.min(Math.max((t - phase.start) / span, 0), 1) : 1;
  const real = share * phase.real;
  let lift = 0;
  if (phase.name === "hold") lift = fall.heightMm;
  else if (phase.name === "fall") lift = Math.max(0, fall.heightMm - 0.5 * G_MM_S2 * real * real);
  else if (phase.name === "hop") lift = Math.max(0, timeline.rebound * real - 0.5 * G_MM_S2 * real * real);
  const contact = timeline.phases.find((entry) => entry.name === "impact").realStart;
  const realS = phase.realStart + real;
  return { phase: phase.name, share, lift, realS, sinceContactS: phase.name === "hold" || phase.name === "fall" ? null : realS - contact, slow: phase.slow };
}

/** A short time as the readout says it: "0 s", "39.6 µs", "1.2 ms", "0.452 s". */
export function shortTime(seconds) {
  const s = Number(seconds) || 0;
  if (s <= 0) return "0 s";
  if (s < 1e-3) return `${threeFigures(s * 1e6)} µs`;
  if (s < 1) return `${threeFigures(s * 1e3)} ms`;
  return `${threeFigures(s)} s`;
}

/** A slow-motion factor in words: "slowed 2,000×", "slowed 1.3×"; "" at real time. */
export function slowWords(slow) {
  if (!(slow > 1.05)) return "";
  const figure = slow >= 10 ? Math.round(slow).toLocaleString("en-US") : String(Number(slow.toFixed(1)));
  return `slowed ${figure}×`;
}

/** A height in words: "1 m", "1.2 m", "500 mm". */
export const heightWords = (heightMm) => (heightMm >= 1000 ? `${threeFigures(heightMm / 1000)} m` : `${threeFigures(heightMm)} mm`);

/**
 * The readout under the routine, in plain words: the real time (`time`: "1 m above the floor" before it falls,
 * "0.32 s" falling (and hopping), "0.21 ms after landing" in the impact, "At rest" at the end) and the slow motion while it is
 * slowed (`scale`: "slowed 2,000×").
 */
export function fallWords(state, fall) {
  const time = state.phase === "rest" ? "At rest"
    : state.phase === "impact" ? `${shortTime(state.sinceContactS)} after landing`
      : state.phase === "hold" ? `${heightWords(fall.heightMm)} above the floor`
        : `${state.realS.toFixed(2)} s`;
  return { time, scale: slowWords(state.slow) };
}

/** How long the impact phase lasts in real time, s: an impact's run (its last frame), else the stop the file says. */
export function impactSpan(result) {
  const frames = result?.series?.frames || [];
  const last = frames.length ? Number(frames[frames.length - 1].value) : 0;
  if (last > 0) return last;
  const ms = result?.analysis?.fall?.impactMs;
  return ms > 0 ? ms / 1000 : 0;
}

/** The two frames of a time series either side of `seconds` and how far between: `{ from, to, weight }`, by their times. */
export function framesAtTime(series, seconds) {
  const frames = series?.frames || [];
  if (frames.length < 2) return { from: 0, to: 0, weight: 0 };
  const t = Number(seconds) || 0;
  if (t <= frames[0].value) return { from: 0, to: 1, weight: 0 };
  for (let index = 0; index < frames.length - 1; index += 1) {
    const a = frames[index].value;
    const b = frames[index + 1].value;
    if (t < b) return { from: index, to: index + 1, weight: b > a ? (t - a) / (b - a) : 0 };
  }
  return { from: frames.length - 2, to: frames.length - 1, weight: 1 };
}

/** The Time row's words at a moment: the real time from when it lands ("−0.45 s", "−0.12 s", "0 s", "0.21 ms"), "At rest" at the end. */
export function landingTime(state, plan) {
  if (state.phase === "rest") return "At rest";
  if (state.phase === "hold" || state.phase === "fall") {
    const left = plan.fallReal - state.realS;
    return left > 5e-4 ? `\u2212${left.toFixed(2)} s` : "0 s";
  }
  return shortTime(state.sinceContactS);
}

/**
 * A drop's motion (`motion.js`), or null for a file with no fall: its timeline (`plan`), the fall, opening on the
 * peak (an impact's peak frame, an estimate's stop at its middle), its states Released, Contact, Peak and At rest,
 * the scrubber stopping at each solved frame, and the impact alone played with reduced motion.
 */
export function fallMotion(result) {
  const fall = result?.analysis?.fall;
  if (!fall) return null;
  const plan = fallTimeline(fall, { impactS: impactSpan(result) });
  const impact = plan.phases.find((phase) => phase.name === "impact");
  const frames = result.series?.frames || [];
  const atReal = (seconds) => impact.start + Math.min(Math.max(Number(seconds) || 0, 0), impact.real) * impact.slow;
  const peakAt = frames.length ? atReal(frames[result.series.default]?.value) : (impact.start + impact.end) / 2;
  return {
    kind: "fall", label: "Drop", plan, fall, duration: plan.duration, defaultAt: peakAt,
    states: [{ id: "released", label: "Released", at: 0 }, { id: "contact", label: "Contact", at: impact.start },
      { id: "peak", label: "Peak", at: peakAt }, { id: "rest", label: "At rest", at: plan.duration }],
    moments: frames.map((frame) => atReal(frame.value)),
    timeAt: (at) => landingTime(fallAt(plan, fall, at), plan),
    hint: "Time from when it lands: before it, falling",
    play: { from: impact.start, to: impact.end },
    atFrame: (index) => (frames[index] ? atReal(frames[index].value) : null),
  };
}
