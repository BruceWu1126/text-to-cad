import * as THREE from 'three';
import { MeshBVH, SAH } from 'three-mesh-bvh';
import { reconstructPreviewScene } from './previewReconstruct.js';
import { createPreviewWorkerRenderer } from './previewWorkerRenderer.js';
import { planPreviewResources } from './resourcePolicy.js';
import { mergePreviewInput } from './previewWorkerInput.js';

let controller, owned, camera, runtime, scheduled = false, paused = false, disposed = false;
let awaitingFrame = false, missedFrame = false, presented, backdrop, composite;
let lastProgress = 0, stageStarted = performance.now(), admissionId = 0, profileKey = null;
let queuedInput = null, applyingInput = false, inferenceBounds = null;
const admissions = new Map();
function sendProgress(stage, extra = {}) {
  const now = performance.now();
  postMessage({ type: 'progress', stage, stageMs: now - stageStarted, ...extra, progress: controller?.progress });
  stageStarted = now; lastProgress = now;
}
function schedule() {
  if (scheduled || disposed || paused) return;
  scheduled = true;
  setTimeout(() => {
    scheduled = false;
    if (disposed || paused || !controller || !camera || applyingInput || (queuedInput && controller.progress.state === 'ready')) return;
    const result = controller.render(null, null, camera, { still: !runtime.moving, moving: runtime.moving });
    if (result.refining) schedule();
  }, 0);
}
function present() {
  if (!presented || !backdrop || disposed) return;
  if (awaitingFrame) { missedFrame = true; return; }
  composite ||= new OffscreenCanvas(1, 1);
  composite.width = presented.width; composite.height = presented.height;
  const context = composite.getContext('2d');
  context.clearRect(0, 0, composite.width, composite.height);
  context.drawImage(backdrop, 0, 0, composite.width, composite.height);
  context.drawImage(presented, 0, 0);
  const bitmap = composite.transferToImageBitmap();
  awaitingFrame = true; missedFrame = false;
  postMessage({ type: 'frame', bitmap, progress: controller.progress }, [bitmap]);
}
function updateCamera(value) {
  if (!camera || camera.type !== value.type) camera = value.type === 'OrthographicCamera' ? new THREE.OrthographicCamera() : new THREE.PerspectiveCamera();
  for (const key of ['near', 'far', 'zoom', 'fov', 'aspect', 'left', 'right', 'top', 'bottom', 'filmGauge', 'filmOffset']) if (value[key] != null && key in camera) camera[key] = value[key];
  camera.position.fromArray(value.position); camera.quaternion.fromArray(value.quaternion); camera.scale.fromArray(value.scale);
  camera.projectionMatrix.fromArray(value.projection); camera.projectionMatrixInverse.fromArray(value.projectionInverse); camera.updateMatrixWorld(true);
}
async function applyInput(msg, acknowledge) {
  applyingInput = true;
  try {
    if (msg.width && msg.height) { runtime.renderer.domElement.width = msg.width; runtime.renderer.domElement.height = msg.height; }
    const nextProfileKey = JSON.stringify([msg.performance || {}, msg.width, msg.height]);
    if (profileKey !== nextProfileKey) await controller.updateProfile(msg.performance || {}, { width: msg.width, height: msg.height });
    profileKey = nextProfileKey;
    updateCamera(msg.camera); runtime.moving = msg.moving;
    if (msg.floorMatrix && owned.photographicStudio.ground) { owned.photographicStudio.ground.matrix.fromArray(msg.floorMatrix); owned.photographicStudio.ground.matrixWorldNeedsUpdate = true; }
    if (msg.transforms) {
      if (msg.transforms.length !== owned.objects.length * 16) throw new Error('Preview placement count changed');
      for (let i = 0; i < owned.objects.length; i++) { owned.objects[i].matrix.fromArray(msg.transforms, i * 16); owned.objects[i].matrixWorldNeedsUpdate = true; }
    }
    if (msg.transforms || msg.frameChanged) controller.invalidate(msg.transforms ? { transformsChanged: true } : { sceneChanged: false });
    if (msg.quality) runtime.previewConfiguration.quality = msg.quality;
    if (msg.performance) runtime.previewConfiguration.performance = msg.performance;
  } catch (error) { postMessage({ type: 'error', message: error?.message || String(error) }); }
  finally {
    applyingInput = false;
    if (acknowledge) postMessage({ type: 'input-ack' });
    if (queuedInput && controller?.progress.state === 'ready') { const latest = queuedInput; queuedInput = null; void applyInput(latest, false); }
    else schedule();
  }
}
self.onmessage = async event => {
  const msg = event.data;
  try {
    if (msg.type === 'probe') {
      if (!navigator.gpu) throw new Error('WebGPU unavailable');
      const adapter = await navigator.gpu.requestAdapter({ powerPreference: 'high-performance', featureLevel: 'compatibility' });
      if (!adapter) throw new Error('WebGPU unavailable');
      const l = adapter.limits;
      postMessage({ type: 'probe', limits: { maxBufferSize: l.maxBufferSize, maxStorageBufferBindingSize: l.maxStorageBufferBindingSize, maxTextureDimension2D: l.maxTextureDimension2D, maxTextureArrayLayers: l.maxTextureArrayLayers, shaderF16: adapter.features.has('shader-f16') } });
    } else if (msg.type === 'init') {
      sendProgress('reconstruct');
      owned = reconstructPreviewScene(msg.scene);
      const element = { width: msg.width, height: msg.height, clientWidth: msg.width, clientHeight: msg.height };
      runtime = { ...owned, THREE, previewConfiguration: msg.scene.configuration,
        renderer: { domElement: element, toneMapping: msg.scene.toneMapping, toneMappingExposure: msg.scene.exposure, getClearAlpha: () => msg.scene.clearAlpha }, moving: false };
      updateCamera(msg.camera);
      profileKey = JSON.stringify([runtime.previewConfiguration.performance || {}, msg.width, msg.height]);
      controller = createPreviewWorkerRenderer(runtime, {
        isHidden: () => paused, diagnostic: msg.diagnostic,
        requestRender: schedule,
        onFailure: error => postMessage({ type: 'error', message: error?.message || String(error) }),
        onPresent: (image, background) => { presented = image; backdrop = background; present(); },
        buildBVH: (geometry, options) => new MeshBVH(geometry, options),
        onStage: stage => { const name = typeof stage === 'string' ? stage : 'bvh'; if (name !== 'trace' || performance.now() - lastProgress >= 200) sendProgress(name, { detail: stage }); if (name === 'ready' && queuedInput && !applyingInput) { const latest = queuedInput; queuedInput = null; void applyInput(latest, false); } },
        onResource: async (admission, deviceLimits, packedNodes, requestedPerformance, viewport, fixedTileSize = null) => {
          const source = msg.scene.resourcePlan;
          const plan = planPreviewResources({ admission: { ...admission, sourceGeometryBytes: source.sourceGeometryBytes, sourceTextureBytes: source.sourceTextureBytes, objectCount: source.objectCount, geometryCount: source.geometryCount },
            viewport: viewport || { width: runtime.renderer.domElement.width, height: runtime.renderer.domElement.height }, performance: requestedPerformance,
            deviceLimits, packedNodes, phase: packedNodes ? 'render' : 'build', ownedGeometry: true, denoiseTileSize: fixedTileSize, inferenceBounds: packedNodes ? inferenceBounds : null, budgetBytes: source.memory.budgetBytes });
          if (!plan.ok) { postMessage({ type: 'resource-diagnostic', memory: plan.memory, profile: plan.profile, reason: plan.reason }); throw new Error(plan.reason); }
          const id = ++admissionId;
          const accepted = new Promise(resolve => admissions.set(id, resolve));
          postMessage({ type: 'resource', id, memory: plan.memory, profile: plan.profile });
          if (!await accepted) throw new Error('Preview resource budget is in use');
          if (packedNodes) inferenceBounds = plan.memory.inference;
          return plan;
        },
      });
      sendProgress('preparing'); schedule();
    } else if (msg.type === 'resource-ack') { admissions.get(msg.id)?.(msg.ok); admissions.delete(msg.id);
    } else if (msg.type === 'input' && controller) {
      if (controller.progress.state !== 'ready' || applyingInput) { queuedInput = mergePreviewInput(queuedInput, msg); postMessage({ type: 'input-ack' }); }
      else void applyInput(msg, true);
    } else if (msg.type === 'frame-ack') {
      awaitingFrame = false; if (missedFrame) present();
    } else if (msg.type === 'pause') {
      paused = msg.paused;
      if (paused) controller?.invalidate(); else schedule();
    } else if (msg.type === 'dispose') {
      disposed = true; controller?.dispose(); owned?.dispose(); close();
    }
  } catch (error) { postMessage({ type: 'error', message: error?.message || String(error) }); }
};
