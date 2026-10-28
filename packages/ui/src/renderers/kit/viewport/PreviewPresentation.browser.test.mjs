import assert from 'node:assert/strict';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { chromium } from 'playwright';
import { PNG } from 'pngjs';

// Exercise presentation with real bitmap/canvas/CSS layout, without GPU tracing.
const fixture = `
import { createProgressivePreview } from './packages/core/src/lib/preview-tracing/progressivePreview.js';
import { capturePreview } from './packages/ui/src/renderers/kit/viewport/capturePreview.js';
const host = document.querySelector('#view'), raster = document.createElement('canvas');
raster.width = 400; raster.height = 300; host.append(raster);
const vector = values => ({toArray:()=>values});
const camera = {type:'PerspectiveCamera',position:vector([0,0,10]),quaternion:vector([0,0,0,1]),scale:vector([1,1,1]),
projectionMatrix:vector([1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]),projectionMatrixInverse:vector([1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]),aspect:4/3};
let worker, hidden = false;
Object.defineProperty(document,'visibilityState',{configurable:true,get:()=>hidden?'hidden':'visible'});
Object.defineProperty(navigator,'gpu',{configurable:true,value:{}});
const runtime = {renderer:{domElement:raster},previewConfiguration:{},drawCurrentFrame(){}};
const controller = createProgressivePreview(runtime, {requestRender(){},snapshotScene:async()=>({payload:{},transfers:[],sourceMeshes:[]}),
workerFactory:()=>worker={postMessage(message){if(message.type==='probe')queueMicrotask(()=>this.onmessage({data:{type:'probe',limits:{}}}));if(message.type==='init')window.ready=true;},terminate(){}}});
runtime.progressivePreview = controller;
window.fixture = {
async frame(width,height){const image=document.createElement('canvas');image.width=width;image.height=height;const ctx=image.getContext('2d');ctx.fillStyle='#08182e';ctx.fillRect(0,0,width,height);ctx.fillStyle='#fff';ctx.beginPath();ctx.arc(width/2,height/2,50,0,Math.PI*2);ctx.fill();const bitmap=await createImageBitmap(image);worker.onmessage({data:{type:'frame',bitmap,progress:{state:'ready',motion:true}}});},
resize(width,height){host.style.width=width+'px';host.style.height=height+'px';raster.width=width;raster.height=height;camera.aspect=width/height;controller.render(null,null,camera);},
hide(value){hidden=value;document.dispatchEvent(new Event('visibilitychange'));},
async capture(){const blob=await capturePreview(runtime);return Array.from(new Uint8Array(await blob.arrayBuffer()));},
dispose(){controller.dispose();}
};
controller.render(null,null,camera);
`;
function whiteBounds(bytes) {
  const png = PNG.sync.read(bytes);
  let minX = png.width, minY = png.height, maxX = -1, maxY = -1;
  for (let y = 0; y < png.height; y++) for (let x = 0; x < png.width; x++) {
    const at = (y * png.width + x) * 4;
    if (png.data[at] > 245 && png.data[at + 1] > 245 && png.data[at + 2] > 245 && png.data[at + 3] > 245) {
      minX = Math.min(minX, x); minY = Math.min(minY, y); maxX = Math.max(maxX, x); maxY = Math.max(maxY, y);
    }
  }
  assert.ok(maxX > minX && maxY > minY, 'the presented circle remains visible');
  return { width: maxX - minX + 1, height: maxY - minY + 1 };
}
function assertCircle(bytes) {
  const { width, height } = whiteBounds(bytes);
  assert.ok(Math.abs(width - height) <= 2, `circle stretched to ${width} by ${height}`);
}

test('a retained Preview bitmap and capture keep their aspect through resize, hide and replacement', async t => {
  const root = fileURLToPath(new URL('../../../../../../', import.meta.url));
  const result = await build({ stdin: { contents: fixture, resolveDir: root, sourcefile: 'presentation-fixture.js' },
    bundle: true, write: false, format: 'esm', platform: 'browser', target: 'esnext' });
  const browser = await chromium.launch({ headless: true, args: ['--disable-gpu'] });
  t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 900, height: 1000 }, deviceScaleFactor: 1 });
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => route.fulfill(route.request().url().endsWith('/fixture.js')
    ? { contentType: 'text/javascript', body: Buffer.from(result.outputFiles[0].contents) }
    : { contentType: 'text/html', body: '<!doctype html><style>body{margin:0;background:#080c12}#view{position:relative;width:400px;height:300px;background:#831b3a}</style><div id="view"></div><script type="module" src="/fixture.js"></script>' }));
  await page.goto('http://preview.test/', { waitUntil: 'domcontentloaded' }); await page.waitForFunction(() => window.ready);
  await page.evaluate(() => window.fixture.frame(400, 300));
  assertCircle(await page.screenshot());
  await page.evaluate(() => { window.fixture.hide(true); window.fixture.resize(842, 927); });
  assertCircle(await page.screenshot());
  await page.evaluate(() => window.fixture.hide(false));
  assertCircle(await page.screenshot());
  assertCircle(Buffer.from(await page.evaluate(() => window.fixture.capture())));
  await page.evaluate(() => window.fixture.frame(842, 927));
  assertCircle(await page.screenshot());
  assertCircle(Buffer.from(await page.evaluate(() => window.fixture.capture())));
  assert.deepEqual(errors, []); await page.evaluate(() => window.fixture.dispose());
});
