import * as THREE from 'three';
import { BufferAttribute, BufferGeometry, Group, Mesh } from 'three';
import { describe, expect, it } from 'vitest';
import { readFeaResult } from './feaResult.js';
import { fallFrame, fallMatrix, feaFallBounds, pastFloor, unionBounds } from './feaFall.js';

// A steel shaft 100 mm long along the part's X (glTF metres: x, z, -y), 10 mm square, dropped 1 m end first along -X
// (the shaft-end gallery study): the routine stands it on its end, the x = 0 face on the floor.
function shaft() {
  const corners: number[] = [];
  for (const x of [0, 0.1]) for (const y of [-0.005, 0.005]) for (const z of [-0.005, 0.005]) corners.push(x, z, -y);
  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new BufferAttribute(new Float32Array(corners), 3));
  geometry.setIndex([0, 1, 2, 1, 3, 2, 4, 6, 5, 5, 6, 7]);
  geometry.setAttribute('_von_mises', new BufferAttribute(new Float32Array(8), 1));
  geometry.setAttribute('_displacement', new BufferAttribute(new Float32Array(24), 3));
  const mesh = new Mesh(geometry);
  Object.assign(mesh.userData, {
    generator: 'cadgen fea', name: 'shaft drop (estimate)', deformation_scale: 1,
    fields: [{ attribute: '_VON_MISES', name: 'von Mises stress', units: 'MPa', min: 0, max: 1, attribute_scale: 1, field: 'von_mises' }],
    analysis: { type: 'drop', tier: 2, word: 'Drop (estimate)', estimate: true, limits: [], warnings: [],
      fall: { height_mm: 1000, direction: [-1, 0, 0], speed_m_s: 4.4287, floor: { point_mm: [0, 0, 0], normal: [1, 0, 0] }, impact_ms: 0.9 } },
    study: { material: { name: 'Steel' }, drop: { height_mm: 1000, onto: [], stop_mm: 2, direction: [-1, 0, 0] } },
  });
  const root = new Group();
  root.add(mesh);
  const fea = readFeaResult(root)!;
  const box = new THREE.Box3().setFromObject(root, true);
  return { fea, mesh, scene: { object3D: root, bounds: { min: box.min.toArray(), max: box.max.toArray() } } };
}

describe('the Drop routine\'s fall, drawn', () => {
  it('stands the part the way it falls, its first point on the floor, and lifts it to its height', () => {
    const { fea, mesh } = shaft();
    const frame = fallFrame(THREE, fea)!;
    const positions = mesh.geometry.getAttribute('position').array as Float32Array;
    expect(pastFloor(positions, frame)).toBeCloseTo(0, 9);
    const drawn = (lift: number) => new THREE.Box3().setFromBufferAttribute(mesh.geometry.getAttribute('position') as BufferAttribute)
      .applyMatrix4(fallMatrix(THREE, frame, { lift }));
    // On the floor: upright (its 100 mm along the fall now up), its end at the floor's level.
    const landed = drawn(0);
    expect(landed.min.y).toBeCloseTo(0, 9);
    expect(landed.max.y).toBeCloseTo(0.1, 6);
    expect(landed.max.x - landed.min.x).toBeCloseTo(0.01, 9);
    expect(drawn(1).min.y).toBeCloseTo(1, 9);
    // Moved 1 mm on through the floor by its centre of mass, it reaches 1 mm past it.
    expect(pastFloor(positions, frame, new THREE.Vector3(-0.001, 0, 0))).toBeCloseTo(0.001, 9);
  });

  it('frames the whole fall: from its height down to the floor, which Preview frames with the part at rest beside it', () => {
    const { fea, scene } = shaft();
    const falls = feaFallBounds(THREE, scene, fea)!;
    expect(falls.min[1]).toBeCloseTo(0, 6);
    expect(falls.max[1]).toBeCloseTo(1.1, 6);
    expect(falls.max[0] - falls.min[0]).toBeCloseTo(0.01, 6);
    const preview = unionBounds(scene.bounds, falls);
    expect(preview.max[0]).toBeCloseTo(0.1, 6);
    expect(preview.max[1]).toBeCloseTo(1.1, 6);
    // A result with no fall has none to frame.
    expect(feaFallBounds(THREE, scene, { ...fea, analysis: { ...fea.analysis, fall: undefined } })).toBeNull();
  });
});
