import type { Group } from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';

/**
 * A small CAM result GLB, as `cadgen cam plan` writes one (`_internal/cam/outputs.py`), built byte by byte: the part
 * (mesh 0, the job on its extras, `_FACE`), the stock, a setup's fixture, two tools (each in its own frame, placed at
 * its first path's start) and two operations' paths as `LINES` with `_T`, `_KIND`, `_OP` and `_TOOL` (the facing's
 * segments T1's, tools[0], the drill's T2's, tools[1], unless `pathTools` says otherwise). In glTF metres:
 *
 * - `path:0:facing` (T1, 0 to 4 s): a rapid down (0 to 1 s), a feed along +X (1 to 3 s), a feed along -Z (3 to 4 s);
 * - `path:1:drill` (T2, 5 to 7 s): a rapid down (5 to 5.5 s), then a canned cycle, down (5.5 to 6.5 s) and back up.
 */
export const FACING = [
  { from: [0, 0.01, 0], to: [0, 0.001, 0], t: [0, 1], kind: 0 },
  { from: [0, 0.001, 0], to: [0.02, 0.001, 0], t: [1, 3], kind: 1 },
  { from: [0.02, 0.001, 0], to: [0.02, 0.001, -0.01], t: [3, 4], kind: 1 },
];
export const DRILL = [
  { from: [0.01, 0.01, -0.005], to: [0.01, 0.002, -0.005], t: [5, 5.5], kind: 0 },
  { from: [0.01, 0.002, -0.005], to: [0.01, -0.005, -0.005], t: [5.5, 6.5], kind: 5 },
  { from: [0.01, -0.005, -0.005], to: [0.01, 0.002, -0.005], t: [6.5, 7], kind: 5 },
];

/** A warning the job's checks found, and a note (`info`) the summary already says. */
export const STICKOUT = { check: 'cam', severity: 'warning', type: 'long_stickout', summary: 'T1 sticks out 6.7 times its diameter: expect chatter',
  description: 'Long stickout.', items: [{ text: 'the pocket floor', ref: '#o1.f19' }] };
const NOTE = { check: 'cam', severity: 'info', type: 'cycle_time', summary: 'Cycle time 7 s', description: '', items: [] };

/** The job's extras, as the part mesh carries them; `overrides` replaces any key. */
export function camExtras(overrides: Record<string, unknown> = {}) {
  return {
    generator: 'cadgen cam', document: 'plate.step', occurrence: '#o1', frame: { of: 'part', units: 'm' },
    machine: { kind: 'mill', axes: 3, post: 'grbl', units: 'mm', rapid_mm_min: 5000, rapid_assumed: true },
    setups: [{ id: 'top', direction: '+Z', origin: 'stock_top_center', quaternion: [0, 0, 0, 1], fixture: { type: 'vise', node: 'fixture:top' }, nc: 'plate.nc' }],
    tools: [
      { id: 'T1', type: 'flat_endmill', diameter_mm: 6, flutes: 3, uses: [{ operation: 0, rpm: 21221, feed_mm_min: 2546.5 }], node: 'tool:T1' },
      { id: 'T2', type: 'drill', diameter_mm: 5, uses: [{ operation: 1, rpm: 14854, feed_mm_min: 1782.5 }], node: 'tool:T2' },
      { id: 'T3', type: 'flat_endmill', diameter_mm: 25, uses: [], node: null },
    ],
    operations: [
      { index: 0, op: 'facing', word: 'Face', setup: 'top', tool: 'T1', tools: ['T1'], rpm: 21221, feed_mm_min: 2546.5, start_s: 0, time_s: 4,
        cut_length_mm: 30, rapid_length_mm: 9, moves: 3, node: 'path:0:facing', lines: ['top face to z 0'] },
      { index: 1, op: 'drill', word: 'Drill', setup: 'top', tool: 'T2', tools: ['T2'], rpm: 14854, feed_mm_min: 1782.5, start_s: 5, time_s: 2,
        cut_length_mm: 7, rapid_length_mm: 8, moves: 2, node: 'path:1:drill', lines: ['1 hole'] },
    ],
    summary: { cycle_time_s: 7, cut_length_mm: 37, rapid_length_mm: 17, tool_changes: 1, operations: 2, setups: 1, programs: 1, post: 'grbl', rapid_mm_min: 5000 },
    findings: [STICKOUT, NOTE], checks: [], playback: { duration_s: 7 }, view: { show: { rapids: true } },
    ...overrides,
  };
}

const ARRAY = 34962;
const FLOAT = 5126;

/** The bytes of a CAM result GLB: `extras` on the part mesh (`camExtras()` unless given). */
export function camGlb(extras: Record<string, unknown> = camExtras(), pathTools: Record<number, number[]> = {}): ArrayBuffer {
  const chunks: Float32Array[] = [];
  const views: object[] = [];
  const accessors: object[] = [];
  let offset = 0;
  const add = (values: number[], type: 'VEC3' | 'SCALAR', bounds = false) => {
    const data = new Float32Array(values);
    chunks.push(data);
    views.push({ buffer: 0, byteOffset: offset, byteLength: data.byteLength, target: ARRAY });
    offset += data.byteLength;
    const size = type === 'VEC3' ? 3 : 1;
    const accessor: Record<string, unknown> = { bufferView: views.length - 1, componentType: FLOAT, count: values.length / size, type };
    if (bounds) {
      accessor.min = [0, 1, 2].map((axis) => Math.min(...values.filter((_, index) => index % 3 === axis)));
      accessor.max = [0, 1, 2].map((axis) => Math.max(...values.filter((_, index) => index % 3 === axis)));
    }
    accessors.push(accessor);
    return accessors.length - 1;
  };
  const meshes: object[] = [];
  const nodes: object[] = [];
  const triangles = (name: string, positions: number[], material: number, more: Record<string, number[]> = {}, node: Record<string, unknown> = {}, meshExtras?: object) => {
    const attributes: Record<string, number> = { POSITION: add(positions, 'VEC3', true) };
    for (const [key, values] of Object.entries(more)) attributes[key] = add(values, 'SCALAR');
    meshes.push({ name, primitives: [{ attributes, material, mode: 4 }], ...(meshExtras ? { extras: meshExtras } : {}) });
    nodes.push({ mesh: meshes.length - 1, name, ...node });
  };
  const lines = (name: string, op: number, segments: typeof FACING) => {
    const positions = segments.flatMap((segment) => [...segment.from, ...segment.to]);
    const tools = pathTools[op] || segments.map(() => op);
    meshes.push({ name, primitives: [{ attributes: {
      POSITION: add(positions, 'VEC3', true),
      _T: add(segments.flatMap((segment) => segment.t), 'SCALAR'),
      _KIND: add(segments.flatMap((segment) => [segment.kind, segment.kind]), 'SCALAR'),
      _OP: add(segments.flatMap(() => [op, op]), 'SCALAR'),
      _TOOL: add(tools.flatMap((tool) => [tool, tool]), 'SCALAR'),
    }, material: 4, mode: 1 }] });
    nodes.push({ mesh: meshes.length - 1, name });
  };
  const quad = (y: number, size: number) => [-size, y, -size, size, y, -size, size, y, size, -size, y, -size, size, y, size, -size, y, size];
  triangles('part', quad(0, 0.03), 0, { _FACE: [1, 1, 1, 2, 2, 2] }, {}, extras);
  triangles('stock', quad(0.001, 0.035), 1);
  triangles('fixture:top', quad(-0.02, 0.05), 2);
  const tool = [0, 0, 0, 0.003, 0.02, 0, -0.003, 0.02, 0];
  triangles('tool:T1', tool, 3, {}, { translation: FACING[0].from, rotation: [0, 0, 0, 1] });
  triangles('tool:T2', tool, 3, {}, { translation: DRILL[0].from, rotation: [0, 0, 0, 1] });
  lines('path:0:facing', 0, FACING);
  lines('path:1:drill', 1, DRILL);
  const gltf = {
    asset: { version: '2.0', generator: 'cadgen cam' }, scene: 0, scenes: [{ nodes: nodes.map((_, index) => index) }], nodes, meshes,
    materials: ['part', 'stock', 'fixture', 'tool', 'toolpath'].map((name) => ({ name, pbrMetallicRoughness: { baseColorFactor: [0.5, 0.5, 0.5, 1] } })),
    buffers: [{ byteLength: offset }], bufferViews: views, accessors,
  };
  let json = new TextEncoder().encode(JSON.stringify(gltf));
  const padded = new Uint8Array(Math.ceil(json.length / 4) * 4).fill(0x20);
  padded.set(json);
  json = padded;
  const total = 12 + 8 + json.length + 8 + offset;
  const out = new ArrayBuffer(total);
  const view = new DataView(out);
  view.setUint32(0, 0x46546c67, true);
  view.setUint32(4, 2, true);
  view.setUint32(8, total, true);
  view.setUint32(12, json.length, true);
  view.setUint32(16, 0x4e4f534a, true);
  new Uint8Array(out, 20, json.length).set(json);
  view.setUint32(20 + json.length, offset, true);
  view.setUint32(24 + json.length, 0x004e4942, true);
  let at = 28 + json.length;
  for (const chunk of chunks) { new Uint8Array(out, at, chunk.byteLength).set(new Uint8Array(chunk.buffer)); at += chunk.byteLength; }
  return out;
}

/** The scene GLTFLoader makes of `camGlb(extras)`: what the viewer reads (names in `userData.name`, attributes lower-cased). */
export async function camScene(extras?: Record<string, unknown>, pathTools?: Record<number, number[]>): Promise<Group> {
  const gltf = await new GLTFLoader().parseAsync(camGlb(extras, pathTools), '');
  return gltf.scene;
}

/** A node of a loaded scene by the name cadgen wrote. */
export function camNode(root: Group, name: string): any {
  let found: any = null;
  root.traverse((object) => { if (!found && (object.userData?.name || object.name) === name) found = object; });
  return found;
}
