import { describe, expect, it } from 'vitest';
import { feaVerdict } from '../../feaResult.js';
import { ANALYSES } from './index.js';
import { feaFile, vec } from '../../__tests__/feaFile.js';

// Every analysis whose response is not a multiple of its load: material laws, contact, flow, time, levels in dB.
const NOT_LINEAR = ['creep', 'nonlinear', 'contact', 'bolt', 'composite', 'impact', 'multiphase', 'fsi', 'cfd', 'cfd_turbulent',
  'cfd_compressible', 'fatigue', 'acoustic'];

// One passing check of `kind` as an older cadgen wrote it: no `scaling` of its own, so the analysis's decides.
function resultOf(type: string, check: Record<string, unknown>) {
  return feaFile({
    position: [0, 0, 0, 0.04, 0, 0, 0, 0.006, 0], _von_mises: [67, 10, 30], _displacement: vec(9), _face: [0, 0, 1],
  }, {
    generator: 'cadgen fea', name: 'part', deformation_scale: 10, faces: ['#o1.f1', '#o1.f2'], occurrence: '#o1', safety_factor: 2.5,
    analysis: { type, tier: 3, word: type, estimate: false, limits: ['x'], warnings: [] },
    fields: [
      { attribute: '_VON_MISES', name: 'von Mises stress', units: 'MPa', min: 0, max: 67, attribute_scale: 1, field: 'von_mises' },
      { attribute: '_DISPLACEMENT', name: 'displacement', units: 'mm', min: 0, max: 0.1, attribute_scale: 1000, field: 'displacement' },
    ],
    study: { material: { name: 'Steel', yield_MPa: 250, youngs_GPa: 200, poisson: 0.3 }, fixtures: [], loads: [] },
    checks: [{ label: 'Check', status: 'passes', close_at: 0.5, where: { ref: '#o1.f1', at: [0, 0, 0] }, ...check }],
    findings: [],
  });
}

const STRESS = { kind: 'stress', value: 100, limit: 250, unit: 'MPa', ratio: 0.4, margin: 2 };
const MOVES = { kind: 'displacement', value: 0.1, limit: 1, unit: 'mm', ratio: 0.1 };

describe('a load multiple in the verdict', () => {
  it('is never said by an analysis that is not linear in its load, whatever its checks', () => {
    for (const type of NOT_LINEAR) {
      expect(ANALYSES[type as keyof typeof ANALYSES], type).toBeTruthy();
      for (const check of [STRESS, MOVES]) {
        const caption = feaVerdict(resultOf(type, check))?.caption ?? '';
        expect(caption, `${type} ${check.kind}: ${caption}`).not.toMatch(/×/);
      }
    }
  });

  it('is said where the check truly scales: a linear analysis, or a check cadgen tags linear or quadratic', () => {
    expect(feaVerdict(resultOf('static', STRESS))!.caption).toMatch(/OK up to 2\.\d?× this load/);
    // A spinning rotor's stress grows with the speed squared: the root of its room, 2.5× the stress is 1.58× the spin.
    expect(feaVerdict(resultOf('rotordynamics', { ...STRESS, scaling: 'quadratic' }))!.caption).toMatch(/OK up to 1\.5\d?× this spin/);
  });
});
