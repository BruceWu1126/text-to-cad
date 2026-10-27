// Refresh the hero's GLB from the repo model. The hero plays the planetary gear as one GLB:
// the model's document exported by `cadgen export`, its parts and colours, and its
// `meshCycle` motion baked into the clip the page plays. It is served as a plain static file,
// so production (Vercel) needs no backend and no Git LFS. Run it after changing the model:
//
//   node apps/docs/scripts/sync-hero-glb.mjs
//
// It builds the model and exports its document (`cadgen.export(model, path)`) to
// public/hero/planetary.glb, then checks what it wrote. PYTHON_BIN names the Python
// that has cadgen's dependencies (default: the repository's .venv); this checkout's own cadgen
// (packages/cadgen/src) goes first on its path, so a worktree builds and exports with its own engine.
//
// It is a hand step, and it reads the sample corpus (models/) on purpose: nothing in the docs
// app's build or check does, so the site ships the committed GLB as it is. The check below holds
// the new file to the model's source (the motion it declares, by name and duration) and to the page
// (the clip it plays), then reads it through @text-to-cad/core's GLB path, the one the page and the
// CAD Viewer read a GLB with, and plays the clip there.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import * as THREE from "three";
import {
  createGlbAnimationRuntime,
  disposeGlbAnimationRuntime,
  setGlbAnimationTime,
} from "@text-to-cad/core/lib/render/glbAnimationRuntime.js";
import { buildGlbDocumentFromBuffer, isPlayableGlbAnimationClip } from "@text-to-cad/core/lib/render/glbMeshData.js";

const docsRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = path.resolve(docsRoot, "..", "..");
// The model the hero shows, and the file its export is served from.
const HERO_MODEL = "models/assemblies/src/planetary_gear_assembly/planetary_gear_assembly.py";
const HERO_GLB = path.join(docsRoot, "public/hero/planetary.glb");
const HERO_PAGE = path.join(docsRoot, "src/components/hero-glb-render.tsx");
// Every tracked file stays under 5 MiB (scripts/github-workflows/check-builds.sh).
const MAX_BYTES = 5 * 1024 * 1024;
const GLB_MAGIC = 0x46546c67; // "glTF"
const JSON_CHUNK = 0x4e4f534a; // "JSON"
const BIN_CHUNK = 0x004e4942; // "BIN\0"

// The motion the model declares: `configuration_sequence(assembly, "meshCycle", ..., duration=6, ...)`. Its clip is the
// motion once; the page repeats it.
function declaredMotion(source) {
  const call = /\bconfiguration_sequence\(\s*\w+\s*,\s*"([^"]+)"([\s\S]*?)\)\s*$/m.exec(source);
  assert.ok(call, `${HERO_MODEL} declares no motion for the hero to play`);
  const [, name, rest] = call;
  const duration = /\bduration\s*=\s*([0-9.]+)/.exec(rest);
  assert.ok(duration, `${HERO_MODEL}: configuration sequence ${name} names no duration`);
  return { name, duration: Number(duration[1]) };
}

// The GLB container: its JSON and its binary chunk.
function readGlb(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  assert.equal(view.getUint32(0, true), GLB_MAGIC, `${HERO_GLB} is not a GLB`);
  assert.equal(view.getUint32(4, true), 2, `${HERO_GLB} is not glTF 2.0`);
  assert.equal(view.getUint32(8, true), bytes.byteLength, `${HERO_GLB} is truncated`);
  const jsonLength = view.getUint32(12, true);
  assert.equal(view.getUint32(16, true), JSON_CHUNK, `${HERO_GLB} has no JSON chunk first`);
  const gltf = JSON.parse(new TextDecoder().decode(bytes.subarray(20, 20 + jsonLength)));
  assert.equal(view.getUint32(20 + jsonLength + 4, true), BIN_CHUNK, `${HERO_GLB} has no binary chunk`);
  return gltf;
}

async function checkHeroGlb() {
  assert.ok(fs.existsSync(HERO_GLB), `${HERO_GLB} was not written`);
  const bytes = new Uint8Array(fs.readFileSync(HERO_GLB));
  assert.ok(bytes.byteLength < MAX_BYTES, `${HERO_GLB} is ${bytes.byteLength} bytes, over the repository's 5 MiB`);

  const motion = declaredMotion(fs.readFileSync(path.join(repoRoot, HERO_MODEL), "utf8"));
  const played = /const HERO_CLIP = "([^"]+)"/.exec(fs.readFileSync(HERO_PAGE, "utf8"));
  assert.ok(played, `${HERO_PAGE} names no HERO_CLIP`);
  assert.equal(played[1], motion.name, `the page plays ${played[1]}, and ${HERO_MODEL} declares ${motion.name}`);

  // The file as cadgen's export writes it: its generator, and the motion as a clip of its name, its
  // duration as the model declares it, every channel on a node of the file.
  const gltf = readGlb(bytes);
  assert.equal(gltf.asset?.generator, "cadgen engine", `${HERO_GLB} was not written by cadgen export`);
  const clip = (gltf.animations || []).find((animation) => animation.name === motion.name);
  assert.ok(clip, `${HERO_GLB} has no ${motion.name} animation`);
  assert.equal(clip.extras?.cadgen?.duration, motion.duration, `${motion.name}'s duration is not the model's`);
  assert.ok(clip.channels.length > 0, `${motion.name} moves nothing`);
  for (const channel of clip.channels) {
    assert.ok(gltf.nodes?.[channel.target.node], `${motion.name} animates node ${channel.target.node}, which the file lacks`);
  }

  // Read and played as the page reads and plays it.
  const document = await buildGlbDocumentFromBuffer(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength), {});
  const playable = document.clips.find((candidate) => candidate.name === motion.name);
  assert.ok(playable && isPlayableGlbAnimationClip(playable), `the page cannot play ${motion.name}`);
  assert.ok(document.animatedBounds, `${HERO_GLB} has no bounds over its clip`);
  const runtime = createGlbAnimationRuntime(THREE, document.scene, playable);
  const before = new Map();
  setGlbAnimationTime(runtime, 0);
  document.scene.traverse((object) => before.set(object, object.quaternion.clone()));
  setGlbAnimationTime(runtime, playable.duration / 7);
  let turned = 0;
  document.scene.traverse((object) => {
    if (before.get(object).angleTo(object.quaternion) > 1e-3) turned += 1;
  });
  disposeGlbAnimationRuntime(runtime);
  assert.ok(turned > 0, `${motion.name} turns nothing on the page`);
  return { bytes: bytes.byteLength, clip: motion.name, duration: motion.duration, turned };
}

const python = process.env.PYTHON_BIN || path.join(repoRoot, ".venv/bin/python");
const env = {
  ...process.env,
  PYTHONPATH: [path.join(repoRoot, "packages/cadgen/src"), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter),
};
const run = (args) => execFileSync(python, args, { cwd: repoRoot, env, stdio: "inherit" });

// The model's own script builds it, and `cadgen.export` writes the GLB from its document, the
// motions baked as clips: the model function itself, run first (a replay when it is current).
const script = path.join(repoRoot, HERO_MODEL);
const exporter = [
  "import sys",
  "from pathlib import Path",
  "from cadgen import export",
  "script = Path(sys.argv[1])",
  "sys.path.insert(0, str(script.parent))",
  "model = getattr(__import__(script.stem), script.stem)",
  "export(model, sys.argv[2])",
].join("\n");

run(["-c", exporter, script, HERO_GLB]);
const checked = await checkHeroGlb();
console.log(`Hero GLB written: ${path.relative(repoRoot, HERO_GLB)}, ${checked.bytes} bytes, ${checked.clip} turns ${checked.turned} parts.`);
