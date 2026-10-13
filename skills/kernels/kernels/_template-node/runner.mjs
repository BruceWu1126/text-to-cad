// Template JavaScript kernel: copy this folder to kernels/<your-id>/, set "id"
// in kernel.json to the folder name, and replace build(). It gets the model
// recorded as a CSG program (references/program.md) and returns a mesh;
// runMeshKernel writes the STL and prints the result line. This placeholder
// fills the part's bounding box.
import { buildKernelMeshData, meshVolume } from "../_shared/meshData.mjs";
import { runMeshKernel } from "../_shared/meshRunner.mjs";
import { nodeBounds } from "../_shared/program.mjs";

const BOX_TRIANGLES = [0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7, 0, 1, 5, 0, 5, 4, 1, 2, 6, 1, 6, 5, 2, 3, 7, 2, 7, 6, 3, 0, 4, 3, 4, 7];

function build(program) {
  const { min, max } = nodeBounds(program.root);
  const positions = [];
  for (const z of [min[2], max[2]]) {
    positions.push(min[0], min[1], z, max[0], min[1], z, max[0], max[1], z, min[0], max[1], z);
  }
  const meshData = buildKernelMeshData({ positions, indices: BOX_TRIANGLES });
  return {
    meshData,
    volume: meshVolume(meshData.vertices, meshData.indices),
    triangles: BOX_TRIANGLES.length / 3,
    warnings: ["template kernel: shows the bounding box"]
  };
}

await runMeshKernel(build);
