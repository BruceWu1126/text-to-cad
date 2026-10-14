import { BufferAttribute, BufferGeometry, Group, Mesh } from 'three';
import { describe, expect, it } from 'vitest';
import {
  camMotion, camSummary, camTime, clockTime, findingOperation, jobPlayback, operationAt, operationRow, pathPointAt, playbackPace, readCamResult, setupRow, toolPlacementAt, toolRow
} from './camResult.js';
import { FIXTURE_LOOK, createCamTool } from './camPaths.js';
import { DRILL, FACING, STICKOUT, camExtras, camNode, camScene } from './__tests__/camFile.js';

// The pure readers of a CAM result, over a GLB built to cadgen's contract and parsed by GLTFLoader, as the viewer gets it.
describe('readCamResult', () => {
  it('reads the job off the part mesh and the nodes by the names cadgen wrote', async () => {
    const root = await camScene();
    const cam = readCamResult(root)!;
    expect(cam.mesh).toBe(camNode(root, 'part'));
    expect(cam.operations.map((operation) => [operation.op, operation.start, operation.end])).toEqual([['facing', 0, 4], ['drill', 5, 7]]);
    expect(cam.tools.map((tool) => [tool.id, tool.rpm, tool.feed, Boolean(tool.node)])).toEqual([['T1', 21221, 2546.5, true], ['T2', 14854, 1782.5, true], ['T3', null, null, false]]);
    expect(cam.setups[0]).toMatchObject({ id: 'top', direction: '+Z', fixture: { type: 'vise', node: 'fixture:top' } });
    expect(cam.duration).toBe(7);
    expect(cam.nodes.stock).toBe(camNode(root, 'stock'));
    expect(cam.nodes.fixtures.get('top')).toBe(camNode(root, 'fixture:top'));
    expect(cam.nodes.tools.get('T2')).toBe(camNode(root, 'tool:T2'));
    // The findings card's: the warning, with its place in the file; the note is the summary's to say.
    expect(cam.findings).toEqual([expect.objectContaining({ severity: 'warning', summary: STICKOUT.summary, index: 0 })]);
  });

  it('reads each path from its lower-cased _t, _kind and _op, a segment a pair of vertices', async () => {
    const cam = readCamResult(await camScene())!;
    const drill = cam.nodes.paths.get(1)!;
    expect(drill.count).toBe(3);
    expect(Array.from(drill.kinds)).toEqual([0, 5, 5]);
    expect(Array.from(drill.starts)).toEqual([5, 5.5, 6.5]);
    expect(Array.from(drill.positions.slice(0, 6))).toEqual([...DRILL[0].from, ...DRILL[0].to].map(Math.fround));
  });

  it('is null for a plain GLB and for an FEA result', () => {
    const plain = new Group();
    plain.add(new Mesh(new BufferGeometry().setAttribute('position', new BufferAttribute(new Float32Array(9), 3))));
    expect(readCamResult(plain)).toBeNull();
    const fea = new Mesh(new BufferGeometry());
    Object.assign(fea.userData, { generator: 'cadgen fea', fields: [] });
    expect(readCamResult(new Group().add(fea))).toBeNull();
  });
});

describe('the tool on the path', () => {
  it('is on the segment under way, by its share of the segment\'s time; at a dwell where the last one ended', async () => {
    const cam = readCamResult(await camScene())!;
    const facing = cam.nodes.paths.get(0)!;
    expect(pathPointAt(facing, 2).map((value) => +value.toFixed(6))).toEqual([0.01, 0.001, 0]);
    expect(pathPointAt(facing, -1).map((value) => +value.toFixed(6))).toEqual(FACING[0].from);
    expect(pathPointAt(facing, 4.5).map((value) => +value.toFixed(6))).toEqual(FACING[2].to);
    // Between the two operations the facing tool waits at its end; the drill takes over when its path begins.
    expect(operationAt(cam, 4.5)!.op).toBe('facing');
    const drilling = toolPlacementAt(cam, 6)!;
    expect([drilling.operation.op, drilling.tool]).toEqual(['drill', 'T2']);
    expect(drilling.point.map((value) => +value.toFixed(6))).toEqual([0.01, -0.0015, -0.005]);
    expect(drilling.quaternion).toEqual([0, 0, 0, 1]);
  });

  it('is the tool cutting the segment under way, by its _TOOL: an operation may run two', async () => {
    // The facing's first segment cut by T2 (a clearing tool, say), the rest by T1.
    const cam = readCamResult(await camScene(undefined, { 0: [1, 0, 0] }))!;
    expect(Array.from(cam.nodes.paths.get(0)!.tools!)).toEqual([1, 0, 0]);
    expect([toolPlacementAt(cam, 0.5)!.tool, toolPlacementAt(cam, 2)!.tool]).toEqual(['T2', 'T1']);
    expect([toolPlacementAt(cam, 0.5)!.operation.op, toolPlacementAt(cam, 2)!.operation.op]).toEqual(['facing', 'facing']);
  });
});

describe('a finding', () => {
  it('names its operation by the integer index cadgen writes on an item, never by parsing a sentence', async () => {
    const gouge = { check: 'cam', severity: 'error', type: 'gouge', summary: 'operations[1] (drill): T2 cuts 1 mm into the part', description: '',
      items: [{ text: 'operations[1] (drill) #o1.f16', operation: 1, ref: '#o1.f16' }, { text: 'operations[0] (facing) T1', operation: 'operations[0] (facing)' }] };
    const cam = readCamResult(await camScene(camExtras({ findings: [gouge] })))!;
    expect(cam.findings[0].items.map((item) => [item.operation, item.ref])).toEqual([[1, '#o1.f16'], [null, null]]);
    expect(findingOperation(cam.findings[0])).toBe(1);
  });
});

describe('the words and the timeline', () => {
  it('says times, rows and the summary in plain words', async () => {
    expect([camTime(2.475), camTime(7), camTime(15.2), camTime(76), camTime(218.279), camTime(3725)]).toEqual(['2.5 s', '7 s', '15 s', '1 min 16 s', '3 min 38 s', '1 h 02 min']);
    expect([clockTime(2.475), clockTime(100), clockTime(218.3), clockTime(3725)]).toEqual(['2.5 s', '1:40', '3:38', '1:02:05']);
    const cam = readCamResult(await camScene())!;
    expect(operationRow(cam.operations[1], 1)).toMatchObject({ label: '2 Drill', detail: '2 s' });
    expect(toolRow(cam.tools[0])).toMatchObject({ label: 'T1', detail: '6 mm flat endmill', speeds: '21,221 rpm · 2,547 mm/min' });
    expect(setupRow(cam.setups[0])).toMatchObject({ label: 'top', detail: '+Z · vise' });
    expect(camSummary(cam)).toMatchObject({ title: '7 s', caption: '2 operations · 1 tool change · grbl' });
  });

  it('is a motion that opens at the job\'s end, each operation\'s start a state', async () => {
    const motion = camMotion(readCamResult(await camScene())!);
    expect([motion.duration, motion.defaultAt]).toEqual([7, 7]);
    expect(motion.states).toEqual([{ id: 'op-0', label: 'Start of 1 Face', at: 0 }, { id: 'op-1', label: 'Start of 2 Drill', at: 5 }]);
  });

  it('plays at a round pace about twenty seconds long, and with reduced motion one operation a second, each whole', async () => {
    expect([playbackPace(7), playbackPace(218), playbackPace(3600)]).toEqual([1, 10, 200]);
    const cam = readCamResult(await camScene(camExtras({ playback: { duration_s: 7 } })))!;
    const smooth = jobPlayback(cam);
    expect([smooth.duration, smooth.jobTimeAt(2.5)]).toEqual([7, 2.5]);
    const stepped = jobPlayback(cam, true);
    expect([stepped.duration, stepped.jobTimeAt(0.2), stepped.jobTimeAt(0.9), stepped.jobTimeAt(1.5)]).toEqual([2, 4, 4, 7]);
  });
});

describe('the fixtures', () => {
  it('are drawn in a see-through steel for the theme, and get the file\'s material back on dispose', async () => {
    const root = await camScene();
    const fixture = camNode(root, 'fixture:top');
    const own = fixture.material;
    const tool = createCamTool(readCamResult(root)!);
    const look = () => [fixture.material.transparent, fixture.material.opacity, `#${fixture.material.color.getHexString()}`];
    tool.look('light');
    expect(fixture.material).not.toBe(own);
    expect(look()).toEqual([true, FIXTURE_LOOK.light.opacity, FIXTURE_LOOK.light.colour]);
    tool.look('dark');
    expect(look()).toEqual([true, FIXTURE_LOOK.dark.opacity, FIXTURE_LOOK.dark.colour]);
    tool.dispose();
    expect(fixture.material).toBe(own);
  });
});
