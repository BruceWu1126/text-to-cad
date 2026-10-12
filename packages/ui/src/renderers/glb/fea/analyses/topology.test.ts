import { describe, expect, it } from 'vitest';
import { checkLine, checkTitle } from '../checkKinds.js';
import { FIELD_WORDS } from '../fields.js';
import { feaControls, feaVerdict, studyRows } from '../../feaResult.js';
import { activeFrame, activeFrameIndex } from '../series.js';
import { heldRows, madeOfRows, pushedRows } from '../setup.js';
import { feaAnalysis } from './index.js';
import topology, { FORMING, TOPOLOGY_SETUP, keepWords, keptSolidWords, lightenRows } from './topology.js';
import { feaFile, vec } from '../../__tests__/feaFile.js';

const LIMIT = 'Linear elastic, small displacement, one material';

// A lightened bracket as cadgen writes it: three design frames over the iterations, the final design the default.
function lightened(extras: Record<string, unknown> = {}) {
  return feaFile({
    position: [0, 0, 0, 0.06, 0, 0, 0, 0.02, 0], _material_kept: [0.3, 0.3, 0.3], _material_kept_f1: [0.8, 0.2, 0.5],
    _material_kept_f2: [1, 0, 1], _von_mises: [40, 0, 22], _displacement: vec(9), _face: [0, 0, 1],
  }, {
    generator: 'cadgen fea', name: 'bracket topology', deformation_scale: 1, faces: ['#o1.f1', '#o1.f2', '#o1.f7'], occurrence: '#o1',
    mass_saved_percent: 69.4, volume_fraction: 0.3,
    analysis: { type: 'topology', tier: 3, word: 'Lighten it', estimate: false, limits: [LIMIT], noun: 'this load', reference_C: null, warnings: [] },
    fields: [
      { attribute: '_MATERIAL_KEPT', name: 'material kept', units: '', min: 0, max: 1, attribute_scale: 1, field: 'material_kept', per_frame: true },
      { attribute: '_VON_MISES', name: 'von Mises stress', units: 'MPa', min: 0, max: 40, attribute_scale: 1, field: 'von_mises' },
      { attribute: '_DISPLACEMENT', name: 'displacement', units: 'mm', min: 0, max: 0.01, attribute_scale: 1000, field: 'displacement' },
    ],
    series: { kind: 'time', unit: '', default: 2, frames: [
      { value: 1, label: 'Iteration 1', attributes: { material_kept: '_MATERIAL_KEPT' } },
      { value: 40, label: 'Iteration 40', attributes: { material_kept: '_MATERIAL_KEPT_F1' } },
      { value: 91, label: 'Final design', attributes: { material_kept: '_MATERIAL_KEPT_F2' } }] },
    study: {
      material: { name: 'Steel (structural, generic)', yield_MPa: 250, youngs_GPa: 200, poisson: 0.3 },
      fixtures: [{ type: 'fixed', faces: ['#o1.f1'] }], loads: [{ type: 'force', faces: ['#o1.f2'], vector_N: [0, 0, -100] }],
      objective: 'stiffest', volume_fraction: 0.3, keep: ['#o1.f7'], kept_solid: ['#o1.f1', '#o1.f2', '#o1.f7'], penalty: 3,
      mesh: { size_mm: 2, order: 1, elements: 2072, refined_from_mm: null },
    },
    checks: [
      { kind: 'mass_saved', label: 'Mass saved', value: 69.4, limit: 25, unit: '%', ratio: 0.36, close_at: 0.9, status: 'passes',
        where: { ref: null, at: [0, 0, 0] } },
      { kind: 'stress', label: 'Strength', value: 40, limit: 250, unit: 'MPa', ratio: 0.16, close_at: 0.5, margin: 2, status: 'passes',
        where: { ref: '#o1.f2', at: [0, 0, 0] } },
    ],
    findings: [],
    ...extras,
  });
}

describe('topology', () => {
  it('is Lighten it, Tier 3 and Lite: no load control, its checks, its setup, its design forming', () => {
    expect(topology).toMatchObject({ name: 'topology', tier: 3, word: 'Lighten it', family: null, scalesWithLoad: false, limitWord: 'Lite',
      checks: ['mass_saved', 'stress', 'displacement'], displayTitle: 'Loads and fixtures' });
    expect(TOPOLOGY_SETUP).toEqual([heldRows, pushedRows, lightenRows, madeOfRows]);
    const result = lightened();
    expect(feaAnalysis(result)).toBe(topology);
    expect(topology.routine(result)).toBe(FORMING);
    expect(FORMING).toEqual({ id: 'fea:play', label: 'Watch it form', kind: 'play' });
    expect(topology.routine({ series: null })).toBeNull();
    expect(FIELD_WORDS._material_kept).toBe('Material kept');
  });

  it('opens on the iteration scrubber at the final design, Material kept first, and greys what goes under 0.5', () => {
    const result = lightened();
    const [iteration, field, threshold] = feaControls(result);
    expect(iteration).toMatchObject({ drives: 'frame', label: 'Iteration', min: 1, max: 91, defaultValue: 91, snaps: [1, 40, 91],
      frameLabels: ['Iteration 1', 'Iteration 40', 'Final design'] });
    expect(field).toMatchObject({ drives: 'field', defaultValue: '_material_kept' });
    expect(field.options.map((option: { label: string }) => option.label)).toEqual(['Material kept', 'Stress', 'Displacement']);
    expect(threshold).toMatchObject({ drives: 'threshold', label: 'Show above', min: 0, max: 1, defaultValue: 0.5, field: '_material_kept' });
    expect(feaControls(result).some((control: { drives: string }) => control.drives === 'load_scale' || control.drives === 'deformation')).toBe(false);
    expect(activeFrameIndex(result)).toBe(2);
    expect(activeFrame(result, { frame: 38 }, result.fields[0]).field.attribute).toBe('_material_kept_f1');
    expect(activeFrame(result, {}, result.fields[0]).field.attribute).toBe('_material_kept_f2');
  });

  it('says what it keeps in Study: "Keep 30% of the material", "Kept solid: face 1, face 2, face 7"', () => {
    expect(keepWords(0.3)).toBe('Keep 30% of the material');
    expect(keepWords(null)).toBe('');
    expect(keptSolidWords(['#o1.f2', '#o1.f7'])).toBe('Kept solid: face 2, face 7');
    const rows = studyRows(lightened());
    expect(rows.slice(0, 4).map((row: { label: string }) => row.label)).toEqual(['Held at', 'Pushed', 'Lighten', 'Made of']);
    const lighten = rows.find((row: { id: string }) => row.id === 'lighten');
    expect(lighten.children.map((row: { label: string }) => row.label)).toEqual(['Keep 30% of the material', 'Kept solid: face 1, face 2, face 7']);
    expect(lighten.children[1].faces).toEqual(['#o1.f1', '#o1.f2', '#o1.f7']);
  });

  it('leads its verdict with "Lite · " and words its mass check as cadgen writes it', () => {
    const verdict = feaVerdict(lightened());
    expect(verdict.status).not.toBe('weak');
    expect(verdict.caption.startsWith('Lite · ')).toBe(true);
    const check = { kind: 'mass_saved', value: 62, shown: 62, limit: 25, unit: '%' };
    expect(checkTitle(check, 'fails')).toBe('Barely lighter');
    expect(checkTitle(check, 'close')).toBe('Close');
    expect(checkTitle(check, 'passes')).toBe('Much lighter');
    expect(checkLine(check).replace(/[  ]/g, ' ')).toBe('Saves 62 % of the mass, needs 25 %');
    const details = studyRows(lightened()).find((row: { id: string }) => row.id === 'details');
    const limits = details.children.find((row: { id: string }) => row.id === 'limits');
    expect(limits.children.map((row: { label: string }) => row.label)).toEqual([LIMIT]);
  });
});
