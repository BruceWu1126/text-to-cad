import { BufferAttribute, BufferGeometry, Group, Mesh } from 'three';
import { describe, expect, it } from 'vitest';
import { feaVerdict, readFeaResult } from '../../feaResult.js';
import { ANALYSES } from './index.js';

// Every analysis whose response is not a multiple of its load: material laws, contact, flow, time, levels in dB.
const NOT_LINEAR = ['creep', 'nonlinear', 'contact', 'bolt', 'composite', 'impact', 'multiphase', 'fsi', 'cfd', 'cfd_turbulent',
  'cfd_compressible', 'fatigue', 'acoustic'];

// One passing check of `kind` as an older cadgen wrote it: no `scaling` of its own, so the analysis's decides.
function resultOf(type: string, check: Record<string, unknown>) {
  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new BufferAttribute(new Float32Array([0, 0, 0, 0.04, 0, 0, 0, 0.006, 0]), 3));
  geometry.setIndex([0, 1, 2]);
  geometry.setAttribute('color', new BufferAttribute(new Uint8Array(12), 4, true));
  geometry.setAttribute('_von_mises', new BufferAttribute(new Float32Array([67, 10, 30]), 1));
  geometry.setAttribute('_displacement', new BufferAttribute(new Float32Array(9), 3));
  geometry.setAttribute('_face', new BufferAttribute(new Float32Array([0, 0, 1]), 1));
  const mesh = new Mesh(geometry);
  Object.assign(mesh.userData, {
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
  const root = new Group();
  root.add(mesh);
  return readFeaResult(root)!;
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
