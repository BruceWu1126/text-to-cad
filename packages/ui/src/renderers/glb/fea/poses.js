/**
 * Poses: the motions of results whose part stays where it is held while what it shows changes over time, on the
 * contract of `./motion.js` (a row in What you see that scrubs it, its named states in the Preset row, and Preview's
 * playbar playing the same timeline). Each motion here is `kind: "pose"`, and adds `row` (the row's word: "Time",
 * "Cycle", "Phase", "Load", "Heat") and `poseAt(at, { field })`: what the model shows at a moment `at` of the
 * timeline, which GlbRenderer draws:
 *
 * - `vector`: `[[attribute, weight], ...]`, the displacement drawn (each a vector attribute, in the file's units);
 *   with `raise: { peakMm }` it is drawn large enough to be seen (its largest motion `peakMm`), and the readout says
 *   by how much; else at the Deformation shown;
 * - `valueScale`: the colours' values times this (a load ramped or cycled), for a field that follows it;
 * - `values`: the colours' own values at that moment (a field turning through its AC phase, a temperature ramped
 *   from ambient), a Float32Array per vertex, with `stamp` naming them;
 * - `frame`, `blend`: the frame of a series shown, and how far toward the next (`{ to, weight }`);
 * - `words`: the readout while it plays, `{ time, scale }`.
 *
 * Every motion is drawn from what cadgen solved and wrote (`extras.analysis.motion`, cadgen's `fea/motion.py`):
 * a shake's modal histories, a load cycle's ratio and rate, an AC field's real and imaginary parts, a bolt's load
 * steps; where it ramps a linear result (a bolt tightening, heat coming on) it says "ramped". Its real time is in the
 * row's value field and the readout, and how much it is slowed. Pure: no three.js, no React.
 */
import { DISPLACEMENT } from "./series.js";
import { roundFactor, shortTime, slowWords } from "./fall.js";
import { threeFigures } from "./numbers.js";
import { motionRoutine } from "./motion.js";

/** What cadgen wrote for the routine (`extras.analysis.motion`), or null. */
export function motionData(result) {
  const data = result?.mesh?.userData?.analysis?.motion;
  return data && typeof data === "object" && !Array.isArray(data) ? data : null;
}

const finite = (value) => Number.isFinite(value);
const attributeOf = (result, name, size = 3) => {
  const attribute = typeof name === "string" ? result?.mesh?.geometry?.getAttribute(name.toLowerCase()) : null;
  return attribute?.itemSize === size ? attribute : null;
};
const lerp = (a, b, w) => a + (b - a) * w;
const clampTo = (value, low, high) => Math.min(Math.max(Number(value) || 0, low), high);
/** A slowed motion's mode on screen swings about this often, Hz; and no motion lasts longer than MAX_S on screen. */
const SWING_HZ = 1.25;
const MAX_S = 10;

/** A count in words: "1,000", "1.2 million", "45 billion". */
export function countWords(value) {
  const v = Number(value) || 0;
  if (v >= 1e9) return `${threeFigures(v / 1e9)} billion`;
  if (v >= 1e6) return `${threeFigures(v / 1e6)} million`;
  return Math.round(v).toLocaleString("en-US");
}

/** A frequency in words: "611 Hz", "1.2 kHz". */
export const hertzWords = (hz) => (hz >= 1000 ? `${threeFigures(hz / 1000)} kHz` : `${threeFigures(hz)} Hz`);

const pose = (spec) => Object.freeze({ kind: "pose", play: null, moments: [], ...spec, states: Object.freeze(spec.states.filter(Boolean)) });

// -- a part shaken at its base: random vibration, shock ---------------------------------------------------------------

/**
 * Random vibration's and shock's motion: the part's motion relative to its shaker, a few mode shapes (hidden
 * attributes, each moving its largest point 1 mm) times their histories (mm) over the window cadgen sampled,
 * slowed so the slowest mode swings about once a second (and the whole lasts at most ten seconds). Its row is Time,
 * its states the start, a shock's pulse ending and the largest swing (where it opens); the readout says the base's
 * acceleration then. null for a file with none.
 */
function shakeMotionOf(result) {
  const data = motionData(result);
  if (data?.kind !== "base_shake") return null;
  const count = Number(data.count);
  const step = Number(data.step_s);
  const base = Array.isArray(data.base_g) ? data.base_g.map(Number) : [];
  const modes = (Array.isArray(data.modes) ? data.modes : [])
    .filter((mode) => attributeOf(result, mode?.attribute) && Array.isArray(mode.q_mm) && mode.q_mm.length === count && finite(mode.frequency_Hz))
    .map((mode) => ({ attribute: mode.attribute.toLowerCase(), hz: Number(mode.frequency_Hz), q: mode.q_mm.map(Number) }));
  if (!(count >= 2) || !(step > 0) || base.length !== count || !modes.length) return null;
  const window = step * (count - 1);
  const slowest = Math.min(...modes.map((mode) => mode.hz));
  const slow = roundFactor(Math.min(slowest / SWING_HZ, MAX_S / window));
  const duration = window * slow;
  // How far the shapes move together at each sample (their sum bounds it), and where the most.
  const reach = Array.from({ length: count }, (_, i) => modes.reduce((sum, mode) => sum + Math.abs(mode.q[i]), 0));
  const peakIndex = reach.reduce((best, value, i) => (value > reach[best] ? i : best), 0);
  const peakMm = reach[peakIndex] || 0;
  const sample = (at) => {
    const x = clampTo(at / slow / step, 0, count - 1);
    const i = Math.min(Math.floor(x), count - 2);
    return { i, w: x - i };
  };
  const shock = data.label === "Shock";
  const pulse = Number(data.pulse_s);
  const timeAt = (at) => shortTime(clampTo(at, 0, duration) / slow);
  const hint = shock
    ? "A base pulse synthesised to match the shock's spectrum, and the modes that move the part most answering it, relative to the shaker"
    : `A ${threeFigures(Number(data.rms_g) || 0)} g rms shake synthesised from the PSD, and the modes that move the part most answering it, relative to the shaker`;
  return pose({
    label: shock ? "Shock" : "Shake", row: "Time", duration, defaultAt: peakIndex * step * slow, hint, timeAt,
    states: [
      { id: "start", label: shock ? "Pulse starts" : "Start", at: 0 },
      shock && pulse > 0 && pulse < window ? { id: "pulse_end", label: "Pulse ends", at: pulse * slow } : null,
      { id: "largest", label: "Largest swing", at: peakIndex * step * slow },
    ],
    slow,
    poseAt(at) {
      const { i, w } = sample(at);
      const g = lerp(base[i], base[i + 1], w);
      return {
        vector: modes.map((mode) => [mode.attribute, lerp(mode.q[i], mode.q[i + 1], w)]), raise: { peakMm },
        words: { time: `${timeAt(at)} · base ${threeFigures(Math.abs(g)) === "0" ? "0" : threeFigures(g)} g`, scale: slowWords(slow) },
      };
    },
  });
}

// -- fatigue's load cycle -----------------------------------------------------------------------------------------

/** The cycles a random shake's fatigue plays: Steinberg's shares, most at 1σ, some at 2σ, one at 3σ, in this order. */
export const STEINBERG_CYCLES = Object.freeze([1, 1, 2, 1, 1, 1, 2, 1, 1, 3, 1, 1, 2, 1, 1, 1, 2, 1, 1, 2, 1, 1, 2]);
const CYCLE_S = 1;
const FIXED_CYCLES = 4;
// The fields the load cycle scales: the stress and the displacement; a life and a margin are of the whole cycle.
const FOLLOWS_LOAD = new Set(["_cycle_stress", DISPLACEMENT]);

/**
 * Fatigue's motion: its load cycle, the load swinging between R times its peak and its peak (a static load four
 * cycles, one a second on screen; a sweep's dwell at its frequency, slowed; a random shake's cycles at 1σ, 2σ and 3σ
 * in Steinberg's shares), the stress (`_cycle_stress`) and the deformation following it while the life and the
 * margin, which are of the whole cycle, stand. Its row is Cycle; its states the peak (where it opens) and the trough.
 */
function cycleMotionOf(result) {
  const data = motionData(result);
  if (data?.kind !== "cycle") return null;
  const ratio = finite(data.ratio) ? Number(data.ratio) : -1;
  const random = Array.isArray(data.levels) && data.levels.length > 0;
  const hz = Number(data.frequency_Hz) > 0 ? Number(data.frequency_Hz) : null;
  const slow = hz ? roundFactor(hz * CYCLE_S) : 1;
  const levels = random ? STEINBERG_CYCLES : Array.from({ length: FIXED_CYCLES }, () => 3);
  const cycleS = hz ? slow / hz : CYCLE_S;
  const duration = levels.length * cycleS;
  // The load's share of its peak: mean + amplitude · sin, from R to 1 (a random cycle's peak its level over 3σ).
  const loadAt = (at) => {
    const t = clampTo(at, 0, duration) / cycleS;
    const n = Math.min(Math.floor(t), levels.length - 1);
    const phase = t - n;
    const level = levels[n] / 3;
    const swing = random ? Math.sin(2 * Math.PI * phase) : (1 + ratio) / 2 + ((1 - ratio) / 2) * Math.sin(2 * Math.PI * phase);
    return { n, load: level * swing, level: levels[n] };
  };
  const timeAt = (at) => {
    const { n, load, level } = loadAt(at);
    return random ? `Cycle ${n + 1} · ${level}σ · ${Math.round((load * 3 / level) * 100)} %` : `Cycle ${n + 1} · ${Math.round(load * 100)} %`;
  };
  const lasts = Number(data.life_cycles) > 0 ? `lasts ${countWords(data.life_cycles)} cycles` : "";
  const rate = hz ? `${hertzWords(hz)}, ${slowWords(slow) || "real time"}` : "one cycle a second: the load gives no rate";
  const third = random ? STEINBERG_CYCLES.indexOf(3) : 0;
  return pose({
    label: "Load cycle", row: "Cycle", duration, defaultAt: (third + 0.25) * cycleS, timeAt,
    hint: random ? "The stress swinging fully reversed, its cycles at 1σ, 2σ and 3σ in Steinberg's shares (68 %, 27 %, 4 %)"
      : `The load swinging between ${Math.round(ratio * 100)} % and 100 % of its peak, the stress and the deformation with it`,
    states: [
      { id: "max", label: random ? "3σ peak" : "Max", at: (third + 0.25) * cycleS },
      { id: "min", label: random ? "3σ trough" : "Min", at: (third + 0.75) * cycleS },
    ],
    poseAt(at, { field } = {}) {
      const { n, load } = loadAt(at);
      const follows = FOLLOWS_LOAD.has(String(field?.attribute || ""));
      return {
        vector: attributeOf(result, DISPLACEMENT) ? [[DISPLACEMENT, load]] : null,
        valueScale: follows ? Math.abs(load) : 1,
        words: { time: `Cycle ${n + 1}${lasts ? ` · the weakest point ${lasts}` : ""}`, scale: rate },
      };
    },
  });
}

// -- an AC field turning through its cycle ------------------------------------------------------------------------

const PHASE_S = 2;

/**
 * An AC magnetic result's motion: the field turning through one cycle at its real frequency, slowed to two seconds:
 * at a phase θ each vertex shows the strength re·cos θ − im·sin θ of the field shown (the eddy current or the flux
 * density, from their real and imaginary parts), on the amplitude's colour range. Its row is Phase; its states 0°,
 * 90° and the moment the strongest point peaks (where it opens). A field with no phase (a temperature) stands.
 */
function acMotionOf(result) {
  const data = motionData(result);
  if (data?.kind !== "ac" || !(Number(data.frequency_Hz) > 0)) return null;
  const hz = Number(data.frequency_Hz);
  const pairs = Object.fromEntries(Object.entries(data.fields || {}).map(([name, pair]) => {
    const re = attributeOf(result, pair?.re);
    const im = attributeOf(result, pair?.im);
    return re && im && re.count === im.count ? [name, { re: re.array, im: im.array, scale: Number(pair.attribute_scale) || 1, name }] : null;
  }).filter(Boolean));
  const names = Object.keys(pairs);
  if (!names.length) return null;
  const slow = roundFactor(hz * PHASE_S);
  const duration = slow / hz;
  const angle = (at) => (2 * Math.PI * clampTo(at, 0, duration)) / duration;
  const kept = {};
  const strength = (pair, theta, out) => {
    const c = Math.cos(theta);
    const s = Math.sin(theta);
    for (let i = 0; i < out.length; i += 1) {
      const x = pair.re[3 * i] * c - pair.im[3 * i] * s;
      const y = pair.re[3 * i + 1] * c - pair.im[3 * i + 1] * s;
      const z = pair.re[3 * i + 2] * c - pair.im[3 * i + 2] * s;
      out[i] = Math.hypot(x, y, z) * pair.scale;
    }
    return out;
  };
  // The phase at which the strongest point of the first field peaks.
  const first = pairs[names[0]];
  let strongest = 0;
  let most = -1;
  for (let i = 0; i < first.re.length / 3; i += 1) {
    const size = Math.hypot(first.re[3 * i], first.re[3 * i + 1], first.re[3 * i + 2], first.im[3 * i], first.im[3 * i + 1], first.im[3 * i + 2]);
    if (size > most) { most = size; strongest = i; }
  }
  let peakAt = 0;
  let peak = -1;
  for (let k = 0; k < 360; k += 1) {
    const theta = (2 * Math.PI * k) / 360;
    const one = strength({ ...first, re: first.re.subarray(3 * strongest, 3 * strongest + 3), im: first.im.subarray(3 * strongest, 3 * strongest + 3) }, theta, new Float32Array(1))[0];
    if (one > peak + 1e-12) { peak = one; peakAt = (k / 360) * duration; }
  }
  const timeAt = (at) => `${Math.round((angle(at) * 180) / Math.PI) % 360}° · ${shortTime(clampTo(at, 0, duration) / slow)}`;
  return pose({
    label: "AC cycle", row: "Phase", duration, defaultAt: peakAt, timeAt,
    hint: `The field turning through one ${hertzWords(hz)} cycle: its strength at each moment, on its amplitude's colours`,
    states: [{ id: "peak", label: "Peak", at: peakAt }, { id: "0", label: "0°", at: 0 }, { id: "90", label: "90°", at: duration / 4 }],
    poseAt(at, { field } = {}) {
      const pair = pairs[field?.view] || null;
      const words = { time: `${Math.round((angle(at) * 180) / Math.PI) % 360}° of the cycle`, scale: `${hertzWords(hz)}, ${slowWords(slow)}` };
      if (!pair) return { words };
      const out = kept[pair.name] || (kept[pair.name] = new Float32Array(pair.re.length / 3));
      const theta = angle(at);
      return { values: strength(pair, theta, out), stamp: `ac:${pair.name}:${theta.toFixed(6)}`, words };
    },
  });
}

// -- a bolt tightened, then loaded ----------------------------------------------------------------------------

const TIGHTEN_S = 2;
const STEP_S = 1;

/**
 * A bolted joint's motion: the bolt tightened to its preload (the preload's solve ramped from none: "ramped", for a
 * joint that settles into contact as it closes is near linear in it), then the service load put on in its solved
 * steps, each blended into the next. Its row is Load; its states Snug, Preloaded and the full service load; a
 * check's frame is a moment on it. null for a file with no load steps.
 */
function boltMotionOf(result) {
  const frames = result?.series?.frames || [];
  if (!frames.length) return null;
  const steps = frames.length - 1;
  const duration = TIGHTEN_S + steps * STEP_S;
  const atFrame = (index) => (Number.isInteger(index) && index >= 0 && index < frames.length ? TIGHTEN_S + index * STEP_S : null);
  const where = (at) => {
    const t = clampTo(at, 0, duration);
    if (t < TIGHTEN_S) return { tightening: t / TIGHTEN_S };
    const x = steps ? (t - TIGHTEN_S) / STEP_S : 0;
    const from = Math.min(Math.floor(x), Math.max(steps - 1, 0));
    return { from, to: Math.min(from + 1, steps), weight: steps ? x - from : 0 };
  };
  const timeAt = (at) => {
    const state = where(at);
    if (state.tightening !== undefined) return `Tightening ${Math.round(state.tightening * 100)} %`;
    return frames[state.weight >= 0.5 ? state.to : state.from]?.label || "";
  };
  return pose({
    label: "Tighten and load", row: "Load", duration, defaultAt: atFrame(result.series.default) ?? TIGHTEN_S, timeAt, atFrame,
    moments: frames.map((_, index) => atFrame(index)),
    hint: "The bolt tightened to its preload (ramped), then the service load put on in its solved steps",
    states: [
      { id: "snug", label: "Snug", at: 0 },
      { id: "preloaded", label: "Preloaded", at: TIGHTEN_S },
      steps ? { id: "service", label: "Service load", at: duration } : null,
    ],
    poseAt(at) {
      const state = where(at);
      if (state.tightening !== undefined) {
        return { frame: 0, valueScale: state.tightening, vector: [[DISPLACEMENT, state.tightening]],
          words: { time: timeAt(at), scale: "ramped" } };
      }
      return { frame: state.from, blend: state.weight > 0 ? { to: state.to, weight: state.weight } : null, words: { time: timeAt(at), scale: "" } };
    },
  });
}

// -- heat coming on: heat stress ----------------------------------------------------------------------------------

const HEAT_S = 2;

/**
 * Heat stress's motion: the part heating from the reference temperature to the solved one, every temperature the
 * same share of the way, the stress and the deformation growing with it: a ramp of a linear result, so every moment
 * is exact for that share of the heat, and it says "ramped". Its row is Heat; its states Ambient and Heated (where
 * it opens: the result as solved).
 */
function heatMotionOf(result) {
  const temperature = result?.fields?.find((entry) => entry.attribute === "_temperature");
  if (!temperature) return null;
  const reference = finite(result.analysis?.referenceC) ? result.analysis.referenceC : 20;
  let own = null;
  let out = null;
  const timeAt = (at) => `${Math.round((clampTo(at, 0, HEAT_S) / HEAT_S) * 100)} % of the heat`;
  return pose({
    label: "Heat up", row: "Heat", duration: HEAT_S, defaultAt: HEAT_S, timeAt,
    hint: `The part heating from ${threeFigures(reference)} °C to its solved temperatures, the stress and the deformation growing with it (ramped: the result is linear in the heat)`,
    states: [{ id: "ambient", label: "Ambient", at: 0 }, { id: "heated", label: "Heated", at: HEAT_S }],
    poseAt(at, { field } = {}) {
      const k = clampTo(at, 0, HEAT_S) / HEAT_S;
      const words = { time: timeAt(at), scale: "ramped" };
      const vector = attributeOf(result, DISPLACEMENT) ? [[DISPLACEMENT, k]] : null;
      if (field?.attribute !== "_temperature") return { vector, valueScale: k, words };
      const attribute = attributeOf(result, "_temperature", 1);
      if (!attribute) return { vector, words };
      own ||= attribute.array;
      out ||= new Float32Array(own.length);
      const scale = temperature.attributeScale || 1;
      for (let i = 0; i < own.length; i += 1) out[i] = reference + k * (own[i] * scale - reference);
      return { vector, values: out, stamp: `heat:${k}`, words };
    },
  });
}

// -- a flow's tracers ---------------------------------------------------------------------------------------------

/** A flow's tracers' trips take about this long on screen, s. */
export const TRIP_S = 4;

/**
 * A flow's tracers (cadgen's `extras.analysis.motion`, kind `tracers`): each path's points (mm, the part's own axes),
 * `step_s` of real time apart; `slow`, the trip of the median path over TRIP_S on screen as a round factor (below 1
 * a fast flow is slowed, above, a creeping one sped up); `duration`, the longest path's time on screen. null for none.
 */
function readTracersOf(result) {
  const data = motionData(result);
  if (data?.kind !== "tracers" || !(Number(data.step_s) > 0) || !Array.isArray(data.paths)) return null;
  const paths = data.paths.filter((path) => Array.isArray(path) && path.length >= 6 && path.length % 3 === 0 && path.every(finite))
    .map((path) => Float32Array.from(path));
  if (!paths.length) return null;
  const step = Number(data.step_s);
  const trips = paths.map((path) => (path.length / 3 - 1) * step).sort((a, b) => a - b);
  const median = trips[Math.floor(trips.length / 2)];
  const wanted = TRIP_S / median;
  const slow = wanted >= 1 ? roundFactor(wanted) : 1 / roundFactor(1 / wanted);
  return Object.freeze({ paths, step, slow, duration: trips[trips.length - 1] * slow, label: String(data.label || "Flow") });
}

/** A flow's routine: its tracers moving through it, for as long as the longest takes on screen. */
export function tracersRoutine(result) {
  const tracers = readTracers(result);
  return tracers ? Object.freeze({ id: "fea:tracers", label: tracers.label, kind: "tracers", duration: tracers.duration }) : null;
}

/** How the readout says a tracer's pace: "slowed 50×", "sped up 4×", or "real time". */
export function paceWords(slow) {
  if (slow > 1.05) return slowWords(slow);
  if (slow < 0.95) return `sped up ${threeFigures(1 / slow)}×`;
  return "real time";
}

/** One motion per result read: a builder is called by the controls, the presets and the routine alike. */
function once(build) {
  const kept = new WeakMap();
  return (result) => {
    if (!result || typeof result !== "object") return null;
    if (!kept.has(result)) kept.set(result, build(result));
    return kept.get(result);
  };
}

export const shakeMotion = once(shakeMotionOf);
export const cycleMotion = once(cycleMotionOf);
export const acMotion = once(acMotionOf);
export const boltMotion = once(boltMotionOf);
export const heatMotion = once(heatMotionOf);
export const readTracers = once(readTracersOf);

/** The routine a motion here plays (`./motion.js`'s), for a registry entry's `routine`. */
export const poseRoutine = (motion) => (motion ? motionRoutine(motion) : null);
