import { describe, expect, it } from 'vitest';
import {
  G_MM_S2, HOLD_S, IMPACT_S, MIN_FALL_S, REST_S, fallAt, fallSeconds, fallTimeline, fallWords, framesAtTime, impactSpan, readFall, roundFactor,
} from './fall.js';

/** A fall as cadgen writes one (`analysis.fall`). */
const fall = (more: Record<string, unknown> = {}) => readFall({
  height_mm: 1000, direction: [0, 0, -1], speed_m_s: 4.4287, floor: { point_mm: [0, 0, 0], normal: [0, 0, 1] }, impact_ms: 0.9, ...more,
})!;

describe('the Drop routine\'s timeline', () => {
  it('falls 1 m at real time, 0.45 s, after waiting at its height, and lands at the speed √(2 g h)', () => {
    const plan = fallTimeline(fall(), { impactS: 0.0009 });
    expect(fallSeconds(1000)).toBeCloseTo(0.4516, 4);
    expect(plan.phases.map((phase) => phase.name)).toEqual(['hold', 'fall', 'impact', 'rest']);
    const [hold, falling, impact, rest] = plan.phases;
    expect([hold.start, hold.end]).toEqual([0, HOLD_S]);
    expect(falling.slow).toBe(1);
    expect(falling.end - falling.start).toBeCloseTo(0.4516, 4);
    expect(rest.end - rest.start).toBe(REST_S);
    expect(plan.duration).toBeCloseTo(rest.end, 9);
    // Halfway through the fall's time it has fallen a quarter of the way; at its end it is on the floor at v = g t.
    expect(fallAt(plan, fall(), HOLD_S).lift).toBe(1000);
    expect(fallAt(plan, fall(), HOLD_S + 0.4516 / 2).lift).toBeCloseTo(750, 0);
    expect(fallAt(plan, fall(), falling.end - 1e-9).lift).toBeCloseTo(0, 3);
    expect(G_MM_S2 * falling.real / 1000).toBeCloseTo(4.4287, 3);
    expect(impact.realStart).toBeCloseTo(falling.real, 12);
  });

  it('slows a fall too short to see, and the impact by a round factor to about two and a half seconds', () => {
    const small = fallTimeline(fall({ height_mm: 20 }), { impactS: 0.0009 });
    const falling = small.phases[1];
    expect(falling.real).toBeCloseTo(0.0639, 4);
    expect(falling.end - falling.start).toBeCloseTo(MIN_FALL_S, 9);
    expect(falling.slow).toBeCloseTo(MIN_FALL_S / 0.0639, 1);
    expect([1, 1.4, 3, 4, 7, 3125, 0.5].map(roundFactor)).toEqual([1, 1, 2, 5, 5, 2000, 1]);
    const impact = small.phases[2];
    expect(impact.slow).toBe(roundFactor(IMPACT_S / 0.0009));
    expect(impact.slow).toBe(2000);
    expect(impact.end - impact.start).toBeCloseTo(1.8, 9);
    expect(fallWords(fallAt(small, fall({ height_mm: 20 }), impact.start + 0.9), fall()))
      .toEqual({ time: '450 µs after landing', scale: 'slowed 2,000×' });
  });

  it('hops off as fast as an impact rebounds, then rests', () => {
    const bounced = fall({ rebound_m_s: 0.4 });
    const plan = fallTimeline(bounced, { impactS: 0.0008 });
    expect(plan.phases.map((phase) => phase.name)).toEqual(['hold', 'fall', 'impact', 'hop', 'rest']);
    const hop = plan.phases[3];
    expect(hop.real).toBeCloseTo(2 * 400 / G_MM_S2, 9);
    // Up v²/2g at the top of its hop, on the floor again at its end.
    expect(fallAt(plan, bounced, (hop.start + hop.end) / 2).lift).toBeCloseTo(400 * 400 / (2 * G_MM_S2), 6);
    expect(fallAt(plan, bounced, hop.end - 1e-9).lift).toBeCloseTo(0, 3);
    expect(fallWords(fallAt(plan, bounced, plan.duration), bounced)).toEqual({ time: 'At rest', scale: '' });
  });

  it('says the time in plain words: its height before it falls, seconds falling, real time after landing', () => {
    const plan = fallTimeline(fall(), { impactS: 0.0009 });
    expect(fallWords(fallAt(plan, fall(), 0.1), fall())).toEqual({ time: '1 m above the floor', scale: '' });
    expect(fallWords(fallAt(plan, fall(), HOLD_S + 0.2), fall())).toEqual({ time: '0.20 s', scale: '' });
  });

  it('takes the impact\'s span from its frames, else the stop the file says, and finds the frames either side of a time', () => {
    const series = { frames: [{ value: 0 }, { value: 2e-5 }, { value: 1e-4 }] };
    expect(impactSpan({ series })).toBe(1e-4);
    expect(impactSpan({ series: null, analysis: { fall: fall() } })).toBeCloseTo(0.0009, 12);
    expect(framesAtTime(series, 1e-5)).toEqual({ from: 0, to: 1, weight: 0.5 });
    expect(framesAtTime(series, 6e-5)).toMatchObject({ from: 1, to: 2 });
    expect(framesAtTime(series, 6e-5).weight).toBeCloseTo(0.5, 12);
    expect(framesAtTime(series, 1)).toEqual({ from: 1, to: 2, weight: 1 });
    expect(readFall({ height_mm: 0, direction: [0, 0, -1], floor: { point_mm: [0, 0, 0] } })).toBeNull();
  });
});
