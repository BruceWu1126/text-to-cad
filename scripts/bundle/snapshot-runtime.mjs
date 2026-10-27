// The wheel's renderer is bundled from core's public, compiled entrypoint. Vite
// carries its workers, decoder and denoising weights just as it does for the viewer.
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { build } from "vite";
import { thirdPartyLicenses } from "@text-to-cad/core/third-party-licenses";

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const output = path.resolve(process.argv[2] || path.join(repo, "packages/cadgen/src/cadgen/_runtime/snapshot"));
await fs.mkdir(path.join(repo, "tmp"), { recursive: true });
const scratch = await fs.mkdtemp(path.join(repo, "tmp/snapshot-bundle-"));
const licenses = thirdPartyLicenses();
try {
  await fs.writeFile(path.join(scratch, "render.html"), `<!doctype html>
<html><head><meta charset="utf-8"><title>CAD snapshot renderer</title>
<style>html,body{margin:0;overflow:hidden;background:transparent}</style></head>
<body><script type="module" src="./entry.js"></script></body></html>`);
  await fs.writeFile(path.join(scratch, "entry.js"),
    'import "@text-to-cad/core/common/headlessRenderEntry.js";\n');
  await build({
    configFile: false,
    root: scratch,
    base: "./",
    plugins: [licenses.plugin()],
    resolve: { dedupe: ["three"] },
    build: {
      outDir: output,
      emptyOutDir: true,
      target: "es2022",
      sourcemap: false,
      chunkSizeWarningLimit: 1600,
      rolldownOptions: {
        input: path.join(scratch, "render.html"),
        output: { entryFileNames: "snapshot-render.js" },
      },
    },
    worker: { format: "es", plugins: licenses.worker },
  });
} finally {
  await fs.rm(scratch, { recursive: true, force: true });
}
