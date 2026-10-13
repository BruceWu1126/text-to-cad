// The runner contract for JavaScript mesh kernels (references/plugins.md):
//
//   node runner.mjs <program.json> <out.stl>
//
// reads the recorded program, builds it with the kernel's build(program),
// writes a binary STL, and prints one JSON line last:
// {"ok", "volume", "triangles", "warnings"} or {"ok": false, "error"}.
import fs from "node:fs";

export function binaryStl({ vertices, indices }) {
  const count = indices.length / 3;
  const buffer = Buffer.alloc(84 + 50 * count);
  buffer.writeUInt32LE(count, 80);
  let offset = 84;
  for (let t = 0; t < count; t += 1) {
    const corners = [0, 1, 2].map((k) => 3 * indices[3 * t + k]);
    const [a, b, c] = corners.map((i) => [vertices[i], vertices[i + 1], vertices[i + 2]]);
    const u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
    const v = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
    const n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]];
    const length = Math.hypot(...n) || 1;
    for (const value of [...n.map((x) => x / length), ...a, ...b, ...c]) {
      buffer.writeFloatLE(value, offset);
      offset += 4;
    }
    offset += 2;
  }
  return buffer;
}

export async function runMeshKernel(build, argv = process.argv.slice(2)) {
  let report;
  try {
    if (argv.length !== 2) throw new Error("usage: runner.mjs <program.json> <out.stl>");
    const [programPath, stlPath] = argv;
    const program = JSON.parse(fs.readFileSync(programPath, "utf8"));
    const result = await build(program);
    fs.writeFileSync(stlPath, binaryStl(result.meshData));
    report = { ok: true, volume: result.volume, triangles: result.triangles, warnings: result.warnings || [] };
  } catch (error) {
    report = { ok: false, error: error instanceof Error ? error.message : String(error) };
    process.exitCode = 1;
  }
  process.stdout.write(`\n${JSON.stringify(report)}\n`);
}
