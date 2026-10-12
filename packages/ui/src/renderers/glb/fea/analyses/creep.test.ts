import { describe, expect, it } from 'vitest';
import { checkLine, checkTitle, hoursWords } from '../checkKinds.js';
import { fieldWord } from '../fields.js';
import { feaControls, feaVerdict, studyRows } from '../../feaResult.js';
import { activeFrame, activeFrameIndex } from '../series.js';
import { heldRows, madeOfRows, pushedRows } from '../setup.js';
import { feaAnalysis } from './index.js';
import creep, { CREEP_SETUP, PLAY, runawayCaption } from './creep.js';
import { feaFile, plain, vec } from '../../__tests__/feaFile.js';

const LIMIT = 'Isotropic, small strain: creep keeps the volume and follows the von Mises stress; rotations stay small';

// A cantilever held for 10,000 h, as cadgen writes it: three time frames, the end the default, its stress relaxing.
function heldResult(extras: Record<string, unknown> = {}) {
  const frames = Object.fromEntries([1, 2].flatMap((index) => [[`_von_mises_f${index}`, [67 - 9 * index, 10, 30]],
    [`_displacement_f${index}`, vec([0, 0, 0, 0, 0, -0.00008 * index, 0, 0, 0])], [`_creep_strain_f${index}`, [0.4 * index, 0, 0.1]]]));
  return feaFile({
    position: [0, 0, 0, 0.04, 0, 0, 0, 0.006, 0], _von_mises: [67, 10, 30], _displacement: vec(9), _creep_strain: [0, 0, 0], ...frames,
    _face: [0, 0, 1],
  }, {
    generator: 'cadgen fea', name: 'bar creep', deformation_scale: 19, faces: ['#o1.f1', '#o1.f2'], occurrence: '#o1',
    duration_h: 10000, reached_h: 10000, stopped: false,
    analysis: { type: 'creep', tier: 3, word: 'Creep', estimate: false, limits: [LIMIT], noun: 'this load', reference_C: null, warnings: [] },
    fields: [
      { attribute: '_VON_MISES', name: 'von Mises stress', units: 'MPa', min: 0, max: 67, attribute_scale: 1, field: 'von_mises', per_frame: true },
      { attribute: '_DISPLACEMENT', name: 'displacement', units: 'mm', min: 0, max: 0.16, attribute_scale: 1000, field: 'displacement', per_frame: true },
      { attribute: '_CREEP_STRAIN', name: 'creep strain', units: '%', min: 0, max: 0.8, attribute_scale: 1, field: 'creep_strain', per_frame: true },
    ],
    series: { kind: 'time', unit: 'h', default: 2, frames: [
      { value: 0, label: '0 h', attributes: { von_mises: '_VON_MISES', displacement: '_DISPLACEMENT', creep_strain: '_CREEP_STRAIN' } },
      { value: 5000, label: '5,000 h', attributes: { von_mises: '_VON_MISES_F1', displacement: '_DISPLACEMENT_F1', creep_strain: '_CREEP_STRAIN_F1' } },
      { value: 10000, label: '10,000 h', attributes: { von_mises: '_VON_MISES_F2', displacement: '_DISPLACEMENT_F2', creep_strain: '_CREEP_STRAIN_F2' } }] },
    study: {
      material: { name: 'Steel (structural, generic)', yield_MPa: 250, youngs_GPa: 200, poisson: 0.3 },
      fixtures: [{ type: 'fixed', faces: ['#o1.f1'] }], loads: [{ type: 'force', faces: ['#o1.f2'], vector_N: [0, 0, -60] }],
      duration_h: 10000, steps: 20, mesh: { size_mm: 3, order: 2, elements: 400, refined_from_mm: null },
    },
    checks: [{ kind: 'creep_strain', label: 'Creep', value: 0.8, limit: 1, unit: '%', ratio: 0.8, close_at: 0.9, status: 'passes',
      where: { ref: '#o1.f1', at: [0, 0, 0] }, duration_h: 10000, at: { frame: 2, value: 10000, unit: 'h' } }],
    findings: [],
    ...extras,
  });
}

describe('creep', () => {
  it('is Creep, Tier 3 and Steady creep: no load control, its checks, static setup, playing its hold', () => {
    expect(creep).toMatchObject({ name: 'creep', tier: 3, word: 'Creep', noun: 'this load', family: null, scalesWithLoad: false,
      limitWord: 'Steady creep', checks: ['creep_strain', 'stress', 'displacement'], displayTitle: 'Loads and fixtures' });
    expect(creep.markers).toEqual(['load', 'fixture', 'body_load']);
    expect(CREEP_SETUP).toEqual([heldRows, pushedRows, madeOfRows]);
    const result = heldResult();
    expect(feaAnalysis(result)).toBe(creep);
    expect(creep.routine(result)).toBe(PLAY);
    expect(PLAY).toEqual({ id: 'fea:play', label: 'Play', kind: 'play' });
    expect(creep.routine({ series: null })).toBeNull();
  });

  it('opens on a time scrubber that snaps to the frames, the field and the deformation, at the end of the hold', () => {
    const result = heldResult();
    const [time, field, deformation] = feaControls(result);
    expect(time).toMatchObject({ drives: 'frame', type: 'number', label: 'Time', min: 0, max: 10000, defaultValue: 10000, unit: 'h',
      snaps: [0, 5000, 10000], frameLabels: ['0 h', '5,000 h', '10,000 h'] });
    expect(field).toMatchObject({ drives: 'field', defaultValue: '_von_mises' });
    expect(field.options.map((option: { label: string }) => option.label)).toEqual(['Stress', 'Displacement', 'Creep strain']);
    expect(deformation).toMatchObject({ drives: 'deformation' });
    expect(feaControls(result).some((control: { drives: string }) => control.drives === 'load_scale')).toBe(false);
    expect(activeFrameIndex(result)).toBe(2);
    expect(activeFrameIndex(result, { frame: 4800 })).toBe(1);
    const shown = activeFrame(result, { frame: 5000 }, result.fields[2]);
    expect(shown.field.attribute).toBe('_creep_strain_f1');
    expect(shown.deformation.attribute).toBe('_displacement_f1');
    expect(fieldWord({ attribute: '_creep_strain_f2', name: 'creep strain' })).toBe('Creep strain');
  });

  it('leads its verdict with "Steady creep · ", says its row line and lists its limits in Details', () => {
    const verdict = feaVerdict(heldResult());
    expect(verdict.status).toBe('strong');
    expect(verdict.title).toBe('Holds its shape');
    expect(verdict.caption.replace(/[  ]/g, ' ')).toBe('Steady creep · 0.8 % creep after 10,000 h, limit 1 %');
    const details = studyRows(heldResult()).find((row: { id: string }) => row.id === 'details');
    const limits = details.children.find((row: { id: string }) => row.id === 'limits');
    expect(limits.children.map((row: { label: string }) => row.label)).toEqual([LIMIT]);
    expect(studyRows(heldResult()).slice(0, 3).map((row: { label: string }) => row.label)).toEqual(['Held at', 'Pushed', 'Made of']);
  });

  it('leads a runaway\'s takeaway with the runaway, and otherwise its worst check', () => {
    const away = heldResult({ stopped: true, analysis: { type: 'creep', tier: 3, word: 'Creep', limits: [LIMIT],
      warnings: ['Creep runs away after about 6,200 h'] } });
    expect(runawayCaption(away)).toBe('Creep runs away after about 6,200 h');
    expect(feaVerdict(away).caption).toBe('Steady creep · Creep runs away after about 6,200 h');
    expect(runawayCaption(heldResult())).toBe('');
  });

  it('words its check as cadgen writes it', () => {
    const check = { kind: 'creep_strain', value: 0.8, shown: 0.8, limit: 1, unit: '%', at: { frame: 2, value: 10000, unit: 'h' } };
    expect(['fails', 'close', 'passes'].map((status) => checkTitle(check, status))).toEqual(['Creeps too far', 'Close to the limit', 'Holds its shape']);
    expect(plain(checkLine(check))).toBe('0.8 % creep after 10,000 h, limit 1 %');
    expect(plain(checkLine({ ...check, at: { frame: 1, value: 250, unit: 'h' } }))).toBe('0.8 % creep after 250 h, limit 1 %');
    expect(plain(checkLine({ ...check, at: undefined }))).toBe('0.8 % creep, limit 1 %');
    expect([2.5, 250, 10000, 87600].map(hoursWords)).toEqual(['2.5 h', '250 h', '10,000 h', '87,600 h']);
  });
});
