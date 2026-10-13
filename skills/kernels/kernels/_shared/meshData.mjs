// Mesh data from plain triangle arrays, shared by the JavaScript kernels:
// positions in mm, Z up.

export function boundsOf(positions) {
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i + 2 < positions.length; i += 3) {
    for (let k = 0; k < 3; k += 1) {
      min[k] = Math.min(min[k], positions[i + k]);
      max[k] = Math.max(max[k], positions[i + k]);
    }
  }
  return min.every(Number.isFinite) ? { min, max } : { min: [0, 0, 0], max: [0, 0, 0] };
}

// Enclosed volume of a closed triangle mesh (divergence theorem).
export function meshVolume(positions, indices) {
  let sum = 0;
  const count = indices ? indices.length : positions.length / 3;
  for (let t = 0; t + 2 < count; t += 3) {
    const a = 3 * (indices ? indices[t] : t);
    const b = 3 * (indices ? indices[t + 1] : t + 1);
    const c = 3 * (indices ? indices[t + 2] : t + 2);
    const ax = positions[a], ay = positions[a + 1], az = positions[a + 2];
    const bx = positions[b], by = positions[b + 1], bz = positions[b + 2];
    const cx = positions[c], cy = positions[c + 1], cz = positions[c + 2];
    sum += ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx);
  }
  return sum / 6;
}

export function buildKernelMeshData({ positions, indices, normals }) {
  const vertices = positions instanceof Float32Array ? positions : new Float32Array(positions);
  let index = indices;
  if (!index) {
    index = new Uint32Array(vertices.length / 3);
    for (let i = 0; i < index.length; i += 1) index[i] = i;
  }
  return {
    vertices,
    indices: index instanceof Uint32Array ? index : new Uint32Array(index),
    normals: normals ? (normals instanceof Float32Array ? normals : new Float32Array(normals)) : new Float32Array(vertices.length),
    colors: new Float32Array(0),
    edge_indices: new Uint32Array(0),
    bounds: boundsOf(vertices),
    parts: []
  };
}
