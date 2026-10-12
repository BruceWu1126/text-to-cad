import { describe, expect, it } from 'vitest';
import { DOTS_PER_PATH, tracerPositions } from '../feaTracers.js';
import { motionControl } from './motion.js';
import { STEINBERG_CYCLES, acMotion, cycleMotion, heatMotion, readTracers, shakeMotion, tracersRoutine } from './poses.js';

const attribute = (values: number[], itemSize: number) => ({ array: Float32Array.from(values), itemSize, count: values.length / itemSize });

// A result as readFeaResult hands it on: what cadgen wrote for its routine, and the mesh's attributes by their lower-case names.
function stub(motion: unknown, attributes: Record<string, unknown> = {}, extra: Record<string, unknown> = {}) {
  return { mesh: { userData: { analysis: { motion } }, geometry: { getAttribute: (name: string) => attributes[name] ?? null } }, fields: [], ...extra };
}

const shake = (more: Record<string, unknown> = {}) => ({
  kind: 'base_shake', label: 'Random shake', step_s: 0.001, count: 5, direction: [0, 0, 1], base_g: [0, 1, 2, 1, 0], rms_g: 6.06,
  modes: [{ attribute: '_MOTION_MODE_1', frequency_Hz: 500, q_mm: [0, 0.02, -0.04, 0.01, 0] },
    { attribute: '_MOTION_MODE_2', frequency_Hz: 1500, q_mm: [0, 0.01, 0.01, 0, 0] }], ...more,
});
const shapes = { _motion_mode_1: attribute([0, 0, 0.001], 3), _motion_mode_2: attribute([0.001, 0, 0], 3) };

describe('a shake (random vibration, shock)', () => {
  it('plays the modes cadgen wrote, relative to the shaker, slowed so the slowest swings about once a second', () => {
    const motion = shakeMotion(stub(shake(), shapes))!;
    // 500 Hz over 1.25 Hz on screen is 400×, the round factor 500×; four milliseconds last two seconds.
    expect(motion).toMatchObject({ kind: 'pose', label: 'Shake', row: 'Time', duration: 2, slow: 500 });
    // It opens on the largest swing: the sample where the shapes move most together (0.04 + 0.01 mm).
    expect(motion.defaultAt).toBeCloseTo(1, 9);
    expect(motion.states.map((state: { label: string }) => state.label)).toEqual(['Start', 'Largest swing']);
    // Between two samples, each history and the base's acceleration halfway; the readout says the real time and g.
    const pose = motion.poseAt(0.75);
    expect(pose.vector).toEqual([['_motion_mode_1', expect.closeTo(-0.01, 9)], ['_motion_mode_2', expect.closeTo(0.01, 9)]]);
    expect(pose.raise).toEqual({ peakMm: 0.05 });
    expect(pose.words).toEqual({ time: '1.5 ms · base 1.5 g', scale: 'slowed 500×' });
    // The row's value field says the real time.
    const row = motionControl(motion);
    expect(row).toMatchObject({ drives: 'motion', label: 'Time', max: 2, defaultValue: motion.defaultAt });
    expect(row.frameLabels[row.snaps.indexOf(1)]).toBe('2 ms');
  });

  it('names a shock\'s pulse ending, and plays nothing from a history that does not fit', () => {
    const shock = shakeMotion(stub(shake({ label: 'Shock', pulse_s: 0.002 }), shapes))!;
    expect(shock.label).toBe('Shock');
    expect(shock.states.map((state: { label: string; at: number }) => [state.label, state.at])).toEqual([['Pulse starts', 0], ['Pulse ends', 1], ['Largest swing', 1]]);
    expect(shakeMotion(stub(shake({ count: 6 }), shapes))).toBeNull();
    expect(shakeMotion(stub(shake(), {}))).toBeNull();
    expect(shakeMotion(stub(null))).toBeNull();
  });
});

describe('a load cycle (fatigue)', () => {
  const displacement = { _displacement: attribute([0, 0, 0.001], 3) };
  const stress = { field: { attribute: '_cycle_stress' } };

  it('swings a static load between R times its peak and its peak, the stress and the deformation with it', () => {
    const motion = cycleMotion(stub({ kind: 'cycle', source: 'static', ratio: 0, frequency_Hz: null, life_cycles: 1.2e6 }, displacement))!;
    expect(motion).toMatchObject({ row: 'Cycle', duration: 4, defaultAt: 0.25 });
    expect(motion.states.map((state: { label: string; at: number }) => [state.label, state.at])).toEqual([['Max', 0.25], ['Min', 0.75]]);
    expect(motion.poseAt(0.25, stress)).toEqual({ vector: [['_displacement', 1]], valueScale: 1,
      words: { time: 'Cycle 1 · the weakest point lasts 1.2 million cycles', scale: 'one cycle a second: the load gives no rate' } });
    expect(motion.poseAt(1.75, stress)).toMatchObject({ vector: [['_displacement', expect.closeTo(0, 9)]], valueScale: expect.closeTo(0, 9) });
    // The life and the margin are of the whole cycle: their colours stand.
    expect(motion.poseAt(0.75, { field: { attribute: '_life' } }).valueScale).toBe(1);
    expect(motion.timeAt(2.25)).toBe('Cycle 3 · 100 %');
    // Fully reversed, it swings through zero to the other side.
    const reversed = cycleMotion(stub({ kind: 'cycle', ratio: -1 }, displacement))!;
    expect(reversed.poseAt(0.75, stress)).toMatchObject({ vector: [['_displacement', -1]], valueScale: 1 });
  });

  it('cycles a sweep at its frequency, slowed, and a random shake\'s at 1σ, 2σ and 3σ in Steinberg\'s shares', () => {
    const sweep = cycleMotion(stub({ kind: 'cycle', ratio: -1, frequency_Hz: 182 }))!;
    expect(sweep.duration).toBeCloseTo((4 * 200) / 182, 9);
    expect(sweep.poseAt(0, stress).words.scale).toBe('182 Hz, slowed 200×');
    const shares = [1, 2, 3].map((level) => STEINBERG_CYCLES.filter((entry) => entry === level).length / STEINBERG_CYCLES.length);
    expect(shares.map((share) => Math.round(share * 100))).toEqual([70, 26, 4]);
    const random = cycleMotion(stub({ kind: 'cycle', ratio: -1, frequency_Hz: 600, levels: [{ sigma: 1, share: 0.683 }] }))!;
    expect(random.states.map((state: { label: string }) => state.label)).toEqual(['3σ peak', '3σ trough']);
    expect(random.poseAt(random.defaultAt, stress).valueScale).toBeCloseTo(1, 9);
    expect(random.poseAt(0.25 * (500 / 600), stress).valueScale).toBeCloseTo(1 / 3, 9);
    expect(random.timeAt(random.defaultAt)).toBe('Cycle 10 · 3σ · 100 %');
  });
});

describe('an AC field (electromagnetic)', () => {
  // One vertex whose field is re·cos θ − im·sin θ with re = im = (1, 0, 0) mT/1000: (cos θ − sin θ) mT, its peak √2 at 135°.
  const pair = { _motion_b_re: attribute([0.001, 0, 0], 3), _motion_b_im: attribute([0.001, 0, 0], 3) };
  const data = { kind: 'ac', frequency_Hz: 50, fields: { magnetic_field: { re: '_MOTION_B_RE', im: '_MOTION_B_IM', attribute_scale: 1000 } } };

  it('turns the field through one cycle at its real frequency, slowed to two seconds, opening where its strongest point peaks', () => {
    const motion = acMotion(stub(data, pair))!;
    expect(motion).toMatchObject({ row: 'Phase', duration: 2, defaultAt: 0.75 });
    expect(motion.states.map((state: { label: string }) => state.label)).toEqual(['Peak', '0°', '90°']);
    const field = { view: 'magnetic_field' };
    expect(motion.poseAt(0, { field }).values[0]).toBeCloseTo(1, 5);
    expect(motion.poseAt(0.75, { field }).values[0]).toBeCloseTo(Math.SQRT2, 5);
    expect(motion.poseAt(0.25, { field }).values[0]).toBeCloseTo(0, 5);
    expect(motion.poseAt(0.5, { field }).words).toEqual({ time: '90° of the cycle', scale: '50 Hz, slowed 100×' });
    expect(motion.timeAt(0.5)).toBe('90° · 5 ms');
    // A field with no phase (a temperature) stands.
    expect(motion.poseAt(0.5, { field: { view: 'temperature' } }).values).toBeUndefined();
    expect(acMotion(stub({ ...data, frequency_Hz: 0 }, pair))).toBeNull();
  });
});

describe('heat coming on (heat stress)', () => {
  it('ramps every temperature from the reference to the solved one, the stress and the deformation with it', () => {
    const result = stub(null, { _temperature: attribute([20, 120], 1), _displacement: attribute([0, 0, 0.001, 0, 0, 0], 3) },
      { fields: [{ attribute: '_temperature', attributeScale: 1 }], analysis: { referenceC: 20 } });
    const motion = heatMotion(result)!;
    expect(motion).toMatchObject({ row: 'Heat', duration: 2, defaultAt: 2 });
    const pose = motion.poseAt(1, { field: { attribute: '_temperature' } });
    expect(Array.from(pose.values)).toEqual([20, 70]);
    expect(pose.vector).toEqual([['_displacement', 0.5]]);
    expect(motion.poseAt(1, { field: { attribute: '_von_mises' } })).toMatchObject({ valueScale: 0.5, words: { time: '50 % of the heat', scale: 'ramped' } });
  });
});

describe('a flow\'s tracers', () => {
  it('stream along the paths cadgen traced, the median trip about four seconds on screen', () => {
    // One path, 10 mm along X in 20 ms: slowed 200×.
    const result = stub({ kind: 'tracers', label: 'Flow', step_s: 0.01, paths: [[0, 0, 0, 5, 0, 0, 10, 0, 0]] });
    const tracers = readTracers(result)!;
    expect(tracers).toMatchObject({ slow: 200, duration: 4, label: 'Flow', step: 0.01 });
    expect(tracersRoutine(result)).toEqual({ id: 'fea:tracers', label: 'Flow', kind: 'tracers', duration: 4 });
    // After 10 ms of flow the first dot is halfway (5 mm along X: 0.005 m in glTF), the others spread a sixth of the trip on.
    const at = tracerPositions(tracers, 0.01);
    expect(at.length).toBe(DOTS_PER_PATH * 3);
    expect(Array.from(at.slice(0, 3))).toEqual([expect.closeTo(0.005, 9), 0, -0]);
    expect(at[3]).toBeCloseTo(0.005 + 0.01 / DOTS_PER_PATH, 9);
    // A creeping flow is sped up: 1 mm in 40 s.
    const slow = readTracers(stub({ kind: 'tracers', step_s: 20, paths: [[0, 0, 0, 0.5, 0, 0, 1, 0, 0]] }))!;
    expect(slow.slow).toBe(0.1);
    expect(readTracers(stub({ kind: 'tracers', step_s: 0.01, paths: [[0, 0, 0]] }))).toBeNull();
  });
});
