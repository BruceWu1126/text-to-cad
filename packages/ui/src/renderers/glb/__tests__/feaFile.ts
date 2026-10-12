import { BufferAttribute, BufferGeometry, Group, Mesh } from 'three';
import { readFeaResult } from '../feaResult.js';

/** A vector attribute, three numbers a vertex (a count: that many zeros): positions, a displacement, a mode shape. */
export const vec = (values: ArrayLike<number> | number) => new BufferAttribute(new Float32Array(values as ArrayLike<number>), 3);

/** Text with its narrow and no-break spaces plain, as a test compares it. */
export const plain = (text: string) => text.replace(/[  ]/g, ' ');

/**
 * A scene as `cadgen fea solve` writes one: a single mesh, its `position` first (three CAD-sized vertices, one
 * triangle, indexed unless `index` is false), a baked `color` after it unless `color` is false, then each other
 * attribute in the order given (a list of numbers is one float a vertex), and its extras.
 */
export function feaScene(attributes: Record<string, number[] | BufferAttribute>, extras: Record<string, unknown>,
  { index = true, color = true } = {}) {
  const geometry = new BufferGeometry();
  const { position, ...rest } = attributes;
  geometry.setAttribute('position', position instanceof BufferAttribute ? position : vec(position));
  if (index) geometry.setIndex([0, 1, 2]);
  if (color) geometry.setAttribute('color', new BufferAttribute(new Uint8Array(12), 4, true));
  for (const [name, values] of Object.entries(rest)) {
    geometry.setAttribute(name, values instanceof BufferAttribute ? values : new BufferAttribute(new Float32Array(values), 1));
  }
  const mesh = new Mesh(geometry);
  Object.assign(mesh.userData, extras);
  const root = new Group();
  root.add(mesh);
  return root;
}

/** The result `readFeaResult` reads from such a scene. */
export const feaFile = (...args: Parameters<typeof feaScene>) => readFeaResult(feaScene(...args))!;
