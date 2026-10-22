// Refresh the hero's GLB from the repo model. The hero plays the planetary gear as one GLB:
// the model's document exported by `cadgen export`, its parts and colours, and its
// `meshCycle` motion baked into the clip the page plays. It is served as a plain static file,
// so production (Vercel) needs no backend and no Git LFS. Run it after changing the model:
//
//   node apps/docs/scripts/sync-hero-glb.mjs
//
// It builds the model (`python <model>.py`), exports the document it wrote to
// public/hero/planetary.glb, and runs the check the docs CI runs on it. PYTHON_BIN names the Python
// that has cadgen's dependencies (default: the repository's .venv); this checkout's own cadgen
// (packages/cadgen/src) goes first on its path, so a worktree builds and exports with its own engine.

import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import { HERO_GLB, HERO_MODEL, checkHeroGlb, repoRoot } from "./check-hero-glb.mjs";

const python = process.env.PYTHON_BIN || path.join(repoRoot, ".venv/bin/python");
const env = {
  ...process.env,
  PYTHONPATH: [path.join(repoRoot, "packages/cadgen/src"), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
};
const run = (args) => execFileSync(python, args, { cwd: repoRoot, env, stdio: "inherit" });

// The model's script declares its document; the export reads that document.
const script = path.join(repoRoot, HERO_MODEL);
const declared = /@xbf\(out="([^"]+)"\)/.exec(fs.readFileSync(script, "utf8"));
if (!declared) {
  throw new Error(`${HERO_MODEL} declares no @xbf(out=...) document to export`);
}
const document = path.resolve(path.dirname(script), declared[1]);

run([script]);
run(["-m", "cadgen.cli", "export", document, HERO_GLB]);
const checked = await checkHeroGlb();
console.log(`Hero GLB written: ${path.relative(repoRoot, HERO_GLB)}, ${checked.bytes} bytes, ${checked.clip} turns ${checked.turned} parts.`);
