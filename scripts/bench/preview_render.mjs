#!/usr/bin/env node
// Manual visual review of the production Preview path. No corpus rendering runs in CI.
// Uses the same FileViewer/GLB renderer, studio, camera, capture and progressive controller.
import { createServer } from 'node:http';
import { readFile, readdir, mkdir, writeFile } from 'node:fs/promises';
import { resolve, join, basename, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { build } from 'esbuild';
import { chromium } from 'playwright';

const root = fileURLToPath(new URL('../../', import.meta.url));
const options = { width: 1200, height: 900, reference: 512, files: [], quality: 'final' };
for (let i = 2; i < process.argv.length; i++) {
  const key = process.argv[i];
  if (key === '--corpus') options.corpus = resolve(process.argv[++i]);
  else if (key === '--out') options.out = resolve(process.argv[++i]);
  else if (key === '--file') options.files.push(process.argv[++i]);
  else if (key === '--reference-samples') options.reference = Number(process.argv[++i]);
  else if (key === '--width') options.width = Number(process.argv[++i]);
  else if (key === '--height') options.height = Number(process.argv[++i]);
  else if (key === '--quality') options.quality = process.argv[++i];
  else if (key === '--profiles') options.profiles = process.argv[++i].split(',');
  else if (key === '--transparent') options.transparent = true;
  else if (key === '--fit-rest') options.fitRest = true;
  else if (key === '--native-url') options.nativeURL = process.argv[++i];
  else if (key === '--exercise-lifecycle') options.lifecycle = true;
  else if (key === '--exercise-pointer') options.pointer = true;
  else if (key === '--skip-reference') options.reference = 0;
  else throw new Error(`Unknown option ${key}`);
}
if ((!options.corpus&&!options.nativeURL) || !options.out) throw new Error('Usage: node scripts/bench/preview_render.mjs --corpus tmp/corpus/glb --out tmp/corpus/review [--file name.glb] [--skip-reference]');
if (!['preview', 'final'].includes(options.quality)) throw new Error('--quality is preview or final');
for (const [key, min, max] of [['width',256,2400],['height',256,1800]]) {
  if(!Number.isInteger(options[key])||options[key]<min||options[key]>max)throw new Error(`--${key} must be an integer between ${min} and ${max}`);
}
if(!Number.isInteger(options.reference)||options.reference<0||options.reference>1024)throw new Error('--reference-samples must be an integer 1..1024; use --skip-reference to omit it');
if(options.reference===0&&options.profiles?.includes('reference'))throw new Error('A reference profile requires --reference-samples 1..1024');
const outputRelative=relative(root,options.out);
if(!outputRelative.startsWith('..')&&outputRelative.split(/[\\/]/)[0]!=='tmp')throw new Error('Repository review outputs must be under ignored tmp/');
await mkdir(options.out, {recursive: true});
if(options.nativeURL){await renderNative(options);process.exit(0);}
const files = options.files.length ? options.files : (await readdir(options.corpus)).filter(n => n.endsWith('.glb')).sort();
if(!files.length||files.some(name=>basename(name)!==name||!name.endsWith('.glb')))throw new Error('The corpus must contain GLB basenames');
const corpus = new Map(await Promise.all(files.map(async name => [name, await readFile(join(options.corpus, name))])));
const actualController = join(root, 'packages/core/dist/lib/preview-tracing/progressivePreview.js');
// Inject diagnostic constructor options at the module boundary; the renderer and
// implementation remain production code. No source string is rewritten.
const diagnosticFactory = {
  name: 'preview-review-factory',
  setup(b) {
    b.onResolve({filter:/progressivePreview\.js\?actual$/}, () => ({path:actualController}));
    b.onResolve({filter:/preview-tracing\/progressivePreview\.js$/}, () => ({path:'controller',namespace:'preview-review'}));
    b.onLoad({filter:/.*/,namespace:'preview-review'}, () => ({loader:'js', resolveDir:root, contents:`
      import {createProgressivePreview as production} from ${JSON.stringify(actualController+'?actual')};
      import {inspectPreviewScene,groupPreviewMeshes} from ${JSON.stringify(join(root,'packages/core/dist/lib/preview-tracing/scene.js'))};
      export function createProgressivePreview(runtime, options) {
        const controller = production(runtime, {...options, ...globalThis.__previewReviewOptions});
        globalThis.__previewReviewController = controller;
        globalThis.__previewReviewRuntime = runtime;
        globalThis.__previewReviewAdmission = () => {
          const a=inspectPreviewScene(runtime.modelGroup), g=groupPreviewMeshes(a.meshes,a.canonical);
          return {placements:a.meshes.length,uniqueGeometries:a.geometries.size,uniqueMaterials:a.materials.size,triangles:a.triangles,vertices:a.vertices,materialGroups:a.groups,batchedGroups:g.length,largestBatch:Math.max(0,...g.map(x=>x.meshes.length)),cloneBytes:a.cloneBytes,reason:a.reason};
        };
        return controller;
      }` }));
  }
};
const bundled = await build({entryPoints:[join(root,'packages/ui/src/renderers/harness/index.tsx')],bundle:true,write:false,format:'esm',platform:'browser',jsx:'automatic',target:'esnext',plugins:[diagnosticFactory],loader:{'.webp':'dataurl','.avif':'dataurl','.woff2':'dataurl','.svg':'dataurl'}});
const javascript = bundled.outputFiles.find(f => f.path.endsWith('.js'))?.contents || bundled.outputFiles[0].contents;
const css = await readFile(join(root,'packages/ui/dist/styles.css'));
const workers = new Map();
for (const relative of ['node_modules/three-mesh-bvh/src/workers/generateMeshBVH.worker.js','packages/core/dist/lib/render/glbMeshWorker.js','packages/core/dist/lib/viewer/raycastBvhWorker.js','packages/ui/dist/renderers/kit/look/studioEnvironment.worker.js']) {
  const result = await build({entryPoints:[join(root,relative)],bundle:true,write:false,format:'esm',platform:'browser'});
  workers.set(basename(relative),result.outputFiles[0].contents);
}
const weights = await readFile(join(root,'packages/core/dist/lib/preview-tracing/weights/rt_hdr_calb_cnrm.tza'));
const hash = value => createHash('sha256').update(value).digest('hex');
const provenance = { rendererBundleSHA256:hash(javascript),weightsSHA256:hash(weights) };
const server = createServer((request,response) => {
  const url = new URL(request.url,'http://review'), name = basename(url.pathname);
  if (name === 'review.js') {response.setHeader('Content-Type','text/javascript');response.end(javascript);}
  else if (name === 'styles.css') {response.setHeader('Content-Type','text/css');response.end(css);}
  else if (workers.has(name)) {response.setHeader('Content-Type','text/javascript');response.end(workers.get(name));}
  else if (name === 'rt_hdr_calb_cnrm.tza') {response.setHeader('Content-Type','application/octet-stream');response.end(weights);}
  else if (name === 'catalog') {response.setHeader('Content-Type','application/json');response.end(JSON.stringify({entries:[...corpus].map(([file,data]) => ({kind:'glb',file:`/models/${file}`,url:`/${file}`,hash:`review-${file}-${data.length}`,bytes:data.length}))}));}
  else if (name === 'server') {response.setHeader('Content-Type','application/json');response.end('{"backend":"cadgen"}');}
  else if (corpus.has(name)) {response.setHeader('Content-Type','model/gltf-binary');response.end(corpus.get(name));}
  else if (url.pathname === '/') {response.setHeader('Content-Type','text/html');response.end(`<!doctype html><link rel="stylesheet" href="/styles.css"><style>body{margin:0}#root>div{width:${options.width}px!important;height:${options.height}px!important}</style><div id="root"></div><script type="module" src="/review.js"></script>`);}
  else {response.statusCode=404;response.end();}
});
await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({headless:true,args:process.platform==='darwin'?['--use-angle=metal']:[]});
const rows = await readFile(join(options.out,'renders.json'),'utf8').then(s=>JSON.parse(s).rows||[]).catch(()=>[]);
let current;

async function capture(page,path,profile) {
  const encoded = await page.evaluate(async ({transparent,profile}) => {
    // The host screenshot intentionally paints its CSS backdrop. Alpha review
    // needs the production controller's unflattened foreground instead.
    const foreground=transparent?window.__previewReviewController?.captureCanvas:null;
    if(foreground)return foreground.toDataURL('image/png').split(',')[1];
    if(transparent&&profile!=='raster')throw new Error('Transparent candidate/reference foreground is unavailable');
    const blob = await window.cadHarness.a.controller.capture();
    const data = new Uint8Array(await blob.arrayBuffer());
    let binary='';for(const byte of data)binary+=String.fromCharCode(byte);
    return btoa(binary);
  },{transparent:Boolean(options.transparent),profile});
  await writeFile(path,Buffer.from(encoded,'base64'));
}

async function render(file,profile) {
  current={file,profile,progress:null};
  const page = await browser.newPage({viewport:{width:options.width,height:options.height},deviceScaleFactor:1});
  const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('console',e=>{if(e.type()==='error')console.error(`${file}/${profile}: ${e.text()}`);});
  await page.addInitScript(({file,profile,options})=>{
    window.__previewReviewOptions = profile==='reference' ? {diagnostic:{rawReferenceSamples:options.reference}} : {};
    if(profile==='raster')Object.defineProperty(navigator,'gpu',{value:undefined});
    window.__cadTabRecord={version:2,settings:{appearance:'light'},files:{[JSON.stringify([`/models/${file}`,'glb'])]:{version:2,camera:null,display:{mode:'solid',appearance:'light'},preview:{lighting:{quality:options.quality},background:{color:'#ededeb',opacity:options.transparent?0:1},floor:{enabled:true,color:'#d9d9d7',opacity:options.transparent?0.6:1,finish:'matte'}},playback:{autoplay:false},renderer:{}}}};
    window.__reviewTasks=[];
    new PerformanceObserver(list=>window.__reviewTasks.push(...list.getEntries().map(e=>({start:e.startTime,duration:e.duration})))).observe({type:'longtask'});
  },{file,profile,options});
  const started = Date.now();
  await page.goto(`${origin}/?file=${encodeURIComponent(file)}`);
  await page.waitForFunction(()=>window.cadHarness?.a.controller?.readState().loading===false,null,{timeout:60000});
  await page.getByTestId('one').getByRole('button',{name:'Preview',exact:true}).click();
  await page.waitForFunction(()=>window.__cadStage()?.studioGround,null,{timeout:60000});
  await page.waitForFunction(()=>window.__previewReviewRuntime,null,{timeout:60000});
  console.log(`${file}/${profile} loaded ${JSON.stringify(await page.evaluate(()=>window.__previewReviewAdmission()))}`);
  if(options.fitRest)await page.evaluate(async()=>{
    const r=window.__previewReviewRuntime,T=r.THREE;
    r.modelGroup.updateWorldMatrix(true,true);
    const box=new T.Box3().setFromObject(r.modelGroup),sphere=box.getBoundingSphere(new T.Sphere());
    const c=window.__cadCamera(),center=sphere.center.toArray(),direction=c.position.map((v,i)=>v-c.target[i]),length=Math.hypot(...direction);
    const distance=sphere.radius/Math.sin((r.camera.fov||38)*Math.PI/360)*1.08;
    await window.cadHarness.a.controller.setCamera({...c,target:center,position:center.map((v,i)=>v+direction[i]/length*distance)});
  });
  let firstSaved=false,last='';
  const milestones=[];
  for (;;) {
    const progress = await page.evaluate(()=>({trace:window.__previewReviewController?.progress,studio:window.__cadStage()?.studioFrame}));
    current={file,profile,elapsed_ms:Date.now()-started,progress};
    const p=progress.trace;
    const stamp=JSON.stringify({state:p?.state,sampleBucket:Math.floor((p?.sample||0)/16),denoise:p?.denoise,denoisedSamples:p?.denoisedSamples,studioDone:progress.studio?.sample>=progress.studio?.samples});
    if(stamp!==last){console.log(`${file}/${profile} ${(Date.now()-started)/1000}s ${stamp}`);last=stamp;}
    if(profile==='raster') {
      if(progress.studio?.sample>=progress.studio?.samples)break;
    } else {
      if(progress.trace?.state==='fallback')throw new Error(`${file}/${profile}: ${JSON.stringify(progress.trace)}`);
      if(progress.trace?.budgetExhausted)throw new Error(`${file}/${profile}: sample budget exhausted before convergence`);
      if(progress.trace?.denoise==='failed')throw new Error(`${file}/${profile}: denoising failed`);
      if(profile==='candidate'&&!firstSaved&&progress.trace?.denoisedSamples>=32){await capture(page,join(options.out,`${basename(file,'.glb')}-first-denoised.png`),profile);firstSaved=true;milestones.push({name:'first-denoised',elapsed_ms:Date.now()-started,progress:progress.trace});}
      if(progress.trace?.sample>=progress.trace?.samples&&(profile==='reference'||progress.trace?.denoisedSamples>=progress.trace?.samples))break;
    }
    if(Date.now()-started>600000)throw new Error(`${file}/${profile}: render did not finish`);
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  }
  await capture(page,join(options.out,`${basename(file,'.glb')}-${profile}.png`),profile);
  const detail=await page.evaluate(()=>({camera:window.__cadCamera(),stage:window.__cadStage(),progress:window.__previewReviewController?.progress,tasks:window.__reviewTasks,configuration:window.__previewReviewRuntime?.previewConfiguration,admission:window.__previewReviewAdmission()}));
  const row={file,profile,captureMode:options.transparent&&profile!=='raster'?'controller-foreground':'host-composited',elapsed_ms:Date.now()-started,...detail,milestones,errors,...provenance,fixtureSHA256:hash(corpus.get(file))};
  const previous=rows.findIndex(row=>row.file===file&&row.profile===profile);
  if(previous>=0)rows.splice(previous,1);
  rows.push(row);
  await writeFile(join(options.out,'renders.json'),JSON.stringify({options,rows},null,2)+'\n');
  await page.close();
  return row;
}
try {
  const profiles=options.profiles||['raster','candidate',...(options.reference?['reference']:[])];
  if(profiles.some(p=>!['raster','candidate','reference'].includes(p)))throw new Error('Profiles: raster,candidate,reference');
  for(const file of files)for(const profile of profiles)await render(file,profile);
} catch(error) {
  await writeFile(join(options.out,'run-failure.json'),JSON.stringify({...current,error:String(error.stack||error)},null,2)+'\n');
  throw error;
} finally {
  await browser.close();await new Promise(resolve=>server.close(resolve));
}
console.log(`Review output: ${options.out}`);

async function renderNative(options){
  const browser=await chromium.launch({headless:true,args:process.platform==='darwin'?['--use-angle=metal']:[]});
  const page=await browser.newPage({viewport:{width:options.width,height:options.height},deviceScaleFactor:1,colorScheme:'light'});
  const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{window.__reviewTasks=[];new PerformanceObserver(list=>window.__reviewTasks.push(...list.getEntries().map(e=>({start:e.startTime,duration:e.duration})))).observe({type:'longtask'});});
  const started=Date.now(),milestones=[];
  const save=async name=>page.locator('[data-preview-view]').screenshot({path:join(options.out,name),style:'[data-preview-bar],[data-preview-playbar]{opacity:0!important;visibility:hidden!important}'});
  const waitImage=async(first=false)=>{
    let last='';
    for(;;){
      const progress=await page.evaluate(()=>window.__cadStage?.()?.pathTracing);
      const key=JSON.stringify({state:progress?.state,bucket:Math.floor((progress?.sample||0)/16),denoise:progress?.denoise,denoisedSamples:progress?.denoisedSamples});
      if(key!==last){console.log(`native ${(Date.now()-started)/1000}s ${JSON.stringify(progress)}`);last=key;}
      if(progress?.state==='fallback')throw new Error(`Native tracing fell back: ${JSON.stringify(progress)}`);
      if(progress?.budgetExhausted)throw new Error('Native sample budget exhausted before convergence');
      if(progress?.denoise==='failed')throw new Error('Native denoising failed');
      if(progress?.denoisedSamples>=32&&!milestones.some(m=>m.name==='first-denoised')){await save('native-first-denoised.png');milestones.push({name:'first-denoised',elapsed_ms:Date.now()-started,progress});}
      if(first?progress?.denoisedSamples>=32:progress?.sample>=progress?.samples&&progress?.denoisedSamples>=progress?.samples)return progress;
      if(Date.now()-started>900000)throw new Error('Native rendering did not finish');
      await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    }
  };
  try{
    await page.goto(options.nativeURL);
    await page.getByRole('button',{name:'Preview',exact:true}).waitFor({timeout:180000});
    await page.waitForFunction(()=>window.__cadViewerLoad?.complete===true,null,{timeout:180000});
    await page.getByRole('button',{name:'Preview',exact:true}).click();
    await page.getByRole('button',{name:'Preview settings',exact:true}).click();
    await page.getByRole('region',{name:'Preview settings',exact:true}).getByRole('combobox',{name:'Quality',exact:true}).click();
    await page.getByRole('option',{name:options.quality==='final'?'Final':'Preview',exact:true}).click();
    await page.getByRole('button',{name:'Preview settings',exact:true}).click();
    let progress=await waitImage(options.pointer);
    await save(options.pointer?'native-default-before-input.png':'native-candidate.png');
    const lifecycle={};
    if(options.pointer){
      const view=await page.locator('[data-preview-view]').boundingBox(),x=view.x+view.width/2,y=view.y+view.height/2;
      const beforeZoom=await page.evaluate(()=>window.__cadCamera());
      const zoomStart=Date.now();await page.mouse.move(x,y);await page.mouse.wheel(0,-150);
      await page.waitForFunction(before=>window.__cadCamera().position.some((v,i)=>Math.abs(v-before.position[i])>1e-6),beforeZoom);
      lifecycle.zoom_response_ms=Date.now()-zoomStart;
      await waitImage(true);lifecycle.zoom_first_denoised_ms=Date.now()-zoomStart;
      const beforeOrbit=await page.evaluate(()=>window.__cadCamera());
      await page.mouse.move(x,y);await page.mouse.down();const orbitStart=Date.now();
      await page.mouse.move(x+45,y+8,{steps:2});
      await page.waitForFunction(before=>window.__cadCamera().position.some((v,i)=>Math.abs(v-before.position[i])>1e-6),beforeOrbit);
      lifecycle.orbit_response_ms=Date.now()-orbitStart;
      lifecycle.duringOrbit=await page.evaluate(()=>({camera:window.__cadCamera(),stage:window.__cadStage(),canvases:[...document.querySelectorAll('[data-preview-view] canvas')].map(c=>({visibility:c.style.visibility,width:c.width,height:c.height}))}));
      await page.mouse.move(x+80,y+15,{steps:2});await page.mouse.up();
      await waitImage(true);lifecycle.orbit_first_denoised_ms=Date.now()-orbitStart;
      progress=await waitImage();await save('native-beauty.png');
    }
    if(options.lifecycle){
      const play=page.getByRole('button',{name:'Play animation',exact:true});
      lifecycle.motionAvailable=Boolean(await play.count());
      if(await play.count()){
        const view=await page.locator('[data-preview-view]').boundingBox();await page.mouse.move(view.width/2,view.height/2);
        await play.click();
        await page.waitForFunction(()=>Number(document.querySelector('[data-preview-playbar] [role=slider][aria-label="Animation time"]')?.getAttribute('aria-valuenow'))>0.1,null,{timeout:30000});
        lifecycle.moving=await page.evaluate(()=>({stage:window.__cadStage(),camera:window.__cadCamera()}));
        await page.getByRole('button',{name:'Pause animation',exact:true}).click();await waitImage(true);await save('native-motion-held.png');
      }
      const view=await page.locator('[data-preview-view]').boundingBox();await page.mouse.move(view.width/2,view.height/2);
      const exitStart=Date.now();await page.getByRole('button',{name:'Exit preview',exact:true}).click();await page.locator('[data-preview-view]').waitFor({state:'detached'});
      lifecycle.exit_ms=Date.now()-exitStart;lifecycle.afterExit=await page.evaluate(()=>({stage:window.__cadStage(),gpu:window.__cadGpuDevice?.()}));
      const reentry=Date.now();await page.getByRole('button',{name:'Preview',exact:true}).click();await waitImage(true);lifecycle.reentry_first_denoised_ms=Date.now()-reentry;await save('native-reentry.png');
    }
    const detail=await page.evaluate(()=>({camera:window.__cadCamera(),stage:window.__cadStage(),tasks:window.__reviewTasks}));
    await writeFile(join(options.out,'native.json'),JSON.stringify({elapsed_ms:Date.now()-started,progress,milestones,lifecycle,...detail,errors},null,2)+'\n');
  }catch(error){await writeFile(join(options.out,'native-failure.json'),JSON.stringify({error:String(error.stack||error),progress:await page.evaluate(()=>window.__cadStage?.()).catch(()=>null),errors},null,2)+'\n');throw error;}
  finally{await browser.close();}
}
