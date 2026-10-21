// Drive the three.js path tracer headless (Chromium, WebGPU on Metal) through a shot file.
// usage: node render.mjs SHOT_NAME OUT_DIR SPP SCALE A:B FRAME_BUDGET ENV_ROT_SIGN
import { chromium } from "/Users/jakefitzgerald/robots/text-to-cad/node_modules/playwright/index.mjs";
import fs from "node:fs";
const [name, outDir, spp, scale, range, budget, envSign] = process.argv.slice(2);
fs.mkdirSync(outDir, { recursive: true });
const T0 = Date.now();
const browser = await chromium.launch({ channel: "chromium", headless: true,
  args: ["--enable-unsafe-webgpu", "--enable-gpu", "--ignore-gpu-blocklist", "--use-angle=metal"] });
const page = await browser.newPage();
const logs = [];
page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") logs.push(m.text().slice(0, 300)); });
page.on("pageerror", (e) => logs.push("PAGEERROR " + String(e).slice(0, 300)));
await page.goto("http://127.0.0.1:8765/scripts/three/render.html");
await page.waitForFunction(() => window.ready === true, null, { timeout: 60000 });
const setup = await page.evaluate(([u, s, b, e]) => window.setup(u, s, b, e),
  [`/shots/${name}.json`, Number(scale), Number(budget), Number(envSign)]);
console.log("SETUP", JSON.stringify(setup));
const [a, b] = range.split(":").map(Number);
const times = [];
for (let f = a; f < b; f++) {
  const t = Date.now();
  const r = await page.evaluate(([f, spp]) => window.renderFrame(f, spp), [f, Number(spp)]);
  fs.writeFileSync(`${outDir}/${String(f).padStart(4, "0")}.f16`, Buffer.from(r.b64, "base64"));
  const ms = Date.now() - t;
  times.push({ f, ms, trace_ms: r.trace_ms, readback_ms: r.readback_ms, samples: r.samples, calls: r.calls });
  console.log("FRAME", f, ms, "ms", JSON.stringify({ trace: r.trace_ms, readback: r.readback_ms, samples: r.samples, avg: r.avg, calls: r.calls, ch: r.channels, t: r.ctor }));
}
fs.writeFileSync(`${outDir}/meta.json`, JSON.stringify({ name, W: setup.W, H: setup.H, spp: Number(spp), setup, times,
  total_s: (Date.now() - T0) / 1000, chromium: browser.version(), logs: logs.slice(0, 20) }, null, 1));
if (logs.length) console.log("LOGS", logs.slice(0, 8).join("\n"));
await browser.close();
