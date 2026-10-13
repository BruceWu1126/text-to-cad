// Manifold: the recorded program built with Manifold's robust mesh booleans
// (csg.mjs). Its package lives in the kernel's runtime folder,
// made by setup.sh, not beside this file: the skill ships no runtime.
//
//   node runner.mjs <runtime> <program.json> <out.stl>
import fs from "node:fs";
import path from "node:path";
import { register } from "node:module";
import { pathToFileURL } from "node:url";
import { runMeshKernel } from "../_shared/meshRunner.mjs";

const [runtime, ...rest] = process.argv.slice(2);
if (!runtime || !fs.existsSync(path.join(runtime, "node_modules", "manifold-3d"))) {
  process.stdout.write(`\n${JSON.stringify({ ok: false, error: "Manifold package missing: run the manifold kernel's setup.sh" })}\n`);
  process.exit(1);
}
// Bare imports ("manifold-3d") resolve from the runtime folder.
register("../_shared/resolveFrom.mjs", import.meta.url, { data: { parentURL: pathToFileURL(path.join(path.resolve(runtime), "package.json")).href } });
const { buildManifoldMesh } = await import("./csg.mjs");
await runMeshKernel(buildManifoldMesh, rest);
