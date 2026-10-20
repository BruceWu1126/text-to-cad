// A cadgen engine document's mechanism as a page poses it: the structure's `kinematics` block
// (`cadgen/engine/mechanism.py`, `block`), which the engine harvests from the model's build123d
// joints. Its links are the instances that move as one, its pairs join two links with a frame on
// each (4x4, column-major, in its link's frame, coinciding at value 0), and link 2 stands at
// `link_1 · item_1 · M(v) · item_2⁻¹`: M a turn about Z (revolute), a slide along Z (prismatic),
// both (cylindrical: angle, then distance) or an intrinsic X-Y-Z rotation (spherical). A gear pair
// drives one pair from another. Degrees and millimetres.
//
// Posing is exact forward kinematics on the tree, from the ground down, the same fold the engine
// makes for a posed snapshot or export: each link's placement at the values over its placement at
// the values its instances stand at now, applied to them. No time: a pose is a configuration.

const DEG = Math.PI / 180;
const IDENTITY = Object.freeze([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);

/** The number of values a pair of `type` has. */
export const XBF_PAIR_VALUES = Object.freeze({ revolute: 1, prismatic: 1, cylindrical: 2, spherical: 3, gear: 0 });
const VALUE_NAMES = Object.freeze({ cylindrical: ["angle", "position"], spherical: ["x", "y", "z"] });

/** A document's mechanism, or null where it has none. */
export function xbfKinematics(structure) {
  const found = structure?.kinematics;
  return found && Array.isArray(found.pairs) && Array.isArray(found.links) ? found : null;
}

function multiply(a, b) {
  const out = new Array(16).fill(0);
  for (let column = 0; column < 4; column += 1) {
    for (let row = 0; row < 4; row += 1) {
      let sum = 0;
      for (let k = 0; k < 4; k += 1) sum += a[k * 4 + row] * b[column * 4 + k];
      out[column * 4 + row] = sum;
    }
  }
  return out;
}

/** The inverse of a rigid placement: its rotation transposed, its translation turned back. */
function invert(m) {
  const out = [m[0], m[4], m[8], 0, m[1], m[5], m[9], 0, m[2], m[6], m[10], 0, 0, 0, 0, 1];
  for (let row = 0; row < 3; row += 1) out[12 + row] = -(out[row] * m[12] + out[4 + row] * m[13] + out[8 + row] * m[14]);
  return out;
}

function rotation(axis, degrees) {
  const c = Math.cos(degrees * DEG), s = Math.sin(degrees * DEG);
  const out = [...IDENTITY];
  const [i, j] = [[1, 2], [2, 0], [0, 1]][axis];
  out[i * 4 + i] = c; out[j * 4 + i] = -s; out[i * 4 + j] = s; out[j * 4 + j] = c;
  return out;
}

/** `M(v)`: what a pair's values do between its two frames (4x4, column-major). */
export function xbfPairMotion(type, values) {
  if (type === "revolute") return rotation(2, values[0]);
  if (type === "prismatic") { const out = [...IDENTITY]; out[14] = values[0]; return out; }
  if (type === "cylindrical") { const out = rotation(2, values[0]); out[14] = values[1]; return out; }
  if (type === "spherical") return multiply(multiply(rotation(0, values[0]), rotation(1, values[1])), rotation(2, values[2]));
  throw new Error(`a ${type} pair has no value`);
}

const asValues = value => (Array.isArray(value) ? value.map(Number) : [Number(value)]);

/**
 * Each pair that cannot be posed, and why: one the engine kept with no value because the built
 * tree contradicts it (`inconsistent`), and each pair of a loop.
 * @returns {Map<string, string>}
 */
export function xbfUnposable(kinematics) {
  const found = new Map();
  for (const pair of kinematics?.pairs || []) if (pair.inconsistent) found.set(pair.label, pair.inconsistent);
  for (const label of kinematics?.loop || []) {
    if (!found.has(label)) found.set(label, `pairs ${kinematics.loop.join(", ")} close a loop, which has no pose without a solver`);
  }
  return found;
}

/**
 * What the engine found inconsistent and no pair's row says: the warnings of the block, but those
 * of a pair kept with no value (its row says it).
 */
export function xbfOtherWarnings(kinematics) {
  const kept = (kinematics?.pairs || []).filter(pair => pair.inconsistent).map(pair => `joint '${pair.label}' is kept`);
  return (kinematics?.warnings || []).filter(text => !kept.some(prefix => text.startsWith(prefix)) && !text.startsWith("pairs "));
}

/** Each pair a gear drives: its driver, the ratio and the gear. */
export function xbfDrivenPairs(kinematics) {
  const found = new Map();
  for (const pair of kinematics?.pairs || []) {
    if (pair.type === "gear" && Array.isArray(pair.drives)) found.set(pair.drives[1], { driver: pair.drives[0], ratio: Number(pair.ratio) || 0, gear: pair.label });
  }
  return found;
}

function checked(pair, values, where) {
  const count = XBF_PAIR_VALUES[pair.type];
  if (values.length !== count || !values.every(Number.isFinite)) {
    throw new Error(`${where}: joint '${pair.label}' (${pair.type}) takes ${count === 1 ? "a number" : `${count} numbers`}`);
  }
  values.forEach((value, at) => {
    const [lower, upper] = pair.limits?.[at] || [null, null];
    if ((lower != null && value < lower - 1e-9) || (upper != null && value > upper + 1e-9)) {
      const unit = pair.units?.[at] || "";
      throw new Error(`${where}: joint '${pair.label}' at ${value} ${unit} is outside its range [${lower ?? "-inf"}, ${upper ?? "inf"}] ${unit}`);
    }
  });
  return values;
}

/**
 * The values a pose sets every pair to: `pose` is null (as built), a configuration's name, or
 * `{ label: value }` (the rest as built). What it names wrong is refused by name, as the engine
 * refuses it.
 * @returns {Record<string, number[]>}
 */
export function xbfPoseValues(kinematics, pose = null) {
  const values = Object.fromEntries(Object.entries(kinematics.asBuilt || {}).map(([label, value]) => [label, asValues(value)]));
  const pairs = new Map(kinematics.pairs.map(pair => [pair.label, pair]));
  const driven = xbfDrivenPairs(kinematics);
  let given = {}, where = "the pose";
  if (typeof pose === "string") {
    const configurations = kinematics.configurations || {};
    if (!(pose in configurations)) throw new Error(`the document has no configuration '${pose}'`);
    given = configurations[pose];
    where = `configuration '${pose}'`;
  } else if (pose) {
    given = pose;
  }
  for (const [label, value] of Object.entries(given)) {
    const pair = pairs.get(label);
    if (!pair || pair.type === "gear") throw new Error(`${where} names no joint '${label}'`);
    const unposable = xbfUnposable(kinematics).get(label);
    if (unposable) throw new Error(`${where}: joint '${label}' cannot be posed: ${unposable}`);
    if (driven.has(label) && typeof pose !== "string") {
      const { driver, gear } = driven.get(label);
      throw new Error(`${where}: joint '${label}' is driven by '${driver}' through gear '${gear}'`);
    }
    values[label] = checked(pair, asValues(value), where);
  }
  for (const [label, { driver, ratio, gear }] of driven) {
    if (!values[driver] || !values[label]) continue;
    values[label] = checked(pairs.get(label), [values[driver][0] * ratio, ...values[label].slice(1)], `gear '${gear}'`);
  }
  return values;
}

/**
 * Each link's placement at `values`: the ground where the document places it, every other link
 * by its pairs from the ground down. A mechanism whose pairs close a loop has no pose.
 * @returns {number[][]} 4x4, column-major, by link.
 */
export function xbfLinkPlacements(kinematics, values) {
  if (!kinematics.links.length) return [];
  const found = kinematics.links.map(() => null);
  found[kinematics.ground] = kinematics.links[kinematics.ground].matrix;
  let pending = kinematics.pairs.filter(pair => pair.type !== "gear" && pair.frames);
  for (let progress = true; pending.length && progress;) {
    progress = false;
    pending = pending.filter((pair) => {
      const [first, second] = pair.links;
      const value = values[pair.label] || (kinematics.asBuilt[pair.label] != null ? asValues(kinematics.asBuilt[pair.label]) : null);
      // A pair with no value (inconsistent) holds its two links as they stand as built.
      const [one, two] = pair.inconsistent || !value
        ? [invert(kinematics.links[first].matrix), invert(kinematics.links[second].matrix)] : pair.frames;
      const moved = pair.inconsistent || !value ? [...IDENTITY] : xbfPairMotion(pair.type, value);
      if (found[first] && !found[second]) found[second] = multiply(multiply(multiply(found[first], one), moved), invert(two));
      else if (found[second] && !found[first]) found[first] = multiply(multiply(multiply(found[second], two), invert(moved)), invert(one));
      else if (!found[first]) return true;
      progress = true;
      return false;
    });
  }
  return found.map((matrix, at) => matrix || kinematics.links[at].matrix);
}

/**
 * Every instance's world matrix (column-major) with the mechanism at `values`: each link's
 * placement at them over its placement at the values the instances stand at now (the structure's
 * `kinematics.pose`, where the engine served it posed, else as built), applied to its instances.
 */
export function xbfPosedMatrices(structure, values) {
  const kinematics = xbfKinematics(structure);
  const matrices = structure.instances.map(instance => instance.matrix);
  if (!kinematics) return matrices;
  const reference = Object.fromEntries(Object.entries({ ...kinematics.asBuilt, ...(kinematics.pose?.values || {}) })
    .map(([label, value]) => [label, asValues(value)]));
  const now = xbfLinkPlacements(kinematics, values);
  const then = xbfLinkPlacements(kinematics, reference);
  kinematics.links.forEach((link, at) => {
    const delta = multiply(now[at], invert(then[at]));
    if (delta.every((value, i) => Math.abs(value - IDENTITY[i]) < 1e-12)) return;
    for (const index of link.instances) matrices[index] = multiply(delta, matrices[index]);
  });
  return matrices;
}

/**
 * What a Position panel drives: one control per value of every posable pair a gear does not drive, within
 * its limits (an unbounded side spans one turn or 100 mm from the other).
 * @returns {{ id: string, pair: string, index: number, label: string, unit: string, min: number, max: number }[]}
 */
export function xbfPoseControls(kinematics) {
  const driven = xbfDrivenPairs(kinematics);
  const unposable = xbfUnposable(kinematics);
  const controls = [];
  for (const pair of kinematics?.pairs || []) {
    if (pair.type === "gear" || driven.has(pair.label) || unposable.has(pair.label)) continue;
    const names = VALUE_NAMES[pair.type];
    for (let index = 0; index < XBF_PAIR_VALUES[pair.type]; index += 1) {
      const unit = pair.units?.[index] || "deg";
      const span = unit === "deg" ? 360 : 100;
      let [min, max] = pair.limits?.[index] || [null, null];
      if (min == null && max == null) [min, max] = unit === "deg" ? [-180, 180] : [-50, 50];
      else if (min == null) min = max - span;
      else if (max == null) max = min + span;
      controls.push({ id: names ? `${pair.label}.${names[index]}` : pair.label, pair: pair.label, index,
        label: names ? `${pair.label} ${names[index]}` : pair.label, unit, min, max });
    }
  }
  return controls;
}

/** Whether two sets of pair values are the same pose. */
export function xbfSamePose(left, right) {
  const labels = new Set([...Object.keys(left || {}), ...Object.keys(right || {})]);
  for (const label of labels) {
    const a = left?.[label] || [], b = right?.[label] || [];
    if (a.length !== b.length || a.some((value, at) => Math.abs(value - b[at]) > 1e-6)) return false;
  }
  return true;
}
