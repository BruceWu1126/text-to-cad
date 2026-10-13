// three.js: the recorded program as three.js primitives combined with
// three-bvh-csg (csg.mjs). Its packages live in the kernel's runtime folder,
// made by setup.sh, not beside this file: the skill ships no runtime.
//
//   node runner.mjs <runtime> <program.json> <out.stl>
import fs from "node:fs";
import path from "node:path";
import { register } from "node:module";
import { pathToFileURL } from "node:url";
import { runMeshKernel } from "../_shared/meshRunner.mjs";

const [runtime, ...rest] = process.argv.slice(2);
if (!runtime || !fs.existsSync(path.join(runtime, "node_modules", "three-bvh-csg"))) {
  process.stdout.write(`\n${JSON.stringify({ ok: false, error: "three.js packages missing: run the three kernel's setup.sh" })}\n`);
  process.exit(1);
}
// Bare imports ("three", "three-bvh-csg") resolve from the runtime folder.
register("../_shared/resolveFrom.mjs", import.meta.url, { data: { parentURL: pathToFileURL(path.join(path.resolve(runtime), "package.json")).href } });
const { buildThreeMesh } = await import("./csg.mjs");
await runMeshKernel(buildThreeMesh, rest);
