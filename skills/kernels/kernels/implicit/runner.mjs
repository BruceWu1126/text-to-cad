// Implicits: the recorded program as one signed distance field, meshed with
// surface nets (sdf.mjs). Pure JavaScript, no dependencies.
import { runMeshKernel } from "../_shared/meshRunner.mjs";
import { buildImplicitMesh } from "./sdf.mjs";

await runMeshKernel(buildImplicitMesh);
